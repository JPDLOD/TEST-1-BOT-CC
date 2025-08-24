# -*- coding: utf-8 -*-
import json
import logging
import re
from typing import List, Tuple, Dict, Set

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import RetryAfter, TimedOut, NetworkError, TelegramError
from telegram.ext import ContextTypes

from config import DB_FILE, SOURCE_CHAT_ID, TARGET_CHAT_ID, BACKUP_CHAT_ID, PAUSE
from database import get_unsent_drafts, mark_sent
from utils import safe_sleep

logger = logging.getLogger(__name__)

# ========= Estado de targets =========
ACTIVE_BACKUP: bool = True  # por defecto ON

def is_active_backup() -> bool:
    return ACTIVE_BACKUP

def set_active_backup(value: bool) -> None:
    global ACTIVE_BACKUP
    ACTIVE_BACKUP = bool(value)

def get_active_targets() -> List[int]:
    targets = [TARGET_CHAT_ID]
    if is_active_backup() and BACKUP_CHAT_ID:
        targets.append(BACKUP_CHAT_ID)
    return targets

# ========= Contadores / locks =========
STATS = {"cancelados": 0, "eliminados": 0}
SCHEDULED_LOCK: Set[int] = set()

# ========= Backoff para envíos =========
async def _send_with_backoff(func_coro_factory, *, base_pause: float):
    tries = 0
    while True:
        try:
            msg = await func_coro_factory()
            await safe_sleep(base_pause)
            return True, msg
        except RetryAfter as e:
            wait = getattr(e, "retry_after", None)
            if wait is None:
                m = re.search(r"Retry in (\d+)", str(e))
                wait = int(m.group(1)) if m else 3
            logger.warning(f"RetryAfter: esperando {wait}s …")
            await safe_sleep(wait + 1.0)
            tries += 1
        except (TimedOut, NetworkError):
            logger.warning("Timeout/NetworkError: esperando 3s …")
            await safe_sleep(3.0)
            tries += 1
        except TelegramError as e:
            if "Flood control exceeded" in str(e):
                m = re.search(r"Retry in (\d+)", str(e))
                wait = int(m.group(1)) if m else 5
                logger.warning(f"Flood control: esperando {wait}s …")
                await safe_sleep(wait + 1.0)
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

# ========= Encuestas =========
def _poll_payload_from_raw(raw: dict):
    p = raw.get("poll") or {}
    question = p.get("question", "Pregunta")
    options_src = p.get("options", []) or []
    options  = [o.get("text", "") for o in options_src]

    is_anon  = p.get("is_anonymous", True)
    allows_multiple = p.get("allows_multiple_answers", False)
    ptype = (p.get("type") or "regular").lower().strip()
    is_quiz = (ptype == "quiz")

    kwargs = dict(
        question=question,
        options=options,
        is_anonymous=is_anon,
    )

    if not is_quiz:
        kwargs["allows_multiple_answers"] = bool(allows_multiple)

    if is_quiz:
        kwargs["type"] = "quiz"
        cid = p.get("correct_option_id")
        try:
            cid = int(cid) if cid is not None else None
        except Exception:
            cid = None
        if cid is None or cid < 0 or cid >= len(options):
            cid = 0
        kwargs["correct_option_id"] = cid

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

    if is_quiz and p.get("explanation"):
        kwargs["explanation"] = str(p["explanation"])

    return kwargs, is_quiz

# ========= Post-proceso: botón @@@ =========
_IV_LINE = re.compile(r"(?mi)^\s*@@@\s*(.+?)\s*\|\s*(https?://\S+)\s*$")

async def _apply_iv_button_if_tagged(context: ContextTypes.DEFAULT_TYPE, *,
                                     dest_chat_id: int, dest_message_id: int,
                                     raw_msg: dict):
    """Si el texto/caption contiene una línea '@@@ TÍTULO | URL',
    borra esa línea y agrega un botón con ese enlace."""
    original_text = (raw_msg.get("text") or "") if raw_msg.get("text") else None
    original_cap  = (raw_msg.get("caption") or "") if raw_msg.get("caption") else None
    field = "text" if original_text is not None else ("caption" if original_cap is not None else None)
    if not field:
        return  # nada que editar

    src = original_text if field == "text" else original_cap
    m = _IV_LINE.search(src or "")
    if not m:
        return

    btn_text = m.group(1).strip()
    btn_url  = m.group(2).strip()
    # quitar solo esa línea
    cleaned = _IV_LINE.sub("", src, count=1).strip()
    if not cleaned:
        cleaned = "·"  # Telegram no admite texto/caption vacío en edición

    markup = InlineKeyboardMarkup([[InlineKeyboardButton(btn_text, url=btn_url)]])

    try:
        if field == "text":
            await context.bot.edit_message_text(
                chat_id=dest_chat_id,
                message_id=dest_message_id,
                text=cleaned,
                reply_markup=markup,
                disable_web_page_preview=False  # botones no crean preview, pero dejamos explícito
            )
        else:
            await context.bot.edit_message_caption(
                chat_id=dest_chat_id,
                message_id=dest_message_id,
                caption=cleaned,
                reply_markup=markup
            )
    except Exception as e:
        logger.warning(f"No se pudo aplicar botón @@@: {e}")

# ========= Publicadores =========
async def _publicar_rows(context: ContextTypes.DEFAULT_TYPE, *, rows: List[Tuple[int, str, str]],
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
                base_kwargs, _ = _poll_payload_from_raw(data)
                kwargs = dict(base_kwargs)
                kwargs["chat_id"] = dest
                coro_factory = lambda k=kwargs: context.bot.send_poll(**k)
                ok, msg = await _send_with_backoff(coro_factory, base_pause=PAUSE)
            else:
                coro_factory = lambda d=dest, m=mid: context.bot.copy_message(
                    chat_id=d, from_chat_id=SOURCE_CHAT_ID, message_id=m
                )
                ok, msg = await _send_with_backoff(coro_factory, base_pause=PAUSE)
                # aplicar botón @@@ si corresponde
                if ok and msg and getattr(msg, "message_id", None):
                    try:
                        await _apply_iv_button_if_tagged(
                            context,
                            dest_chat_id=dest,
                            dest_message_id=msg.message_id,
                            raw_msg=data
                        )
                    except Exception as e:
                        logger.warning(f"Post-proceso @@@ falló: {e}")

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
