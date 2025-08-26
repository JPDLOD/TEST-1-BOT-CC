# -*- coding: utf-8 -*-
"""
Sistema de Justificaciones Protegidas - INTEGRADO
Detecta automáticamente enlaces de justificación y los convierte en botones protegidos
"""

import logging
import asyncio
import re
import os
import json
from typing import Optional, Dict, Set, Tuple
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import ContextTypes, MessageHandler, filters
from telegram.error import TelegramError

from config import TZ, SOURCE_CHAT_ID

# Importar configuración desde config.py
try:
    from config import JUSTIFICATIONS_CHAT_ID, AUTO_DELETE_MINUTES, JUSTIFICATIONS_CHANNEL_USERNAME
except ImportError:
    # Fallback si no están definidas en config.py
    JUSTIFICATIONS_CHAT_ID = int(os.environ.get("JUSTIFICATIONS_CHAT_ID", "-1003058530208"))
    AUTO_DELETE_MINUTES = int(os.environ.get("AUTO_DELETE_MINUTES", "10"))
    JUSTIFICATIONS_CHANNEL_USERNAME = os.environ.get("JUSTIFICATIONS_CHANNEL_USERNAME", "ccjustificaciones")

logger = logging.getLogger(__name__)

# Cache para rastrear mensajes enviados y sus timers de eliminación
sent_justifications: Dict[str, Dict] = {}

# ========= DETECCIÓN DE ENLACES DE JUSTIFICACIÓN =========

def extract_justification_link(text: str) -> Optional[int]:
    """
    Detecta enlaces del canal de justificaciones en el texto.
    Formatos soportados:
    - https://t.me/ccjustificaciones/123 (username)
    - https://t.me/c/3058530208/123 (ID numérico)
    - t.me/ccjustificaciones/123 (sin https)
    
    Returns: message_id de la justificación o None
    """
    if not text:
        return None
    
    # MÉTODO 1: Por username del canal (ccjustificaciones)
    # Usar la variable importada desde config
    justif_username = JUSTIFICATIONS_CHANNEL_USERNAME
    
    # Patrón para username: t.me/ccjustificaciones/123
    username_pattern = rf"(?:https?://)?t\.me/{re.escape(justif_username)}/(\d+)/?(?:\s|$)"
    match = re.search(username_pattern, text, re.IGNORECASE)
    if match:
        message_id = int(match.group(1))
        logger.info(f"🔗 Detectado enlace de justificación (username): @{justif_username}/{message_id}")
        return message_id
    
    # MÉTODO 2: Por ID numérico del canal (formato c/NUMERO/ID)
    justif_channel_clean = str(JUSTIFICATIONS_CHAT_ID)
    if justif_channel_clean.startswith("-100"):
        justif_channel_clean = justif_channel_clean[4:]
    
    # Patrón para ID numérico: t.me/c/3058530208/123
    numeric_pattern = rf"(?:https?://)?t\.me/c/{re.escape(justif_channel_clean)}/(\d+)/?(?:\s|$)"
    match = re.search(numeric_pattern, text)
    if match:
        message_id = int(match.group(1))
        logger.info(f"🔗 Detectado enlace de justificación (numérico): c/{justif_channel_clean}/{message_id}")
        return message_id
    
    return None

def remove_justification_link_from_text(text: str, justification_id: int) -> str:
    """
    Remueve el enlace de justificación del texto, dejando el resto intacto.
    Soporta ambos formatos: username y ID numérico.
    """
    if not text:
        return text
    
    # Obtener username del canal desde config
    justif_username = JUSTIFICATIONS_CHANNEL_USERNAME
    
    # Patrón para username
    username_pattern = rf"(?:https?://)?t\.me/{re.escape(justif_username)}/{justification_id}/?(?:\s|$)"
    
    # Patrón para ID numérico  
    justif_channel_clean = str(JUSTIFICATIONS_CHAT_ID)
    if justif_channel_clean.startswith("-100"):
        justif_channel_clean = justif_channel_clean[4:]
    numeric_pattern = rf"(?:https?://)?t\.me/c/{re.escape(justif_channel_clean)}/{justification_id}/?(?:\s|$)"
    
    # Remover ambos patrones
    clean_text = re.sub(username_pattern, "", text, flags=re.IGNORECASE).strip()
    clean_text = re.sub(numeric_pattern, "", clean_text).strip()
    
    # Limpiar múltiples saltos de línea
    clean_text = re.sub(r'\n\s*\n\s*\n', '\n\n', clean_text)
    
    return clean_text

# ========= FUNCIONES DE JUSTIFICACIÓN CORE =========

def generate_justification_deep_link(bot_username: str, message_id: int) -> str:
    """Genera el deep-link para una justificación específica."""
    return f"https://t.me/{bot_username}?start=just_{message_id}"

def create_justification_button(bot_username: str, message_id: int) -> InlineKeyboardMarkup:
    """Crea el botón inline "Ver justificación 🔒" con deep-link."""
    deep_link = generate_justification_deep_link(bot_username, message_id)
    button = InlineKeyboardButton("Ver justificación 🔒", url=deep_link)
    return InlineKeyboardMarkup([[button]])

async def send_protected_justification(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    justification_message_id: int
) -> bool:
    """Envía una justificación protegida específica al usuario."""
    
    try:
        logger.info(f"📋 Enviando justificación {justification_message_id} a usuario {user_id}")
        
        # Copiar el mensaje desde el canal de justificaciones al usuario
        copied_message = await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=JUSTIFICATIONS_CHAT_ID,
            message_id=justification_message_id,
            protect_content=True  # PROTECCIÓN: No se puede copiar/reenviar/capturar
        )
        
        if not copied_message:
            logger.error(f"❌ No se pudo copiar justificación {justification_message_id}")
            return False
        
        logger.info(f"✅ Justificación {justification_message_id} enviada a {user_id} (mensaje {copied_message.message_id})")
        
        # Programar auto-eliminación si está configurada
        if AUTO_DELETE_MINUTES > 0:
            await schedule_message_deletion(
                context, 
                user_id, 
                copied_message.message_id, 
                justification_message_id
            )
        
        return True
        
    except TelegramError as e:
        if "chat not found" in str(e).lower():
            logger.warning(f"⚠️ Usuario {user_id} no ha iniciado chat con el bot")
        elif "message not found" in str(e).lower():
            logger.error(f"❌ Justificación {justification_message_id} no encontrada en canal")
        elif "not enough rights" in str(e).lower():
            logger.error(f"❌ Bot no tiene permisos en canal de justificaciones")
        else:
            logger.error(f"❌ Error enviando justificación: {e}")
        return False
    
    except Exception as e:
        logger.exception(f"❌ Error inesperado enviando justificación: {e}")
        return False

async def schedule_message_deletion(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    message_id: int,
    justification_id: int
):
    """Programa la eliminación automática de una justificación."""
    
    async def delete_justification():
        try:
            await asyncio.sleep(AUTO_DELETE_MINUTES * 60)
            
            await context.bot.delete_message(chat_id=user_id, message_id=message_id)
            logger.info(f"🗑️ Auto-eliminada justificación {justification_id} del usuario {user_id}")
            
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="🕐 La justificación se ha eliminado automáticamente por seguridad.",
                    disable_notification=True
                )
            except:
                pass
                
        except TelegramError as e:
            if "message not found" not in str(e).lower():
                logger.warning(f"⚠️ No se pudo auto-eliminar justificación: {e}")
        except Exception as e:
            logger.error(f"❌ Error en auto-eliminación: {e}")
        finally:
            cache_key = f"{user_id}_{message_id}"
            sent_justifications.pop(cache_key, None)
    
    deletion_task = asyncio.create_task(delete_justification())
    cache_key = f"{user_id}_{message_id}"
    
    sent_justifications[cache_key] = {
        "user_id": user_id,
        "message_id": message_id,
        "justification_id": justification_id,
        "sent_at": datetime.now(tz=TZ),
        "deletion_task": deletion_task
    }
    
    logger.info(f"⏰ Programada auto-eliminación de justificación {justification_id} en {AUTO_DELETE_MINUTES} minutos")

# ========= HANDLER PARA DEEP-LINKS =========

async def handle_justification_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Maneja las solicitudes de justificación que llegan vía deep-link /start just_MESSAGE_ID"""
    
    if not update.message or not update.message.text:
        return False
    
    text = update.message.text.strip()
    user_id = update.message.from_user.id
    
    # Verificar si es una solicitud de justificación
    if not text.startswith("/start just_"):
        return False
    
    # Extraer el ID de la justificación
    try:
        justification_id_str = text.replace("/start just_", "")
        justification_id = int(justification_id_str)
    except ValueError:
        logger.warning(f"⚠️ ID de justificación inválido: {text}")
        await update.message.reply_text(
            "❌ Link de justificación inválido. Verifica que el enlace sea correcto."
        )
        return True
    
    logger.info(f"🔍 Solicitud de justificación {justification_id} por usuario {user_id}")
    
    # Enviar mensaje de "procesando"
    processing_msg = await update.message.reply_text(
        "🔄 Obteniendo justificación...",
        disable_notification=True
    )
    
    # Intentar enviar la justificación
    success = await send_protected_justification(context, user_id, justification_id)
    
    # Borrar el mensaje de "procesando"
    try:
        await processing_msg.delete()
    except:
        pass
    
    if success:
        success_text = "✅ Justificación enviada con protección anti-copia."
        if AUTO_DELETE_MINUTES > 0:
            success_text += f"\n🕐 Se eliminará automáticamente en {AUTO_DELETE_MINUTES} minutos."
        
        await update.message.reply_text(
            success_text,
            disable_notification=True
        )
    else:
        await update.message.reply_text(
            "❌ No se pudo obtener la justificación. Puede que el enlace sea inválido o haya un problema temporal.",
            disable_notification=True
        )
    
    return True

# ========= PROCESAMIENTO DE MENSAJES CON JUSTIFICACIONES =========

def process_message_with_justification(raw_json: str, bot_username: str) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """
    Procesa un mensaje que podría contener enlaces de justificación.
    
    Returns:
        Tuple[modified_json, keyboard_markup]: 
        - modified_json: JSON del mensaje sin los enlaces de justificación
        - keyboard_markup: Botón de justificación si se encontró, None si no
    """
    try:
        data = json.loads(raw_json)
        
        # Buscar en text o caption
        text_field = None
        original_text = ""
        
        if "text" in data and data["text"]:
            text_field = "text"
            original_text = data["text"]
        elif "caption" in data and data["caption"]:
            text_field = "caption"  
            original_text = data["caption"]
        
        if not text_field or not original_text:
            return raw_json, None
        
        # Buscar enlace de justificación
        justification_id = extract_justification_link(original_text)
        if not justification_id:
            return raw_json, None
        
        # Remover el enlace del texto
        clean_text = remove_justification_link_from_text(original_text, justification_id)
        
        # Actualizar el JSON
        modified_data = data.copy()
        modified_data[text_field] = clean_text
        
        # Crear el botón de justificación
        keyboard = create_justification_button(bot_username, justification_id)
        
        modified_json = json.dumps(modified_data, ensure_ascii=False)
        
        logger.info(f"🔗 Procesado mensaje con justificación {justification_id}")
        logger.info(f"📝 Texto original: {len(original_text)} chars → Texto limpio: {len(clean_text)} chars")
        
        return modified_json, keyboard
        
    except Exception as e:
        logger.error(f"❌ Error procesando justificación: {e}")
        return raw_json, None

# ========= COMANDOS ADMINISTRATIVOS =========

async def cmd_test_justification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando para probar el sistema de justificaciones."""
    
    if not context.args:
        await update.message.reply_text("Uso: /test_just <message_id>")
        return
    
    try:
        message_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID de mensaje inválido")
        return
    
    user_id = update.message.from_user.id
    success = await send_protected_justification(context, user_id, message_id)
    
    if success:
        await update.message.reply_text(f"✅ Justificación {message_id} enviada como prueba")
    else:
        await update.message.reply_text(f"❌ No se pudo enviar justificación {message_id}")

async def cmd_justification_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra estadísticas del sistema de justificaciones."""
    
    active_justifications = len(sent_justifications)
    
    stats_text = f"""📊 **Estadísticas de Justificaciones**

🔒 Justificaciones activas: {active_justifications}
🕐 Auto-eliminación: {'ON (' + str(AUTO_DELETE_MINUTES) + ' min)' if AUTO_DELETE_MINUTES > 0 else 'OFF'}
📁 Canal justificaciones: `{JUSTIFICATIONS_CHAT_ID}`
🏷️ Username: @{JUSTIFICATIONS_CHANNEL_USERNAME}
"""
    
    if active_justifications > 0:
        stats_text += "\n📋 **Activas actualmente:**\n"
        for cache_key, info in list(sent_justifications.items())[:5]:
            sent_time = info['sent_at'].strftime("%H:%M:%S")
            stats_text += f"• Usuario {info['user_id']} - Justif {info['justification_id']} ({sent_time})\n"
        
        if active_justifications > 5:
            stats_text += f"... y {active_justifications - 5} más\n"
    
    await update.message.reply_text(stats_text, parse_mode="Markdown")

# ========= INTEGRACIÓN CON EL BOT PRINCIPAL =========

def add_justification_handlers(application):
    """
    Agrega los handlers de justificaciones al bot principal.
    """
    
    from telegram.ext import CommandHandler
    
    # Handler para /start just_ID (debe ir ANTES del handler general de /start)
    application.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r"^/start just_\d+$"), 
        handle_justification_request
    ), group=0)  # Grupo 0 para que tenga prioridad
    
    # Comandos administrativos
    application.add_handler(CommandHandler("test_just", cmd_test_justification))
    application.add_handler(CommandHandler("just_stats", cmd_justification_stats))
    
    logger.info("✅ Handlers de justificaciones agregados al bot")

# ========= FUNCIÓN PARA USAR DESDE PUBLISHER =========

async def get_bot_username(context: ContextTypes.DEFAULT_TYPE) -> str:
    """Obtiene el username del bot para generar deep-links."""
    try:
        bot_info = await context.bot.get_me()
        return bot_info.username or "bot"
    except Exception as e:
        logger.error(f"Error obteniendo username del bot: {e}")
        return "bot"

async def process_draft_for_justifications(
    context: ContextTypes.DEFAULT_TYPE, 
    raw_json: str
) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """
    Función principal para que publisher.py procese justificaciones automáticamente.
    
    Returns:
        Tuple[modified_json, justification_keyboard]
    """
    bot_username = await get_bot_username(context)
    return process_message_with_justification(raw_json, bot_username)
