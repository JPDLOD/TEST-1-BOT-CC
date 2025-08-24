# -*- coding: utf-8 -*-
import json
import logging
import re
from typing import List, Tuple, Dict, Set

from telegram.error import RetryAfter, TimedOut, NetworkError, TelegramError
from telegram.ext import ContextTypes
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

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
                m = re.search(r"Retry in (\d+)", str(e))
                wait = int(m.group(1)) if m else 3
            logger.warning(f"RetryAfter: esperando {wait}s …")
            import asyncio
            await asyncio.sleep(wait + 1.0)
            tries += 1
        except (TimedOut, NetworkError):
            logger.warning("Timeout/NetworkError: esperando 3s …")
            import asyncio
            await asyncio.sleep(3.0)
            tries += 1
        except TelegramError as e:
            if "Flood control exceeded" in str(e):
                logger.warning("Flood control… esperando 5s …")
                import asyncio
                await asyncio.sleep(5.0)
                tries += 1
            else:
                logger.error(f"TelegramError no recuperable: {e}")
                return False, None
        except Exception as e:
            logger.exception(f"Error enviando: {e}")
            return False, None

        if tries >= 5:
            logger.error("Demasiados reintentos; abandono este mensaje.")
            return False, None

# ========= Detectar regla @@@ label | url =========
_AT_RULE = re.compile(r"^\s*@@@\s*(.+?)\s*\|\s*(\S+)\s*$", re.MULTILINE)

def _extract_at_rule(raw: dict) -> Tuple[str, str, str] | None:
    """
    Si encuentra una línea '@@@ label | url' en el texto/caption:
      - devuelve (texto_sin_regla, label, url)
    Si no, None.
    """
    text = (raw.get("text") or raw.get("caption") or "").strip()
    if not text:
        return None
    m = _AT_RULE.search(text)
    if not m:
        return None
    label = m.group(1).strip()
    url = m.group(2).strip()
    # quitar la línea con @@@ del cuerpo
    new_text = _AT_RULE.sub("", text, count=1).strip()
    # para asegurar vista previa, dejamos el URL visible en el texto:
    if url not in new_text:
        new_text = f"{new_text}\n\n{url}" if new_text else url
    return (new_text, label, url)

# ========= Publicadores =========
async def _publicar_rows(context: ContextTypes.DEFAULT_TYPE, *, rows: List[Tuple[int, str, str]],
                         targets: List[int], mark_as_sent: bool) -> Tuple[int, int, Dict[int, List[int]]]:
    publicados = 0
    fallidos = 0
    enviados_ids: List[int] = []
    posted_by_target: Dict[int, List[int]] = {t: [] for t in targets}

    for mid, _snip, raw in rows:
        try:
            data = json.loads(raw or "{}")
        except Exception:
            data = {}

        # ¿aplica la regla @@@?
        at_rule = _extract_at_rule(data)

        any_success = False
        for dest in targets:
            if "poll" in data:
                # reconstrucción de encuestas
                p = data.get("poll") or {}
                question = p.get("question", "Pregunta")
                options = [o.get("text", "") for o in (p.get("options") or [])]
                is_anon = p.get("is_anonymous", True)
                allows_multiple = p.get("allows_multiple_answers", False)
                ptype = (p.get("type") or "regular").lower().strip()

                kwargs = dict(question=question, options=options, is_anonymous=is_anon)
                if ptype == "quiz":
                    kwargs["type"] = "quiz"
                    cid = p.get("correct_option_id")
                    try:
                        cid = int(cid) if cid is not None else None
                    except Exception:
                        cid = None
                    if cid is None or cid < 0 or cid >= len(options):
                        cid = 0
                    kwargs["correct_option_id"] = cid
                    if p.get("explanation"):
                        kwargs["explanation"] = str(p["explanation"])
                else:
                    kwargs["allows_multiple_answers"] = bool(allows_multiple)

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

                kwargs["chat_id"] = dest
                coro_factory = lambda k=kwargs: context.bot.send_poll(**k)
                ok, msg = await _send_with_backoff(coro_factory, base_pause=PAUSE)

            elif at_rule:
                # Envío modificado con botón + preview
                new_text, label, url = at_rule
                kb = InlineKeyboardMarkup([[InlineKeyboardButton(label, url=url)]])
                coro_factory = lambda d=dest, t=new_text, k=kb: context.bot.send_message(
                    chat_id=d, text=t, reply_markup=k, disable_web_page_preview=False
                )
                ok, msg = await _send_with_backoff(coro_factory, base_pause=PAUSE)

            else:
                # Copia 1:1 (por defecto)
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
    return await _publicar_rows(context, rows=rows, targets=targets, mark_as_sent=mark_as_sent)

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
    return await _publicar_rows(context, rows=rows, targets=targets, mark_as_sent=mark_as_sent)

async def publicar_todo_activos(context: ContextTypes.DEFAULT_TYPE):
    pubs, fails, _ = await publicar(context, targets=get_active_targets(), mark_as_sent=True)
    return pubs, fails
