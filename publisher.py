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

logger = logging.getLogger(**name**)

# ========= Estado de targets =========

# BACKUP siempre activo por seguridad

ACTIVE_BACKUP: bool = True  # SIEMPRE ON - No se puede cambiar

def is_active_backup() -> bool:
“”“Backup siempre activo por seguridad.”””
return True  # Siempre retorna True

def set_active_backup(value: bool) -> None:
“”“DEPRECADO - Backup siempre activo.”””
# No hace nada, backup siempre ON
pass

def get_active_targets() -> List[int]:
targets = [TARGET_CHAT_ID]
if BACKUP_CHAT_ID:  # Siempre incluye backup si está configurado
targets.append(BACKUP_CHAT_ID)
return targets

# ========= Contadores / locks (usados por otros módulos) =========

STATS = {“cancelados”: 0, “eliminados”: 0}
SCHEDULED_LOCK: Set[int] = set()

# ========= CACHE GLOBAL PARA RESPUESTAS CORRECTAS DETECTADAS =========

DETECTED_CORRECT_ANSWERS: Dict[int, int] = {}  # {message_id: correct_option_index}
POLL_ID_TO_MESSAGE_ID: Dict[str, int] = {}     # {poll_id: message_id} mapeo

# ========= PATRÓN PARA DETECTAR LINKS DE JUSTIFICACIONES =========

# Mejorado para detectar múltiples formatos

JUSTIFICATION_LINK_PATTERN = re.compile(r’https?://t.me/ccjustificaciones/(\d+(?:[,-]\d+)*)’, re.IGNORECASE)

# ========= Función para detectar y extraer justificaciones =========

def extract_justification_from_text(text: str) -> Optional[Tuple[List[int], str, str]]:
“””
Detecta si un texto contiene links de justificación y extrae el nombre del caso.
Soporta múltiples IDs y rangos.
Retorna: ([lista_ids], nombre_caso, texto_limpio) o None
“””
if not text:
return None

```
justification_ids = []
case_name = ""

# Buscar todos los matches de justificaciones
has_justification = JUSTIFICATION_LINK_PATTERN.search(text)
if not has_justification:
    return None

# Buscar nombre del caso (texto antes del primer link)
case_match = re.search(r'^(.*?)https://t\.me/ccjustificaciones/', text, re.IGNORECASE)
if case_match:
    potential_case = case_match.group(1).strip()
    if potential_case:
        # Limpiar pero mantener el formato del caso
        case_name = potential_case.replace("*", "").replace("_", "").strip()

# Extraer todos los IDs de todos los links encontrados
for match in JUSTIFICATION_LINK_PATTERN.finditer(text):
    id_string = match.group(1)
    
    # Procesar el string de IDs que puede contener comas y/o rangos
    # Por ejemplo: "10,11,15-18,20"
    parts = id_string.split(',')
    for part in parts:
        part = part.strip()
        if '-' in part:
            # Es un rango
            try:
                range_parts = part.split('-')
                if len(range_parts) == 2:
                    start = int(range_parts[0].strip())
                    end = int(range_parts[1].strip())
                    justification_ids.extend(range(start, end + 1))
            except (ValueError, IndexError):
                # Si hay error, intentar agregar como ID individual
                try:
                    justification_ids.append(int(part))
                except:
                    pass
        else:
            # Es un ID simple
            try:
                justification_ids.append(int(part.strip()))
            except ValueError:
                pass

if not justification_ids:
    return None

# Eliminar duplicados y ordenar
justification_ids = sorted(list(set(justification_ids)))

# Eliminar TODOS los links del texto
clean_text = JUSTIFICATION_LINK_PATTERN.sub('', text).strip()

logger.info(f"📚 Justificaciones detectadas: {justification_ids} con caso: '{case_name}'")

return justification_ids, case_name, clean_text
```

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
wait = getattr(e, “retry_after”, None)
if wait is None:
import re
m = re.search(r”Retry in (\d+)”, str(e))
wait = int(m.group(1)) if m else 3
logger.warning(f”RetryAfter: esperando {wait}s …”)
import asyncio
await asyncio.sleep(wait + 1.0);  tries += 1
except TimedOut:
logger.warning(“TimedOut: esperando 3s …”)
import asyncio
await asyncio.sleep(3.0);  tries += 1
except NetworkError:
logger.warning(“NetworkError: esperando 3s …”)
import asyncio
await asyncio.sleep(3.0);  tries += 1
except TelegramError as e:
if “Flood control exceeded” in str(e):
logger.warning(“Flood control… esperando 5s …”)
import asyncio
await asyncio.sleep(5.0);  tries += 1
else:
logger.error(f”TelegramError no recuperable: {e}”)
return False, None
except Exception as e:
logger.exception(f”Error enviando: {e}”)
return False, None

```
    if tries >= 5:
        logger.error("Demasiados reintentos; abandono este mensaje.")
        return False, None
```

# ========= DETECCIÓN DE RESPUESTA CORRECTA EN POLLS =========

def detect_voted_polls_on_save(message_id: int, raw_json: str):
“””
Se ejecuta cuando se guarda un borrador.
Detecta si es una encuesta quiz y guarda el mapeo poll_id.
“””
try:
data = json.loads(raw_json)
if “poll” not in data:
return

```
    poll = data["poll"]
    if poll.get("type") != "quiz":
        return
    
    # Crear mapeo poll_id -> message_id
    if "id" in poll:
        poll_id = str(poll["id"])
        POLL_ID_TO_MESSAGE_ID[poll_id] = message_id
        logger.info(f"🗺️ Quiz detectado: poll_id {poll_id} → message_id {message_id}")
        
        # Si ya tiene correct_option_id, usarlo
        correct_option_id = poll.get("correct_option_id")
        if correct_option_id is not None:
            try:
                correct_id = int(correct_option_id)
                DETECTED_CORRECT_ANSWERS[message_id] = correct_id
                logger.info(f"✅ Quiz {message_id} tiene correct_option_id = {correct_id}")
            except (ValueError, TypeError):
                pass

except Exception as e:
    logger.error(f"Error analizando poll: {e}")
```

async def extract_correct_answer_via_stop_poll(context: ContextTypes.DEFAULT_TYPE, message_id: int, is_forwarded: bool = False, raw_data: dict = None) -> Optional[int]:
“””
Intenta stopPoll en canal origen si es reenviado, o en el canal actual si no.
“””

```
# Si es forwardeado, intentar en el canal origen
if is_forwarded and raw_data:
    forward_from_chat_id = raw_data.get("forward_from_chat", {}).get("id")
    forward_from_message_id = raw_data.get("forward_from_message_id")
    
    if forward_from_chat_id and forward_from_message_id:
        try:
            logger.info(f"🔄 Mensaje reenviado. Intentando stopPoll en origen {forward_from_chat_id}")
            
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
        logger.info(f"🛑 Ejecutando stopPoll en quiz {message_id}...")
        
        stopped_poll = await context.bot.stop_poll(
            chat_id=SOURCE_CHAT_ID, 
            message_id=message_id
        )
        
        if stopped_poll and hasattr(stopped_poll, 'correct_option_id') and stopped_poll.correct_option_id is not None:
            correct_id = stopped_poll.correct_option_id
            logger.info(f"🎯 correct_option_id = {correct_id}")
            DETECTED_CORRECT_ANSWERS[message_id] = correct_id
            return correct_id
        
        # Analizar opciones por votos
        if hasattr(stopped_poll, 'options') and stopped_poll.options:
            for i, option in enumerate(stopped_poll.options):
                if option.voter_count > 0:
                    logger.info(f"🗳️ Voto detectado en opción {i}")
                    DETECTED_CORRECT_ANSWERS[message_id] = i
                    return i
    
    except TelegramError as e:
        logger.error(f"❌ Error en stopPoll: {e}")

return None
```

def is_message_forwarded(raw_data: dict) -> bool:
“”“Detecta si un mensaje es forwardeado.”””
forward_fields = [“forward_from”, “forward_from_chat”, “forward_from_message_id”, “forward_sender_name”, “forward_date”]
return any(field in raw_data for field in forward_fields)

def extract_correct_answer_from_json_deep_analysis(poll_data: dict, message_id: int) -> Optional[int]:
“”“Análisis del JSON del poll para encontrar la respuesta correcta.”””
try:
# Método 1: correct_option_id directo
if “correct_option_id” in poll_data and poll_data[“correct_option_id”] is not None:
try:
correct_id = int(poll_data[“correct_option_id”])
logger.info(f”✅ correct_option_id directo = {correct_id}”)
return correct_id
except (ValueError, TypeError):
pass

```
    # Método 2: Buscar en opciones por voter_count
    options = poll_data.get("options", [])
    for i, option in enumerate(options):
        if isinstance(option, dict):
            voter_count = option.get("voter_count", 0)
            if voter_count > 0:
                logger.info(f"✅ Opción {i} tiene {voter_count} voto(s)")
                return i
    
    return None
    
except Exception as e:
    logger.error(f"Error en análisis: {e}")
    return None
```

def extract_correct_answer_from_forwarded_poll_analysis(poll_data: dict, message_id: int) -> Optional[int]:
“”“Análisis especial para mensajes forwardeados.”””
return extract_correct_answer_from_json_deep_analysis(poll_data, message_id)

async def get_correct_answer_comprehensive(context: ContextTypes.DEFAULT_TYPE, message_id: int, poll_data: dict, raw_message_data: dict = None) -> int:
“”“Método integral para detectar la respuesta correcta.”””

```
# Verificar cache primero
if message_id in DETECTED_CORRECT_ANSWERS:
    detected_answer = DETECTED_CORRECT_ANSWERS[message_id]
    logger.info(f"🎯 Quiz {message_id}: usando respuesta cacheada → {chr(65+detected_answer)}")
    return detected_answer

# Detectar si es forwardeado
is_forwarded = False
if raw_message_data:
    is_forwarded = is_message_forwarded(raw_message_data)

# Análisis del JSON
json_result = extract_correct_answer_from_json_deep_analysis(poll_data, message_id)
if json_result is not None:
    DETECTED_CORRECT_ANSWERS[message_id] = json_result
    return json_result

# stopPoll
stop_poll_result = await extract_correct_answer_via_stop_poll(
    context, message_id, is_forwarded, raw_message_data
)
if stop_poll_result is not None:
    return stop_poll_result

logger.error(f"❌ No se pudo detectar respuesta correcta para quiz {message_id}")
return 0  # Fallback a A
```

def get_correct_answer_sync(message_id: int, poll_data: dict) -> int:
“”“Versión síncrona para casos donde no hay contexto async.”””
if message_id in DETECTED_CORRECT_ANSWERS:
return DETECTED_CORRECT_ANSWERS[message_id]

```
json_result = extract_correct_answer_from_json_deep_analysis(poll_data, message_id)
if json_result is not None:
    DETECTED_CORRECT_ANSWERS[message_id] = json_result
    return json_result

return 0
```

# ========= HANDLERS PARA CAPTURAR VOTOS =========

async def handle_poll_update(update, context):
“”“Handler para capturar cuando una encuesta es actualizada.”””
if not update.poll:
return

```
poll = update.poll
poll_id = str(poll.id)

message_id = POLL_ID_TO_MESSAGE_ID.get(poll_id)
if not message_id:
    return

if hasattr(poll, 'correct_option_id') and poll.correct_option_id is not None:
    correct_id = poll.correct_option_id
    DETECTED_CORRECT_ANSWERS[message_id] = correct_id
    logger.info(f"✅ Poll update: Quiz {message_id} → correct_option_id = {correct_id}")
```

async def handle_poll_answer_update(update, context):
“”“Handler para capturar respuestas de usuarios.”””
if not update.poll_answer:
return

```
poll_answer = update.poll_answer
poll_id = str(poll_answer.poll_id)
option_ids = list(poll_answer.option_ids) if poll_answer.option_ids else []

message_id = POLL_ID_TO_MESSAGE_ID.get(poll_id)
if not message_id:
    return

if option_ids and len(option_ids) > 0:
    chosen_option = option_ids[0]
    DETECTED_CORRECT_ANSWERS[message_id] = chosen_option
    logger.info(f"✅ Poll answer: Quiz {message_id} → opción {chosen_option}")
```

# ========= Encuestas =========

def _poll_payload_from_raw(raw: dict, message_id: int = None):
“”“Extrae parámetros de la encuesta.”””
p = raw.get(“poll”) or {}
question = p.get(“question”, “Pregunta”)
options_src = p.get(“options”, []) or []
options = [o.get(“text”, “”) for o in options_src]

```
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
```

# ========= Publicadores =========

async def _publicar_rows(context: ContextTypes.DEFAULT_TYPE, *, rows: List[Tuple[int, str, str]],
targets: List[int], mark_as_sent: bool) -> Tuple[int, int, Dict[int, List[int]]]:

```
# ANÁLISIS PREVIO: Procesar quizzes sin respuesta detectada
quizzes_to_analyze = []

for mid, _t, raw in rows:
    try:
        data = json.loads(raw or "{}")
        if "poll" in data and data["poll"].get("type") == "quiz":
            if mid not in DETECTED_CORRECT_ANSWERS:
                quizzes_to_analyze.append((mid, data["poll"], data))
    except:
        continue

if quizzes_to_analyze:
    logger.info(f"🔬 Procesando {len(quizzes_to_analyze)} quizzes")
    
    for quiz_mid, poll_data, full_message_data in quizzes_to_analyze:
        is_forwarded = is_message_forwarded(full_message_data)
        detected = await get_correct_answer_comprehensive(context, quiz_mid, poll_data, full_message_data)
        logger.info(f"🎯 Quiz {quiz_mid}: respuesta → {chr(65+detected)}")
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

for i, (mid, _t, raw) in enumerate(rows):
    # SALTAR mensajes que son solo links de justificación
    if i in messages_to_skip:
        logger.info(f"⏭️ Saltando mensaje {mid} (link de justificación)")
        if mark_as_sent:
            enviados_ids.append(mid)
        continue
    
    try:
        data = json.loads(raw or "{}")
    except Exception as e:
        logger.error(f"Error parseando JSON para mensaje {mid}: {e}")
        data = {}

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
                    logger.info(f"📊 Enviando quiz {mid} a {dest} → respuesta {chr(65+cid)}")
                
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
```

async def publicar(context: ContextTypes.DEFAULT_TYPE, *, targets: List[int], mark_as_sent: bool):
“”“Envía la cola completa EXCLUYENDO los bloqueados (SCHEDULED_LOCK).”””
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
placeholders = “,”.join(”?” for _ in ids)
sql = f”SELECT message_id, snippet, raw_json FROM drafts WHERE sent=0 AND deleted=0 AND message_id IN ({placeholders}) ORDER BY message_id ASC”
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