# -*- coding: utf-8 -*-
"""
Bot de Justificaciones: entrega UNA justificación bajo demanda.

Uso:
- Pon este bot como *admin* (o al menos "Read messages") en el canal privado
  que almacena las justificaciones (JST_CHANNEL_ID).
- Publica la justificación allí (texto, foto, media). `copy_message` sirve para mensajes individuales.
- En tu post público (o en el canal de casos), agrega un deep-link del bot:
    t.me/<TU_BOT_USERNAME>?start=jst_12345
  donde 12345 es el message_id de la justificación en el canal JST_CHANNEL_ID.

Cuando el usuario toca ese link, el bot recibe /start con payload "jst_12345"
y copia el mensaje original al chat del usuario.
"""
import logging
from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config import JUSTIFICATIONS_BOT_TOKEN, JST_CHANNEL_ID

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
log = logging.getLogger("justifications_bot")


def _parse_payload(text: Optional[str]) -> Optional[int]:
    """
    A partir de '/start jst_12345' devuelve 12345. Si no hay payload válido, None.
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
        # start sin payload -> instrucción corta
        await context.bot.send_message(
            chat_id,
            "👋 Hola. Envíame un *enlace de inicio* con formato\n"
            "`/start jst_<message_id>` y te devolveré esa justificación.\n\n"
            "Ejemplo: `t.me/TU_BOT?start=jst_12345`",
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        return

    try:
        # copy_message funciona con texto, foto, video, etc. (mensajes individuales)
        await context.bot.copy_message(
            chat_id=chat_id,
            from_chat_id=JST_CHANNEL_ID,
            message_id=mid
        )
        # Opcional: borrar el comando del usuario para dejar limpia la conversación
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
            "❌ No pude obtener esa justificación. Revisa que el bot sea *admin* del canal "
            "de justificaciones y que el ID exista.",
            parse_mode="Markdown"
        )


def main():
    if not JUSTIFICATIONS_BOT_TOKEN:
        raise RuntimeError("Falta JUSTIFICATIONS_BOT_TOKEN en variables de entorno.")

    app = Application.builder().token(JUSTIFICATIONS_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))

    log.info("Justifications bot listo ✅")
    app.run_polling(allowed_updates=["message"], drop_pending_updates=True)


if __name__ == "__main__":
    main()