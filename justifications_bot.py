#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Bot separado SOLO para entregar justificaciones
Ejecutar independiente del bot principal
NO interfiere con el bot principal
"""

import os
import logging
import asyncio
from typing import Dict, List
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import TelegramError

# ========= CONFIGURACIÓN =========
import os

# Token del bot (desde variable de entorno o hardcoded)
BOT_TOKEN = os.environ.get("JUSTIFICATIONS_BOT_TOKEN", "8256996324:AAH2cD9VBEK7iQrlmiwCi11zwOzAJgyg1d4")  # @JUST_CC_bot

# Canal de justificaciones (cambiar a tu canal privado si quieres)
JUSTIFICATIONS_CHAT_ID = int(os.environ.get("JUSTIFICATIONS_CHAT_ID", "-1003058530208"))

# Configuraciones
AUTO_DELETE_MINUTES = int(os.environ.get("AUTO_DELETE_MINUTES", "10"))  # Tiempo antes de borrar
MAX_CONCURRENT_REQUESTS = int(os.environ.get("MAX_CONCURRENT_REQUESTS", "10"))  # Máximo de solicitudes concurrentes

# Admins del bot (agrega tu ID aquí)
ADMIN_IDS = [int(id) for id in os.environ.get("ADMIN_IDS", "123456789").split(",")]  # IDs separados por comas

# ========= LOGGING =========
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========= CACHE Y CONTROL =========
user_sessions: Dict[int, Dict] = {}  # {user_id: {"messages": [], "task": asyncio.Task, "semaphore": asyncio.Semaphore}}

# ========= IMPORTAR MENSAJES DESDE EL ARCHIVO SEPARADO =========
try:
    from justification_messages import get_random_message, get_weighted_random_message
    logger.info("✅ Mensajes creativos cargados desde justification_messages.py")
except ImportError:
    logger.warning("⚠️ No se pudo cargar justification_messages.py, usando mensajes por defecto")
    import random
    
    # Mensajes fallback mínimos si no existe el archivo
    FALLBACK_MESSAGES = [
        "📚 ¡Justificación lista! Revisa con calma.",
        "✨ Material de estudio enviado.",
        "🎯 ¡Justificación disponible!",
        "📖 Contenido académico listo para revisar.",
        "💊 Tu dosis de conocimiento ha sido enviada.",
    ]
    
    def get_random_message():
        return random.choice(FALLBACK_MESSAGES)
    
    def get_weighted_random_message():
        return random.choice(FALLBACK_MESSAGES)

# ========= FUNCIONES PRINCIPALES =========

async def send_justification(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    justification_id: int
) -> bool:
    """Envía una justificación individual con protección."""
    try:
        # Copiar mensaje protegido
        sent = await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=JUSTIFICATIONS_CHAT_ID,
            message_id=justification_id,
            protect_content=True
        )
        
        if sent:
            # Agregar a la sesión del usuario
            if user_id not in user_sessions:
                user_sessions[user_id] = {
                    "messages": [],
                    "task": None,
                    "semaphore": asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
                }
            
            user_sessions[user_id]["messages"].append(sent.message_id)
            logger.info(f"✅ Justificación {justification_id} enviada a usuario {user_id}")
            return True
            
    except TelegramError as e:
        logger.error(f"❌ Error enviando justificación {justification_id}: {e}")
    
    return False

async def send_multiple_justifications(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    justification_ids: List[int]
) -> bool:
    """Envía múltiples justificaciones."""
    success_count = 0
    
    for just_id in justification_ids:
        if await send_justification(context, user_id, just_id):
            success_count += 1
            # Pequeña pausa entre mensajes múltiples
            if len(justification_ids) > 1:
                await asyncio.sleep(0.3)
    
    return success_count > 0

async def handle_justification_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja solicitudes de justificación con mejor concurrencia."""
    if not update.message or not update.message.text:
        return
    
    text = update.message.text.strip()
    user_id = update.message.from_user.id
    
    # Extraer IDs de justificación
    justification_ids = []
    
    if text.startswith("/start just_"):
        try:
            # Extraer múltiples IDs formato: just_123_456_789
            id_string = text.replace("/start just_", "")
            for id_part in id_string.split("_"):
                if id_part.isdigit():
                    justification_ids.append(int(id_part))
        except:
            pass
    
    elif text.startswith("/just"):
        # Comando directo /just 123 o /just 123,456,789
        try:
            args = text.replace("/just", "").strip()
            if "," in args:
                # Múltiples IDs separados por comas
                for id_str in args.split(","):
                    if id_str.strip().isdigit():
                        justification_ids.append(int(id_str.strip()))
            else:
                # Un solo ID
                justification_ids.append(int(args))
        except:
            pass
    else:
        return
    
    if not justification_ids:
        await update.message.reply_text("❌ Formato inválido. Usa: /just <número> o /just <num1,num2,num3>")
        return
    
    # Crear semáforo para el usuario si no existe
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "messages": [],
            "task": None,
            "semaphore": asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        }
    
    user_session = user_sessions[user_id]
    
    # Usar semáforo para controlar concurrencia
    async with user_session["semaphore"]:
        # Limpiar mensajes previos si hay
        await clean_previous_messages(context, user_id)
        
        # Enviar mensaje de procesando
        if len(justification_ids) == 1:
            processing_text = "🔄 Obteniendo justificación..."
        else:
            processing_text = f"🔄 Obteniendo {len(justification_ids)} justificaciones..."
        
        processing = await update.message.reply_text(processing_text)
        
        try:
            # Enviar justificaciones
            success = await send_multiple_justifications(context, user_id, justification_ids)
            
            # Borrar mensaje de procesando
            try:
                await processing.delete()
            except:
                pass
            
            if success:
                # Enviar mensaje aleatorio de éxito (USANDO LA FUNCIÓN IMPORTADA)
                success_msg = await update.message.reply_text(
                    get_weighted_random_message(),  # Usar función importada con peso
                    disable_notification=True
                )
                
                # Agregar mensaje de éxito a la lista para borrar después
                user_session["messages"].append(success_msg.message_id)
                
                # Programar eliminación
                if AUTO_DELETE_MINUTES > 0:
                    await schedule_deletion(context, user_id)
            else:
                await update.message.reply_text(
                    "❌ No se pudo obtener la justificación. Verifica el ID.",
                    disable_notification=True
                )
                
        except Exception as e:
            logger.error(f"Error procesando solicitud: {e}")
            try:
                await processing.delete()
            except:
                pass
            await update.message.reply_text("❌ Error inesperado. Intenta de nuevo.")

async def clean_previous_messages(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Limpia mensajes previos del usuario."""
    if user_id not in user_sessions:
        return
    
    user_session = user_sessions[user_id]
    
    # Cancelar tarea de eliminación anterior si existe
    if user_session.get("task"):
        user_session["task"].cancel()
    
    # Borrar mensajes previos
    for msg_id in user_session.get("messages", []):
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
        except:
            pass
    
    # Limpiar lista
    user_session["messages"] = []

async def schedule_deletion(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Programa la eliminación de mensajes después del tiempo configurado."""
    if user_id not in user_sessions:
        return
    
    user_session = user_sessions[user_id]
    
    # Cancelar tarea anterior si existe
    if user_session.get("task"):
        user_session["task"].cancel()
    
    async def delete_messages():
        try:
            await asyncio.sleep(AUTO_DELETE_MINUTES * 60)
            
            # Borrar todos los mensajes del usuario
            for msg_id in user_session.get("messages", []):
                try:
                    await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
                except:
                    pass
            
            # Limpiar sesión
            if user_id in user_sessions:
                del user_sessions[user_id]
            
            logger.info(f"🗑️ Mensajes eliminados para usuario {user_id}")
            
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error en eliminación automática: {e}")
    
    # Crear nueva tarea
    task = asyncio.create_task(delete_messages())
    user_session["task"] = task

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start general."""
    if context.args and context.args[0].startswith("just_"):
        # Es una justificación
        await handle_justification_request(update, context)
    else:
        # Mensaje de bienvenida
        await update.message.reply_text(
            "🩺 *Bot de Justificaciones Médicas*\n\n"
            "Este bot te entrega justificaciones protegidas de casos clínicos.\n\n"
            "📚 *Cómo funciona:*\n"
            "1. Recibe un enlace de justificación\n"
            "2. Haz click en el enlace\n"
            "3. Recibirás el material protegido\n"
            "4. Se auto-eliminará en 10 minutos\n\n"
            "💡 *Comandos:*\n"
            "• `/just <número>` - Obtener justificación directa\n"
            "• `/just <num1,num2,num3>` - Múltiples justificaciones\n"
            "• `/ayuda` - Ver esta información\n\n"
            "_Bot exclusivo para estudiantes de medicina_",
            parse_mode="Markdown"
        )

async def cmd_just(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando directo /just <id>."""
    await handle_justification_request(update, context)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando de ayuda."""
    await cmd_start(update, context)

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Estado del bot (solo admins)."""
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ No autorizado")
        return
    
    active_sessions = len(user_sessions)
    total_messages = sum(len(s["messages"]) for s in user_sessions.values())
    
    status_text = (
        f"📊 *Estado del Bot de Justificaciones*\n\n"
        f"• Sesiones activas: {active_sessions}\n"
        f"• Mensajes pendientes: {total_messages}\n"
        f"• Auto-eliminación: {AUTO_DELETE_MINUTES} min\n"
        f"• Canal fuente: `{JUSTIFICATIONS_CHAT_ID}`\n"
        f"• Max concurrencia: {MAX_CONCURRENT_REQUESTS}\n"
    )
    
    if active_sessions > 0:
        status_text += "\n*Usuarios activos:*\n"
        for uid, session in list(user_sessions.items())[:5]:
            msg_count = len(session.get("messages", []))
            status_text += f"• User {uid}: {msg_count} msgs\n"
        
        if active_sessions > 5:
            status_text += f"... y {active_sessions - 5} más\n"
    
    await update.message.reply_text(status_text, parse_mode="Markdown")

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Limpia mensajes del usuario actual."""
    user_id = update.message.from_user.id
    
    if user_id in user_sessions:
        await clean_previous_messages(context, user_id)
        del user_sessions[user_id]
        await update.message.reply_text("✅ Mensajes anteriores eliminados")
    else:
        await update.message.reply_text("No tienes mensajes pendientes")

async def cmd_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Información sobre el bot."""
    info_text = (
        "*🩺 Bot de Justificaciones Médicas*\n\n"
        "Bot dedicado exclusivamente para entregar justificaciones "
        "protegidas de casos clínicos.\n\n"
        "*Características:*\n"
        "• Contenido protegido (no se puede reenviar)\n"
        "• Auto-eliminación después de 10 minutos\n"
        "• Soporte para múltiples justificaciones\n"
        "• Sin interferencia con otros bots\n"
        "• Siempre disponible\n\n"
        "*Desarrollado para:* Estudiantes de medicina\n"
        "*Versión:* 2.0 (Bot separado)\n"
    )
    
    await update.message.reply_text(info_text, parse_mode="Markdown")

# ========= ERROR HANDLER =========
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja errores del bot."""
    logger.error("Exception while handling an update:", exc_info=context.error)

# ========= MAIN =========
def main():
    """Función principal del bot."""
    # Crear aplicación
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Agregar handlers de comandos
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("just", cmd_just))
    app.add_handler(CommandHandler("ayuda", cmd_help))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("info", cmd_info))
    
    # Handler para deep links de justificaciones
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r"^/start just_[\d_]+"),
        handle_justification_request
    ))
    
    # Error handler
    app.add_error_handler(error_handler)
    
    logger.info("="*50)
    logger.info("🚀 Bot de Justificaciones iniciado!")
    logger.info(f"📚 Canal fuente: {JUSTIFICATIONS_CHAT_ID}")
    logger.info(f"⏰ Auto-eliminación: {AUTO_DELETE_MINUTES} minutos")
    logger.info(f"🔄 Max concurrencia: {MAX_CONCURRENT_REQUESTS}")
    logger.info("="*50)
    
    # Iniciar bot
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
