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

# ========= Cache para respuestas correctas =========
CORRECT_ANSWERS_CACHE: Dict[int, int] = {}
HINT_MESSAGES_TO_DELETE: Set[int] = set()

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

# ========= DETECCIÓN MEJORADA DE RESPUESTA CORRECTA =========

def _detect_chosen_option_from_poll(poll_data: dict) -> Optional[int]:
    """
    MÉTODO MEJORADO: Detecta la opción que el usuario eligió analizando
    múltiples campos del poll JSON que podrían contener esta información.
    """
    try:
        # Método 1: Revisar options directamente por voter_count
        options = poll_data.get("options", [])
        for i, option in enumerate(options):
            if isinstance(option, dict):
                voter_count = option.get("voter_count", 0)
                if voter_count > 0:
                    logger.info(f"🗳️ MÉTODO 1: Detectada opción votada en posición {i} (letra {chr(65+i)}) - voter_count: {voter_count}")
                    return i
        
        # Método 2: Revisar poll results si existen
        if "results" in poll_data:
            results = poll_data["results"]
            if isinstance(results, dict):
                results_list = results.get("results", [])
                for i, result in enumerate(results_list):
                    if isinstance(result, dict):
                        # Buscar indicadores de voto
                        if result.get("chosen", False) or result.get("voter_count", 0) > 0:
                            logger.info(f"🗳️ MÉTODO 2: Detectada opción votada en results posición {i} (letra {chr(65+i)})")
                            return i
        
        # Método 3: Revisar si hay información en poll_results
        if "poll_results" in poll_data:
            poll_results = poll_data["poll_results"]
            if isinstance(poll_results, dict):
                results_list = poll_results.get("results", [])
                for i, result in enumerate(results_list):
                    if isinstance(result, dict) and result.get("voter_count", 0) > 0:
                        logger.info(f"🗳️ MÉTODO 3: Detectada opción votada en poll_results posición {i} (letra {chr(65+i)})")
                        return i
        
        # Método 4: Buscar en cualquier estructura anidada que contenga voter_count > 0
        def _recursive_search_voter_count(obj, path=""):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    current_path = f"{path}.{key}" if path else key
                    if key == "voter_count" and isinstance(value, int) and value > 0:
                        logger.info(f"🗳️ MÉTODO 4: Encontrado voter_count > 0 en {current_path}")
                        return True
                    result = _recursive_search_voter_count(value, current_path)
                    if result:
                        return result
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    current_path = f"{path}[{i}]"
                    result = _recursive_search_voter_count(item, current_path)
                    if result:
                        return result
            return False
        
        if _recursive_search_voter_count(poll_data):
            logger.info("🗳️ MÉTODO 4: Se encontró evidencia de voto en estructura anidada")
            # Intentar extraer la posición si es posible
            pass
            
    except Exception as e:
        logger.error(f"Error detectando voto del usuario: {e}")
        # Log del JSON completo para debugging
        logger.debug(f"Poll data para debugging: {json.dumps(poll_data, indent=2, default=str)}")
    
    return None

def _parse_answer_hint_from_db(message_id: int) -> Optional[int]:
    """
    MÉTODO ###X: Busca mensajes con patrón ###X cerca del message_id de la encuesta.
    """
    try:
        import sqlite3
        import re
        
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        
        # Buscar mensajes cerca del message_id actual (±10 mensajes para mayor alcance)
        query = """
        SELECT message_id, snippet, raw_json 
        FROM drafts 
        WHERE message_id BETWEEN ? AND ? 
        AND deleted = 0
        ORDER BY message_id ASC
        """
        
        range_start = message_id - 10
        range_end = message_id + 10
        
        results = cur.execute(query, (range_start, range_end)).fetchall()
        con.close()
        
        # Patrón mejorado para detectar ###A, ###B, ###C, ###D (también espacios)
        hint_pattern = re.compile(r'###\s*([A-Da-d])', re.IGNORECASE)
        
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
                    
                    logger.info(f"🔍 MÉTODO ###X: Encontrado hint '###({letter})' = opción {option_index} en mensaje {mid}")
                    
                    # Marcar mensaje hint para eliminación
                    HINT_MESSAGES_TO_DELETE.add(mid)
                    
                    return option_index
    
    except Exception as e:
        logger.error(f"Error buscando hint de respuesta: {e}")
    
    return None

def _detect_correct_answer_comprehensive(message_id: int, poll_data: dict) -> int:
    """
    FUNCIÓN PRINCIPAL: Intenta TODOS los métodos disponibles para detectar la respuesta correcta.
    """
    
    # Cache check
    if message_id in CORRECT_ANSWERS_CACHE:
        cached_answer = CORRECT_ANSWERS_CACHE[message_id]
        logger.info(f"📂 Quiz {message_id}: usando respuesta cacheada {chr(65+cached_answer)} (pos {cached_answer})")
        return cached_answer
    
    # Log del poll data para debugging
    logger.debug(f"🔍 Analizando poll {message_id}:")
    logger.debug(f"Poll keys: {list(poll_data.keys()) if isinstance(poll_data, dict) else 'No es dict'}")
    
    # MÉTODO 1: Detectar voto del usuario
    user_vote = _detect_chosen_option_from_poll(poll_data)
    if user_vote is not None:
        logger.info(f"✅ MÉTODO VOTO: Detectada respuesta correcta {chr(65+user_vote)} para quiz {message_id}")
        CORRECT_ANSWERS_CACHE[message_id] = user_vote
        return user_vote
    
    # MÉTODO 2: Buscar hint ###X
    hint_answer = _parse_answer_hint_from_db(message_id)
    if hint_answer is not None:
        logger.info(f"✅ MÉTODO HINT: Detectada respuesta correcta {chr(65+hint_answer)} para quiz {message_id}")
        CORRECT_ANSWERS_CACHE[message_id] = hint_answer
        return hint_answer
    
    # MÉTODO 3: Revisar si existe correct_option_id (por si acaso)
    direct_correct = poll_data.get("correct_option_id")
    if direct_correct is not None:
        try:
            direct_correct = int(direct_correct)
            options_count = len(poll_data.get("options", []))
            if 0 <= direct_correct < options_count:
                logger.info(f"✅ MÉTODO DIRECTO: Encontrado correct_option_id {chr(65+direct_correct)} para quiz {message_id}")
                CORRECT_ANSWERS_CACHE[message_id] = direct_correct
                return direct_correct
        except (ValueError, TypeError):
            pass
    
    # FALLBACK: Si ningún método funciona
    logger.warning(f"⚠️ NINGÚN MÉTODO funcionó para quiz {message_id}")
    logger.warning(f"📄 Poll data disponible: {json.dumps(poll_data, indent=2, default=str)[:500]}...")
    
    # Usar fallback A (0) pero sugerir el método ###X
    logger.warning(f"💡 SUGERENCIA: Usa un mensaje '###C' cerca de la encuesta si C es la respuesta correcta")
    return 0

async def _delete_hint_messages(context: ContextTypes.DEFAULT_TYPE):
    """Elimina mensajes hint del canal BORRADOR."""
    deleted_count = 0
    
    for hint_mid in list(HINT_MESSAGES_TO_DELETE):
        try:
            await context.bot.delete_message(chat_id=SOURCE_CHAT_ID, message_id=hint_mid)
            logger.info(f"🗑️ Eliminado mensaje hint {hint_mid} del canal")
            deleted_count += 1
        except Exception as e:
            logger.warning(f"No pude eliminar hint {hint_mid}: {e}")
        
        # Marcar como deleted en DB
        try:
            import sqlite3
            con = sqlite3.connect(DB_FILE)
            cur = con.cursor()
            cur.execute("UPDATE drafts SET deleted=1 WHERE message_id=?", (hint_mid,))
            con.commit()
            con.close()
        except:
            pass
    
    HINT_MESSAGES_TO_DELETE.clear()
    
    if deleted_count > 0:
        logger.info(f"✅ Eliminados {deleted_count} mensajes hint")

# ========= Encuestas - VERSIÓN FINAL =========
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
        
        # DETECCIÓN INTEGRAL DE RESPUESTA CORRECTA
        if message_id:
            correct_option_id = _detect_correct_answer_comprehensive(message_id, p)
        else:
            correct_option_id = 0
        
        # Aplicar respuesta detectada
        kwargs["correct_option_id"] = correct_option_id
        
        logger.info(f"🎯 Quiz {message_id}: establecida respuesta correcta → {chr(65+correct_option_id)} (posición {correct_option_id})")

    # Otros parámetros de la encuesta
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
    
    # Eliminar mensajes hint ANTES de procesar
    await _delete_hint_messages(context)
    
    publicados = 0
    fallidos = 0
    enviados_ids: List[int] = []
    posted_by_target: Dict[int, List[int]] = {t: [] for t in targets}

    for mid, _t, raw in rows:
        # Skip mensajes hint marcados para eliminación
        if mid in HINT_MESSAGES_TO_DELETE:
            continue
            
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
                        logger.info(f"📊 Enviando quiz {mid} a {dest}: respuesta → {chr(65+cid)} ✓")
                    
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
