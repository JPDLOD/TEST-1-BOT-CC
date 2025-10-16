# -*- coding: utf-8 -*-
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, filters, ContextTypes

from config import BOT_TOKEN, DB_FILE, JUSTIFICATIONS_CHAT_ID
from database import init_db
from cases_handler import cmd_random_cases, handle_answer, detect_case_from_message
from justifications_handler import handle_justification_request, handle_next_case, detect_justification_from_message
from admin_panel import cmd_admin, cmd_set_limit, cmd_set_sub, handle_admin_callback, is_admin
from channels_handler import handle_send_announcement, process_announcement

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

init_db(DB_FILE)
logger.info("✅ Base de datos inicializada")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 ¡Bienvenido a Casos Clínicos Bot!\n\n"
        "📚 Usa /random_cases para obtener 5 casos aleatorios\n"
        "📊 Responde y aprende con estadísticas en tiempo real\n\n"
        "¡Buena suerte! 🔥"
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Comandos disponibles:\n\n"
        "/random_cases - 5 casos aleatorios\n"
        "/start - Iniciar bot\n"
        "/help - Ver ayuda"
    )

async def handle_justifications_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg or msg.chat_id != JUSTIFICATIONS_CHAT_ID:
        return
    
    text = msg.text or msg.caption or ""
    
    await detect_case_from_message(msg.message_id, text)
    await detect_justification_from_message(msg.message_id, text)

async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    
    if "pending_announcement" in context.user_data:
        if is_admin(update.effective_user.id):
            await process_announcement(update, context)
        return

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if data.startswith("ans_"):
        await handle_answer(update, context)
    elif data.startswith("just_"):
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
    
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("random_cases", cmd_random_cases))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("set_limit", cmd_set_limit))
    app.add_handler(CommandHandler("set_sub", cmd_set_sub))
    
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_justifications_channel))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT, handle_private_message))
    
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    app.add_error_handler(on_error)
    
    logger.info("🚀 Bot iniciado")
    app.run_polling(allowed_updates=["message", "channel_post", "callback_query"])

if __name__ == "__main__":
    main()
