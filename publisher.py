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

# ========= Backoff para envíos =========
async def _send_with_backoff(func_coro_factory, *, base_pause: float):
    tries = 0
    while True:
        try:
            msg = await func_coro_factory()
            # pausa corta entre mensajes
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

# ========= DETECCIÓN EXHAUSTIVA DE RESPUESTA CORRECTA =========

def detect_voted_polls_on_save(message_id: int, raw_json: str):
    """
    Se ejecuta cuando se guarda un borrador.
    Detecta si es una encuesta quiz y construye el mapeo poll_id.
    Ahora también detecta si el mensaje es forwardeado.
    """
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
        
        # Revisar campos que indican forwarding
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
            
            # Log información disponible
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
            
            # Para mensajes forwardeados, marcar para análisis especial
            if is_forwarded:
                logger.info(f"⚠️ Quiz {message_id} es FORWARDEADO - stopPoll no funcionará, usando métodos alternativos")
    
    except Exception as e:
        logger.error(f"Error analizando poll en save: {e}")

async def extract_correct_answer_via_stop_poll(context: ContextTypes.DEFAULT_TYPE, message_id: int, is_forwarded: bool = False) -> Optional[int]:
    """
    MÉTODO DEFINITIVO: Usa stopPoll para cerrar la encuesta y obtener correct_option_id.
    Solo funciona para mensajes NO forwardeados.
    """
    
    if is_forwarded:
        logger.warning(f"🚫 Quiz {message_id} es FORWARDEADO - skipPoll no funcionará, saltando stopPoll")
        return None
    
    try:
        logger.info(f"🛑 EJECUTANDO stopPoll en quiz {message_id} para obtener correct_option_id...")
        
        # Hacer stopPoll para cerrar la encuesta
        stopped_poll = await context.bot.stop_poll(
            chat_id=SOURCE_CHAT_ID, 
            message_id=message_id
        )
        
        if not stopped_poll:
            logger.error(f"❌ stopPoll no devolvió resultado para {message_id}")
            return None
        
        logger.info(f"🔍 stopPoll exitoso en {message_id}")
        logger.info(f"📊 Poll cerrado: id={stopped_poll.id}, tipo={stopped_poll.type}, cerrado={stopped_poll.is_closed}")
        
        # CLAVE: Ahora que está cerrado, correct_option_id debe estar disponible
        if hasattr(stopped_poll, 'correct_option_id') and stopped_poll.correct_option_id is not None:
            correct_id = stopped_poll.correct_option_id
            logger.info(f"🎯 ¡ENCONTRADO! correct_option_id = {correct_id} ({chr(65+correct_id)}) en quiz {message_id}")
            DETECTED_CORRECT_ANSWERS[message_id] = correct_id
            return correct_id
        else:
            logger.warning(f"⚠️ Poll cerrado pero correct_option_id aún no disponible en {message_id}")
            
            # PLAN B: Analizar opciones por patrones
            if hasattr(stopped_poll, 'options') and stopped_poll.options:
                logger.info(f"📋 Opciones disponibles: {len(stopped_poll.options)} opciones")
                for i, option in enumerate(stopped_poll.options):
                    logger.info(f"   {i}: '{option.text}' (votos: {option.voter_count})")
                    
                    # Detectar qué opción tiene votos (tu voto)
                    if option.voter_count > 0:
                        logger.info(f"🗳️ DETECTADO: Tu voto está en opción {i} ({chr(65+i)})")
                        DETECTED_CORRECT_ANSWERS[message_id] = i
                        return i
        
        return None
        
    except TelegramError as e:
        if "poll can't be stopped" in str(e).lower():
            logger.warning(f"⚠️ Poll {message_id} no se puede cerrar (confirmado: es forwarded o no tienes permisos)")
        elif "message not found" in str(e).lower():
            logger.warning(f"⚠️ Poll {message_id} no encontrado para stopPoll")
        else:
            logger.error(f"❌ Error en stopPoll para {message_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Error general en stopPoll para {message_id}: {e}")
        return None

def get_correct_answer_sync(message_id: int, poll_data: dict) -> int:
    """Versión síncrona para casos donde no hay contexto async."""
    
    if message_id in DETECTED_CORRECT_ANSWERS:
        return DETECTED_CORRECT_ANSWERS[message_id]
    
    # Solo análisis del JSON (sin stopPoll)
    if "correct_option_id" in poll_data and poll_data["correct_option_id"] is not None:
        try:
            correct_id = int(poll_data["correct_option_id"])
            DETECTED_CORRECT_ANSWERS[message_id] = correct_id
            return correct_id
        except (ValueError, TypeError):
            pass
    
    return 0

# ========= HANDLERS PARA CAPTURAR VOTOS EN TIEMPO REAL =========

async def handle_poll_update(update, context):
    """Handler para capturar cuando una encuesta es actualizada (alguien votó)."""
    if not update.poll:
        return
    
    poll = update.poll
    poll_id = str(poll.id)
    
    logger.info(f"🔄 UPDATE POLL: poll_id={poll_id}, votos totales={poll.total_voter_count}")
    
    # Encontrar message_id correspondiente
    message_id = POLL_ID_TO_MESSAGE_ID.get(poll_id)
    if not message_id:
        logger.warning(f"⚠️ No se encontró message_id para poll_id {poll_id}")
        return
    
    # Intentar extraer correct_option_id del poll actualizado
    if hasattr(poll, 'correct_option_id') and poll.correct_option_id is not None:
        correct_id = poll.correct_option_id
        DETECTED_CORRECT_ANSWERS[message_id] = correct_id
        logger.info(f"✅ UPDATE POLL: Quiz {message_id} → correct_option_id = {correct_id} ({chr(65+correct_id)})")
        return
    
    # Si no tiene correct_option_id, detectar por votos
    for i, option in enumerate(poll.options):
        if option.voter_count > 0:
            DETECTED_CORRECT_ANSWERS[message_id] = i
            logger.info(f"✅ UPDATE POLL: Quiz {message_id} → Detectado voto en opción {i} ({chr(65+i)})")
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
        logger.warning(f"⚠️ No se encontró message_id para poll_id {poll_id}")
        return
    
    if option_ids and len(option_ids) > 0:
        chosen_option = option_ids[0]  # Quiz solo permite una opción
        DETECTED_CORRECT_ANSWERS[message_id] = chosen_option
        logger.info(f"✅ POLL ANSWER: Quiz {message_id} → Usuario eligió {chr(65+chosen_option)} (posición {chosen_option})")

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
        
        # USAR DETECCIÓN INTEGRAL (versión síncrona para construcción inicial)
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

# ========= PUBLICADORES CON INTEGRACIÓN DE JUSTIFICACIONES =========
async def _publicar_rows(context: ContextTypes.DEFAULT_TYPE, *, rows: List[Tuple[int, str, str]],
                         targets: List[int], mark_as_sent: bool) -> Tuple[int, int, Dict[int, List[int]]]:
    
    publicados = 0
    fallidos = 0
    enviados_ids: List[int] = []
    posted_by_target: Dict[int, List[int]] = {t: [] for t in targets}

    for mid, _t, raw in rows:
        logger.info(f"🔍 Procesando mensaje {mid}")
        
        # ========= INTEGRACIÓN CRÍTICA: PROCESAR JUSTIFICACIONES =========
        justification_keyboard = None
        modified_raw = raw
        
        try:
            from justifications_handler import process_draft_for_justifications
            
            logger.info(f"🔗 Verificando justificaciones en mensaje {mid}")
            
            # Procesar el mensaje para detectar justificaciones
            modified_raw, justification_keyboard = await process_draft_for_justifications(context, raw)
            
            if justification_keyboard:
                logger.info(f"✅ JUSTIFICACIÓN DETECTADA en mensaje {mid}: botón creado")
            else:
                logger.info(f"ℹ️ Mensaje {mid}: sin justificaciones detectadas")
            
        except ImportError as e:
            logger.error(f"❌ Error importando justifications_handler: {e}")
        except Exception as e:
            logger.error(f"❌ Error procesando justificaciones para {mid}: {e}")

        # Parsear el JSON (modificado o original)
        try:
            data = json.loads(modified_raw)
        except Exception as e:
            logger.error(f"Error parseando JSON para mensaje {mid}: {e}")
            data = {}

        any_success = False
        for dest in targets:
            if "poll" in data:
                try:
                    base_kwargs, is_quiz = _poll_payload_from_raw(data, message_id=mid)
                    kwargs = dict(base_kwargs)
                    kwargs["chat_id"] = dest
                    
                    # AGREGAR BOTÓN DE JUSTIFICACIÓN SI EXISTE
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
                # Para mensajes normales, usar copy_message
                async def send_normal_message():
                    copied_msg = await context.bot.copy_message(
                        chat_id=dest, 
                        from_chat_id=SOURCE_CHAT_ID, 
                        message_id=mid
                    )
                    
                    # Si hay botón de justificación, editarlo para agregarlo
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
    # Query puntual sin duplicar lógica del módulo database
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
