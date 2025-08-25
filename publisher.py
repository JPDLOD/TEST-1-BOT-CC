# -*- coding: utf-8 -*-
import json
import logging
from typing import List, Tuple, Dict, Set, Optional

from telegram.error import RetryAfter, TimedOut, NetworkError, TelegramError
from telegram.ext import ContextTypes

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

# ========= FUNCIONES PARA DETECTAR RESPUESTAS CORRECTAS =========

def _build_poll_id_mapping():
    """
    Construye un mapeo entre poll_id y message_id leyendo la base de datos.
    Esto es necesario porque los handlers de poll reciben poll_id, no message_id.
    """
    try:
        import sqlite3
        
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        
        # Buscar todos los quizzes en la base de datos
        query = "SELECT message_id, raw_json FROM drafts WHERE deleted = 0 AND raw_json IS NOT NULL"
        results = cur.execute(query).fetchall()
        con.close()
        
        for message_id, raw_json in results:
            try:
                data = json.loads(raw_json)
                if "poll" in data:
                    poll = data["poll"]
                    if poll.get("type") == "quiz" and "id" in poll:
                        poll_id = str(poll["id"])
                        POLL_ID_TO_MESSAGE_ID[poll_id] = message_id
                        logger.info(f"🗺️ Mapeado: poll_id {poll_id} → message_id {message_id}")
            except:
                continue
        
        logger.info(f"📊 Construido mapeo: {len(POLL_ID_TO_MESSAGE_ID)} polls encontrados")
        
    except Exception as e:
        logger.error(f"Error construyendo mapeo poll_id: {e}")

def detect_chosen_answer_from_poll_update(poll_data) -> Optional[int]:
    """
    Detecta la opción elegida desde un update de poll.
    Esta función analiza update.poll cuando alguien vota.
    """
    try:
        # Método 1: Revisar options directamente
        if hasattr(poll_data, 'options'):
            options = poll_data.options
        else:
            options = poll_data.get('options', [])
        
        for i, option in enumerate(options):
            if hasattr(option, 'voter_count'):
                voter_count = option.voter_count
            else:
                voter_count = option.get('voter_count', 0)
            
            if voter_count > 0:
                logger.info(f"🗳️ DETECTADO: Opción {i} ({chr(65+i)}) tiene {voter_count} voto(s)")
                return i
        
        # Método 2: Si poll_data es un objeto Poll, usar propiedades directas
        if hasattr(poll_data, 'total_voter_count') and poll_data.total_voter_count > 0:
            logger.info(f"📊 Poll tiene {poll_data.total_voter_count} votos totales")
            
            # Buscar la opción con votos
            for i, option in enumerate(poll_data.options):
                if option.voter_count > 0:
                    logger.info(f"🎯 CONFIRMADO: Usuario votó por opción {i} ({chr(65+i)})")
                    return i
    
    except Exception as e:
        logger.error(f"Error detectando voto: {e}")
        logger.debug(f"Poll data type: {type(poll_data)}")
        logger.debug(f"Poll data: {poll_data}")
    
    return None

def detect_chosen_answer_from_poll_answer(poll_answer_data) -> Optional[Tuple[str, List[int]]]:
    """
    Detecta la respuesta elegida desde update.poll_answer.
    Retorna (poll_id, [option_ids_chosen])
    """
    try:
        if hasattr(poll_answer_data, 'poll_id'):
            poll_id = str(poll_answer_data.poll_id)
        else:
            poll_id = str(poll_answer_data.get('poll_id', ''))
        
        if hasattr(poll_answer_data, 'option_ids'):
            option_ids = list(poll_answer_data.option_ids)
        else:
            option_ids = poll_answer_data.get('option_ids', [])
        
        if poll_id and option_ids:
            logger.info(f"📨 PollAnswer: poll_id={poll_id}, opciones elegidas={option_ids}")
            return poll_id, option_ids
    
    except Exception as e:
        logger.error(f"Error procesando poll_answer: {e}")
    
    return None

# ========= HANDLERS PARA CAPTURAR VOTOS (PARA AGREGAR EN main.py) =========

async def handle_poll_update(update, context):
    """
    Handler para capturar cuando una encuesta es actualizada (alguien votó).
    ESTE HANDLER DEBE SER AGREGADO EN main.py
    """
    if not update.poll:
        return
    
    poll = update.poll
    poll_id = str(poll.id)
    
    logger.info(f"🔄 UPDATE POLL: poll_id={poll_id}, votos totales={poll.total_voter_count}")
    
    # Construir mapeo si no existe
    if not POLL_ID_TO_MESSAGE_ID:
        _build_poll_id_mapping()
    
    # Encontrar message_id correspondiente
    message_id = POLL_ID_TO_MESSAGE_ID.get(poll_id)
    if not message_id:
        logger.warning(f"⚠️ No se encontró message_id para poll_id {poll_id}")
        return
    
    # Detectar qué opción fue elegida
    chosen_option = detect_chosen_answer_from_poll_update(poll)
    
    if chosen_option is not None:
        DETECTED_CORRECT_ANSWERS[message_id] = chosen_option
        logger.info(f"✅ VOTO DETECTADO: Quiz {message_id} → Respuesta correcta {chr(65+chosen_option)} (posición {chosen_option})")
    else:
        logger.warning(f"⚠️ No se pudo detectar el voto en poll {poll_id}")

async def handle_poll_answer_update(update, context):
    """
    Handler para capturar respuestas individuales de usuarios.
    ESTE HANDLER DEBE SER AGREGADO EN main.py
    """
    if not update.poll_answer:
        return
    
    poll_answer = update.poll_answer
    user_id = poll_answer.user.id if poll_answer.user else None
    
    # Solo procesar si es del administrador (ajustar según tu user_id)
    # ADMIN_USER_ID = 123456789  # Cambiar por tu ID real
    # if user_id != ADMIN_USER_ID:
    #     return
    
    result = detect_chosen_answer_from_poll_answer(poll_answer)
    if not result:
        return
    
    poll_id, option_ids = result
    
    # Construir mapeo si no existe
    if not POLL_ID_TO_MESSAGE_ID:
        _build_poll_id_mapping()
    
    message_id = POLL_ID_TO_MESSAGE_ID.get(poll_id)
    if not message_id:
        logger.warning(f"⚠️ No se encontró message_id para poll_id {poll_id}")
        return
    
    # Asumimos que solo se elige una opción en quiz
    if option_ids and len(option_ids) > 0:
        chosen_option = option_ids[0]
        DETECTED_CORRECT_ANSWERS[message_id] = chosen_option
        logger.info(f"✅ POLL ANSWER: Quiz {message_id} → Usuario eligió {chr(65+chosen_option)} (posición {chosen_option})")

# ========= Función principal para obtener respuesta correcta =========
def get_detected_correct_answer(message_id: int, poll_data: dict) -> int:
    """
    Obtiene la respuesta correcta para un quiz, usando la detección en tiempo real.
    """
    
    # Construir mapeo si es la primera vez
    if not POLL_ID_TO_MESSAGE_ID:
        _build_poll_id_mapping()
    
    # Verificar si ya tenemos la respuesta detectada
    if message_id in DETECTED_CORRECT_ANSWERS:
        detected_answer = DETECTED_CORRECT_ANSWERS[message_id]
        logger.info(f"🎯 Quiz {message_id}: usando respuesta detectada → {chr(65+detected_answer)} (pos {detected_answer})")
        return detected_answer
    
    # Si no se detectó voto, usar fallback
    logger.warning(f"⚠️ Quiz {message_id}: NO se detectó voto del usuario")
    logger.warning(f"💡 INSTRUCCIONES: Después de crear la encuesta, VOTA por la opción correcta antes de usar /enviar")
    
    return 0  # Fallback a A

# ========= Encuestas - VERSIÓN CON DETECCIÓN DE VOTOS =========
def _poll_payload_from_raw(raw: dict, message_id: int = None):
    """
    Extrae parámetros de la encuesta con detección inteligente de respuesta correcta.
    """
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
        
        # USAR DETECCIÓN DE VOTOS EN TIEMPO REAL
        if message_id:
            correct_option_id = get_detected_correct_answer(message_id, p)
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

        any_success = False
        for dest in targets:
            if "poll" in data:
                try:
                    base_kwargs, is_quiz = _poll_payload_from_raw(data, message_id=mid)
                    kwargs = dict(base_kwargs)
                    kwargs["chat_id"] = dest
                    
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
