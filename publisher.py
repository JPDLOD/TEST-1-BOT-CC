# -*- coding: utf-8 -*-
import json
import logging
import asyncio
from typing import List, Tuple, Dict, Set
import sqlite3

from telegram.error import RetryAfter, TimedOut, NetworkError, TelegramError
from telegram.ext import ContextTypes
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import DB_FILE, SOURCE_CHAT_ID, TARGET_CHAT_ID, BACKUP_CHAT_ID, PAUSE
from database import get_unsent_drafts, mark_sent, get_buttons_map_for_ids

logger = logging.getLogger(__name__)

ACTIVE_BACKUP: bool = True
STATS = {"cancelados": 0, "eliminados": 0}
SCHEDULED_LOCK: Set[int] = set()

def is_active_backup() -> bool:
    return True

def set_active_backup(value: bool) -> None:
    pass

def get_active_targets() -> List[int]:
    targets = [TARGET_CHAT_ID]
    if BACKUP_CHAT_ID:
        targets.append(BACKUP_CHAT_ID)
    return targets

async def _send_with_backoff(func_coro_factory, *, base_pause: float):
    tries = 0
    while True:
        try:
            msg = await func_coro_factory()
            await asyncio.sleep(max(0.0, base_pause))
            return True, msg
        except RetryAfter as e:
            wait = getattr(e, "retry_after", None)
            if wait is None:
                import re
                m = re.search(r"Retry in (\d+)", str(e))
                wait = int(m.group(1)) if m else 3
            logger.warning(f"RetryAfter: esperando {wait}s")
            await asyncio.sleep(wait + 1.0)
            tries += 1
        except TimedOut:
            logger.warning("TimedOut: esperando 3s")
            await asyncio.sleep(3.0)
            tries += 1
        except NetworkError:
            logger.warning("NetworkError: esperando 3s")
            await asyncio.sleep(3.0)
            tries += 1
        except TelegramError as e:
            if "Flood control exceeded" in str(e):
                logger.warning("Flood control: esperando 5s")
                await asyncio.sleep(5.0)
                tries += 1
            else:
                logger.error(f"TelegramError: {e}")
                return False, None
        except Exception as e:
            logger.exception(f"Error enviando: {e}")
            return False, None

        if tries >= 5:
            logger.error("Demasiados reintentos")
            return False, None

def _poll_payload_from_raw(raw: dict, message_id: int = None):
    p = raw.get("poll") or {}
    question = p.get("question", "Pregunta")
    options_src = p.get("options", []) or []
    options = [o.get("text", "") for o in options_src]

    is_anon = p.get("is_anonymous", True)
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
    else:
        kwargs["type"] = "quiz"
        correct_option_id = p.get("correct_option_id")
        if correct_option_id is not None:
            try:
                kwargs["correct_option_id"] = int(correct_option_id)
            except:
                kwargs["correct_option_id"] = 0
        else:
            kwargs["correct_option_id"] = 0

    if p.get("open_period") is not None and p.get("close_date") is None:
        try:
            kwargs["open_period"] = int(p["open_period"])
        except:
            pass
    elif p.get("close_date") is not None:
        try:
            kwargs["close_date"] = int(p["close_date"])
        except:
            pass

    if is_quiz and p.get("explanation"):
        kwargs["explanation"] = str(p["explanation"])

    return kwargs, is_quiz

async def _publicar_rows(context: ContextTypes.DEFAULT_TYPE, *, rows: List[Tuple[int, str, str]],
                         targets: List[int], mark_as_sent: bool) -> Tuple[int, int, Dict[int, List[int]]]:
    
    publicados = 0
    fallidos = 0
    enviados_ids: List[int] = []
    posted_by_target: Dict[int, List[int]] = {t: [] for t in targets}
    
    ids = [mid for (mid, _, _) in rows]
    buttons_map = get_buttons_map_for_ids(DB_FILE, ids)

    for mid, _t, raw in rows:
        try:
            data = json.loads(raw or "{}")
        except Exception as e:
            logger.error(f"Error parseando JSON {mid}: {e}")
            data = {}

        any_success = False
        
        for dest in targets:
            
            msg_buttons = buttons_map.get(mid, [])
            keyboard = None
            if msg_buttons:
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton(label, url=url)] for label, url in msg_buttons
                ])
            
            if "poll" in data:
                try:
                    base_kwargs, is_quiz = _poll_payload_from_raw(data, message_id=mid)
                    kwargs = dict(base_kwargs)
                    kwargs["chat_id"] = dest
                    
                    if keyboard:
                        kwargs["reply_markup"] = keyboard
                    
                    if is_quiz:
                        cid = kwargs.get("correct_option_id", 0)
                        logger.info(f"Enviando quiz {mid} a {dest} → respuesta {chr(65+cid)}")
                    
                    coro_factory = lambda k=kwargs: context.bot.send_poll(**k)
                    ok, msg = await _send_with_backoff(coro_factory, base_pause=PAUSE)
                    
                except Exception as e:
                    logger.error(f"Error poll {mid}: {e}")
                    ok, msg = False, None
            else:
                coro_factory = lambda d=dest, m=mid: context.bot.copy_message(
                    chat_id=d, from_chat_id=SOURCE_CHAT_ID, message_id=m
                )
                ok, msg = await _send_with_backoff(coro_factory, base_pause=PAUSE)
                
                if ok and msg and keyboard:
                    try:
                        await context.bot.edit_message_reply_markup(
                            chat_id=dest,
                            message_id=msg.message_id,
                            reply_markup=keyboard
                        )
                        logger.info(f"Botones agregados a mensaje {msg.message_id}")
                    except Exception as e:
                        logger.error(f"Error agregando botones: {e}")

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
    all_rows = get_unsent_drafts(DB_FILE)
    if not all_rows:
        return 0, 0, {t: [] for t in targets}
    rows = [(m, t, r) for (m, t, r) in all_rows if m not in SCHEDULED_LOCK]
    if not rows:
        return 0, 0, {t: [] for t in targets}
    return await _publicar_rows(context, rows=rows, targets=targets, mark_as_sent=mark_as_sent)

async def publicar_ids(context: ContextTypes.DEFAULT_TYPE, *, ids: List[int],
                       targets: List[int], mark_as_sent: bool):
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

def handle_poll_update(update, context):
    pass

def handle_poll_answer_update(update, context):
    pass

def detect_voted_polls_on_save(message_id: int, raw_json: str):
    pass
