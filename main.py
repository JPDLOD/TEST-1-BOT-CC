# -*- coding: utf-8 -*-
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, filters, ContextTypes

from config import BOT_TOKEN, CASES_UPLOADER_ID
from database import init_db, count_cases
from cases_handler import cmd_random_cases, handle_answer
from justifications_handler import handle_justification_request, handle_next_case
from channels_handler import handle_uploader_message, cmd_refresh_catalog, cmd_replace_caso
from admin_panel import cmd_admin, cmd_set_limit, cmd_set_sub, handle_admin_callback, is_admin

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

init_db()

total_cases = count_cases()
if total_cases == 0:
    logger.warning("⚠️ No hay casos en la base de datos")
    logger.info(f"📤 ID del uploader autorizado: {CASES_UPLOADER_ID}")
    logger.info("💡 Envía casos al bot con formato: ###CASE_0001 #A#")
else:
    logger.info(f"📚 {total_cases} casos disponibles")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Si es el uploader, mostrar info especial
    if user_id == CASES_UPLOADER_ID:
        await update.message.reply_text(
            "🔧 **Modo Uploader**

"
            "Envía casos con formato:
"
            "`###CASE_0001 #A#` + archivo/texto

"
            "Envía justificaciones con:
"
            "`###JUST_0001` + archivo/texto",
            parse_mode="Markdown"
        )
        return
    
    await update.message.reply_text(
        "👋 ¡Bienvenido a Casos Clínicos Bot!

"
        "🎯 **Comandos disponibles:**
"
        "• /random_cases - 5 casos clínicos aleatorios
"
        "• /help - Ver ayuda completa

"
        "¡Buena suerte! 🔥",
        parse_mode="Markdown"
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **Comandos disponibles:**

"
        "📚 **Para usuarios:**
"
        "• /start - Iniciar bot
"
        "• /random_cases - 5 casos aleatorios
"
        "• /help - Ver esta ayuda

"
        "⏰ **Límite:** 5 casos/día
"
        "🔄 **Reset:** 12:00 AM diario",
        parse_mode="Markdown"
    )

async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    
    user_id = update.effective_user.id
    
    # PRIORIDAD 1: Si es el uploader, procesar casos/justificaciones
    if user_id == CASES_UPLOADER_ID:
        await handle_uploader_message(update, context)
        return
    
    # PRIORIDAD 2: Si es respuesta A/B/C/D
    text = update.message.text.strip().upper()
    if text in ["A", "B", "C", "D"]:
        await handle_answer(update, context)
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

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Error", exc_info=context.error)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("random_cases", cmd_random_cases))
    
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("set_limit", cmd_set_limit))
    app.add_handler(CommandHandler("set_sub", cmd_set_sub))
    app.add_handler(CommandHandler("refresh_catalog", cmd_refresh_catalog))
    app.add_handler(CommandHandler("replace_caso", cmd_replace_caso))
    
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT, handle_private_message))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.AUDIO | filters.VOICE), handle_uploader_message))
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    app.add_error_handler(on_error)
    
    logger.info("🚀 Bot iniciado")
    app.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    main()
