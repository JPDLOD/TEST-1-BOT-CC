# -*- coding: utf-8 -*-
import json
import logging
from typing import List, Tuple, Dict, Set

from telegram.error import RetryAfter, TimedOut, NetworkError, TelegramError
from telegram.ext import ContextTypes

from config import DB_FILE, SOURCE_CHAT_ID, TARGET_CHAT_ID, BACKUP_CHAT_ID, PAUSE
from database import get_unsent_drafts, mark_sent

logger = logging.getLogger(__name__)

# ========= Estado de targets =========
ACTIVE_BACKUP: bool = True  # por defecto ON

def is_active_backup() -> bool:
    """Lee el estado actual del backup (True/False)."""
    return ACTIVE_BACKUP

def set_active_backup(value: bool) -> None:
    """Actualiza el estado global del backup de forma segura."""
    global ACTIVE_BACKUP
    ACTIVE_BACKUP = bool(value)

def get_active_targets() -> List[int]:
    targets = [TARGET_CHAT_ID]
    if is_active_backup() and BACKUP_CHAT_ID:
        targets.append(BACKUP_CHAT_ID)
    return targets

# ========= Contadores / locks que usan otros módulos =========
STATS = {"cancelados": 0, "eliminados": 0}
SCHEDULED_LOCK: Set[int] = set()

# ========= Backoff para envíos =========
async def _send_with_backoff(func_coro_factory, *, base_pause: float):
    tries = 0
    while True:
        try:
            msg = await func_coro_factory()
            # pausa corta entre mensajes
            import asyncio
            await asyncio.sleep(max(0.0, base_pause))
            return True, msg
        except RetryAfter as e:
            wait = getattr(e, "retry_after", None)
            if wait is None:
                import re
                m = re.search(r"Retry in (\d+)", str(e))
                wait = int(m.group(1)) if m else 3
            logger.warning(f"RetryAfter: esperando {wait}s …")
            import asyncio
            await asyncio.sleep(wait + 1.0)
            tries += 1
        except TimedOut:
            logger.warning("TimedOut: esperando 3s …")
            import asyncio
            await asyncio.sleep(3.0);  tries += 1
        except NetworkError:
            logger.warning("NetworkError: esperando 3s …")
            import asyncio
            await asyncio.sleep(3.0);  tries += 1
        except TelegramError as e:
            if "Flood control exceeded" in str(e):
                logger.warning("Flood control… esperando 5s …")
                import asyncio
                await asyncio.sleep(5.0);  tries += 1
            else:
                logger.error(f"TelegramError no recuperable: {e}")
                return False, None
        except Exception as e:
            logger.exception(f"Error enviando: {e}")
            return False, None

        if tries >= 5:
            logger.error("Demasiados reintentos; abandono este mensaje.")
            return False, None

# ========= Encuestas =========
def _poll_payload_from_raw(raw: dict):
    """
    Devuelve kwargs para send_poll y un flag is_quiz. Reglas:
    - Si el origen es QUIZ y trae correct_option_id válido -> enviamos como quiz (mantenemos índice).
    - Si el origen es QUIZ pero NO trae correct_option_id (Telegram no lo expone si el bot no creó la encuesta)
      -> degradamos a 'regular' con allows_multiple_answers=False (para no marcar A por error).
    - Si el origen es 'regular' -> replicamos 'regular' y forzamos allows_multiple_answers=False.
    """
    p = (raw or {}).get("poll") or {}

    question = str(p.get("question", "Pregunta"))
    options_src = p.get("options", []) or []
    options = [str(o.get("text", "")) for o in options_src]

    is_anon = bool(p.get("is_anonymous", True))
    ptype = str(p.get("type") or "regular").lower().strip()

    # Presentes solo si el bot es creador de la encuesta original
    cid_raw = p.get("correct_option_id")
    cid_present = isinstance(cid_raw, int)

    # Base kwargs comunes
    kwargs = dict(
        question=question,
        options=options,
        is_anonymous=is_anon,
    )

    # Manejo de ventanas de tiempo (si vienen)
    if p.get("open_period") is not None and p.get("close_date") is None:
        try:
            kwargs["open_period"] = int(p["open_period"])
        except Exception:
            pass
    elif p.get("close_date") is not None:
        try:
            kwargs["close_date"] = int(p["close_date"])
        except Exception:
            pass

    # Decisión final
    if ptype == "quiz" and cid_present:
        # Validar rango
        cid = int(cid_raw)
        if not (0 <= cid < len(options)):
            # Si por algún motivo no cuadra, NO forzamos A; degradamos a regular
            kwargs["allows_multiple_answers"] = False
            return kwargs, False
        kwargs["type"] = "quiz"
        kwargs["correct_option_id"] = cid
        # Explicación (opcional)
        if p.get("explanation"):
            kwargs["explanation"] = str(p["explanation"])
        return kwargs, True

    # Cualquier otro caso -> regular de una sola respuesta
    kwargs["allows_multiple_answers"] = False
    return kwargs, False

# ========= Publicadores =========
async def publicar_rows(context: ContextTypes.DEFAULT_TYPE, *, rows: List[Tuple[int, str, str]],
                         targets: List[int], mark_as_sent: bool) -> Tuple[int, int, Dict[int, List[int]]]:
    publicados = 0
    fallidos = 0
    enviados_ids: List[int] = []
    posted_by_target: Dict[int, List[int]] = {t: [] for t in targets}

    for mid, _t, raw in rows:
        try:
            data = json.loads(raw or "{}")
        except Exception:
            data = {}

        any_success = False
        for dest in targets:
            if "poll" in data:
                base_kwargs, is_quiz = _poll_payload_from_raw(data)
                kwargs = dict(base_kwargs)
                kwargs["chat_id"] = dest
                coro_factory = lambda k=kwargs: context.bot.send_poll(**k)
                ok, msg = await _send_with_backoff(coro_factory, base_pause=PAUSE)
            else:
                coro_factory = lambda d=dest, m=mid: context.bot.copy_message(
                    chat_id=d, from_chat_id=SOURCE_CHAT_ID, message_id=m
                )
                ok, msg = await _send_with_backoff(coro_factory, base_pause=PAUSE)

            if ok:
                any_success = True
                if msg and getattr(msg, "message_id", None):
                    posted_by_target[dest].append(msg.message_id)

        if any_success:
            publicados += 1
            if mark_as_sent:
                enviados_ids.append(mid)
        else:
            fallidos += 1

    if enviados_ids and mark_as_sent:
        mark_sent(DB_FILE, enviados_ids)

    return publicados, fallidos, posted_by_target

async def publicar(context: ContextTypes.DEFAULT_TYPE, *, targets: List[int], mark_as_sent: bool):
    """Envía la cola completa EXCLUYENDO los bloqueados (SCHEDULED_LOCK)."""
    all_rows = get_unsent_drafts(DB_FILE)  # [(message_id, text, raw_json)]
    if not all_rows:
        return 0, 0, {t: [] for t in targets}
    rows = [(m, t, r) for (m, t, r) in all_rows if m not in SCHEDULED_LOCK]
    if not rows:
        return 0, 0, {t: [] for t in targets}
    return await publicar_rows(context, rows=rows, targets=targets, mark_as_sent=mark_as_sent)

async def publicar_ids(context: ContextTypes.DEFAULT_TYPE, *, ids: List[int],
                       targets: List[int], mark_as_sent: bool):
    # Query puntual sin duplicar lógica pública del módulo database
    import sqlite3
    if not ids:
        return 0, 0, {t: [] for t in targets}
    placeholders = ",".join("?" for _ in ids)
    sql = f"SELECT message_id, snippet, raw_json FROM drafts WHERE sent=0 AND deleted=0 AND message_id IN ({placeholders}) ORDER BY message_id ASC"
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    rows = list(cur.execute(sql, ids).fetchall())
    con.close()
    if not rows:
        return 0, 0, {t: [] for t in targets}
    return await publicar_rows(context, rows=rows, targets=targets, mark_as_sent=mark_as_sent)

async def publicar_todo_activos(context: ContextTypes.DEFAULT_TYPE):
    pubs, fails, _ = await publicar(context, targets=get_active_targets(), mark_as_sent=True)
    return pubs, fails
