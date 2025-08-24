# -*- coding: utf-8 -*-
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.error import TelegramError

from config import BOT_TOKEN, DB_FILE, TZ, TZNAME, SOURCE_CHAT_ID, PREVIEW_CHAT_ID
from database import init_db, save_draft, list_drafts, mark_deleted, restore_draft
from ui import kb_main, text_main, kb_settings, text_settings
from utils import temp_notice, extract_id_from_text, deep_link_for_channel_message
from scheduler import schedule_ids, cmd_programar, cmd_programados, cmd_desprogramar, SCHEDULES
from publisher import (
    publicar_todo_activos, publicar_ids, get_active_targets,
    STATS, SCHEDULED_LOCK, set_active_backup, is_active_backup
)

# ========= LOGGING =========
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ========= DB =========
init_db(DB_FILE)   # ✅ ahora pasa el argumento correcto

# -------------------------------------------------------
# Helpers
# -------------------------------------------------------
def _is_command_text(txt: Optional[str]) -> bool:
    return bool(txt and txt.strip().startswith("/"))

# -------------------------------------------------------
# Nueva función: detectar @@@ texto | link y convertir a botón
# -------------------------------------------------------
async def _handle_at_button(msg, context: ContextTypes.DEFAULT_TYPE):
    txt = msg.text or msg.caption or ""
    if not txt.startswith("@@@"):
        return False

    # formato: @@@ Texto del botón | link
    try:
        _, rest = txt.split("@@@", 1)
        label, link = [p.strip() for p in rest.split("|", 1)]
    except Exception:
        return False

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(label, url=link)]
    ])

    await context.bot.send_message(
        chat_id=SOURCE_CHAT_ID,
        text=f"🔗 {label}",
        reply_markup=kb,
        disable_web_page_preview=False  # deja que Telegram haga Instant View si aplica
    )

    # eliminar el mensaje original del canal borrador
    try:
        await context.bot.delete_message(SOURCE_CHAT_ID, msg.message_id)
    except Exception:
        pass

    return True

# -------------------------------------------------------
# Comandos principales
# -------------------------------------------------------
async def _cmd_listar(context: ContextTypes.DEFAULT_TYPE):
    drafts_all = list_drafts(DB_FILE)
    drafts = [(did, snip) for (did, snip) in drafts_all if did not in SCHEDULED_LOCK]

    if not drafts:
        out = ["📋 Borradores pendientes: 0"]
    else:
        out = ["📋 Borradores pendientes:"]
        for i, (did, snip) in enumerate(drafts, start=1):
            s = (snip or "").strip()
            if len(s) > 60:
                s = s[:60] + "…"
            out.append(f"• {i:>2} — {s or '[contenido]'}  (id:{did})")

    if not SCHEDULES:
        out.append("\n🗒 Programaciones pendientes: 0")
    else:
        out.append("\n🗒 Programaciones pendientes:")
        for pid, rec in sorted(SCHEDULES.items()):
            when = rec["when"].astimezone(TZ).strftime("%Y-%m-%d %H:%M")
            ids = rec["ids"]
            out.append(f"• #{pid} — {when} ({TZNAME}) — {len(ids)} mensajes")

    await context.bot.send_message(SOURCE_CHAT_ID, "\n".join(out))

# -------------------------------------------------------
# Handler del canal
# -------------------------------------------------------
async def handle_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg or msg.chat_id != SOURCE_CHAT_ID:
        return

    txt = (msg.text or "").strip()

    # primero: detectar función especial @@@
    done = await _handle_at_button(msg, context)
    if done:
        return

    # comandos
    if _is_command_text(txt):
        low = txt.lower()

        if low.startswith("/listar"):
            await _cmd_listar(context);  return

        if low.startswith("/programar"):
            parts = txt.split(maxsplit=2)
            if len(parts) >= 3:
                when_str = f"{parts[1]} {parts[2]}"
                await cmd_programar(context, when_str)
            else:
                await context.bot.send_message(
                    SOURCE_CHAT_ID,
                    "Usa: /programar YYYY-MM-DD HH:MM  (formato 24 h)"
                )
            return

        if low.startswith("/programados"):
            await cmd_programados(context);  return

        if low.startswith("/desprogramar"):
            arg = (txt.split(maxsplit=1)[1] if len(txt.split()) > 1 else "")
            await cmd_desprogramar(context, arg);  return

        if low.startswith("/enviar"):
            await temp_notice(context.bot, "⏳ Procesando envío…", ttl=4)
            ok, fail = await publicar_todo_activos(context)
            msg_out = f"✅ Publicados {ok}."
            if fail: msg_out += f" Fallidos: {fail}."
            await context.bot.send_message(SOURCE_CHAT_ID, msg_out)
            return

        if low.startswith("/preview"):
            drafts_all = list_drafts(DB_FILE)
            ids = [did for (did, _s) in drafts_all if did not in SCHEDULED_LOCK]
            if not ids:
                await temp_notice(context.bot, "🧪 Preview: 0 mensajes.", ttl=4); return
            pubs, fails, _ = await publicar_ids(context, ids=ids, targets=[PREVIEW_CHAT_ID], mark_as_sent=False)
            await context.bot.send_message(SOURCE_CHAT_ID, f"🧪 Preview: enviados {pubs}, fallidos {fails}.")
            return

        if low.startswith("/backup"):
            arg = (txt.split(maxsplit=1)[1] if len(txt.split()) > 1 else "")
            if arg in ("on","1","true","si","sí"): set_active_backup(True)
            elif arg in ("off","0","false","no"): set_active_backup(False)
            else:
                await context.bot.send_message(SOURCE_CHAT_ID,"Usa: /backup on|off"); return
            await context.bot.send_message(SOURCE_CHAT_ID, text_settings(), reply_markup=kb_settings(), parse_mode="Markdown")
            return

        if low.startswith(("/comandos","/start","/ayuda")):
            await context.bot.send_message(SOURCE_CHAT_ID, text_main(), reply_markup=kb_main()); return

        await context.bot.send_message(SOURCE_CHAT_ID, "Comando no reconocido. Usa /comandos.")
        return

    # si no es comando ni @@@ → guardar como borrador
    snippet = msg.text or msg.caption or ""
    raw_json = json.dumps(msg.to_dict(), ensure_ascii=False)
    save_draft(DB_FILE, msg.message_id, snippet, raw_json)
    logger.info(f"Guardado en borrador: {msg.message_id}")

# -------------------------------------------------------
# MAIN
# -------------------------------------------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel))
    logger.info("Bot iniciado 🚀 Escuchando channel_post en el BORRADOR.")
    app.run_polling(allowed_updates=["channel_post"], drop_pending_updates=True)

if __name__ == "__main__":
    main()
