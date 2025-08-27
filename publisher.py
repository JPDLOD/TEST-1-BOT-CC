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
        # Si no hay match directo, buscar cualquier formato t.me/c/
        logger.warning(f"No se encontró patrón de justificación en: {text}")
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
