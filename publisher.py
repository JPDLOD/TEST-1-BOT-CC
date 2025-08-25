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

# ========= Cache para respuestas correctas detectadas =========
CORRECT_ANSWERS_CACHE: Dict[int, int] = {}  # {message_id: correct_option_index}

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

# ========= Funciones para detectar la respuesta correcta =========

def _detect_user_vote_from_poll(poll_data: dict) -> Optional[int]:
    """
    MÉTODO 1: Detecta cuál opción votó el usuario (tú) en la encuesta.
    Busca en los resultados de la poll cuál opción tiene voter_count > 0
    y fue votada por el creador.
    """
    try:
        # Si hay resultados de votos
        if "results" in poll_data:
            results = poll_data["results"]
            if "results" in results:
                result_list = results["results"]
                
                # Buscar la opción con votos (asumiendo que solo tú votaste)
                for i, option_result in enumerate(result_list):
                    if option_result and option_result.get("voter_count", 0) > 0:
                        logger.info(f"🗳️ MÉTODO 1: Detectado voto del usuario en opción {i} (letra {chr(65+i)})")
                        return i
                        
        # También revisar en la estructura de opciones directamente
        options = poll_data.get("options", [])
        for i, option in enumerate(options):
            if option and option.get("voter_count", 0) > 0:
                logger.info(f"🗳️ MÉTODO 1: Detectado voto en opción {i} via options (letra {chr(65+i)})")
                return i
                
    except Exception as e:
        logger.error(f"Error detectando voto del usuario: {e}")
    
    return None

def _parse_answer_hint_from_db(message_id: int) -> Optional[int]:
    """
    MÉTODO 2: Busca mensajes con patrón ###X cerca del message_id de la encuesta.
    Busca en la base de datos mensajes anteriores o posteriores con el patrón.
    """
    try:
        import sqlite3
        import re
        
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        
        # Buscar mensajes cerca del message_id actual (±5 mensajes)
        query = """
        SELECT message_id, snippet, raw_json 
        FROM drafts 
        WHERE message_id BETWEEN ? AND ? 
        AND deleted = 0
        ORDER BY message_id ASC
        """
        
        range_start = message_id - 5
        range_end = message_id + 5
        
        results = cur.execute(query, (range_start, range_end)).fetchall()
        con.close()
        
        # Patrón para detectar ###A, ###B, ###C, ###D
        hint_pattern = re.compile(r'###([A-Da-d])', re.IGNORECASE)
        
        for mid, snippet, raw_json in results:
            if mid == message_id:  # Skip la encuesta misma
                continue
                
            # Buscar en snippet primero
            text_to_search = snippet or ""
            
            # Si no hay snippet, buscar en el raw_json
            if not text_to_search and raw_json:
                try:
                    data = json.loads(raw_json)
                    text_to_search = data.get("text", "") or data.get("caption", "")
                except:
                    pass
            
            if text_to_search:
                match = hint_pattern.search(text_to_search)
                if match:
                    letter = match.group(1).upper()
                    option_index = ord(letter) - ord('A')  # A=0, B=1, C=2, D=3
                    
                    logger.info(f"🔍 MÉTODO 2: Encontrado hint ###({letter}) = opción {option_index} en mensaje {mid}")
                    
                    # Guardar para eliminación posterior
                    CORRECT_ANSWERS_CACHE[message_id] = option_index
                    # Marcar el mensaje hint para eliminación
                    _mark_hint_message_for_deletion(mid)
                    
                    return option_index
    
    except Exception as e:
        logger.error(f"Error buscando hint de respuesta: {e}")
    
    return None

def _mark_hint_message_for_deletion(hint_message_id: int):
    """
    Marca un mensaje hint (###X) para ser eliminado del canal borrador.
    Lo agregamos a una lista especial para eliminación.
    """
    try:
        import sqlite3
        # Marcar como deleted para que no se publique
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        cur.execute("UPDATE drafts SET deleted=1 WHERE message_id=?", (hint_message_id,))
        con.commit()
        con.close()
        
        logger.info(f"📝 Mensaje hint {hint_message_id} marcado para no publicar")
    except Exception as e:
        logger.error(f"Error marcando mensaje hint para eliminación: {e}")

def _detect_correct_answer_for_quiz(message_id: int, poll_data: dict) -> int:
    """
    Función principal que intenta ambos métodos para detectar la respuesta correcta.
    
    MÉTODO 1: Detectar el voto del usuario en la encuesta
    MÉTODO 2: Buscar mensajes con patrón ###X cerca de la encuesta
    """
    
    # Verificar cache primero
    if message_id in CORRECT_ANSWERS_CACHE:
        cached_answer = CORRECT_ANSWERS_CACHE[message_id]
        logger.info(f"📂 Usando respuesta correcta cacheada para {message_id}: {cached_answer}")
        return cached_answer
    
    # MÉTODO 1: Detectar voto del usuario
    user_vote = _detect_user_vote_from_poll(poll_data)
    if user_vote is not None:
        CORRECT_ANSWERS_CACHE[message_id] = user_vote
        return user_vote
    
    # MÉTODO 2: Buscar hint ###X
    hint_answer = _parse_answer_hint_from_db(message_id)
    if hint_answer is not None:
        return hint_answer
    
    # FALLBACK: Si no se detecta nada, usar 0 (A)
    logger.warning(f"⚠️ No se pudo detectar respuesta correcta para quiz {message_id}, usando A (0)")
    return 0

# ========= Encuestas - SOLUCIÓN CON DETECCIÓN INTELIGENTE =========
def _poll_payload_from_raw(raw: dict, message_id: int = None):
    """
    Extrae los parámetros de la encuesta del JSON raw.
    Para quiz, usa detección inteligente de la respuesta correcta.
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
        
        # DETECCIÓN INTELIGENTE DE LA RESPUESTA CORRECTA
        if message_id:
            correct_option_id = _detect_correct_answer_for_quiz(message_id, p)
        else:
            # Fallback si no tenemos message_id
            correct_option_id = p.get("correct_option_id", 0)
            if correct_option_id is None:
                correct_option_id = 0
        
        # Validar rango
        try:
            correct_option_id = int(correct_option_id)
            if 0 <= correct_option_id < len(options):
                kwargs["correct_option_id"] = correct_option_id
                logger.info(f"✅ Quiz {message_id}: respuesta correcta en posición {correct_option_id} (opción {chr(65+correct_option_id)})")
            else:
                logger.warning(f"Respuesta fuera de rango: {correct_option_id}, opciones: {len(options)}")
                kwargs["correct_option_id"] = 0
        except (ValueError, TypeError):
            logger.warning(f"Respuesta inválida: {correct_option_id}")
            kwargs["correct_option_id"] = 0

    # Manejo de tiempo de la encuesta
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

    # Explicación para quiz
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
                    # ¡CLAVE! Pasar el message_id para detección inteligente
                    base_kwargs, is_quiz = _poll_payload_from_raw(data, message_id=mid)
                    kwargs = dict(base_kwargs)
                    kwargs["chat_id"] = dest
                    
                    if is_quiz:
                        cid = kwargs.get("correct_option_id", 0)
                        logger.info(f"📊 Enviando quiz {mid} a {dest}: respuesta correcta en {chr(65+cid)} (posición {cid})")
                    
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
