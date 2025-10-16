# -*- coding: utf-8 -*-
import logging
import random
import re
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config import DB_FILE, JUSTIFICATIONS_CHAT_ID
from database import get_justifications_for_case, increment_daily_progress

logger = logging.getLogger(__name__)

JUST_PATTERN = re.compile(r'###JUST_(\d{3,})', re.IGNORECASE)

async def detect_justification_from_message(message_id: int, text: str):
    just_match = JUST_PATTERN.search(text)
    if not just_match:
        return
    
    case_id = f"#{just_match.group(1)}"
    
    from database import save_justification
    save_justification(DB_FILE, case_id, message_id)
    
    logger.info(f"Justificación detectada: {case_id} → {message_id}")

async def handle_justification_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if not data.startswith("just_"):
        return
    
    case_id = data.replace("just_", "")
    user_id = query.from_user.id
    
    justification_ids = get_justifications_for_case(DB_FILE, case_id)
    
    if not justification_ids:
        await query.edit_message_text("❌ Justificación no disponible")
        return
    
    try:
        from justification_messages import get_weighted_random_message
        intro_text = get_weighted_random_message()
    except:
        intro_text = "📚 Justificación enviada"
    
    await context.bot.send_message(user_id, intro_text)
    
    for just_id in justification_ids:
        try:
            copied = await context.bot.copy_message(
                chat_id=user_id,
                from_chat_id=JUSTIFICATIONS_CHAT_ID,
                message_id=just_id,
                protect_content=True
            )
            
            text = copied.text or copied.caption or ""
            clean_text = JUST_PATTERN.sub('', text).strip()
            
            if clean_text != text and copied.text:
                await context.bot.edit_message_text(
                    chat_id=user_id,
                    message_id=copied.message_id,
                    text=clean_text
                )
            elif clean_text != text and copied.caption:
                await context.bot.edit_message_caption(
                    chat_id=user_id,
                    message_id=copied.message_id,
                    caption=clean_text
                )
            
            await asyncio.sleep(0.3)
        except TelegramError as e:
            logger.error(f"Error copiando justificación {just_id}: {e}")
    
    from cases_handler import user_sessions
    session = user_sessions.get(user_id)
    
    if session:
        session["current_index"] += 1
        increment_daily_progress(DB_FILE, user_id)
        
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Siguiente caso ➡️", callback_data="next_case")
        ]])
        
        await context.bot.send_message(
            user_id,
            "✅ Justificación entregada",
            reply_markup=keyboard
        )
    else:
        await context.bot.send_message(user_id, "✅ Justificación entregada")

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
