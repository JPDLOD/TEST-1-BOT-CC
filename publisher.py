# -*- coding: utf-8 -*-
import json
import logging
import re
from typing import List, Tuple, Dict, Set, Optional
import asyncio

from telegram.error import RetryAfter, TimedOut, NetworkError, TelegramError
from telegram.ext import ContextTypes
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import DB_FILE, SOURCE_CHAT_ID, TARGET_CHAT_ID, BACKUP_CHAT_ID, PAUSE
from database import get_unsent_drafts, mark_sent, get_buttons_map_for_ids

logger = logging.getLogger(__name__)

ACTIVE_BACKUP: bool = True
STATS = {"cancelados": 0, "eliminados": 0}
SCHEDULED_LOCK: Set[int] = set()
DETECTED_CORRECT_ANSWERS: Dict[int, int] = {}
POLL_ID_TO_MESSAGE_ID: Dict[str, int] = {}

def is_active_backup() -> bool:
    return True

def set_active_backup(value: bool) -> None:
    pass

def get_active_targets() -> List[int]:
    targets = [TARGET_CHAT_ID]
    if BACKUP_CHAT_ID:
        targets.append(BACKUP_CHAT_ID)
    return targets

JUSTIFICATION_LINK_PATTERN = re.compile(r'https?://t\.me/ccjustificaciones/(\d+(?:[,\-]\d+)*)', re.IGNORECASE)

def extract_justification_from_text(text: str) -> Optional[Tuple[List[int], str, str]]:
    if not text:
        return None
    
    justification_ids = []
    case_name = ""
    
    case_pattern = re.search(r'^(.*?)(?=https://)', text)
    if case_pattern:
        potential_case = case_pattern.group(1).strip()
        if potential_case:
            case_name = potential_case.replace("📚", "").replace("*", "").replace("_", "").strip()
    
    for match in JUSTIFICATION_LINK_PATTERN.finditer(text):
        id_string = match.group(1)
        parts = id_string.split(',')
        for part in parts:
            if '-' in part:
                try:
                    start, end = map(int, part.split('-'))
                    justification_ids.extend(range(start, end + 1))
                except:
                    pass
            else:
                try:
                    justification_ids.append(int(part))
                except:
                    pass
    
    if not justification_ids:
        return None
    
    justification_ids = sorted(list(set(justification_ids)))
    clean_text = JUSTIFICATION_LINK_PATTERN.sub('', text).strip()
    
    return justification_ids, case_name, clean_text

def detect_voted_polls_on_save(message_id: int, raw_json: str):
    try:
        data = json.loads(raw_json)
        if "poll" not in data:
            return
        
        poll = data["poll"]
        if poll.get("type") != "quiz":
            return
        
        if "id" in poll:
            poll_id = str(poll["id"])
            POLL_ID_TO_MESSAGE_ID[poll_id] = message_id
            
            if "correct_option_id" in poll and poll["correct_option_id"] is not None:
                try:
                    correct_id = int(poll["correct_option_id"])
                    DETECTED_CORRECT_ANSWERS[message_id] = correct_id
                except:
                    pass
    except Exception as e:
        logger.error(f"Error: {e}")

async def handle_poll_update(update, context):
    if not update.poll:
        return
    
    poll = update.poll
    poll_id = str(poll.id)
    message_id = POLL_ID_TO_MESSAGE_ID.get(poll_id)
    
    if not message_id:
        return
    
    if hasattr(poll, 'correct_option_id') and poll.correct_option_id is not None:
        correct_id = poll.correct_option_id
        DETECTED_CORRECT_ANSWERS[message_id] = correct_id
        return
    
    for i, option in enumerate(poll.options):
        if option.voter_count > 0:
            DETECTED_CORRECT_ANSWERS[message_id] = i
            return

async def handle_poll_answer_update(update, context):
    if not update.poll_answer:
        return
    
    poll_answer = update.poll_answer
    poll_id = str(poll_answer.poll_id)
    option_ids = list(poll_answer.option_ids) if poll_answer.option_ids else []
    
    message_id = POLL_ID_TO_MESSAGE_ID.get(poll_id)
    if not message_id or not option_ids:
        return
    
    chosen_option = option_ids[0]
    DETECTED_CORRECT_ANSWERS[message_id] = chosen_option

def get_correct_answer_simple(message_id: int, poll_data: dict) -> int:
    if message_id in DETECTED_CORRECT_ANSWERS:
        return DETECTED_CORRECT_ANSWERS[message_id]
    
    if "correct_option_id" in poll_data and poll_data["correct_option_id"] is not None:
        try:
            correct_id = int(poll_data["correct_option_id"])
            DETECTED_CORRECT_ANSWERS[message_id] = correct_id
            return correct_id
        except:
            pass
    
    for i, option in enumerate(poll_data.get("options", [])):
        if isinstance(option, dict) and option.get("voter_count", 0) > 0:
            DETECTED_CORRECT_ANSWERS[message_id] = i
            return i
    
    return 0

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
        
        if message_id:
            correct_option_id = get_correct_answer_simple(message_id, p)
        else:
            correct_option_id = 0
        
        kwargs["correct_option_id"] = correct_option_id

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
                m = re.search(r"Retry in (\d+)", str(e))
                wait = int(m.group(1)) if m else 3
            await asyncio.sleep(wait + 1.0)
            tries += 1
        except TimedOut:
            await asyncio.sleep(3.0)
            tries += 1
        except NetworkError:
            await asyncio.sleep(3.0)
            tries += 1
        except TelegramError as e:
            if "Flood control exceeded" in str(e):
                await asyncio.sleep(5.0)
                tries += 1
            else:
                return False, None
        except Exception:
            return False, None

        if tries >= 5:
            return False, None

async def _publicar_rows(context: ContextTypes.DEFAULT_TYPE, *, rows: List[Tuple[int, str, str]],
                         targets: List[int], mark_as_sent: bool) -> Tuple[int, int, Dict[int, List[int]]]:
    
    all_ids = [mid for mid, _, _ in rows]
    buttons_map = get_buttons_map_for_ids(DB_FILE, all_ids)
    
    messages_to_skip = set()
    justification_buttons_for_previous = {}
    
    for i, (mid, _t, raw) in enumerate(rows):
        try:
            data = json.loads(raw or "{}")
            text_content = data.get("text", "") or data.get("caption", "")
            
            if not text_content:
                continue
            
            if 'https://t.me/ccjustificaciones/' in text_content.lower():
                justification_info = extract_justification_from_text(text_content)
                
                if justification_info:
                    justification_ids, case_name, clean_text = justification_info
                    
                    if not clean_text.strip():
                        
                        if i > 0:
                            try:
                                bot_info = await context.bot.get_me()
                                bot_username = bot_info.username
                                
                                ids_string = "_".join(map(str, justification_ids))
                                deep_link = f"https://t.me/{bot_username}?start=just_{ids_string}"
                                
                                if case_name:
                                    button_text = f"Ver justificación {case_name} 📚"
                                else:
                                    button_text = "Ver justificación 📚"
                                
                                button = InlineKeyboardMarkup([[
                                    InlineKeyboardButton(button_text, url=deep_link)
                                ]])
                                
                                justification_buttons_for_previous[i-1] = button
                            except:
                                pass
                        
                        messages_to_skip.add(i)
        except:
            pass
    
    publicados = 0
    fallidos = 0
    enviados_ids: List[int] = []
    posted_by_target: Dict[int, List[int]] = {t: [] for t in targets}

    for i, (mid, _t, raw) in enumerate(rows):
        if i in messages_to_skip:
            if mark_as_sent:
                enviados_ids.append(mid)
            continue
        
        try:
            data = json.loads(raw or "{}")
        except:
            data = {}

        text_content = data.get("text", "") or data.get("caption", "")
        if text_content and JUSTIFICATION_LINK_PATTERN.search(text_content):
            clean_text = JUSTIFICATION_LINK_PATTERN.sub('', text_content).strip()
            if clean_text:
                if "text" in data:
                    data["text"] = clean_text
                elif "caption" in data:
                    data["caption"] = clean_text

        any_success = False
        for dest in targets:
            sent_message = None
            
            if "poll" in data:
                try:
                    base_kwargs, is_quiz = _poll_payload_from_raw(data, message_id=mid)
                    kwargs = dict(base_kwargs)
                    kwargs["chat_id"] = dest
                    
                    if i in justification_buttons_for_previous:
                        kwargs["reply_markup"] = justification_buttons_for_previous[i]
                    
                    coro_factory = lambda k=kwargs: context.bot.send_poll(**k)
                    ok, msg = await _send_with_backoff(coro_factory, base_pause=PAUSE)
                    sent_message = msg
                    
                except:
                    ok, msg = False, None
            else:
                coro_factory = lambda d=dest, m=mid: context.bot.copy_message(
                    chat_id=d, from_chat_id=SOURCE_CHAT_ID, message_id=m
                )
                ok, msg = await _send_with_backoff(coro_factory, base_pause=PAUSE)
                sent_message = msg
                
                if ok and sent_message:
                    db_buttons = buttons_map.get(mid, [])
                    just_button = justification_buttons_for_previous.get(i)
                    
                    if db_buttons or just_button:
                        try:
                            keyboard_rows = []
                            
                            for label, url in db_buttons:
                                keyboard_rows.append([InlineKeyboardButton(label, url=url)])
                            
                            if just_button:
                                keyboard_rows.extend(just_button.inline_keyboard)
                            
                            final_keyboard = InlineKeyboardMarkup(keyboard_rows)
                            
                            await context.bot.edit_message_reply_markup(
                                chat_id=dest,
                                message_id=sent_message.message_id,
                                reply_markup=final_keyboard
                            )
                        except:
                            pass

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
