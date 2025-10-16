# -*- coding: utf-8 -*-
import logging
import asyncio
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config import FREE_CHANNEL_ID, SUBS_CHANNEL_ID, PAUSE, DB_FILE
from database import get_all_users, get_subscribers
from admin_panel import is_admin, parse_message_with_buttons

logger = logging.getLogger(__name__)

async def handle_send_announcement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if not is_admin(query.from_user.id):
        return
    
    data = query.data
    
    await query.edit_message_text(
        "📝 Envía el mensaje que quieres publicar.\n\n"
        "Puedes usar:\n"
        "@@@ Texto | url - Para botones\n"
        "%%% Texto | url - Para links inline"
    )
    
    context.user_data["pending_announcement"] = data

async def process_announcement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "pending_announcement" not in context.user_data:
        return
    
    destination = context.user_data["pending_announcement"]
    del context.user_data["pending_announcement"]
    
    message = update.message
    text = message.text or message.caption or ""
    
    clean_text, keyboard = parse_message_with_buttons(text)
    
    if destination == "send_free":
        try:
            if message.photo:
                await context.bot.send_photo(
                    FREE_CHANNEL_ID,
                    message.photo[-1].file_id,
                    caption=clean_text,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            else:
                await context.bot.send_message(
                    FREE_CHANNEL_ID,
                    clean_text,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            await message.reply_text("✅ Enviado a Canal FREE")
        except TelegramError as e:
            await message.reply_text(f"❌ Error: {e}")
    
    elif destination == "send_subs":
        try:
            if message.photo:
                await context.bot.send_photo(
                    SUBS_CHANNEL_ID,
                    message.photo[-1].file_id,
                    caption=clean_text,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            else:
                await context.bot.send_message(
                    SUBS_CHANNEL_ID,
                    clean_text,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            await message.reply_text("✅ Enviado a Canal SUBS")
        except TelegramError as e:
            await message.reply_text(f"❌ Error: {e}")
    
    elif destination == "send_bot_free":
        users = get_all_users(DB_FILE)
        sent = 0
        failed = 0
        
        status_msg = await message.reply_text(f"📤 Enviando a {len(users)} usuarios...")
        
        for user_id in users:
            try:
                if message.photo:
                    await context.bot.send_photo(
                        user_id,
                        message.photo[-1].file_id,
                        caption=clean_text,
                        reply_markup=keyboard,
                        parse_mode="Markdown"
                    )
                else:
                    await context.bot.send_message(
                        user_id,
                        clean_text,
                        reply_markup=keyboard,
                        parse_mode="Markdown"
                    )
                sent += 1
                await asyncio.sleep(PAUSE)
            except TelegramError:
                failed += 1
        
        await status_msg.edit_text(f"✅ Enviado: {sent}\n❌ Fallidos: {failed}")
    
    elif destination == "send_bot_subs":
        users = get_subscribers(DB_FILE)
        sent = 0
        failed = 0
        
        status_msg = await message.reply_text(f"📤 Enviando a {len(users)} subscriptores...")
        
        for user_id in users:
            try:
                if message.photo:
                    await context.bot.send_photo(
                        user_id,
                        message.photo[-1].file_id,
                        caption=clean_text,
                        reply_markup=keyboard,
                        parse_mode="Markdown"
                    )
                else:
                    await context.bot.send_message(
                        user_id,
                        clean_text,
                        reply_markup=keyboard,
                        parse_mode="Markdown"
                    )
                sent += 1
                await asyncio.sleep(PAUSE)
            except TelegramError:
                failed += 1
        
        await status_msg.edit_text(f"✅ Enviado: {sent}\n❌ Fallidos: {failed}")
