# -*- coding: utf-8 -*-
"""
Bot PRIVADO de justificaciones.
Responde a:
  • /start jst_<message_id>  → copia ese mensaje desde JUSTIFICATIONS_CHAT_ID al usuario
  • /start                   → ayuda breve
  • /ping                    → pong
  • texto con número         → intenta copiar ese message_id
Acceso: si JUST_ADMIN_IDS tiene valores, SOLO esos IDs pueden usarlo. Si está vacío, acceso libre.
Auto-borrado: si JUST_AUTO_DELETE_MINUTES > 0, borra lo que envía tras ese tiempo.
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
    log.error("JUST_BOT_TOKEN no está definido en el entorno.")
if not JUSTIFICATIONS_CHAT_ID:
    log.error("JUSTIFICATIONS_CHAT_ID no está definido en el entorno.")

def _allowed(user_id: Optional[int]) -> bool:
    if not JUST_ADMIN_IDS:
        return True
    try:
        return int(user_id) in JUST_ADMIN_IDS
    except Exception:
        return False

async def _auto_delete(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    if JUST_AUTO_DELETE_MINUTES and JUST_AUTO_DELETE_MINUTES > 0:
        try:
            await asyncio.sleep(JUST_AUTO_DELETE_MINUTES * 60)
            await ctx.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass

def _parse_start_arg(args) -> Optional[int]:
    """Acepta: jst_123  |  123"""
    if not args:
        return None
    raw = str(args[0]).strip()
    if raw.lower().startswith("jst_"):
        raw = raw[4:]
    if raw.isdigit():
        return int(raw)
    return None

async def _copy_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE, mid: int):
    chat_id = update.effective_chat.id
    try:
        msg = await context.bot.copy_message(
            chat_id=chat_id,
            from_chat_id=JUSTIFICATIONS_CHAT_ID,
            message_id=mid,
            disable_notification=True
        )
        # auto-delete del entregable (si está configurado)
        asyncio.create_task(_auto_delete(context, chat_id, msg.message_id))
    except Exception as e:
        log.warning(f"No pude copiar message_id={mid} desde {JUSTIFICATIONS_CHAT_ID}: {e}")
        info = await context.bot.send_message(
            chat_id=chat_id,
            text="❌ No encontré esa justificación. Verifica el ID (o que el bot sea admin del canal)."
        )
        asyncio.create_task(_auto_delete(context, chat_id, info.message_id))

# ---------- handlers ----------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update.effective_user.id):
        return
    mid = _parse_start_arg(context.args)
    if mid:
        await _copy_by_id(update, context, mid)
        return
    txt = (
        "🤖 Bot de Justificaciones\n\n"
        "Envíame `/start jst_<ID>` o solo el número de ID del mensaje de la "
        "justificación para recibirla.\n"
        "Ej.: `/start jst_12345`"
    )
    info = await context.bot.send_message(update.effective_chat.id, txt, parse_mode="Markdown")
    asyncio.create_task(_auto_delete(context, update.effective_chat.id, info.message_id))

async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update.effective_user.id):
        return
    m = await context.bot.send_message(update.effective_chat.id, "pong")
    asyncio.create_task(_auto_delete(context, update.effective_chat.id, m.message_id))

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update.effective_user.id):
        return
    text = (update.message.text or "").strip()
    if text.lower().startswith("jst_"):
        text = text[4:].strip()
    if text.isdigit():
        await _copy_by_id(update, context, int(text))
        return
    # mensaje no válido → ayuda corta
    info = await context.bot.send_message(
        update.effective_chat.id,
        "Envíame `/start jst_<ID>` o el número de ID de la justificación.",
        parse_mode="Markdown",
    )
    asyncio.create_task(_auto_delete(context, update.effective_chat.id, info.message_id))

def build_just_app() -> Application:
    app = (
        ApplicationBuilder()
        .token(JUST_BOT_TOKEN)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info("Justifications bot cargado.")
    return app

if __name__ == "__main__":
    application = build_just_app()
    log.info("Justifications bot iniciando…")
    application.run_polling(allowed_updates=["message", "edited_message"])