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

# ========= CONFIGURACIÓN DEL BOT DE JUSTIFICACIONES =========
JUSTIFICATIONS_BOT_USERNAME = "JUST_CC_bot"  # Username del bot de justificaciones

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

# ========= FUNCIÓN PARA PROCESAR JUSTIFICACIONES CON DEEP LINKS =========
def process_justification_text(text: str) -> Tuple[str, bool]:
    """
    Convierte enlaces de justificación en deep links al bot de justificaciones.
    
    Soporta ambos formatos:
    - https://t.me/c/3058530208/123
    - https://t.me/ccjustificaciones/123
    
    Entrada: "CASO #3 https://t.me/c/3058530208/123"
    Salida: "<a href='https://t.me/JUST_CC_bot?start=just_123'>📚 Ver justificación CASO #3</a>"
    
    Returns:
        (texto_procesado, tiene_justificacion)
    """
    if not text:
        return text, False
    
    # PATRÓN MEJORADO: Detecta ambos formatos de enlaces
    # Formato 1: https://t.me/c/CHAT_ID/MESSAGE_ID
    # Formato 2: https://t.me/CHANNEL_USERNAME/MESSAGE_ID
    patterns = [
        r'(.*?)(https://t\.me/c/\d+/(\d+))',  # Formato con ID del chat
        r'(.*?)(https://t\.me/ccjustificaciones/(\d+))',  # Formato con username
    ]
    
    match = None
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            break
    
    if not match:
        return text, False
    
    case_name = match.group(1).strip()
    original_link = match.group(2)
    justification_id = match.group(3)  # Extraer el ID del mensaje
    
    logger.info(f"🔍 Detectado enlace: {original_link} → ID: {justification_id}")
    
    # Limpiar el nombre del caso
    if case_name:
        # Eliminar emojis y caracteres especiales comunes
        case_name = re.sub(r'[📚🏥\*_]', '', case_name).strip()
        if case_name:
            link_text = f"📚 Ver justificación {case_name}"
        else:
            link_text = "📚 Ver justificación"
    else:
        link_text = "📚 Ver justificación"
    
    # Crear deep link al bot de justificaciones
    # IMPORTANTE: Este deep link abre el bot privado con el comando /start just_ID
    deep_link = f"https://t.me/{JUSTIFICATIONS_BOT_USERNAME}?start=just_{justification_id}"
    
    # Crear enlace HTML clicable que redirige al bot
    html_link = f'<a href="{deep_link}">{link_text}</a>'
    
    # Obtener cualquier texto adicional después del enlace
    remaining_text = text[match.end():].strip()
    
    # Si hay texto adicional, agregarlo
    if remaining_text:
        processed_text = html_link + "\n\n" + remaining_text
    else:
        processed_text = html_link
    
    logger.info(f"✅ Convertido a deep link: {deep_link}")
    logger.info(f"📝 Texto del enlace: {link_text}")
    
    return processed_text, True

# ========= Función helper para compatibilidad =========
def extract_justification_from_text(text: str) -> Optional[Tuple[List[int], str]]:
    """
    Helper de compatibilidad - ya no se usa para botones.
    """
    return None  # Ya no creamos botones, usamos enlaces

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

# ========= DETECCIÓN DE POLLS/QUIZ =========

def detect_voted_polls_on_save(message_id: int, raw_json: str):
    """
    Se ejecuta cuando se guarda un borrador.
    Detecta si es una encuesta quiz y construye el mapeo poll_id.
    """
    try:
        data = json.loads(raw_json)
        if "poll" not in data:
            return
        
        poll = data["poll"]
        if poll.get("type") != "quiz":
            return
        
        # Crear mapeo poll_id -> message_id
        if "id" in poll:
            poll_id = str(poll["id"])
            POLL_ID_TO_MESSAGE_ID[poll_id] = message_id
            logger.info(f"🗺️ Quiz detectado: poll_id {poll_id} → message_id {message_id}")
            
            # Si ya tiene correct_option_id disponible, usarlo
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

async def handle_poll_update(update, context):
    """Handler para capturar cuando una encuesta es actualizada."""
    if not update.poll:
        return
    
    poll = update.poll
    poll_id = str(poll.id)
    
    logger.info(f"🔄 UPDATE POLL: poll_id={poll_id}")
    
    # Encontrar message_id correspondiente
    message_id = POLL_ID_TO_MESSAGE_ID.get(poll_id)
    if not message_id:
        return
    
    # Intentar extraer correct_option_id
    if hasattr(poll, 'correct_option_id') and poll.correct_option_id is not None:
        correct_id = poll.correct_option_id
        DETECTED_CORRECT_ANSWERS[message_id] = correct_id
        logger.info(f"✅ Quiz {message_id} → correct_option_id = {correct_id}")

async def handle_poll_answer_update(update, context):
    """Handler para capturar respuestas individuales."""
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
        logger.info(f"✅ Quiz {message_id} → Usuario eligió opción {chosen_option}")

# ========= Funciones de encuestas =========
def _poll_payload_from_raw(raw: dict, message_id: int = None):
    """Extrae parámetros de la encuesta."""
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
        
        # Usar respuesta detectada o del JSON
        if message_id and message_id in DETECTED_CORRECT_ANSWERS:
            correct_option_id = DETECTED_CORRECT_ANSWERS[message_id]
        elif p.get("correct_option_id") is not None:
            correct_option_id = int(p.get("correct_option_id", 0))
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

    for i, (mid, _t, raw) in enumerate(rows):
        try:
            data = json.loads(raw or "{}")
        except Exception as e:
            logger.error(f"Error parseando JSON para mensaje {mid}: {e}")
            data = {}

        # ========= PROCESAR JUSTIFICACIONES CON DEEP LINKS =========
        text_content = data.get("text", "") or data.get("caption", "")
        has_justification = False
        
        if text_content:
            processed_text, has_justification = process_justification_text(text_content)
            
            if has_justification:
                # Actualizar el texto procesado con deep link al bot
                if "text" in data:
                    data["text"] = processed_text
                elif "caption" in data:
                    data["caption"] = processed_text

        any_success = False
        for dest in targets:
            
            if "poll" in data:
                # Enviar encuesta
                try:
                    base_kwargs, is_quiz = _poll_payload_from_raw(data, message_id=mid)
                    kwargs = dict(base_kwargs)
                    kwargs["chat_id"] = dest
                    
                    if is_quiz:
                        cid = kwargs.get("correct_option_id", 0)
                        logger.info(f"📊 Enviando quiz {mid} a {dest} → respuesta correcta: {chr(65+cid)}")
                    
                    coro_factory = lambda k=kwargs: context.bot.send_poll(**k)
                    ok, msg = await _send_with_backoff(coro_factory, base_pause=PAUSE)
                    
                except Exception as e:
                    logger.error(f"Error procesando poll {mid}: {e}")
                    ok, msg = False, None
                    
            elif has_justification:
                # ========= ENVIAR MENSAJE CON DEEP LINK =========
                try:
                    # Extraer componentes del mensaje
                    photo = data.get("photo")
                    document = data.get("document")
                    video = data.get("video")
                    
                    # Si es solo texto, enviar con parse_mode HTML
                    if not photo and not document and not video:
                        coro_factory = lambda: context.bot.send_message(
                            chat_id=dest,
                            text=processed_text,
                            parse_mode="HTML",
                            disable_web_page_preview=False
                        )
                    else:
                        # Si tiene media, copiar el mensaje pero con caption modificado
                        coro_factory = lambda d=dest, m=mid: context.bot.copy_message(
                            chat_id=d,
                            from_chat_id=SOURCE_CHAT_ID,
                            message_id=m,
                            caption=processed_text,
                            parse_mode="HTML"
                        )
                    
                    ok, msg = await _send_with_backoff(coro_factory, base_pause=PAUSE)
                    
                except Exception as e:
                    logger.error(f"Error enviando mensaje con justificación {mid}: {e}")
                    # Fallback: copiar mensaje original
                    coro_factory = lambda d=dest, m=mid: context.bot.copy_message(
                        chat_id=d, from_chat_id=SOURCE_CHAT_ID, message_id=m
                    )
                    ok, msg = await _send_with_backoff(coro_factory, base_pause=PAUSE)
            else:
                # Mensaje normal sin justificación
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
    all_rows = get_unsent_drafts(DB_FILE)
    if not all_rows:
        return 0, 0, {t: [] for t in targets}
    rows = [(m, t, r) for (m, t, r) in all_rows if m not in SCHEDULED_LOCK]
    if not rows:
        return 0, 0, {t: [] for t in targets}
    return await _publicar_rows(context, rows=rows, targets=targets, mark_as_sent=mark_as_sent)

async def publicar_ids(context: ContextTypes.DEFAULT_TYPE, *, ids: List[int],
                       targets: List[int], mark_as_sent: bool):
    """Publica mensajes específicos por ID."""
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
