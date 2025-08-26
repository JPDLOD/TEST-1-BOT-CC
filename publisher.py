# -*- coding: utf-8 -*-
import json
import logging
from typing import List, Tuple, Dict, Set, Optional
import asyncio
import re

from telegram.error import RetryAfter, TimedOut, NetworkError, TelegramError
from telegram.ext import ContextTypes
from telegram import InlineKeyboardMarkup

from config import DB_FILE, SOURCE_CHAT_ID, TARGET_CHAT_ID, BACKUP_CHAT_ID, PAUSE
from database import get_unsent_drafts, mark_sent, get_buttons_map_for_ids

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

# ========= Contadores / locks (usados por otros módulos) =========
STATS = {"cancelados": 0, "eliminados": 0}
SCHEDULED_LOCK: Set[int] = set()

# ========= CACHE GLOBAL PARA RESPUESTAS CORRECTAS DETECTADAS =========
DETECTED_CORRECT_ANSWERS: Dict[int, int] = {}  # {message_id: correct_option_index}
POLL_ID_TO_MESSAGE_ID: Dict[str, int] = {}     # {poll_id: message_id} mapeo

# ========= DETECCIÓN DE JUSTIFICACIONES Y CASOS =========

# Patrón para detectar enlaces de justificación
JUSTIFICATION_LINK_PATTERN = re.compile(
    r'https://t\.me/c/1058530208/(\d+)',
    re.IGNORECASE
)

# Patrones para detectar comandos de botón @@@
BUTTON_SHORTCUT_PATTERN = re.compile(r'^\s*@@@\s*([^|]+?)\s*\|\s*(https?://\S+)\s*$', re.IGNORECASE | re.MULTILINE)

def detect_justification_links(text: str) -> List[int]:
    """Detecta enlaces de justificación en el texto."""
    if not text:
        return []
    
    matches = JUSTIFICATION_LINK_PATTERN.findall(text)
    justification_ids = []
    
    for message_id in matches:
        try:
            justification_ids.append(int(message_id))
        except ValueError:
            continue
    
    return justification_ids

def extract_case_name(text: str) -> str:
    """Extrae el nombre del caso del texto."""
    if not text:
        return ''
    
    patterns = [
        r'([🤣😂]*\s*CASO\s*#\d+[^.\n]*)',
        r'(CASO\s+[^.\n]+)',
        r'(CASP\s*#\d+[^.\n]*)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    
    return ''

def should_skip_justification_link_message(text: str) -> bool:
    """
    Determina si un mensaje debe ser saltado porque solo contiene enlaces de justificación.
    """
    if not text:
        return False
    
    # Limpiar el texto de enlaces de justificación
    clean_text = JUSTIFICATION_LINK_PATTERN.sub('', text).strip()
    
    # Si después de quitar los enlaces queda muy poco contenido, saltar
    return len(clean_text) < 10

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
            await asyncio.sleep(wait + 1.0);  tries += 1
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

# ========= DETECCIÓN EXHAUSTIVA DE RESPUESTA CORRECTA =========

def detect_voted_polls_on_save(message_id: int, raw_json: str):
    """Se ejecuta cuando se guarda un borrador. Detecta si es una encuesta quiz."""
    try:
        data = json.loads(raw_json)
        if "poll" not in data:
            return
        
        poll = data["poll"]
        if poll.get("type") != "quiz":
            return
        
        # DETECTAR SI ES MENSAJE FORWARDEADO
        is_forwarded = False
        forward_info = ""
        
        forward_fields = ["forward_from", "forward_from_chat", "forward_from_message_id", "forward_sender_name", "forward_date"]
        for field in forward_fields:
            if field in data:
                is_forwarded = True
                forward_info += f" {field}={data[field]}"
                break
        
        # Crear mapeo poll_id -> message_id SIEMPRE
        if "id" in poll:
            poll_id = str(poll["id"])
            POLL_ID_TO_MESSAGE_ID[poll_id] = message_id
            
            status = "FORWARDEADO" if is_forwarded else "DIRECTO"
            logger.info(f"🗺️ Quiz {status}: poll_id {poll_id} → message_id {message_id}")
            if is_forwarded:
                logger.info(f"📤 Forward info:{forward_info}")
            
            total_voters = poll.get("total_voter_count", 0)
            is_closed = poll.get("is_closed", False)
            correct_option_id = poll.get("correct_option_id")
            
            logger.info(f"📊 Quiz {message_id}: votos={total_voters}, cerrado={is_closed}, correct_id={correct_option_id}")
            
            # Si ya tiene correct_option_id disponible, usarlo inmediatamente
            if correct_option_id is not None:
                try:
                    correct_id = int(correct_option_id)
                    DETECTED_CORRECT_ANSWERS[message_id] = correct_id
                    logger.info(f"✅ DIRECTO: Quiz {message_id} ya tiene correct_option_id = {correct_id} ({chr(65+correct_id)})")
                except (ValueError, TypeError):
                    pass
    
    except Exception as e:
        logger.error(f"Error analizando poll en save: {e}")

async def extract_correct_answer_via_stop_poll(context: ContextTypes.DEFAULT_TYPE, message_id: int, is_forwarded: bool = False) -> Optional[int]:
    """Usa stopPoll para cerrar la encuesta y obtener correct_option_id."""
    
    if is_forwarded:
        logger.warning(f"🚫 Quiz {message_id} es FORWARDEADO - stopPoll no funcionará, saltando")
        return None
    
    try:
        logger.info(f"🛑 EJECUTANDO stopPoll en quiz {message_id} para obtener correct_option_id...")
        
        stopped_poll = await context.bot.stop_poll(
            chat_id=SOURCE_CHAT_ID, 
            message_id=message_id
        )
        
        if not stopped_poll:
            logger.error(f"❌ stopPoll no devolvió resultado para {message_id}")
            return None
        
        logger.info(f"🔍 stopPoll exitoso en {message_id}")
        
        if hasattr(stopped_poll, 'correct_option_id') and stopped_poll.correct_option_id is not None:
            correct_id = stopped_poll.correct_option_id
            logger.info(f"🎯 ¡ENCONTRADO! correct_option_id = {correct_id} ({chr(65+correct_id)}) en quiz {message_id}")
            DETECTED_CORRECT_ANSWERS[message_id] = correct_id
            return correct_id
        else:
            # Analizar opciones por votos
            if hasattr(stopped_poll, 'options') and stopped_poll.options:
                for i, option in enumerate(stopped_poll.options):
                    if option.voter_count > 0:
                        logger.info(f"🗳️ DETECTADO: Tu voto está en opción {i} ({chr(65+i)})")
                        DETECTED_CORRECT_ANSWERS[message_id] = i
                        return i
        
        return None
        
    except TelegramError as e:
        if "poll can't be stopped" in str(e).lower():
            logger.warning(f"⚠️ Poll {message_id} no se puede cerrar (es forwarded o no tienes permisos)")
        else:
            logger.error(f"❌ Error en stopPoll para {message_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Error general en stopPoll para {message_id}: {e}")
        return None

def get_correct_answer_sync(message_id: int, poll_data: dict) -> int:
    """Versión síncrona para obtener respuesta correcta."""
    
    if message_id in DETECTED_CORRECT_ANSWERS:
        return DETECTED_CORRECT_ANSWERS[message_id]
    
    # Análisis del JSON
    if "correct_option_id" in poll_data and poll_data["correct_option_id"] is not None:
        try:
            correct_id = int(poll_data["correct_option_id"])
            DETECTED_CORRECT_ANSWERS[message_id] = correct_id
            return correct_id
        except (ValueError, TypeError):
            pass
    
    # Buscar por votos
    options = poll_data.get("options", [])
    for i, option in enumerate(options):
        if isinstance(option, dict) and option.get("voter_count", 0) > 0:
            DETECTED_CORRECT_ANSWERS[message_id] = i
            return i
    
    return 0

# ========= HANDLERS PARA CAPTURAR VOTOS EN TIEMPO REAL =========

async def handle_poll_update(update, context):
    """Handler para capturar cuando una encuesta es actualizada (alguien votó)."""
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
        logger.info(f"✅ Poll update: Quiz {message_id} → correct_option_id = {correct_id}")
        return
    
    # Detectar por votos
    for i, option in enumerate(poll.options):
        if option.voter_count > 0:
            DETECTED_CORRECT_ANSWERS[message_id] = i
            logger.info(f"✅ Poll update: Quiz {message_id} → Detectado voto en opción {i}")
            return

async def handle_poll_answer_update(update, context):
    """Handler para capturar respuestas individuales de usuarios."""
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
        logger.info(f"✅ Poll answer: Quiz {message_id} → Usuario eligió {chr(65+chosen_option)}")

# ========= Encuestas - VERSIÓN FINAL =========
def _poll_payload_from_raw(raw: dict, message_id: int = None):
    """Extrae parámetros de la encuesta con detección integral."""
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

# ========= Procesamiento de botones @@@ =========
def process_button_shortcuts(text: str) -> Tuple[str, List[Tuple[str, str]]]:
    """
    Procesa los atajos @@@ en el texto y retorna el texto limpio + lista de botones.
    """
    if not text:
        return text, []
    
    buttons = []
    lines = text.split('\n')
    clean_lines = []
    
    for line in lines:
        match = BUTTON_SHORTCUT_PATTERN.match(line.strip())
        if match:
            label = match.group(1).strip()
            url = match.group(2).strip()
            buttons.append((label, url))
            logger.info(f"📎 Botón detectado: '{label}' → {url}")
        else:
            clean_lines.append(line)
    
    clean_text = '\n'.join(clean_lines).strip()
    return clean_text, buttons

# ========= Publicadores =========
async def _publicar_rows(context: ContextTypes.DEFAULT_TYPE, *, rows: List[Tuple[int, str, str]],
                         targets: List[int], mark_as_sent: bool) -> Tuple[int, int, Dict[int, List[int]]]:
    
    publicados = 0
    fallidos = 0
    enviados_ids: List[int] = []
    posted_by_target: Dict[int, List[int]] = {t: [] for t in targets}
    
    # Obtener botones para todos los mensajes de una vez
    all_ids = [mid for mid, _, _ in rows]
    buttons_map = get_buttons_map_for_ids(DB_FILE, all_ids)
    
    # Variables para rastrear mensajes anteriores (para justificaciones)
    previous_message_info = None
    pending_justification_data = None

    for mid, snippet, raw in rows:
        try:
            data = json.loads(raw or "{}")
        except Exception as e:
            logger.error(f"Error parseando JSON para mensaje {mid}: {e}")
            data = {}

        # ========= DETECCIÓN DE JUSTIFICACIONES =========
        message_text = data.get("text") or data.get("caption") or ""
        justification_ids = detect_justification_links(message_text)
        
        # Si es un mensaje que solo contiene links de justificación, saltarlo pero guardar la info
        if should_skip_justification_link_message(message_text):
            case_name = extract_case_name(message_text)
            pending_justification_data = {
                'justification_ids': justification_ids,
                'case_name': case_name
            }
            logger.info(f"⏭️ Saltando mensaje {mid} (link de justificación)")
            if mark_as_sent:
                enviados_ids.append(mid)
            continue

        any_success = False
        for dest in targets:
            sent_message = None
            
            # ========= ENVÍO SEGÚN TIPO DE MENSAJE =========
            if "poll" in data:
                # Es una encuesta
                try:
                    base_kwargs, is_quiz = _poll_payload_from_raw(data, message_id=mid)
                    kwargs = dict(base_kwargs)
                    kwargs["chat_id"] = dest
                    
                    if is_quiz:
                        cid = kwargs.get("correct_option_id", 0)
                        status = "✅ DETECTADO" if mid in DETECTED_CORRECT_ANSWERS else "⚠️ FALLBACK"
                        
                        # Verificar si hay justificaciones pendientes para agregar
                        if pending_justification_data:
                            logger.info(f"📎 Agregando botón de justificación a encuesta {mid}")
                        
                        logger.info(f"📊 {status}: Enviando quiz {mid} a {dest} → respuesta {chr(65+cid)}")
                    
                    coro_factory = lambda k=kwargs: context.bot.send_poll(**k)
                    ok, sent_message = await _send_with_backoff(coro_factory, base_pause=PAUSE)
                    
                    # Si se envió exitosamente y hay datos de justificación pendientes
                    if ok and sent_message and pending_justification_data:
                        try:
                            from justifications_handler import add_justification_button_to_message
                            await add_justification_button_to_message(
                                context,
                                dest,
                                sent_message.message_id,
                                pending_justification_data['justification_ids'],
                                pending_justification_data['case_name']
                            )
                        except ImportError:
                            logger.warning("Módulo justifications_handler no disponible")
                        except Exception as e:
                            logger.error(f"Error agregando botón de justificación: {e}")
                        
                        # Limpiar datos de justificación pendientes
                        pending_justification_data = None
                    
                except Exception as e:
                    logger.error(f"Error procesando poll {mid}: {e}")
                    ok, sent_message = False, None
            else:
                # Es un mensaje regular - verificar si tiene botones o justificaciones
                has_buttons = mid in buttons_map and buttons_map[mid]
                
                # Procesar botones @@@ si existen
                original_text = data.get("text") or data.get("caption") or ""
                clean_text, shortcut_buttons = process_button_shortcuts(original_text)
                
                # Combinar botones de @@@ con botones de BD
                all_buttons = buttons_map.get(mid, []) + shortcut_buttons
                
                if all_buttons or justification_ids:
                    # Mensaje con botones o justificaciones - usar copy + edit
                    coro_factory = lambda d=dest, m=mid: context.bot.copy_message(
                        chat_id=d, from_chat_id=SOURCE_CHAT_ID, message_id=m
                    )
                    ok, sent_message = await _send_with_backoff(coro_factory, base_pause=PAUSE)
                    
                    if ok and sent_message:
                        try:
                            # Crear teclado inline
                            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                            keyboard_buttons = []
                            
                            # Agregar botones regulares
                            for label, url in all_buttons:
                                keyboard_buttons.append([InlineKeyboardButton(label, url=url)])
                            
                            # Agregar botón de justificación si corresponde
                            if justification_ids:
                                case_name = extract_case_name(original_text)
                                try:
                                    from justifications_handler import create_justification_button
                                    bot_info = await context.bot.get_me()
                                    justif_keyboard = create_justification_button(bot_info.username, justification_ids, case_name)
                                    if justif_keyboard.inline_keyboard:
                                        keyboard_buttons.extend(justif_keyboard.inline_keyboard)
                                except ImportError:
                                    logger.warning("Módulo justifications_handler no disponible")
                            
                            if keyboard_buttons:
                                keyboard = InlineKeyboardMarkup(keyboard_buttons)
                                await context.bot.edit_message_reply_markup(
                                    chat_id=dest,
                                    message_id=sent_message.message_id,
                                    reply_markup=keyboard
                                )
                        
                        except Exception as e:
                            logger.error(f"Error agregando botones/justificaciones: {e}")
                else:
                    # Mensaje simple - copy normal
                    coro_factory = lambda d=dest, m=mid: context.bot.copy_message(
                        chat_id=d, from_chat_id=SOURCE_CHAT_ID, message_id=m
                    )
                    ok, sent_message = await _send_with_backoff(coro_factory, base_pause=PAUSE)

            if ok:
                any_success = True
                if sent_message and getattr(sent_message, "message_id", None):
                    posted_by_target[dest].append(sent_message.message_id)

        # Limpiar datos de justificación si se procesaron
        if any_success and pending_justification_data:
            pending_justification_data = None

        if any_success:
            publicados += 1
            if mark_as_sent:
                enviados_ids.append(mid)
        else:
            fallidos += 1
        
        # Guardar info del mensaje para la siguiente iteración
        previous_message_info = {
            'message_id': mid,
            'has_poll': "poll" in data,
            'text': message_text
        }

    if enviados_ids and mark_as_sent:
        mark_sent(DB_FILE, enviados_ids)

    return publicados, fallidos, posted_by_target

async def publicar(context: ContextTypes.DEFAULT_TYPE, *, targets: List[int], mark_as_sent: bool):
    """Envía la cola completa EXCLUYENDO los bloqueados (SCHEDULED_LOCK)."""
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
