#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Bot de justificaciones - Optimizado"""

import os
import logging
import asyncio
from typing import Dict, List

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import TelegramError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# TODO desde variables de entorno
BOT_TOKEN = os.environ["JUST_BOT_TOKEN"]
JUSTIFICATIONS_CHAT_ID = int(os.environ["JUST_CHAT_ID"])
AUTO_DELETE_MINUTES = int(os.environ.get("JUST_AUTO_DELETE_MINUTES", "10"))
ADMIN_IDS = [int(x) for x in os.environ.get("JUST_ADMIN_IDS", "").split(",") if x]

user_sessions: Dict = {}

try:
    from justification_messages import get_weighted_random_message
except ImportError:
    import random
    def get_weighted_random_message():
        return random.choice([
            "📚 ¡Justificación lista!",
            "✨ Material enviado.",
            "🎯 ¡Disponible!",
            "📖 Contenido listo."
        ])

async def send_justification(ctx: ContextTypes.DEFAULT_TYPE, user_id: int, just_id: int) -> bool:
    try:
        sent = await ctx.bot.copy_message(
            chat_id=user_id,
            from_chat_id=JUSTIFICATIONS_CHAT_ID,
            message_id=just_id,
            protect_content=True
        )
        if sent:
            if user_id not in user_sessions:
                user_sessions[user_id] = {"messages": [], "task": None}
            user_sessions[user_id]["messages"].append(sent.message_id)
            return True
    except Exception as e:
        logger.error(f"Error: {e}")
    return False

async def clean_messages(ctx: ContextTypes.DEFAULT_TYPE, user_id: int):
    if user_id in user_sessions:
        for msg_id in user_sessions[user_id].get("messages", []):
            try:
                await ctx.bot.delete_message(chat_id=user_id, message_id=msg_id)
            except:
                pass
        user_sessions[user_id]["messages"] = []

async def auto_delete(ctx: ContextTypes.DEFAULT_TYPE, user_id: int):
    await asyncio.sleep(AUTO_DELETE_MINUTES * 60)
    await clean_messages(ctx, user_id)
    if user_id in user_sessions:
        del user_sessions[user_id]

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    
    user_id = update.message.from_user.id
    text = update.message.text
    
    # Deep link
    if " just_" in text:
        ids = []
        try:
            parts = text.split("just_")[1].split("_")
            ids = [int(p) for p in parts if p.isdigit()]
        except:
            pass
        
        if ids:
            await clean_messages(context, user_id)
            proc = await update.message.reply_text("🔄 Obteniendo...")
            
            ok = False
            for jid in ids:
                if await send_justification(context, user_id, jid):
                    ok = True
                    await asyncio.sleep(0.3)
            
            try:
                await proc.delete()
            except:
                pass
            
            if ok:
                msg = await update.message.reply_text(get_weighted_random_message())
                if user_id not in user_sessions:
                    user_sessions[user_id] = {"messages": [], "task": None}
                user_sessions[user_id]["messages"].append(msg.message_id)
                
                if AUTO_DELETE_MINUTES > 0:
                    asyncio.create_task(auto_delete(context, user_id))
            else:
                await update.message.reply_text("❌ Error")
            return
    
    # Welcome
    await update.message.reply_text(
        "🩺 Bot de Justificaciones\n\n"
        "/just <id> - Obtener\n"
        "/status - Estado"
    )

async def cmd_just(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not context.args:
        return
    
    try:
        just_id = int(context.args[0])
    except:
        await update.message.reply_text("❌ ID inválido")
        return
    
    user_id = update.message.from_user.id
    await clean_messages(context, user_id)
    
    proc = await update.message.reply_text("🔄 Obteniendo...")
    
    if await send_justification(context, user_id, just_id):
        try:
            await proc.delete()
        except:
            pass
        msg = await update.message.reply_text(get_weighted_random_message())
        if user_id not in user_sessions:
            user_sessions[user_id] = {"messages": [], "task": None}
        user_sessions[user_id]["messages"].append(msg.message_id)
        
        if AUTO_DELETE_MINUTES > 0:
            asyncio.create_task(auto_delete(context, user_id))
    else:
        await proc.edit_text("❌ Error")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    
    if update.message.from_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ No autorizado")
        return
    
    await update.message.reply_text(
        f"📊 Estado\n"
        f"Sesiones: {len(user_sessions)}\n"
        f"Auto-delete: {AUTO_DELETE_MINUTES} min"
    )

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("just", cmd_just))
    app.add_handler(CommandHandler("status", cmd_status))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
