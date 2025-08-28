# -*- coding: utf-8 -*-
"""
Bot PRIVADO de justificaciones @clinicase_bot
Responde a /start jst_<message_id>
"""

import asyncio
import logging
from typing import Optional

from telegram import Update
from telegram.ext import (
    Application, ApplicationBuilder, ContextTypes,
    CommandHandler, MessageHandler, filters
)

from config import (
    JUST_BOT_TOKEN, JUSTIFICATIONS_CHAT_ID,
    JUST_ADMIN_IDS, JUST_AUTO_DELETE_MINUTES,
)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
log = logging.getLogger("justifications")

if not JUST_BOT_TOKEN:
    log.error("JUST_BOT_TOKEN no está definido")
if not JUSTIFICATIONS_CHAT_ID:
    log.error("JUSTIFICATIONS_CHAT_ID no está definido")

def _allowed(user_id: Optional[int]) -> bool:
    """Verifica si el usuario tiene acceso."""
    if not JUST_ADMIN_IDS:
        return True  # Si no hay restricción, acceso libre
    try:
        return int(user_id) in JUST_ADMIN_IDS
    except Exception:
        return False

async def _auto_delete(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    """Auto-elimina mensaje después del tiempo configurado."""
    if JUST_AUTO_DELETE_MINUTES and JUST_AUTO_DELETE_MINUTES > 0:
        try:
            await asyncio.sleep(JUST_AUTO_DELETE_MINUTES * 60)
            await ctx.bot.delete_message(chat_id=chat_id, message_id=message_id)
            log.info(f"Auto-eliminado mensaje {message_id}")
        except Exception:
            pass

def _parse_start_arg(args) -> Optional[int]:
    """Acepta: jst_123 o solo 123"""
    if not args:
        return None
    raw = str(args[0]).strip()
    if raw.lower().startswith("jst_"):
        raw = raw[4:]
    if raw.isdigit():
        return int(raw)
    return None

async def _copy_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE, mid: int):
    """Copia un mensaje del canal de justificaciones al usuario."""
    chat_id = update.effective_chat.id
    try:
        log.info(f"Copiando mensaje {mid} desde {JUSTIFICATIONS_CHAT_ID} a {chat_id}")
        msg = await context.bot.copy_message(
            chat_id=chat_id,
            from_chat_id=JUSTIFICATIONS_CHAT_ID,
            message_id=mid,
            protect_content=True,  # Proteger contenido
            disable_notification=True
        )
        log.info(f"✅ Justificación {mid} enviada a usuario {chat_id}")
        
        # Auto-delete si está configurado
        asyncio.create_task(_auto_delete(context, chat_id, msg.message_id))
        
        # Mensaje aleatorio de éxito
        try:
            from justification_messages import get_random_message
            success_msg = get_random_message()
        except ImportError:
            import random
            success_msg = random.choice([
                "📚 ¡Justificación lista! Revisa con calma.",
                "✨ Material de estudio enviado.",
                "🎯 ¡Justificación disponible!",
                "📖 Contenido académico listo."
            ])
        
        info = await context.bot.send_message(
            chat_id=chat_id,
            text=success_msg,
            disable_notification=True
        )
        asyncio.create_task(_auto_delete(context, chat_id, info.message_id))
        
    except Exception as e:
        log.warning(f"No pude copiar mensaje {mid}: {e}")
        info = await context.bot.send_message(
            chat_id=chat_id,
            text="❌ No encontré esa justificación. Verifica el ID."
        )
        asyncio.create_task(_auto_delete(context, chat_id, info.message_id))

# ========= HANDLERS =========
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para /start y /start jst_ID"""
    if not _allowed(update.effective_user.id):
        await update.message.reply_text("❌ No tienes acceso a este bot.")
        return
    
    mid = _parse_start_arg(context.args)
    if mid:
        await _copy_by_id(update, context, mid)
        return
    
    # Mensaje de bienvenida
    txt = (
        "🤖 **Bot de Justificaciones Médicas**\n\n"
        "Este bot entrega justificaciones protegidas de casos clínicos.\n\n"
        "Para recibir una justificación:\n"
        "1. Haz clic en el enlace desde el canal\n"
        "2. O envía el número ID directamente\n\n"
        "Ejemplo: `/start jst_123` o solo `123`"
    )
    info = await context.bot.send_message(update.effective_chat.id, txt, parse_mode="Markdown")
    asyncio.create_task(_auto_delete(context, update.effective_chat.id, info.message_id))

async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando de prueba."""
    if not _allowed(update.effective_user.id):
        return
    m = await context.bot.send_message(update.effective_chat.id, "🏓 pong")
    asyncio.create_task(_auto_delete(context, update.effective_chat.id, m.message_id))

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para texto normal (números de ID)."""
    if not _allowed(update.effective_user.id):
        return
    
    text = (update.message.text or "").strip()
    
    # Si empieza con jst_, procesarlo
    if text.lower().startswith("jst_"):
        text = text[4:].strip()
    
    # Si es un número, copiar la justificación
    if text.isdigit():
        await _copy_by_id(update, context, int(text))
        return
    
    # No es válido, mostrar ayuda
    info = await context.bot.send_message(
        update.effective_chat.id,
        "💡 Envíame el número ID de la justificación o usa `/start jst_ID`",
        parse_mode="Markdown",
    )
    asyncio.create_task(_auto_delete(context, update.effective_chat.id, info.message_id))

def build_just_app() -> Application:
    """Construye la aplicación del bot de justificaciones."""
    app = (
        ApplicationBuilder()
        .token(JUST_BOT_TOKEN)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info("Bot de justificaciones cargado")
    return app

if __name__ == "__main__":
    application = build_just_app()
    log.info("🚀 Bot de justificaciones iniciando...")
    log.info(f"📚 Canal de justificaciones: {JUSTIFICATIONS_CHAT_ID}")
    log.info(f"🔒 Restricción de acceso: {'Sí' if JUST_ADMIN_IDS else 'No'}")
    log.info(f"⏰ Auto-eliminación: {JUST_AUTO_DELETE_MINUTES} minutos" if JUST_AUTO_DELETE_MINUTES else "⏰ Sin auto-eliminación")
    
    application.run_polling(allowed_updates=["message", "edited_message"])
