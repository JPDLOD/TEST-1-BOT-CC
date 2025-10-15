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

# ========= Estado de targets =========
# BACKUP siempre activo por seguridad
ACTIVE_BACKUP: bool = True  # SIEMPRE ON - No se puede cambiar

def is_active_backup() -> bool:
    """Backup siempre activo por seguridad."""
    return True

def set_active_backup(value: bool) -> None:
    """DEPRECADO - Backup siempre activo."""
    pass

def get_active_targets() -> List[int]:
    targets = [TARGET_CHAT_ID]
    if BACKUP_CHAT_ID:
        targets.append(BACKUP_CHAT_ID)
    return targets

# ========= Contadores / locks =========
STATS = {"cancelados": 0, "eliminados": 0}
SCHEDULED_LOCK: Set[int] = set()

# ========= CACHE SIMPLE PARA RESPUESTAS DE QUIZ =========
DETECTED_CORRECT_ANSWERS: Dict[int, int] = {}  # {message_id: correct_option_index}
POLL_ID_TO_MESSAGE_ID: Dict[str, int] = {}     # {poll_id: message_id}

# ========= PATRÓN PARA DETECTAR LINKS DE JUSTIFICACIONES =========
JUSTIFICATION_LINK_PATTERN = re.compile(r'https?://t\.me/ccjustificaciones/(\d+(?:[,\-]\d+)*)', re.IGNORECASE)

def extract_justification_from_text(text: str) -> Optional[Tuple[List[int], str, str]]:
    """
    Detecta si un texto contiene links de justificación.
    Retorna: ([lista_ids], nombre_caso, texto_limpio) o None
    """
    if not text:
        return None
    
    justification_ids = []
    case_name = ""
    
    # Buscar nombre del caso
    case_pattern = re.search(r'^(.*?)(?=https://)', text)
    if case_pattern:
        potential_case = case_pattern.group(1).strip()
        if potential_case:
            case_name = potential_case.replace("📚", "").replace("*", "").replace("_", "").strip()
    
    # Extraer IDs
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

# ========= HANDLERS PARA CAPTURAR VOTOS (mantener compatibilidad) =========

def detect_voted_polls_on_save(message_id: int, raw_json: str):
    """Se ejecuta cuando se guarda un borrador para mapear poll_id."""
    try:
        data = json.loads(raw_json)
        if "poll" not in data:
            return
        
        poll = data["poll"]
        if poll.get("type") != "quiz":
            return
        
        # Mapear poll_id -> message_id
        if "id" in poll:
            poll_id = str(poll["id"])
            POLL_ID_TO_MESSAGE_ID[poll_id] = message_id
            logger.info(f"📊 Quiz {message_id}: poll_id {poll_id} mapeado")
            
            # Si ya tiene correct_option_id, usarlo
            if "correct_option_id" in poll and poll["correct_option_id"] is not None:
                try:
                    correct_id = int(poll["correct_option_id"])
                    DETECTED_CORRECT_ANSWERS[message_id] = correct_id
                    logger.info(f"✅ Quiz {message_id}: correct_option_id = {correct_id} ({chr(65+correct_id)})")
                except (ValueError, TypeError):
                    pass
    except Exception as e:
        logger.error(f"Error analizando poll: {e}")

async def handle_poll_update(update, context):
    """Handler para capturar actualizaciones de poll."""
    if not update.poll:
        return
    
    poll = update.poll
    poll_id = str(poll.id)
    message_id = POLL_ID_TO_MESSAGE_ID.get(poll_id)
    
    if not message_id:
        return
    
    # Intentar extraer correct_option_id
    if hasattr(poll, 'correct_option_id') and poll.correct_option_id is not None:
        correct_id = poll.correct_option_id
        DETECTED_CORRECT_ANSWERS[message_id] = correct_id
        logger.info(f"✅ Poll update: Quiz {message_id} → {chr(65+correct_id)}")
        return
    
    # Detectar por votos
    for i, option in enumerate(poll.options):
        if option.voter_count > 0:
            DETECTED_CORRECT_ANSWERS[message_id] = i
            logger.info(f"✅ Poll update: Quiz {message_id} → voto en {chr(65+i)}")
            return

async def handle_poll_answer_update(update, context):
    """Handler para capturar respuestas de usuarios."""
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
    logger.info(f"✅ Poll answer: Quiz {message_id} → {chr(65+chosen_option)}")

# ========= DETECCIÓN SIMPLE DE RESPUESTA CORRECTA =========

def get_correct_answer_simple(message_id: int, poll_data: dict) -> int:
    """
    Método SIMPLE para obtener respuesta correcta.
    Prioridad:
    1. Cache (si ya se detectó)
    2. correct_option_id directo del JSON
    3. Detectar por voter_count
    4. Fallback a 0 (A)
    """
    # 1. Cache
    if message_id in DETECTED_CORRECT_ANSWERS:
        return DETECTED_CORRECT_ANSWERS[message_id]
    
    # 2. correct_option_id directo
    if "correct_option_id" in poll_data and poll_data["correct_option_id"] is not None:
        try:
            correct_id = int(poll_data["correct_option_id"])
            DETECTED_CORRECT_ANSWERS[message_id] = correct_id
            logger.info(f"🎯 Quiz {message_id}: correct_option_id = {correct_id}")
            return correct_id
        except (ValueError, TypeError):
            pass
    
    # 3. Detectar por votos
    options = poll_data.get("options", [])
    for i, option in enumerate(options):
        if isinstance(option, dict):
            voter_count = option.get("voter_count", 0)
            if voter_count > 0:
                DETECTED_CORRECT_ANSWERS[message_id] = i
                logger.info(f"🗳️ Quiz {message_id}: detectado voto en {chr(65+i)}")
                return i
    
    # 4. Fallback
    logger.warning(f"⚠️ Quiz {message_id}: usando fallback (A)")
    return 0

# ========= CONSTRUCCIÓN DE POLL =========

def _poll_payload_from_raw(raw: dict, message_id: int = None):
    """Extrae parámetros de la encuesta - VERSIÓN SIMPLE."""
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
        
        # Usar detección simple
        if message_id:
            correct_option_id = get_correct_answer_simple(message_id, p)
        else:
            correct_option_id = 0
        
        kwargs["correct_option_id"] = correct_option_id

    # Otros parámetros
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

# ========= BACKOFF PARA ENVÍOS =========

async def _send_with_backoff(func_coro_factory, *, base_pause: float):
    """Envía con reintentos y backoff."""
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
                logger.error(f"TelegramError no recuperable: {e}")
                return False, None
        except Exception as e:
            logger.exception(f"Error enviando: {e}")
            return False, None

        if tries >= 5:
            logger.error("Demasiados reintentos")
            return False, None

# ========= PUBLICADOR PRINCIPAL =========

async def _publicar_rows(context: ContextTypes.DEFAULT_TYPE, *, rows: List[Tuple[int, str, str]],
                         targets: List[int], mark_as_sent: bool) -> Tuple[int, int, Dict[int, List[int]]]:
    """
    Publica mensajes manteniendo ESTRICTAMENTE el orden original.
    NO hace análisis previos complejos.
    """
    
    # Obtener botones de una sola vez
    all_ids = [mid for mid, _, _ in rows]
    buttons_map = get_buttons_map_for_ids(DB_FILE, all_ids)
    
    # Pre-análisis MÍNIMO: Solo identificar mensajes que son ÚNICAMENTE links de justificación
    messages_to_skip = set()
    justification_buttons_for_previous = {}
    
    for i, (mid, _t, raw) in enumerate(rows):
        try:
            data = json.loads(raw or "{}")
            text_content = data.get("text", "") or data.get("caption", "")
            
            if not text_content:
                continue
            
            # Verificar si contiene links de justificación
            if 'https://t.me/ccjustificaciones/' in text_content.lower():
                justification_info = extract_justification_from_text(text_content)
                
                if justification_info:
                    justification_ids, case_name, clean_text = justification_info
                    
                    # Si el mensaje es SOLO el link (sin otro contenido), saltarlo
                    if not clean_text.strip():
                        logger.info(f"🔗 Mensaje {mid}: solo justificación {justification_ids}")
                        
                        # Preparar botón para el mensaje anterior
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
                                logger.info(f"📎 Botón preparado para mensaje anterior")
                            except Exception as e:
                                logger.error(f"Error preparando botón: {e}")
                        
                        messages_to_skip.add(i)
        except Exception as e:
            logger.error(f"Error analizando mensaje {mid}: {e}")
    
    # ENVÍO SECUENCIAL ESTRICTO
    publicados = 0
    fallidos = 0
    enviados_ids: List[int] = []
    posted_by_target: Dict[int, List[int]] = {t: [] for t in targets}

    for i, (mid, _t, raw) in enumerate(rows):
        # Saltar mensajes que son solo links de justificación
        if i in messages_to_skip:
            logger.info(f"⏭️ Saltando mensaje {mid} (solo link)")
            if mark_as_sent:
                enviados_ids.append(mid)
            continue
        
        try:
            data = json.loads(raw or "{}")
        except Exception as e:
            logger.error(f"Error parseando JSON {mid}: {e}")
            data = {}

        # Limpiar links de justificación del texto si hay más contenido
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
                # ES UNA ENCUESTA
                try:
                    base_kwargs, is_quiz = _poll_payload_from_raw(data, message_id=mid)
                    kwargs = dict(base_kwargs)
                    kwargs["chat_id"] = dest
                    
                    # Agregar botón de justificación si corresponde
                    if i in justification_buttons_for_previous:
                        kwargs["reply_markup"] = justification_buttons_for_previous[i]
                        logger.info(f"📎 Agregando botón de justificación a quiz {mid}")
                    
                    if is_quiz:
                        cid = kwargs.get("correct_option_id", 0)
                        logger.info(f"📊 Enviando quiz {mid} a {dest} → {chr(65+cid)}")
                    
                    coro_factory = lambda k=kwargs: context.bot.send_poll(**k)
                    ok, msg = await _send_with_backoff(coro_factory, base_pause=PAUSE)
                    sent_message = msg
                    
                except Exception as e:
                    logger.error(f"Error procesando poll {mid}: {e}")
                    ok, msg = False, None
            else:
                # MENSAJE NORMAL
                coro_factory = lambda d=dest, m=mid: context.bot.copy_message(
                    chat_id=d, from_chat_id=SOURCE_CHAT_ID, message_id=m
                )
                ok, msg = await _send_with_backoff(coro_factory, base_pause=PAUSE)
                sent_message = msg
                
                # Agregar botones si existen
                if ok and sent_message:
                    # Botones de @@@
                    db_buttons = buttons_map.get(mid, [])
                    # Botones de justificación
                    just_button = justification_buttons_for_previous.get(i)
                    
                    if db_buttons or just_button:
                        try:
                            keyboard_rows = []
                            
                            # Botones de @@@ (de la DB)
                            for label, url in db_buttons:
                                keyboard_rows.append([InlineKeyboardButton(label, url=url)])
                            
                            # Botón de justificación
                            if just_button:
                                keyboard_rows.extend(just_button.inline_keyboard)
                            
                            final_keyboard = InlineKeyboardMarkup(keyboard_rows)
                            
                            await context.bot.edit_message_reply_markup(
                                chat_id=dest,
                                message_id=sent_message.message_id,
                                reply_markup=final_keyboard
                            )
                            logger.info(f"✅ Botones agregados a mensaje {sent_message.message_id}")
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
    """Envía la cola completa EXCLUYENDO los bloqueados."""
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
