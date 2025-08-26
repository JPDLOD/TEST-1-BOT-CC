import logging
import asyncio
import re
import json
from typing import Optional, Dict, Tuple
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, filters
from telegram.error import TelegramError

from config import TZ, SOURCE_CHAT_ID, JUSTIFICATIONS_CHAT_ID, AUTO_DELETE_MINUTES, JUSTIFICATIONS_CHANNEL_USERNAME

logger = logging.getLogger(__name__)

# Cache para justificaciones activas
sent_justifications: Dict[str, Dict] = {}

def extract_justification_link(text: str) -> Optional[int]:
    """Detecta enlaces de justificación y extrae el ID del mensaje."""
    if not text:
        return None
    
    # Patrón para t.me/ccjustificaciones/123
    username_pattern = rf"(?:https?://)?t\.me/{re.escape(JUSTIFICATIONS_CHANNEL_USERNAME)}/(\d+)/?(?:\s|$)"
    match = re.search(username_pattern, text, re.IGNORECASE)
    if match:
        message_id = int(match.group(1))
        logger.info(f"DETECTADO enlace justificación: @{JUSTIFICATIONS_CHANNEL_USERNAME}/{message_id}")
        return message_id
    
    # Patrón para ID numérico
    justif_clean = str(JUSTIFICATIONS_CHAT_ID).replace("-100", "")
    numeric_pattern = rf"(?:https?://)?t\.me/c/{re.escape(justif_clean)}/(\d+)/?(?:\s|$)"
    match = re.search(numeric_pattern, text)
    if match:
        message_id = int(match.group(1))
        logger.info(f"DETECTADO enlace justificación numérico: c/{justif_clean}/{message_id}")
        return message_id
    
    return None

def remove_justification_link_from_text(text: str, justification_id: int) -> str:
    """Remueve el enlace de justificación del texto."""
    if not text:
        return text
    
    # Remover patrón de username
    username_pattern = rf"(?:https?://)?t\.me/{re.escape(JUSTIFICATIONS_CHANNEL_USERNAME)}/{justification_id}/?(?:\s|$)"
    clean_text = re.sub(username_pattern, "", text, flags=re.IGNORECASE).strip()
    
    # Remover patrón numérico
    justif_clean = str(JUSTIFICATIONS_CHAT_ID).replace("-100", "")
    numeric_pattern = rf"(?:https?://)?t\.me/c/{re.escape(justif_clean)}/{justification_id}/?(?:\s|$)"
    clean_text = re.sub(numeric_pattern, "", clean_text).strip()
    
    # Limpiar saltos de línea múltiples
    clean_text = re.sub(r'\n\s*\n\s*\n', '\n\n', clean_text)
    
    return clean_text

def create_justification_button(bot_username: str, message_id: int) -> InlineKeyboardMarkup:
    """Crea botón inline que abre el bot en privado."""
    deep_link = f"https://t.me/{bot_username}?start=just_{message_id}"
    button = InlineKeyboardButton("Ver justificación 🔒", url=deep_link)
    return InlineKeyboardMarkup([[button]])

async def send_protected_justification(context: ContextTypes.DEFAULT_TYPE, user_id: int, justification_message_id: int) -> bool:
    """Envía justificación protegida al usuario."""
    try:
        logger.info(f"Enviando justificación {justification_message_id} a usuario {user_id}")
        
        copied_message = await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=JUSTIFICATIONS_CHAT_ID,
            message_id=justification_message_id,
            protect_content=True
        )
        
        if copied_message and AUTO_DELETE_MINUTES > 0:
            # Programar eliminación
            async def delete_after_delay():
                await asyncio.sleep(AUTO_DELETE_MINUTES * 60)
                try:
                    await context.bot.delete_message(chat_id=user_id, message_id=copied_message.message_id)
                    await context.bot.send_message(
                        chat_id=user_id,
                        text="La justificación se ha eliminado automáticamente por seguridad.",
                        disable_notification=True
                    )
                except:
                    pass
            
            asyncio.create_task(delete_after_delay())
        
        return True
        
    except TelegramError as e:
        logger.error(f"Error enviando justificación: {e}")
        return False

async def handle_justification_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Maneja solicitudes de justificación vía /start just_123"""
    if not update.message or not update.message.text:
        return False
    
    text = update.message.text.strip()
    if not text.startswith("/start just_"):
        return False
    
    try:
        justification_id = int(text.replace("/start just_", ""))
        user_id = update.message.from_user.id
        
        logger.info(f"Solicitud justificación {justification_id} por usuario {user_id}")
        
        processing_msg = await update.message.reply_text("Obteniendo justificación...")
        
        success = await send_protected_justification(context, user_id, justification_id)
        
        await processing_msg.delete()
        
        if success:
            await update.message.reply_text(
                f"Justificación enviada con protección anti-copia.\n"
                f"Se eliminará automáticamente en {AUTO_DELETE_MINUTES} minutos.",
                disable_notification=True
            )
        else:
            await update.message.reply_text("No se pudo obtener la justificación.")
        
        return True
        
    except ValueError:
        await update.message.reply_text("Link de justificación inválido.")
        return True

async def process_message_for_justifications(context: ContextTypes.DEFAULT_TYPE, raw_json: str) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """FUNCIÓN PRINCIPAL: Procesa un mensaje y devuelve JSON limpio + botón si tiene justificación."""
    try:
        data = json.loads(raw_json)
        
        # Buscar texto en 'text' o 'caption'
        text_field = None
        original_text = ""
        
        if data.get("text"):
            text_field = "text"
            original_text = data["text"]
        elif data.get("caption"):
            text_field = "caption"
            original_text = data["caption"]
        
        if not text_field:
            return raw_json, None
        
        # Detectar enlace de justificación
        justification_id = extract_justification_link(original_text)
        if not justification_id:
            return raw_json, None
        
        # Remover enlace del texto
        clean_text = remove_justification_link_from_text(original_text, justification_id)
        
        # Crear JSON modificado
        modified_data = data.copy()
        modified_data[text_field] = clean_text
        modified_json = json.dumps(modified_data, ensure_ascii=False)
        
        # Obtener username del bot
        bot_info = await context.bot.get_me()
        bot_username = bot_info.username or "bot"
        
        # Crear botón
        keyboard = create_justification_button(bot_username, justification_id)
        
        logger.info(f"PROCESADO: Justificación {justification_id} convertida en botón")
        
        return modified_json, keyboard
        
    except Exception as e:
        logger.error(f"Error procesando justificaciones: {e}")
        return raw_json, None

def add_justification_handlers(application):
    """Agrega handlers al bot."""
    application.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r"^/start just_\d+$"), 
        handle_justification_request
    ), group=0)
    logger.info("Handlers de justificaciones agregados")
