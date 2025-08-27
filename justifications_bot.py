#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Bot de justificaciones - Versión corregida con mejor logging"""

import os
import logging
import asyncio
from typing import Dict, List

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import TelegramError

# Configuración de logging más detallada
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuración desde variables de entorno
BOT_TOKEN = os.environ.get("JUST_BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("❌ JUST_BOT_TOKEN no está configurado")
    exit(1)

# Usar la misma variable que el bot principal para consistencia
JUSTIFICATIONS_CHAT_ID = int(os.environ.get("JUSTIFICATIONS_CHAT_ID", "-1003058530208"))
AUTO_DELETE_MINUTES = int(os.environ.get("JUST_AUTO_DELETE_MINUTES", "10"))
ADMIN_IDS = [int(x) for x in os.environ.get("JUST_ADMIN_IDS", "").split(",") if x]

logger.info(f"📚 Bot de justificaciones iniciando...")
logger.info(f"📁 Canal de justificaciones: {JUSTIFICATIONS_CHAT_ID}")
logger.info(f"⏰ Auto-eliminación: {AUTO_DELETE_MINUTES} minutos")

user_sessions: Dict = {}

try:
    from justification_messages import get_weighted_random_message
except ImportError:
    import random
    def get_weighted_random_message():
        return random.choice([
            "📚 ¡Justificación lista! Revisa con calma.",
            "✨ Material de estudio enviado.",
            "🎯 ¡Justificación disponible!",
            "📖 Contenido académico listo para revisar."
        ])

async def send_justification(ctx: ContextTypes.DEFAULT_TYPE, user_id: int, just_id: int) -> bool:
    """Envía una justificación protegida al usuario."""
    try:
        logger.info(f"📋 Intentando copiar mensaje {just_id} del canal {JUSTIFICATIONS_CHAT_ID} al usuario {user_id}")
        
        sent = await ctx.bot.copy_message(
            chat_id=user_id,
            from_chat_id=JUSTIFICATIONS_CHAT_ID,
            message_id=just_id,
            protect_content=True
        )
        
        if sent:
            logger.info(f"✅ Justificación {just_id} enviada exitosamente a usuario {user_id}")
            if user_id not in user_sessions:
                user_sessions[user_id] = {"messages": [], "task": None}
            user_sessions[user_id]["messages"].append(sent.message_id)
            return True
        else:
            logger.error(f"❌ No se pudo enviar justificación {just_id} - respuesta vacía")
            return False
            
    except TelegramError as e:
        logger.error(f"❌ Error de Telegram al enviar justificación {just_id}: {e}")
        if "message not found" in str(e).lower():
            logger.error(f"⚠️ El mensaje {just_id} no existe en el canal {JUSTIFICATIONS_CHAT_ID}")
        elif "chat not found" in str(e).lower():
            logger.error(f"⚠️ El bot no tiene acceso al canal {JUSTIFICATIONS_CHAT_ID}")
        elif "no rights" in str(e).lower():
            logger.error(f"⚠️ El bot no tiene permisos para leer mensajes del canal {JUSTIFICATIONS_CHAT_ID}")
        return False
    except Exception as e:
        logger.error(f"❌ Error inesperado al enviar justificación {just_id}: {e}")
        return False

async def clean_messages(ctx: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Limpia mensajes previos del usuario."""
    if user_id in user_sessions:
        for msg_id in user_sessions[user_id].get("messages", []):
            try:
                await ctx.bot.delete_message(chat_id=user_id, message_id=msg_id)
                logger.info(f"🗑️ Borrado mensaje {msg_id} del usuario {user_id}")
            except:
                pass
        user_sessions[user_id]["messages"] = []

async def auto_delete(ctx: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Auto-elimina mensajes después del tiempo configurado."""
    logger.info(f"⏰ Programando auto-eliminación en {AUTO_DELETE_MINUTES} minutos para usuario {user_id}")
    await asyncio.sleep(AUTO_DELETE_MINUTES * 60)
    await clean_messages(ctx, user_id)
    if user_id in user_sessions:
        del user_sessions[user_id]
    logger.info(f"🗑️ Auto-eliminación completada para usuario {user_id}")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el comando /start y deep links."""
    if not update.message:
        return
    
    user_id = update.message.from_user.id
    text = update.message.text
    
    logger.info(f"👤 Usuario {user_id} envió: {text}")
    
    # Verificar si es un deep link de justificación
    if "just_" in text:
        logger.info(f"🔗 Deep link detectado: {text}")
        
        ids = []
        try:
            # Extraer IDs del formato: /start just_123 o /start just_123_456_789
            if " just_" in text:
                parts = text.split(" just_")[1].split("_")
            else:
                parts = text.split("just_")[1].split("_")
            
            ids = [int(p.strip()) for p in parts if p.strip().isdigit()]
            logger.info(f"📝 IDs extraídos: {ids}")
        except Exception as e:
            logger.error(f"❌ Error extrayendo IDs de '{text}': {e}")
            await update.message.reply_text("❌ Link de justificación inválido")
            return
        
        if not ids:
            logger.warning(f"⚠️ No se pudieron extraer IDs válidos de: {text}")
            await update.message.reply_text("❌ No se encontraron justificaciones válidas en el enlace")
            return
        
        # Limpiar mensajes previos
        await clean_messages(context, user_id)
        
        # Mensaje de procesando
        proc = await update.message.reply_text(
            "🔄 Obteniendo justificación..." if len(ids) == 1 else f"🔄 Obteniendo {len(ids)} justificaciones..."
        )
        
        # Enviar justificaciones
        success_count = 0
        failed_ids = []
        
        for jid in ids:
            logger.info(f"📤 Enviando justificación {jid} a usuario {user_id}")
            if await send_justification(context, user_id, jid):
                success_count += 1
                if len(ids) > 1:
                    await asyncio.sleep(0.3)
            else:
                failed_ids.append(jid)
                logger.error(f"❌ Falló envío de justificación {jid}")
        
        # Borrar mensaje de procesando
        try:
            await proc.delete()
        except:
            pass
        
        if success_count > 0:
            # Mensaje de éxito
            msg = await update.message.reply_text(get_weighted_random_message())
            if user_id not in user_sessions:
                user_sessions[user_id] = {"messages": [], "task": None}
            user_sessions[user_id]["messages"].append(msg.message_id)
            
            # Programar auto-eliminación
            if AUTO_DELETE_MINUTES > 0:
                asyncio.create_task(auto_delete(context, user_id))
            
            logger.info(f"✅ {success_count}/{len(ids)} justificaciones enviadas exitosamente")
        else:
            error_msg = "❌ No se pudieron obtener las justificaciones.\n\n"
            error_msg += "Posibles causas:\n"
            error_msg += f"• Los IDs {failed_ids} no existen en el canal\n"
            error_msg += "• El bot no tiene acceso al canal de justificaciones\n"
            error_msg += "• Hay un problema temporal\n\n"
            error_msg += "Por favor, contacta al administrador si el problema persiste."
            await update.message.reply_text(error_msg)
            logger.error(f"❌ Fallo completo al enviar justificaciones {ids}")
        
        return
    
    # Comando /start normal (sin deep link)
    welcome_msg = (
        "🩺 **Bot de Justificaciones Médicas**\n\n"
        "Este bot entrega justificaciones protegidas de casos clínicos.\n\n"
        "**Cómo usar:**\n"
        "• Haz clic en los enlaces de justificación que aparecen en el canal\n"
        "• Las justificaciones se auto-eliminarán después de 10 minutos\n\n"
        "**Comandos:**\n"
        "• /just <id> - Obtener justificación por ID\n"
        "• /help - Ver esta ayuda\n"
    )
    
    if user_id in ADMIN_IDS:
        welcome_msg += "\n**Admin:**\n• /status - Ver estado del bot"
    
    await update.message.reply_text(welcome_msg, parse_mode="Markdown")

async def cmd_just(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando directo para obtener justificación por ID."""
    if not update.message or not context.args:
        await update.message.reply_text("Uso: /just <id>\nEjemplo: /just 123")
        return
    
    try:
        just_id = int(context.args[0])
    except:
        await update.message.reply_text("❌ ID inválido. Usa números enteros.")
        return
    
    user_id = update.message.from_user.id
    logger.info(f"📝 Usuario {user_id} solicitó justificación {just_id} via comando")
    
    await clean_messages(context, user_id)
    
    proc = await update.message.reply_text("🔄 Obteniendo justificación...")
    
    if await send_justification(context, user_id, just_id):
        try:
            await proc.delete()
        except:
            pass
        msg = await update.message.reply_text(get_weighted_random_message())
        if user_id not in user_sessions:
            user_sessions[user_id] = {"messages": [], "task": None}
        user_sessions[user_id]["messages"].append(msg.message_id)
        
        if AUTO_DELETE_MINUTES > 0:
            asyncio.create_task(auto_delete(context, user_id))
    else:
        await proc.edit_text(
            f"❌ No se pudo obtener la justificación {just_id}.\n"
            "Verifica que el ID sea correcto."
        )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el estado del bot (solo admins)."""
    if not update.message:
        return
    
    if update.message.from_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ No autorizado")
        return
    
    active_sessions = len(user_sessions)
    total_messages = sum(len(s.get("messages", [])) for s in user_sessions.values())
    
    status_text = (
        f"📊 **Estado del Bot**\n\n"
        f"• Sesiones activas: {active_sessions}\n"
        f"• Mensajes enviados: {total_messages}\n"
        f"• Canal justificaciones: `{JUSTIFICATIONS_CHAT_ID}`\n"
        f"• Auto-eliminación: {AUTO_DELETE_MINUTES} min\n"
    )
    
    # Verificar acceso al canal
    try:
        chat = await context.bot.get_chat(JUSTIFICATIONS_CHAT_ID)
        status_text += f"• Canal: ✅ {chat.title}\n"
    except Exception as e:
        status_text += f"• Canal: ❌ Sin acceso\n"
        logger.error(f"No se puede acceder al canal: {e}")
    
    await update.message.reply_text(status_text, parse_mode="Markdown")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra la ayuda."""
    await cmd_start(update, context)

def main():
    """Función principal del bot."""
    logger.info("🚀 Iniciando bot de justificaciones...")
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("just", cmd_just))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    
    # Handler para mensajes que empiecen con /start (para capturar deep links)
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r"^/start"), 
        cmd_start
    ))
    
    logger.info(f"✅ Bot de justificaciones iniciado correctamente")
    logger.info(f"📁 Leyendo justificaciones del canal: {JUSTIFICATIONS_CHAT_ID}")
    
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
