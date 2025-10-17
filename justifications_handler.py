# -*- coding: utf-8 -*-
import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from database import get_justifications_for_case, increment_daily_progress

logger = logging.getLogger(__name__)

async def handle_justification_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if not data.startswith("just_"):
        return
    
    case_id = data.replace("just_", "")
    user_id = query.from_user.id
    
    justifications = get_justifications_for_case(case_id)
    
    if not justifications:
        await query.edit_message_text("❌ Justificación no disponible")
        return
    
    for file_id, file_type, caption in justifications:
        try:
            logger.info(f"📤 Enviando justificación ({file_type}) con file_id")
            
            if file_type == "document":
                await context.bot.send_document(chat_id=user_id, document=file_id, caption=caption if caption else None, protect_content=True)
            elif file_type == "photo":
                await context.bot.send_photo(chat_id=user_id, photo=file_id, caption=caption if caption else None, protect_content=True)
            elif file_type == "video":
                await context.bot.send_video(chat_id=user_id, video=file_id, caption=caption if caption else None, protect_content=True)
            elif file_type == "audio":
                await context.bot.send_audio(chat_id=user_id, audio=file_id, caption=caption if caption else None, protect_content=True)
            elif file_type == "text":
                await context.bot.send_message(chat_id=user_id, text=caption, protect_content=True)
            
            logger.info(f"✅ Justificación enviada exitosamente")
            await asyncio.sleep(0.3)
            
        except TelegramError as e:
            logger.error(f"❌ Error enviando justificación: {e}")
    
    try:
        from justification_messages import get_weighted_random_message
        motivational_text = get_weighted_random_message()
    except:
        motivational_text = "📚 Justificación enviada"
    
    from cases_handler import user_sessions
    session = user_sessions.get(user_id)
    
    if session:
        session["current_index"] += 1
        increment_daily_progress(user_id)
        
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Siguiente caso ➡️", callback_data="next_case")]])
        await context.bot.send_message(user_id, motivational_text, reply_markup=keyboard)
    else:
        await context.bot.send_message(user_id, motivational_text)

async def handle_next_case(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    from cases_handler import send_case
    await send_case(update, context, user_id)
    
    try:
        await query.message.delete()
    except:
        pass
