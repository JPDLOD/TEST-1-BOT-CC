#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Bot separado SOLO para entregar justificaciones
NO interfiere con el bot principal
"""

import os
import logging
import asyncio
import random
from typing import Dict, List
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import TelegramError

# ========= CONFIGURACIÓN =========
BOT_TOKEN = "8256996324:AAH2cD9VBEK7iQrlmiwCi11zwOzAJgyg1d4"  # @JUST_CC_bot
JUSTIFICATIONS_CHAT_ID = -1003058530208  # Canal de justificaciones
AUTO_DELETE_MINUTES = 10  # Tiempo antes de borrar
MAX_CONCURRENT_REQUESTS = 10  # Máximo de solicitudes concurrentes por usuario

# ========= LOGGING =========
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========= CACHE Y CONTROL =========
user_sessions: Dict[int, Dict] = {}  # {user_id: {"messages": [], "task": asyncio.Task, "semaphore": asyncio.Semaphore}}

# ========= MENSAJES ALEATORIOS =========
SUCCESS_MESSAGES = [
    "📚 ¡Justificación lista! Revisa con calma.",
    "✨ Material de estudio enviado.",
    "🎯 ¡Justificación disponible!",
    "📖 Contenido académico listo para revisar.",
    "💊 Tu dosis de conocimiento ha sido enviada.",
    "🩺 Diagnóstico: Necesitas esta justificación. Tratamiento: Leerla.",
    "📋 Historia clínica del caso: Completa.",
    "🔬 Resultados del laboratorio de conocimiento listos.",
    "💉 Inyección de sabiduría administrada con éxito.",
    "🏥 Interconsulta con la justificación: Aprobada.",
    "🚑 Justificación de emergencia despachada.",
    "👨‍⚕️ El Dr. Bot te envió la justificación STAT!",
    "🫀 Tu nodo SA está enviando impulsos de felicidad.",
    "🧬 Mutación detectada en el gen del conocimiento: +100 IQ.",
    "💊 Farmacocinética: Absorción inmediata.",
    "🦠 Gram positivo para el aprendizaje.",
    "🩸 Tu Hb subió 2 puntos solo de ver esta justificación.",
    "🧠 Neuronas activadas exitosamente.",
    "📚 Como el café del hospital: Necesario aunque no sea el mejor.",
    "🎓 Un paso más cerca de la residencia.",
]

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
                user_sessions[user_id] = {"messages": [], "task": None, "semaphore": asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)}
            
            user_sessions[user_id]["messages"].append(sent.message_id)
            logger.info(f"✅ Justificación {justification_id} enviada a usuario {user_id}")
            return True
            
    except TelegramError as e:
        logger.error(f"❌ Error enviando justificación {justification_id}: {e}")
    
    return False

async def handle_justification_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja solicitudes de justificación con mejor concurrencia."""
    if not update.message or not update.message.text:
        return
    
    text = update.message.text.strip()
    user_id = update.message.from_user.id
    
    # Extraer ID de justificación
    if text.startswith("/start just_"):
        try:
            # Extraer ID único (por ahora solo soportamos uno por solicitud)
            id_part = text.replace("/start just_", "").split("_")[0]
            justification_id = int(id_part)
        except:
            await update.message.reply_text("❌ Enlace inválido.")
            return
    elif text.startswith("/just"):
        # Comando directo /just 123
        try:
            justification_id = int(text.split()[1])
        except:
            await update.message.reply_text("❌ Usa: /just <número>")
            return
    else:
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
        # Enviar mensaje de procesando
        processing = await update.message.reply_text("🔄 Obteniendo justificación...")
        
        try:
            # Enviar justificación
            success = await send_justification(context, user_id, justification_id)
            
            # Borrar mensaje de procesando
            await processing.delete()
            
            if success:
                # Enviar mensaje aleatorio de éxito
                success_msg = await update.message.reply_text(
                    random.choice(SUCCESS_MESSAGES),
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

async strongly schedule_deletion(context: ContextTypes.DEFAULT_TYPE, user_id: int):
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
            for msg_id in user_session["messages"]:
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
    """Estado del bot (admin)."""
    user_id = update.message.from_user.id
    
    # Solo para admins (puedes agregar IDs de admin aquí)
    ADMIN_IDS = [123456789]  # Agrega tu ID aquí
    
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ No autorizado")
        return
    
    active_sessions = len(user_sessions)
    total_messages = sum(len(s["messages"]) for s in user_sessions.values())
    
    status_text = (
        f"📊 *Estado del Bot*\n\n"
        f"• Sesiones activas: {active_sessions}\n"
        f"• Mensajes pendientes: {total_messages}\n"
        f"• Auto-eliminación: {AUTO_DELETE_MINUTES} min\n"
        f"• Canal fuente: `{JUSTIFICATIONS_CHAT_ID}`"
    )
    
    await update.message.reply_text(status_text, parse_mode="Markdown")

# ========= MAIN =========
def main():
    """Función principal del bot."""
    # Crear aplicación
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Agregar handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("just", cmd_just))
    app.add_handler(CommandHandler("ayuda", cmd_help))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    
    # Handler para deep links
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r"^/start just_\d+"),
        handle_justification_request
    ))
    
    logger.info("🚀 Bot de Justificaciones iniciado!")
    logger.info(f"📚 Canal fuente: {JUSTIFICATIONS_CHAT_ID}")
    logger.info(f"⏰ Auto-eliminación: {AUTO_DELETE_MINUTES} minutos")
    
    # Iniciar bot
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()