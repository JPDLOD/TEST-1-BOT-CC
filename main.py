# -*- coding: utf-8 -*-
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, filters, ContextTypes

from config import BOT_TOKEN, JUSTIFICATIONS_CHAT_ID
from database import init_db, count_cases
from cases_handler import cmd_random_cases, handle_answer
from justifications_handler import handle_justification_request, handle_next_case
from channel_scanner import process_message_for_catalog, cmd_refresh_catalog
from admin_panel import cmd_admin, cmd_set_limit, cmd_set_sub, handle_admin_callback, is_admin
from channels_handler import handle_send_announcement, process_announcement

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

init_db()
logger.info("✅ Base de datos inicializada")

# Verificar si hay casos
total_cases = count_cases()
if total_cases == 0:
    logger.warning("⚠️ No hay casos en la base de datos")
    logger.info("💡 Los casos se detectan automáticamente al publicar en el canal")
    logger.info("💡 O usa /refresh_catalog (admin) para forzar actualización")
else:
    logger.info(f"📚 {total_cases} casos disponibles")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 ¡Bienvenido a Casos Clínicos Bot!\n\n"
        "🎯 **Comandos disponibles:**\n"
        "• /random_cases - 5 casos clínicos aleatorios\n"
        "• /help - Ver ayuda completa\n\n"
        "📊 **Cómo funciona:**\n"
        "1️⃣ Solicita casos con /random_cases\n"
        "2️⃣ Lee el caso y selecciona tu respuesta (A, B, C o D)\n"
        "3️⃣ Ve estadísticas en tiempo real\n"
        "4️⃣ Consulta la justificación después de responder\n\n"
        "¡Buena suerte! 🔥",
        parse_mode="Markdown"
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **Comandos disponibles:**\n\n"
        "📚 **Para usuarios:**\n"
        "• /start - Iniciar bot y ver bienvenida\n"
        "• /random_cases - Obtener 5 casos clínicos aleatorios\n"
        "• /help - Ver esta ayuda\n\n"
        "🎯 **Cómo usar:**\n"
        "1. Escribe /random_cases\n"
        "2. Lee el caso clínico\n"
        "3. Presiona el botón con tu respuesta (A, B, C o D)\n"
        "4. Ve las estadísticas de otros usuarios\n"
        "5. Presiona 'Ver justificación' para aprender\n"
        "6. Continúa con el siguiente caso\n\n"
        "⏰ **Límite diario:** 5 casos por día\n"
        "🔄 **Reset:** Todos los días a las 12:00 AM\n\n"
        "💡 **Formato de casos:**\n"
        "`###CASE_0000_ESPECIALIDAD_TEMA_0001 #C#`\n"
        "• 0000 = ID general\n"
        "• ESPECIALIDAD = Pediatría, Medicina, etc.\n"
        "• TEMA = Dengue, Neumonía, etc.\n"
        "• 0001 = # del tema\n"
        "• #C# = Respuesta correcta",
        parse_mode="Markdown"
    )

async def handle_justifications_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detecta casos y justificaciones del canal automáticamente"""
    msg = update.channel_post
    if not msg or msg.chat_id != JUSTIFICATIONS_CHAT_ID:
        return
    
    text = msg.text or msg.caption or ""
    
    # Procesar para guardar en catálogo
    await process_message_for_catalog(msg.message_id, text)

async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    
    # Handler para respuestas A, B, C, D
    text = update.message.text.strip().upper()
    if text in ["A", "B", "C", "D"]:
        await handle_answer(update, context)
        return
    
    # Handler para mensajes de admin
    if "pending_announcement" in context.user_data:
        if is_admin(update.effective_user.id):
            await process_announcement(update, context)
        return

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if data.startswith("just_"):
        await handle_justification_request(update, context)
    elif data == "next_case":
        await handle_next_case(update, context)
    elif data.startswith("admin_"):
        await handle_admin_callback(update, context)
    elif data.startswith("send_"):
        await handle_send_announcement(update, context)

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Error", exc_info=context.error)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Comandos básicos
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("random_cases", cmd_random_cases))
    
    # Comandos admin
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("set_limit", cmd_set_limit))
    app.add_handler(CommandHandler("set_sub", cmd_set_sub))
    app.add_handler(CommandHandler("refresh_catalog", cmd_refresh_catalog))
    
    # Handlers de canal y mensajes
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_justifications_channel))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT, handle_private_message))
    
    # Callbacks
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    # Errores
    app.add_error_handler(on_error)
    
    logger.info("🚀 Bot iniciado")
    app.run_polling(allowed_updates=["message", "channel_post", "callback_query"])

if __name__ == "__main__":
    main()
