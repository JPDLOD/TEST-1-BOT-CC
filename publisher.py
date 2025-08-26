# -*- coding: utf-8 -*-
import json
import logging
from typing import List, Tuple, Dict, Set, Optional
import asyncio

from telegram.error import RetryAfter, TimedOut, NetworkError, TelegramError
from telegram.ext import ContextTypes
from telegram import InlineKeyboardMarkup

from config import DB_FILE, SOURCE_CHAT_ID, TARGET_CHAT_ID, BACKUP_CHAT_ID, PAUSE
from database import get_unsent_drafts, mark_sent

logger = logging.getLogger(__name__)

# ========= Estado de targets =========
ACTIVE_BACKUP: bool = True

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

# ========= CACHE GLOBAL PARA RESPUESTAS CORRECTAS =========
DETECTED_CORRECT_ANSWERS: Dict[int, int] = {}
POLL_ID_TO_MESSAGE_ID: Dict[str, int] = {}

# ========= Backoff para envíos =========
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
            logger.warning(f"RetryAfter: esperando {wait}s …")
            await asyncio.sleep(wait + 1.0)
            tries += 1
        except TimedOut:
            logger.warning("TimedOut: esperando 3s …")
            await asyncio.sleep(3.0)
            tries += 1
        except NetworkError:
            logger.warning("NetworkError: esperando 3s …")
            await asyncio.sleep(3.0)
            tries += 1
        except TelegramError as e:
            if "Flood control exceeded" in str(e):
                logger.warning("Flood control… esperando 5s …")
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

# ========= DETECCIÓN DE RESPUESTA CORRECTA =========
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
            logger.info(f"🗺️ Quiz: poll_id {poll_id} → message_id {message_id}")
            
            correct_option_id = poll.get("correct_option_id")
            if correct_option_id is not None:
                try:
                    correct_id = int(correct_option_id)
                    DETECTED_CORRECT_ANSWERS[message_id] = correct_id
                    logger.info(f"✅ Quiz {message_id} tiene correct_option_id = {correct_id}")
                except (ValueError, TypeError):
                    pass
    except Exception as e:
        logger.error(f"Error analizando poll en save: {e}")

async def extract_correct_answer_via_stop_poll(context: ContextTypes.DEFAULT_TYPE, message_id: int) -> Optional[int]:
    try:
        logger.info(f"🛑 EJECUTANDO stopPoll en quiz {message_id}")
        
        stopped_poll = await context.bot.stop_poll(
            chat_id=SOURCE_CHAT_ID, 
            message_id=message_id
        )
        
        if stopped_poll and hasattr(stopped_poll, 'correct_option_id') and stopped_poll.correct_option_id is not None:
            correct_id = stopped_poll.correct_option_id
            logger.info(f"🎯 Encontrado correct_option_id = {correct_id} en quiz {message_id}")
            DETECTED_CORRECT_ANSWERS[message_id] = correct_id
            return correct_id
        
        return None
        
    except TelegramError as e:
        logger.error(f"❌ Error en stopPoll para {message_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Error general en stopPoll para {message_id}: {e}")
        return None

def get_correct_answer_sync(message_id: int, poll_data: dict) -> int:
    if message_id in DETECTED_CORRECT_ANSWERS:
        return DETECTED_CORRECT_ANSWERS[message_id]
    
    if "correct_option_id" in poll_data and poll_data["correct_option_id"] is not None:
        try:
            correct_id = int(poll_data["correct_option_id"])
            DETECTED_CORRECT_ANSWERS[message_id] = correct_id
            return correct_id
        except (ValueError, TypeError):
            pass
    
    return 0

# ========= HANDLERS PARA CAPTURAR VOTOS =========
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
        logger.info(f"✅ UPDATE POLL: Quiz {message_id} → correct_option_id = {correct_id}")
        return
    
    for i, option in enumerate(poll.options):
        if option.voter_count > 0:
            DETECTED_CORRECT_ANSWERS[message_id] = i
            logger.info(f"✅ UPDATE POLL: Quiz {message_id} → Detectado voto en opción {i}")
            return

async def handle_poll_answer_update(update, context):
    if not update.poll_answer:
        return
    
    poll_answer = update.poll_answer
    poll_id = str(poll_answer.poll_id)
    option_ids = list(poll_answer.option_ids) if poll_answer.option_ids else []
    
    message_id = POLL_ID_TO_MESSAGE_ID.get(poll_id)
    if not message_id:
        return
    
    if option_ids and len(option_ids) > 0:
        chosen_option = option_ids[0]
        DETECTED_CORRECT_ANSWERS[message_id] = chosen_option
        logger.info(f"✅ POLL ANSWER: Quiz {message_id} → Usuario eligió opción {chosen_option}")

# ========= Encuestas =========
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
            correct_option_id = get_correct_answer_sync(message_id, p)
        else:
            correct_option_id = 0
        
        kwargs["correct_option_id"] = correct_option_id

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

# ========= INTEGRACIÓN DE JUSTIFICACIONES =========
async def process_message_for_justifications(context: ContextTypes.DEFAULT_TYPE, raw_json: str) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    try:
        from justifications_handler import process_draft_for_justifications
        logger.info(f"🔗 Procesando mensaje para justificaciones...")
        
        modified_json, justification_keyboard = await process_draft_for_justifications(context, raw_json)
        
        if justification_keyboard:
            logger.info(f"✅ JUSTIFICACIÓN DETECTADA: botón creado")
        else:
            logger.info(f"ℹ️ No se detectaron justificaciones en el mensaje")
            
        return modified_json, justification_keyboard
        
    except ImportError as e:
        logger.error(f"❌ Error importando justifications_handler: {e}")
        return raw_json, None
    except Exception as e:
        logger.error(f"❌ Error procesando justificaciones: {e}")
        return raw_json, None

# ========= PUBLICADORES =========
async def _publicar_rows(context: ContextTypes.DEFAULT_TYPE, *, rows: List[Tuple[int, str, str]],
                         targets: List[int], mark_as_sent: bool) -> Tuple[int, int, Dict[int, List[int]]]:
    
    publicados = 0
    fallidos = 0
    enviados_ids: List[int] = []
    posted_by_target: Dict[int, List[int]] = {t: [] for t in targets}

    for mid, _t, raw in rows:
        try:
            data = json.loads(raw or "{}")
        except Exception as e:
            logger.error(f"Error parseando JSON para mensaje {mid}: {e}")
            data = {}

        # PROCESAR JUSTIFICACIONES
        justification_keyboard = None
        try:
            logger.info(f"🔍 Procesando justificaciones para mensaje {mid}")
            
            modified_raw, justification_keyboard = await process_message_for_justifications(context, raw)
            
            if justification_keyboard:
                logger.info(f"🔗 Mensaje {mid}: detectada justificación, botón añadido")
                try:
                    data = json.loads(modified_raw)
                    logger.info(f"✅ JSON modificado aplicado para mensaje {mid}")
                except Exception as e:
                    logger.error(f"❌ Error parseando JSON modificado: {e}")
            else:
                logger.info(f"ℹ️ Mensaje {mid}: sin justificaciones")
            
        except Exception as e:
            logger.error(f"❌ Error procesando justificaciones para {mid}: {e}")

        any_success = False
        for dest in targets:
            if "poll" in data:
                try:
                    base_kwargs, is_quiz = _poll_payload_from_raw(data, message_id=mid)
                    kwargs = dict(base_kwargs)
                    kwargs["chat_id"] = dest
                    
                    if justification_keyboard:
                        kwargs["reply_markup"] = justification_keyboard
                        logger.info(f"🔗 Botón de justificación agregado a encuesta {mid}")
                    
                    if is_quiz:
                        cid = kwargs.get("correct_option_id", 0)
                        status = "✅ DETECTADO" if mid in DETECTED_CORRECT_ANSWERS else "⚠️ FALLBACK"
                        logger.info(f"📊 {status}: Enviando quiz {mid} a {dest} → respuesta {chr(65+cid)}")
                    
                    coro_factory = lambda k=kwargs: context.bot.send_poll(**k)
                    ok, msg = await _send_with_backoff(coro_factory, base_pause=PAUSE)
                    
                except Exception as e:
                    logger.error(f"Error procesando poll {mid}: {e}")
                    ok, msg = False, None
            else:
                async def send_normal_message():
                    copied_msg = await context.bot.copy_message(
                        chat_id=dest, 
                        from_chat_id=SOURCE_CHAT_ID, 
                        message_id=mid
                    )
                    
                    if justification_keyboard and copied_msg:
                        try:
                            await context.bot.edit_message_reply_markup(
                                chat_id=dest,
                                message_id=copied_msg.message_id,
                                reply_markup=justification_keyboard
                            )
                            logger.info(f"🔗 Botón de justificación agregado a mensaje copiado {copied_msg.message_id}")
                        except Exception as e:
                            logger.warning(f"⚠️ No se pudo agregar botón de justificación: {e}")
                    
                    return copied_msg
                
                coro_factory = send_normal_message
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
