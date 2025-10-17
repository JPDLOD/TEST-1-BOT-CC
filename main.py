# -*- coding: utf-8 -*-
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, filters, ContextTypes

from config import BOT_TOKEN, CASES_UPLOADER_ID
from database import init_db, count_cases
from cases_handler import cmd_random_cases, handle_answer
from justifications_handler import handle_justification_request, handle_next_case
from channels_handler import handle_uploader_message, cmd_refresh_catalog, cmd_replace_caso
from admin_panel import cmd_admin, cmd_set_limit, cmd_set_sub, handle_admin_callback

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
    
    if user_id == CASES_UPLOADER_ID:
        text = (
            "🔧 Modo Uploader\n\n"
            "Envía casos con formato:\n"
            "###CASE_0001 #A# + archivo/texto\n\n"
            "Envía justificaciones con:\n"
            "###JUST_0001 + archivo/texto"
        )
        await update.message.reply_text(text)
        return
    
    text = (
        "👋 Bienvenido a Casos Clínicos Bot\n\n"
        "🎯 Comandos disponibles\n"
        "• /random_cases - 5 casos clínicos aleatorios\n"
        "• /help - Ver ayuda completa\n\n"
        "Buena suerte 🔥"
    )
    await update.message.reply_text(text)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 Comandos disponibles\n\n"
        "📚 Para usuarios\n"
        "• /start - Iniciar bot\n"
        "• /random_cases - 5 casos aleatorios\n"
        "• /help - Ver esta ayuda\n\n"
        "⏰ Límite: 5 casos por día\n"
        "🔄 Reset: 12:00 AM diario"
    )
    await update.message.reply_text(text)

async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    
    user_id = update.effective_user.id
    
    if user_id == CASES_UPLOADER_ID:
        await handle_uploader_message(update, context)
        return
    
    text = (update.message.text or "").strip().upper()
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
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)

if __name__ == "__main__":
    main()
