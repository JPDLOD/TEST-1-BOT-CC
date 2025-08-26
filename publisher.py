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
from database import get_unsent_drafts, mark_sent

logger = logging.getLogger(__name__)

# ========= Estado de targets =========
# BACKUP siempre activo por seguridad
ACTIVE_BACKUP: bool = True  # SIEMPRE ON - No se puede cambiar

def is_active_backup() -> bool:
    """Backup siempre activo por seguridad."""
    return True  # Siempre retorna True

def set_active_backup(value: bool) -> None:
    """DEPRECADO - Backup siempre activo."""
    # No hace nada, backup siempre ON
    pass

def get_active_targets() -> List[int]:
    targets = [TARGET_CHAT_ID]
    if BACKUP_CHAT_ID:  # Siempre incluye backup si está configurado
        targets.append(BACKUP_CHAT_ID)
    return targets

# ========= Contadores / locks (usados por otros módulos) =========
STATS = {"cancelados": 0, "eliminados": 0}
SCHEDULED_LOCK: Set[int] = set()

# ========= CACHE GLOBAL PARA RESPUESTAS CORRECTAS DETECTADAS =========
DETECTED_CORRECT_ANSWERS: Dict[int, int] = {}  # {message_id: correct_option_index}
POLL_ID_TO_MESSAGE_ID: Dict[str, int] = {}     # {poll_id: message_id} mapeo

# ========= PATRÓN PARA DETECTAR LINKS DE JUSTIFICACIONES =========
JUSTIFICATION_LINK_PATTERN = re.compile(r'https?://t\.me/ccjustificaciones/(\d+(?:[,\-]\d+)*)', re.IGNORECASE)

# ========= Función para detectar y extraer justificaciones =========
def extract_justification_from_text(text: str) -> Optional[Tuple[List[int], str]]:
    """
    Detecta si un texto contiene links de justificación y extrae el nombre del caso.
    Soporta múltiples IDs y rangos.
    Retorna: ([lista_ids], nombre_caso, texto_limpio) o None
    """
    if not text:
        return None
    
    justification_ids = []
    case_name = ""
    
    # Buscar nombre del caso (texto antes del link)
    case_pattern = re.search(r'^(.*?)(?=https://)', text)
    if case_pattern:
        potential_case = case_pattern.group(1).strip()
        # Limpiar emojis y caracteres especiales comunes
        if potential_case:
            case_name = potential_case.replace("📚", "").replace("*", "").replace("_", "").strip()
    
    # Extraer todos los IDs
    for match in JUSTIFICATION_LINK_PATTERN.finditer(text):
        id_string = match.group(1)
        
        # Procesar rangos y comas
        parts = id_string.split(',')
        for part in parts:
            if '-' in part:
                # Es un rango
                try:
                    start, end = map(int, part.split('-'))
                    justification_ids.extend(range(start, end + 1))
                except:
                    pass
            else:
                # Es un ID simple
                try:
                    justification_ids.append(int(part))
                except:
                    pass
    
    if not justification_ids:
        return None
    
    # Eliminar duplicados y ordenar
    justification_ids = sorted(list(set(justification_ids)))
    
    # Eliminar los links del texto
    clean_text = JUSTIFICATION_LINK_PATTERN.sub('', text).strip()
    
    return justification_ids, case_name, clean_text

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

async def extract_correct_answer_via_stop_poll(context: ContextTypes.DEFAULT_TYPE, message_id: int, is_forwarded: bool = False, raw_data: dict = None) -> Optional[int]:
    """
    MÉTODO MEJORADO: Intenta stopPoll en canal origen si es reenviado.
    """
    
    # Si es forwardeado, intentar en el canal origen
    if is_forwarded and raw_data:
        forward_from_chat_id = raw_data.get("forward_from_chat", {}).get("id")
        forward_from_message_id = raw_data.get("forward_from_message_id")
        
        if forward_from_chat_id and forward_from_message_id:
            try:
                logger.info(f"🔄 Mensaje reenviado detectado. Intentando stopPoll en canal origen {forward_from_chat_id}")
                
                # Intentar stopPoll en el canal origen
                stopped_poll = await context.bot.stop_poll(
                    chat_id=forward_from_chat_id,
                    message_id=forward_from_message_id
                )
                
                if stopped_poll and hasattr(stopped_poll, 'correct_option_id') and stopped_poll.correct_option_id is not None:
                    correct_id = stopped_poll.correct_option_id
                    logger.info(f"✅ StopPoll en origen exitoso! correct_option_id = {correct_id}")
                    DETECTED_CORRECT_ANSWERS[message_id] = correct_id
                    return correct_id
                    
            except TelegramError as e:
                logger.warning(f"⚠️ No se pudo hacer stopPoll en canal origen: {e}")
    
    # Si no es forwarded o falló en origen, intentar en el canal actual
    if not is_forwarded:
        try:
            logger.info(f"🛑 EJECUTANDO stopPoll en quiz {message_id} (mensaje directo)...")
            
            stopped_poll = await context.bot.stop_poll(
                chat_id=SOURCE_CHAT_ID, 
                message_id=message_id
            )
            
            if not stopped_poll:
                logger.error(f"❌ stopPoll no devolvió resultado para {message_id}")
                return None
            
            if hasattr(stopped_poll, 'correct_option_id') and stopped_poll.correct_option_id is not None:
                correct_id = stopped_poll.correct_option_id
                logger.info(f"🎯 ¡ENCONTRADO! correct_option_id = {correct_id}")
                DETECTED_CORRECT_ANSWERS[message_id] = correct_id
                return correct_id
            
            # Analizar opciones por votos
            if hasattr(stopped_poll, 'options') and stopped_poll.options:
                for i, option in enumerate(stopped_poll.options):
                    if option.voter_count > 0:
                        logger.info(f"🗳️ Tu voto detectado en opción {i}")
                        DETECTED_CORRECT_ANSWERS[message_id] = i
                        return i
        
        except TelegramError as e:
            logger.error(f"❌ Error en stopPoll: {e}")
    
    return None

def extract_correct_answer_from_forwarded_poll_analysis(poll_data: dict, message_id: int) -> Optional[int]:
    """
    MÉTODO ESPECIAL para mensajes FORWARDEADOS donde stopPoll no funciona.
    Usa análisis más agresivo del JSON y patrones de detección.
    """
    try:
        logger.info(f"🔍 ANÁLISIS FORWARDEADO: Quiz {message_id}")
        
        # Método 1: correct_option_id directo (a veces existe en forwardeados)
        if "correct_option_id" in poll_data and poll_data["correct_option_id"] is not None:
            try:
                correct_id = int(poll_data["correct_option_id"])
                logger.info(f"✅ FORWARDED MÉTODO 1: correct_option_id directo = {correct_id}")
                return correct_id
            except (ValueError, TypeError):
                pass
        
        # Método 2: Buscar en opciones por voter_count (más común en forwardeados)
        options = poll_data.get("options", [])
        vote_pattern = []
        
        for i, option in enumerate(options):
            if isinstance(option, dict):
                voter_count = option.get("voter_count", 0)
                vote_pattern.append((i, voter_count))
                if voter_count > 0:
                    logger.info(f"✅ FORWARDED MÉTODO 2: Opción {i} ({chr(65+i)}) tiene {voter_count} voto(s)")
                    return i
        
        logger.info(f"📊 Patrón de votos: {vote_pattern}")
        
        # Método 3: Análisis de explanation para pistas
        explanation = poll_data.get("explanation", "")
        if explanation:
            logger.info(f"📝 Analizando explanation: '{explanation[:100]}...'")
            
            # Buscar patrones como "La respuesta correcta es C" o "Opción correcta: D"
            import re
            patterns = [
                r'respuesta correcta es ([A-D])',
                r'opci[óo]n correcta[:\s]*([A-D])',
                r'correcta[:\s]*([A-D])',
                r'la ([A-D]) es correcta',
            ]
            
            for pattern in patterns:
                match = re.search(pattern, explanation, re.IGNORECASE)
                if match:
                    letter = match.group(1).upper()
                    correct_id = ord(letter) - ord('A')
                    logger.info(f"✅ FORWARDED MÉTODO 3: Explanation indica {letter} (pos {correct_id})")
                    return correct_id
        
        # Método 4: Heurística - si solo una opción está votada y total_voter_count = 1
        total_voters = poll_data.get("total_voter_count", 0)
        if total_voters == 1:
            for i, (option_i, voter_count) in enumerate(vote_pattern):
                if voter_count == 1:
                    logger.info(f"✅ FORWARDED MÉTODO 4: Heurística - única opción votada {option_i} ({chr(65+option_i)})")
                    return option_i
        
        logger.warning(f"⚠️ FORWARDED: No se pudo detectar respuesta correcta para quiz {message_id}")
        return None
        
    except Exception as e:
        logger.error(f"Error en análisis de poll forwardeado: {e}")
        return None

def is_message_forwarded(raw_data: dict) -> bool:
    """Detecta si un mensaje es forwardeado basándose en el JSON."""
    forward_fields = ["forward_from", "forward_from_chat", "forward_from_message_id", "forward_sender_name", "forward_date"]
    return any(field in raw_data for field in forward_fields)

def extract_correct_answer_from_json_deep_analysis(poll_data: dict, message_id: int) -> Optional[int]:
    """
    ANÁLISIS PROFUNDO del JSON del poll para encontrar cualquier pista sobre la respuesta correcta.
    """
    try:
        logger.info(f"🔬 ANÁLISIS PROFUNDO del JSON para quiz {message_id}")
        
        # Método 1: correct_option_id directo
        if "correct_option_id" in poll_data and poll_data["correct_option_id"] is not None:
            try:
                correct_id = int(poll_data["correct_option_id"])
                logger.info(f"✅ MÉTODO 1: correct_option_id directo = {correct_id}")
                return correct_id
            except (ValueError, TypeError):
                pass
        
        # Método 2: Buscar en opciones por voter_count
        options = poll_data.get("options", [])
        for i, option in enumerate(options):
            if isinstance(option, dict):
                voter_count = option.get("voter_count", 0)
                if voter_count > 0:
                    logger.info(f"✅ MÉTODO 2: Opción {i} ({chr(65+i)}) tiene {voter_count} voto(s)")
                    return i
        
        # Método 3: Buscar en results/poll_results
        for results_key in ["results", "poll_results"]:
            if results_key in poll_data:
                results = poll_data[results_key]
                if isinstance(results, dict) and "results" in results:
                    results_list = results["results"]
                    for i, result in enumerate(results_list):
                        if isinstance(result, dict):
                            if result.get("correct", False):
                                logger.info(f"✅ MÉTODO 3: Opción {i} marcada como 'correct' en {results_key}")
                                return i
                            elif result.get("voter_count", 0) > 0:
                                logger.info(f"✅ MÉTODO 3: Opción {i} tiene votos en {results_key}")
                                return i
        
        # Método 4: Buscar recursivamente cualquier campo que contenga "correct"
        def buscar_correct_recursivo(obj, path=""):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    current_path = f"{path}.{key}" if path else key
                    
                    if key == "correct" and value is True:
                        logger.info(f"🔍 MÉTODO 4: Encontrado 'correct': True en {current_path}")
                        return True
                    elif key == "correct_option_id" and value is not None:
                        logger.info(f"🔍 MÉTODO 4: Encontrado correct_option_id = {value} en {current_path}")
                        try:
                            return int(value)
                        except (ValueError, TypeError):
                            pass
                    
                    result = buscar_correct_recursivo(value, current_path)
                    if result is not None:
                        return result
            
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    current_path = f"{path}[{i}]"
                    result = buscar_correct_recursivo(item, current_path)
                    if result is not None:
                        return result
            
            return None
        
        resultado_recursivo = buscar_correct_recursivo(poll_data)
        if resultado_recursivo is not None:
            logger.info(f"✅ MÉTODO 4: Búsqueda recursiva encontró: {resultado_recursivo}")
            if isinstance(resultado_recursivo, int):
                return resultado_recursivo
        
        # Método 5: Log completo del JSON para debugging manual
        logger.warning(f"⚠️ ANÁLISIS EXHAUSTIVO: No se encontró respuesta correcta en quiz {message_id}")
        logger.debug(f"📄 JSON completo del poll: {json.dumps(poll_data, indent=2, default=str)}")
        
        return None
        
    except Exception as e:
        logger.error(f"Error en análisis profundo: {e}")
        return None

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

# ========= FUNCIÓN PRINCIPAL PARA OBTENER RESPUESTA CORRECTA =========

async def get_correct_answer_comprehensive(context: ContextTypes.DEFAULT_TYPE, message_id: int, poll_data: dict, raw_message_data: dict = None) -> int:
    """
    MÉTODO INTEGRAL mejorado con soporte para mensajes reenviados.
    """
    
    # Verificar cache primero
    if message_id in DETECTED_CORRECT_ANSWERS:
        detected_answer = DETECTED_CORRECT_ANSWERS[message_id]
        logger.info(f"🎯 Quiz {message_id}: usando respuesta cacheada → {chr(65+detected_answer)} (pos {detected_answer})")
        return detected_answer
    
    # Detectar si el mensaje es forwardeado
    is_forwarded = False
    if raw_message_data:
        is_forwarded = is_message_forwarded(raw_message_data)
    
    logger.info(f"🔍 Quiz {message_id}: tipo={'FORWARDEADO' if is_forwarded else 'DIRECTO'}")
    
    # ENFOQUE 1: Análisis profundo del JSON
    json_result = extract_correct_answer_from_json_deep_analysis(poll_data, message_id)
    if json_result is not None:
        DETECTED_CORRECT_ANSWERS[message_id] = json_result
        return json_result
    
    # ENFOQUE 2: stopPoll (ahora también funciona para forwardeados)
    stop_poll_result = await extract_correct_answer_via_stop_poll(
        context, message_id, is_forwarded, raw_message_data
    )
    if stop_poll_result is not None:
        return stop_poll_result
    
    # ENFOQUE 3: Análisis específico para forwardeados
    if is_forwarded:
        logger.info(f"🔍 Quiz {message_id}: aplicando análisis adicional para FORWARDEADOS...")
        forwarded_result = extract_correct_answer_from_forwarded_poll_analysis(poll_data, message_id)
        if forwarded_result is not None:
            DETECTED_CORRECT_ANSWERS[message_id] = forwarded_result
            return forwarded_result
    
    # FALLBACK: Si todo falla
    logger.error(f"❌ FALLO: Quiz {message_id} - No se pudo detectar respuesta correcta")
    logger.warning(f"💡 TIP: Vota en la encuesta antes de reenviarla al borrador")
    
    return 0  # Fallback a A

def get_correct_answer_sync(message_id: int, poll_data: dict) -> int:
    """Versión síncrona para casos donde no hay contexto async."""
    
    if message_id in DETECTED_CORRECT_ANSWERS:
        return DETECTED_CORRECT_ANSWERS[message_id]
    
    # Solo análisis del JSON (sin stopPoll)
    json_result = extract_correct_answer_from_json_deep_analysis(poll_data, message_id)
    if json_result is not None:
        DETECTED_CORRECT_ANSWERS[message_id] = json_result
        return json_result
    
    return 0

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

# ========= Publicadores =========
async def _publicar_rows(context: ContextTypes.DEFAULT_TYPE, *, rows: List[Tuple[int, str, str]],
                         targets: List[int], mark_as_sent: bool) -> Tuple[int, int, Dict[int, List[int]]]:
    
    # ANÁLISIS PREVIO EXHAUSTIVO: Procesar todos los quizzes que no tengan respuesta detectada
    quizzes_to_analyze = []
    
    for mid, _t, raw in rows:
        try:
            data = json.loads(raw or "{}")
            if "poll" in data and data["poll"].get("type") == "quiz":
                if mid not in DETECTED_CORRECT_ANSWERS:
                    quizzes_to_analyze.append((mid, data["poll"], data))  # Agregamos data completa
        except:
            continue
    
    if quizzes_to_analyze:
        logger.info(f"🔬 ANÁLISIS PREVIO: Procesando {len(quizzes_to_analyze)} quizzes sin respuesta detectada")
        
        for quiz_mid, poll_data, full_message_data in quizzes_to_analyze:
            is_forwarded = is_message_forwarded(full_message_data)
            logger.info(f"🧪 Analizando quiz {quiz_mid} ({'FORWARDED' if is_forwarded else 'DIRECT'})...")
            
            # Usar método integral con información completa del mensaje
            detected = await get_correct_answer_comprehensive(context, quiz_mid, poll_data, full_message_data)
            logger.info(f"🎯 Quiz {quiz_mid}: análisis completado → {chr(65+detected)}")
            
            # Pequeña pausa entre análisis
            await asyncio.sleep(0.3)
    
    # ANÁLISIS PREVIO: Detectar mensajes que son links de justificación
    justification_buttons_for_previous = {}  # {message_index: button}
    messages_to_skip = set()  # Índices de mensajes que NO se deben enviar
    
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
                    
                    # SIEMPRE procesar como justificación si tiene el link
                    logger.info(f"🔗 Mensaje {mid}: justificaciones {justification_ids}, caso: '{case_name}'")
                    
                    # Buscar el mensaje anterior (generalmente una encuesta)
                    if i > 0:
                        try:
                            bot_info = await context.bot.get_me()
                            bot_username = bot_info.username
                            
                            # Crear deep-link para múltiples justificaciones
                            ids_string = "_".join(map(str, justification_ids))
                            deep_link = f"https://t.me/{bot_username}?start=just_{ids_string}"
                            
                            # Personalizar texto del botón
                            if case_name:
                                button_text = f"Ver justificación {case_name} 📚"
                            else:
                                button_text = "Ver justificación 📚"
                            
                            button = InlineKeyboardMarkup([[
                                InlineKeyboardButton(button_text, url=deep_link)
                            ]])
                            
                            justification_buttons_for_previous[i-1] = button
                            logger.info(f"📎 Botón '{button_text}' preparado para mensaje anterior")
                        except Exception as e:
                            logger.error(f"Error preparando botón: {e}")
                    
                    # Marcar este mensaje para NO enviarlo
                    messages_to_skip.add(i)
        except Exception as e:
            logger.error(f"Error analizando mensaje {mid}: {e}")
    
    # PROCEDER CON LA PUBLICACIÓN
    publicados = 0
    fallidos = 0
    enviados_ids: List[int] = []
    posted_by_target: Dict[int, List[int]] = {t: [] for t in targets}
    last_sent_messages = {}  # {target: last_message}

    for i, (mid, _t, raw) in enumerate(rows):
        # SALTAR mensajes que son solo links de justificación
        if i in messages_to_skip:
            logger.info(f"⏭️ Saltando mensaje {mid} (solo link de justificación)")
            # Marcar como enviado para que no quede pendiente
            if mark_as_sent:
                enviados_ids.append(mid)
            continue
        
        try:
            data = json.loads(raw or "{}")
        except Exception as e:
            logger.error(f"Error parseando JSON para mensaje {mid}: {e}")
            data = {}

        # Procesar texto para quitar links de justificación si hay más contenido
        text_content = data.get("text", "") or data.get("caption", "")
        justification_match = JUSTIFICATION_LINK_PATTERN.search(text_content)
        if justification_match:
            clean_text = JUSTIFICATION_LINK_PATTERN.sub('', text_content).strip()
            if clean_text:
                # Hay más contenido, actualizar
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
                    
                    # Agregar botón de justificación si corresponde
                    if i in justification_buttons_for_previous:
                        kwargs["reply_markup"] = justification_buttons_for_previous[i]
                        logger.info(f"📎 Agregando botón de justificación a encuesta {mid}")
                    
                    if is_quiz:
                        cid = kwargs.get("correct_option_id", 0)
                        status = "✅ DETECTADO" if mid in DETECTED_CORRECT_ANSWERS else "⚠️ FALLBACK"
                        logger.info(f"📊 {status}: Enviando quiz {mid} a {dest} → respuesta {chr(65+cid)}")
                    
                    coro_factory = lambda k=kwargs: context.bot.send_poll(**k)
                    ok, msg = await _send_with_backoff(coro_factory, base_pause=PAUSE)
                    sent_message = msg
                    
                except Exception as e:
                    logger.error(f"Error procesando poll {mid}: {e}")
                    ok, msg = False, None
            else:
                # Mensaje normal
                coro_factory = lambda d=dest, m=mid: context.bot.copy_message(
                    chat_id=d, from_chat_id=SOURCE_CHAT_ID, message_id=m
                )
                ok, msg = await _send_with_backoff(coro_factory, base_pause=PAUSE)
                sent_message = msg
                
                # Si este mensaje tiene botón de justificación para agregar
                if ok and sent_message and i in justification_buttons_for_previous:
                    try:
                        await context.bot.edit_message_reply_markup(
                            chat_id=dest,
                            message_id=sent_message.message_id,
                            reply_markup=justification_buttons_for_previous[i]
                        )
                        logger.info(f"✅ Botón de justificación agregado al mensaje {sent_message.message_id}")
                    except Exception as e:
                        logger.error(f"Error agregando botón: {e}")

            if ok:
                any_success = True
                if msg and getattr(msg, "message_id", None):
                    posted_by_target[dest].append(msg.message_id)
                    last_sent_messages[dest] = msg

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
