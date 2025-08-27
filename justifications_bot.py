# -*- coding: utf-8 -*-
"""
Bot de Justificaciones: devuelve una justificación (mensaje/foto/video) bajo demanda.

Deep link:
  https://t.me/<TU_BOT_JST_USERNAME>?start=jst_<message_id>

Requisitos:
- JUSTIFICATIONS_BOT_TOKEN en ENV
- JST_CHANNEL_ID (numérico, -100…) en config.py
- El bot de justificaciones agregado al canal de justificaciones con permiso de leer (idealmente admin).
"""
import logging
import os
from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Lee config sin reventar si otras env faltan (este archivo solo necesita el canal)
try:
    from config import JST_CHANNEL_ID
except Exception:
    # fallback duro si no hay config importable
    JST_CHANNEL_ID = int(os.environ.get("JST_CHANNEL_ID", "0") or "0")

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
log = logging.getLogger("justifications_bot")


def _parse_payload(text: Optional[str]) -> Optional[int]:
    """
    '/start jst_12345' -> 12345 ; si no hay payload válido, None.
    """
    if not text:
        return None
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None
    payload = parts[1].strip()
    if payload.lower().startswith("jst_"):
        rest = payload[4:]
        if rest.isdigit():
            return int(rest)
    return None


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text if update.message else ""
    mid = _parse_payload(text)

    if mid is None:
        await context.bot.send_message(
            chat_id,
            "👋 Envíame un *enlace de inicio* con formato `/start jst_<message_id>`\n"
            "Ejemplo: `t.me/TU_BOT?start=jst_12345`",
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        return

    if not JST_CHANNEL_ID:
        await context.bot.send_message(chat_id, "❌ Falta JST_CHANNEL_ID. Configúralo y vuelve a intentar.")
        return

    try:
        await context.bot.copy_message(
            chat_id=chat_id,
            from_chat_id=JST_CHANNEL_ID,
            message_id=mid
        )
        # Limpia el /start del usuario (si se puede)
        try:
            if update.message:
                await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
        except Exception:
            pass
        log.info("Justificación %s copiada a %s ok", mid, chat_id)
    except Exception as e:
        log.exception("Error copiando justificación %s: %s", mid, e)
        await context.bot.send_message(
            chat_id,
            "❌ No pude obtener esa justificación. Verifica:\n"
            "• Que el bot sea admin/lector del canal de justificaciones\n"
            "• Que el ID exista en ese canal\n"
            "• Que JST_CHANNEL_ID sea el correcto (-100…)",
        )


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("pong")


def main():
    token = os.environ.get("JUSTIFICATIONS_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Falta JUSTIFICATIONS_BOT_TOKEN en variables de entorno.")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ping", cmd_ping))

    log.info("Justifications bot listo ✅")
    app.run_polling(allowed_updates=["message"], drop_pending_updates=True)


if __name__ == "__main__":
    main()