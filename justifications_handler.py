# -*- coding: utf-8 -*-
import logging
import re
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config import JUSTIFICATIONS_CHAT_ID
from database import get_justifications_for_case, increment_daily_progress

logger = logging.getLogger(__name__)

JUST_PATTERN = re.compile(r'###JUST[_\s]*(\d{3,})', re.IGNORECASE)
JUST_CLEANUP_PATTERN = re.compile(r'###JUST[_\s]*\d{3,}', re.IGNORECASE)

async def detect_justification_from_message(message_id: int, text: str):
    just_match = JUST_PATTERN.search(text)
    if not just_match:
        return
    
    case_id = f"#{just_match.group(1)}"
    
    from database import save_justification
    save_justification(case_id, message_id)
    
    logger.info(f"Justificación detectada: {case_id} → {message_id}")

async def handle_justification_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if not data.startswith("just_"):
        return
    
    case_id = data.replace("just_", "")
    user_id = query.from_user.id
    
    justification_ids = get_justifications_for_case(case_id)
    
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
            msg = await context.bot.forward_message(
                chat_id=user_id,
                from_chat_id=JUSTIFICATIONS_CHAT_ID,
                message_id=just_id
            )
            
            text = msg.text or msg.caption or ""
            clean_text = JUST_CLEANUP_PATTERN.sub('', text).strip()
            
            photo_id = msg.photo[-1].file_id if msg.photo else None
            doc_id = msg.document.file_id if msg.document else None
            
            try:
                await context.bot.delete_message(user_id, msg.message_id)
            except:
                pass
            
            if photo_id:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=photo_id,
                    caption=clean_text if clean_text else None,
                    protect_content=True
                )
            elif doc_id:
                await context.bot.send_document(
                    chat_id=user_id,
                    document=doc_id,
                    caption=clean_text if clean_text else None,
                    protect_content=True
                )
            elif clean_text:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=clean_text,
                    protect_content=True
                )
            
            await asyncio.sleep(0.3)
        except TelegramError as e:
            logger.error(f"Error enviando justificación {just_id}: {e}")
    
    from cases_handler import user_sessions
    session = user_sessions.get(user_id)
    
    if session:
        session["current_index"] += 1
        increment_daily_progress(user_id)
        
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
