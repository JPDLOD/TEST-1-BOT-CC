# -*- coding: utf-8 -*-
import json
import logging
import re
from typing import List, Tuple, Dict, Set, Optional
import asyncio

from telegram.error import RetryAfter, TimedOut, NetworkError, TelegramError
from telegram.ext import ContextTypes

from config import DB_FILE, SOURCE_CHAT_ID, TARGET_CHAT_ID, BACKUP_CHAT_ID, PAUSE, JUSTIFICATIONS_BOT_USERNAME
from database import _conn, get_unsent_drafts, mark_sent, update_draft_json

logger = logging.getLogger(__name__)

# ========= Estado de targets =========
ACTIVE_BACKUP: bool = True  # Siempre activo

def is_active_backup() -> bool:
    return True

def get_active_targets() -> List[int]:
    targets = [TARGET_CHAT_ID]
    if BACKUP_CHAT_ID:
        targets.append(BACKUP_CHAT_ID)
    return targets

# ========= Contadores y locks =========
STATS = {"cancelados": 0, "eliminados": 0}
SCHEDULED_LOCK: Set[int] = set()

# ========= Cache para respuestas de quiz =========
DETECTED_CORRECT_ANSWERS: Dict[int, int] = {}
POLL_ID_TO_MESSAGE_ID: Dict[str, int] = {}
VOTED_POLLS: Set[int] = set()  # Agregar cache de polls ya votados

# ========= Procesar justificaciones =========
def process_justification_text(text: str) -> Tuple[str, bool]:
    """
    Convierte enlaces de justificación en deep links al bot @clinicase_bot.
    """
    if not text:
        return text, False
    
    patterns = [
        r'(.*?)(https://t\.me/c/\d+/(\d+))',
        r'(.*?)(https://t\.me/ccjustificaciones/(\d+))',
    ]
    
    match = None
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            break
    
    if not match:
        return text, False
    
    case_name = match.group(1).strip()
    justification_id = match.group(3)
    
    if case_name:
        case_name = re.sub(r'[📚🏥\*_]', '', case_name).strip()
        link_text = f"📚 Ver justificación {case_name}" if case_name else "📚 Ver justificación"
    else:
        link_text = "📚 Ver justificación"
    
    deep_link = f"https://t.me/{JUSTIFICATIONS_BOT_USERNAME}?start=jst_{justification_id}"
    html_link = f'<a href="{deep_link}">{link_text}</a>'
    
    remaining_text = text[match.end():].strip()
    processed_text = html_link + "\n\n" + remaining_text if remaining_text else html_link
    
    logger.info(f"✅ Justificación convertida: {deep_link}")
    
    return processed_text, True

# ========= Backoff para envíos =========
async def _send_with_backoff(func_coro_factory, *, base_pause: float):
    tries = 0
    while True:
        try:
            msg = await func_coro_factory()
            await asyncio.sleep(max(0.0, base_pause))
            return True, msg
        except RetryAfter as e:
            wait = getattr(e, "retry_after", 3)
            logger.warning(f"RetryAfter: esperando {wait}s")
            await asyncio.sleep(wait + 1.0)
            tries += 1
        except (TimedOut, NetworkError):
            logger.warning("Timeout/Network error")
            await asyncio.sleep(3.0)
            tries += 1
        except TelegramError as e:
            if "Flood control" in str(e):
                logger.warning("Flood control")
                await asyncio.sleep(5.0)
                tries += 1
            else:
                logger.error(f"TelegramError: {e}")
                return False, None
        except Exception as e:
            logger.exception(f"Error: {e}")
            return False, None

        if tries >= 5:
            logger.error("Demasiados reintentos")
            return False, None

# ========= NUEVA FUNCIÓN: Votar para detectar respuesta correcta =========
async def vote_to_detect_correct_answer(context: ContextTypes.DEFAULT_TYPE, message_id: int) -> Optional[int]:
    """
    Vota en la encuesta para detectar la respuesta correcta.
    Funciona con mensajes directos y reenviados.
    """
    if message_id in VOTED_POLLS:
        # Ya votamos antes, usar cache
        return DETECTED_CORRECT_ANSWERS.get(message_id)
    
    try:
        logger.info(f"🗳️ Votando en quiz {message_id} para detectar respuesta correcta...")
        
        # Intentar votar en cada opción hasta encontrar la correcta
        for option in range(10):  # Max 10 opciones en Telegram
            try:
                # Intentar votar
                await context.bot.answer_poll_query(
                    poll_id=message_id,
                    option_ids=[option]
                )
                
                # Si llegamos aquí sin error, esta es la respuesta correcta
                logger.info(f"✅ Respuesta correcta detectada: opción {option} ({chr(65+option)})")
                DETECTED_CORRECT_ANSWERS[message_id] = option
                VOTED_POLLS.add(message_id)
                return option
                
            except TelegramError as e:
                if "POLL_ANSWER_INVALID" in str(e) or "wrong" in str(e).lower():
                    # Opción incorrecta, probar siguiente
                    continue
                elif "POLL_CLOSED" in str(e):
                    logger.warning(f"Quiz {message_id} ya está cerrado")
                    break
                elif "already voted" in str(e).lower():
                    # Ya votamos, intentar obtener del cache
                    logger.info(f"Ya votado en quiz {message_id}")
                    break
    except Exception as e:
        logger.error(f"Error votando: {e}")
    
    return None

# ========= Polls/Quiz =========
def detect_voted_polls_on_save(message_id: int, raw_json: str):
    """Detecta si es quiz y mapea poll_id."""
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
            logger.info(f"Quiz detectado: {poll_id} → {message_id}")
            
            correct_option_id = poll.get("correct_option_id")
            if correct_option_id is not None:
                DETECTED_CORRECT_ANSWERS[message_id] = int(correct_option_id)
                logger.info(f"Respuesta correcta en JSON: {correct_option_id}")
    except Exception as e:
        logger.error(f"Error: {e}")

async def handle_poll_update(update, context):
    logger.info(f"Inside handle_poll_update")
    if not update.message or not update.message.poll:
        return
    
    poll = update.message.poll
    source_chat = update.message.chat
    
    poll_id = str(poll.id)

    message_id = POLL_ID_TO_MESSAGE_ID.get(poll_id)
    if not message_id:
        return

    if poll.correct_option_id is not None:
        # Load the old draft JSON
        c = _conn(DB_FILE)
        cur = c.execute("SELECT raw_json FROM drafts WHERE message_id=?", (message_id,))
        row = cur.fetchone()
        if row:
            raw_json = json.loads(row[0])

            # Merge in the correct_option_id
            if "poll" in raw_json:
                raw_json["poll"]["correct_option_id"] = poll.correct_option_id

            # Save back to DB
            update_draft_json(DB_FILE, message_id, raw_json)

        DETECTED_CORRECT_ANSWERS[message_id] = poll.correct_option_id
        logger.info(f"Quiz {message_id}: correct={poll.correct_option_id}")


async def handle_poll_answer_update(update, context):
    """Handler para respuestas de usuarios."""
    if not update.poll_answer:
        return
    
    poll_answer = update.poll_answer
    poll_id = str(poll_answer.poll_id)
    
    message_id = POLL_ID_TO_MESSAGE_ID.get(poll_id)
    if not message_id:
        return
    
    if poll_answer.option_ids:
        chosen = poll_answer.option_ids[0]
        DETECTED_CORRECT_ANSWERS[message_id] = chosen
        logger.info(f"Quiz {message_id}: usuario eligió {chosen}")

def _poll_payload_from_raw(raw: dict, message_id: int = None):
    """Extrae parámetros de la encuesta."""
    p = raw.get("poll") or {}
    question = p.get("question", "Pregunta")
    options = [o.get("text", "") for o in p.get("options", [])]

    kwargs = dict(
        question=question,
        options=options,
        is_anonymous=p.get("is_anonymous", True),
    )

    ptype = (p.get("type") or "regular").lower().strip()
    is_quiz = (ptype == "quiz")

    if not is_quiz:
        kwargs["allows_multiple_answers"] = p.get("allows_multiple_answers", False)
    else:
        kwargs["type"] = "quiz"
        
        # Usar respuesta detectada si existe
        if message_id and message_id in DETECTED_CORRECT_ANSWERS:
            correct_option_id = DETECTED_CORRECT_ANSWERS[message_id]
        elif p.get("correct_option_id") is not None:
            correct_option_id = int(p.get("correct_option_id", 0))
        else:
            correct_option_id = 0
        
        kwargs["correct_option_id"] = correct_option_id

    if p.get("open_period") is not None:
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

# ========= Publicadores =========
async def _publicar_rows(context: ContextTypes.DEFAULT_TYPE, *, rows: List[Tuple[int, str, str]],
                         targets: List[int], mark_as_sent: bool) -> Tuple[int, int, Dict[int, List[int]]]:
    
    # NUEVO: Pre-procesar quizzes para detectar respuestas correctas
    for mid, _t, raw in rows:
        try:
            data = json.loads(raw or "{}")
            if "poll" in data and data["poll"].get("type") == "quiz":
                if mid not in DETECTED_CORRECT_ANSWERS and mid not in VOTED_POLLS:
                    # Intentar votar para detectar respuesta correcta
                    detected = await vote_to_detect_correct_answer(context, mid)
                    if detected is not None:
                        logger.info(f"✅ Quiz {mid}: respuesta detectada votando → {chr(65+detected)}")
        except:
            continue
    
    publicados = 0
    fallidos = 0
    enviados_ids: List[int] = []
    posted_by_target: Dict[int, List[int]] = {t: [] for t in targets}

    for i, (mid, _t, raw) in enumerate(rows):
        try:
            data = json.loads(raw or "{}")
        except:
            data = {}

        # Procesar justificaciones
        text_content = data.get("text", "") or data.get("caption", "")
        has_justification = False
        
        if text_content:
            processed_text, has_justification = process_justification_text(text_content)
            
            if has_justification:
                if "text" in data:
                    data["text"] = processed_text
                elif "caption" in data:
                    data["caption"] = processed_text

        any_success = False
        for dest in targets:
            
            if "poll" in data:
                try:
                    base_kwargs, is_quiz = _poll_payload_from_raw(data, message_id=mid)
                    kwargs = dict(base_kwargs)
                    kwargs["chat_id"] = dest
                    
                    if is_quiz:
                        cid = kwargs.get("correct_option_id", 0)
                        status = "✅ DETECTADO" if mid in DETECTED_CORRECT_ANSWERS else "⚠️ DEFAULT"
                        logger.info(f"📊 {status}: Enviando quiz {mid} → respuesta: {chr(65+cid)}")
                    
                    coro_factory = lambda k=kwargs: context.bot.send_poll(**k)
                    ok, msg = await _send_with_backoff(coro_factory, base_pause=PAUSE)
                except Exception as e:
                    logger.error(f"Error poll {mid}: {e}")
                    ok, msg = False, None
                    
            elif has_justification:
                try:
                    if not any([data.get("photo"), data.get("document"), data.get("video")]):
                        coro_factory = lambda: context.bot.send_message(
                            chat_id=dest,
                            text=processed_text,
                            parse_mode="HTML",
                            disable_web_page_preview=False
                        )
                    else:
                        coro_factory = lambda d=dest, m=mid: context.bot.copy_message(
                            chat_id=d,
                            from_chat_id=SOURCE_CHAT_ID,
                            message_id=m,
                            caption=processed_text,
                            parse_mode="HTML"
                        )
                    
                    ok, msg = await _send_with_backoff(coro_factory, base_pause=PAUSE)
                except:
                    coro_factory = lambda d=dest, m=mid: context.bot.copy_message(
                        chat_id=d, from_chat_id=SOURCE_CHAT_ID, message_id=m
                    )
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
    """Envía la cola completa."""
    all_rows = get_unsent_drafts(DB_FILE)
    if not all_rows:
        return 0, 0, {t: [] for t in targets}
    rows = [(m, t, r) for (m, t, r) in all_rows if m not in SCHEDULED_LOCK]
    if not rows:
        return 0, 0, {t: [] for t in targets}
    return await _publicar_rows(context, rows=rows, targets=targets, mark_as_sent=mark_as_sent)

async def publicar_ids(context: ContextTypes.DEFAULT_TYPE, *, ids: List[int],
                       targets: List[int], mark_as_sent: bool):
    """Publica mensajes específicos."""
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
    """Publica todo a los targets activos."""
    pubs, fails, _ = await publicar(context, targets=get_active_targets(), mark_as_sent=True)
    return pubs, fails
