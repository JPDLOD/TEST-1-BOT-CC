# -*- coding: utf-8 -*-
"""
Sistema de Justificaciones Protegidas - VERSIÓN CORREGIDA
"""

import logging
import asyncio
import re
from typing import Optional, Dict, List, Tuple
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config import TZ, JUSTIFICATIONS_CHAT_ID, AUTO_DELETE_MINUTES

logger = logging.getLogger(__name__)

# Cache para rastrear mensajes enviados
sent_justifications: Dict[str, Dict] = {}
user_joke_messages: Dict[int, List[int]] = {}

def parse_justification_links(text: str) -> Tuple[List[int], str]:
    """Extrae múltiples IDs de justificación y el nombre del caso."""
    justification_ids = []
    case_name = ""
    
    case_pattern = re.search(r'^(.*?)(?=https://)', text)
    if case_pattern:
        potential_case = case_pattern.group(1).strip()
        if potential_case:
            case_name = potential_case.replace("📚", "").replace("*", "").replace("_", "").strip()
    
    link_pattern = re.compile(r'https?://t\.me/ccjustificaciones/(\d+(?:[,\-]\d+)*)', re.IGNORECASE)
    
    for match in link_pattern.finditer(text):
        id_string = match.group(1)
        parts = id_string.split(',')
        for part in parts:
            if '-' in part:
                try:
                    start, end = map(int, part.split('-'))
                    justification_ids.extend(range(start, end + 1))
                except:
                    pass
            else:
                try:
                    justification_ids.append(int(part))
                except:
                    pass
    
    justification_ids = sorted(list(set(justification_ids)))
    return justification_ids, case_name

def generate_justification_deep_link(bot_username: str, message_ids: List[int]) -> str:
    """Genera el deep-link para justificaciones."""
    ids_string = "_".join(map(str, message_ids))
    return f"https://t.me/{bot_username}?start=just_{ids_string}"

def create_justification_button(bot_username: str, message_ids: List[int], case_name: str = "") -> InlineKeyboardMarkup:
    """Crea el botón inline con el nombre del caso."""
    deep_link = generate_justification_deep_link(bot_username, message_ids)
    
    if case_name:
        clean_case = case_name.replace("*", "").replace("_", "").strip()
        button_text = f"Ver justificación {clean_case} 📚"
    else:
        button_text = "Ver justificación 📚"
    
    button = InlineKeyboardButton(button_text, url=deep_link)
    return InlineKeyboardMarkup([[button]])

async def clean_previous_messages(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Limpia mensajes previos del usuario."""
    user_key = str(user_id)
    
    # Limpiar justificaciones previas
    if user_key in sent_justifications:
        user_data = sent_justifications[user_key]
        
        if "timer_task" in user_data and user_data["timer_task"]:
            user_data["timer_task"].cancel()
        
        for msg_id in user_data.get("message_ids", []):
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
            except:
                pass
        
        del sent_justifications[user_key]
    
    # Limpiar mensajes de chistes
    if user_id in user_joke_messages:
        for msg_id in user_joke_messages[user_id]:
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
            except:
                pass
        del user_joke_messages[user_id]

async def send_protected_justifications(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    justification_message_ids: List[int]
) -> bool:
    """Envía múltiples justificaciones protegidas al usuario."""
    try:
        # Limpiar mensajes previos
        await clean_previous_messages(context, user_id)
        
        sent_messages = []
        
        for justification_id in justification_message_ids:
            try:
                logger.info(f"📋 Enviando justificación {justification_id} a usuario {user_id}")
                
                # Copiar el mensaje desde el canal de justificaciones
                copied_message = await context.bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=JUSTIFICATIONS_CHAT_ID,
                    message_id=justification_id,
                    protect_content=True
                )
                
                if copied_message:
                    sent_messages.append(copied_message.message_id)
                    logger.info(f"✅ Justificación {justification_id} enviada")
                
                if len(justification_message_ids) > 1:
                    await asyncio.sleep(0.3)
                    
            except TelegramError as e:
                logger.error(f"❌ Error enviando justificación {justification_id}: {e}")
                continue
        
        if not sent_messages:
            return False
        
        # Guardar referencias
        user_key = str(user_id)
        sent_justifications[user_key] = {
            "message_ids": sent_messages,
            "sent_at": datetime.now(tz=TZ),
            "timer_task": None
        }
        
        # Programar auto-eliminación si está configurada
        if AUTO_DELETE_MINUTES > 0:
            await schedule_messages_deletion(context, user_id, sent_messages)
        
        return True
        
    except Exception as e:
        logger.exception(f"❌ Error inesperado enviando justificaciones: {e}")
        return False

async def schedule_messages_deletion(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    message_ids: List[int]
):
    """Programa la eliminación automática de mensajes."""
    async def delete_messages():
        try:
            await asyncio.sleep(AUTO_DELETE_MINUTES * 60)
            
            for msg_id in message_ids:
                try:
                    await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
                except:
                    pass
            
            if user_id in user_joke_messages:
                for msg_id in user_joke_messages[user_id]:
                    try:
                        await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
                    except:
                        pass
                del user_joke_messages[user_id]
            
            logger.info(f"🗑️ Auto-eliminadas justificaciones del usuario {user_id}")
            
        except Exception as e:
            logger.error(f"❌ Error en auto-eliminación: {e}")
        finally:
            user_key = str(user_id)
            if user_key in sent_justifications:
                del sent_justifications[user_key]
    
    deletion_task = asyncio.create_task(delete_messages())
    
    user_key = str(user_id)
    if user_key in sent_justifications:
        sent_justifications[user_key]["timer_task"] = deletion_task
    
    logger.info(f"⏰ Programada auto-eliminación en {AUTO_DELETE_MINUTES} minutos")

async def handle_justification_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Maneja las solicitudes de justificación - CORREGIDO."""
    if not update.message or not update.message.text:
        return False
    
    text = update.message.text.strip()
    user_id = update.message.from_user.id
    
    # Verificar si es una solicitud de justificación
    if not text.startswith("/start just_"):
        return False
    
    # Extraer IDs (pueden ser múltiples)
    try:
        ids_string = text.replace("/start just_", "")
        justification_ids = []
        
        # Soportar formato ID1_ID2_ID3
        for id_str in ids_string.split("_"):
            if id_str.isdigit():
                justification_ids.append(int(id_str))
        
        if not justification_ids:
            raise ValueError("No se encontraron IDs válidos")
            
    except ValueError as e:
        logger.warning(f"⚠️ IDs de justificación inválidos: {text} - Error: {e}")
        await update.message.reply_text(
            "❌ Link de justificación inválido. Verifica que el enlace sea correcto."
        )
        return True
    
    logger.info(f"🔍 Solicitud de justificaciones {justification_ids} por usuario {user_id}")
    
    # Enviar mensaje de procesando
    processing_msg = await update.message.reply_text(
        "🔄 Obteniendo justificación..." if len(justification_ids) == 1 else f"🔄 Obteniendo {len(justification_ids)} justificaciones...",
        disable_notification=True
    )
    
    # Intentar enviar las justificaciones
    success = await send_protected_justifications(context, user_id, justification_ids)
    
    # Borrar mensaje de procesando
    try:
        await processing_msg.delete()
    except:
        pass
    
    if success:
        try:
            from justification_messages import get_random_message
            success_text = get_random_message()
        except ImportError:
            import random
            fallback_messages = [
                "📚 ¡Justificación lista! Revisa con calma.",
                "✨ Material de estudio enviado.",
                "🎯 ¡Justificación disponible!",
                "📖 Contenido académico listo para revisar.",
            ]
            success_text = random.choice(fallback_messages)
        
        joke_msg = await update.message.reply_text(
            success_text,
            disable_notification=True
        )
        
        if user_id not in user_joke_messages:
            user_joke_messages[user_id] = []
        user_joke_messages[user_id].append(joke_msg.message_id)
        
    else:
        await update.message.reply_text(
            "❌ No se pudo obtener la justificación. Puede que el enlace sea inválido o haya un problema temporal.",
            disable_notification=True
        )
    
    return True

# Función helper para el publisher
def extract_justification_info(text: str) -> Tuple[List[int], str]:
    """Función helper para el publisher."""
    return parse_justification_links(text)

# Comandos administrativos
async def cmd_test_justification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando para probar el sistema de justificaciones."""
    if not update.message:
        return
        
    if not context.args:
        await update.message.reply_text("Uso: /test_just <message_id> o /test_just <id1,id2,id3>")
        return
    
    try:
        ids_str = context.args[0]
        if ',' in ids_str:
            message_ids = [int(id.strip()) for id in ids_str.split(',')]
        else:
            message_ids = [int(ids_str)]
            
    except ValueError:
        await update.message.reply_text("❌ ID(s) de mensaje inválido(s)")
        return
    
    user_id = update.message.from_user.id
    success = await send_protected_justifications(context, user_id, message_ids)
    
    if success:
        await update.message.reply_text(f"✅ Justificación(es) {message_ids} enviada(s) como prueba")
    else:
        await update.message.reply_text(f"❌ No se pudieron enviar justificaciones {message_ids}")

def add_justification_handlers(application):
    """Agrega los handlers de justificaciones al bot principal - CORREGIDO."""
    from telegram.ext import CommandHandler, MessageHandler, filters
    
    # Handler CORREGIDO para /start just_ID1_ID2_ID3
    application.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r"^/start just_[\d_]+$"), 
        handle_justification_request
    ), group=0)
    
    # Comando de prueba
    application.add_handler(CommandHandler("test_just", cmd_test_justification))
    
    logger.info("✅ Handlers de justificaciones agregados al bot")
