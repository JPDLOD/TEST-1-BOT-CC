# -*- coding: utf-8 -*-
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from telegram import Update
from telegram.ext import Application, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.error import TelegramError

from config import BOT_TOKEN, DB_FILE, TZ, TZNAME, SOURCE_CHAT_ID, PREVIEW_CHAT_ID
from database import init_db, save_draft, list_drafts, mark_deleted, restore_draft, get_last_deleted
from keyboards import kb_main, text_main, kb_settings, text_settings
from core_utils import temp_notice, extract_id_from_text, deep_link_for_channel_message, parse_nuke_selection
from publisher import (
    publicar_todo_activos, publicar_ids, get_active_targets,
    STATS, SCHEDULED_LOCK, set_active_backup, is_active_backup
)

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

init_db(DB_FILE)

# ===== Helpers =====
def _is_command_text(txt: Optional[str]) -> bool:
    return bool(txt and txt.strip().startswith("/"))

# ===== Comandos =====
async def _cmd_listar(context: ContextTypes.DEFAULT_TYPE):
    drafts_all = list_drafts(DB_FILE)  # [(id, snip)]
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

    # Programaciones (si tu proyecto usa scheduler, aquí solo mostramos contador básico)
    out.append("\n🗒 Programaciones pendientes: (usa /programados si ya tienes scheduler.py)")

    await context.bot.send_message(SOURCE_CHAT_ID, "\n".join(out))

async def _cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE, txt: str):
    mid = extract_id_from_text(txt)
    if not mid and update.channel_post and update.channel_post.reply_to_message:
        mid = update.channel_post.reply_to_message.message_id
    if not mid:
        await context.bot.send_message(SOURCE_CHAT_ID, "❌ Usa: /cancelar <id> o responde al mensaje a cancelar.")
        return
    mark_deleted(DB_FILE, mid)
    SCHEDULED_LOCK.discard(mid)
    STATS["cancelados"] += 1
    restantes = len(list_drafts(DB_FILE))
    await temp_notice(context.bot, f"🚫 Cancelado id:{mid}. Quedan {restantes} en la cola.", ttl=6)

async def _cmd_eliminar(update: Update, context: ContextTypes.DEFAULT_TYPE, txt: str):
    mid = extract_id_from_text(txt)
    if not mid and update.channel_post and update.channel_post.reply_to_message:
        mid = update.channel_post.reply_to_message.message_id
    if not mid:
        await context.bot.send_message(SOURCE_CHAT_ID, "❌ Usa: /eliminar <id> o responde al mensaje a eliminar.")
        return
    ok_del = True
    try:
        await context.bot.delete_message(chat_id=SOURCE_CHAT_ID, message_id=mid)
    except TelegramError as e:
        ok_del = False
        logger.warning(f"No pude borrar en el canal id:{mid} → {e}")

    # hard delete
    import sqlite3
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("DELETE FROM drafts WHERE message_id = ?", (mid,))
    con.commit();  con.close()

    SCHEDULED_LOCK.discard(mid)
    STATS["eliminados"] += 1
    restantes = len(list_drafts(DB_FILE))
    txt_ok = "🗑️ Eliminado del canal y de la cola." if ok_del else "🗑️ Quitado de la cola (no pude borrar en el canal)."
    await temp_notice(context.bot, f"{txt_ok} id:{mid}. Quedan {restantes} en la cola.", ttl=7)

async def _cmd_deshacer(update: Update, context: ContextTypes.DEFAULT_TYPE, txt: str):
    mid = extract_id_from_text(txt)
    if not mid and update.channel_post and update.channel_post.reply_to_message:
        mid = update.channel_post.reply_to_message.message_id
    if not mid:
        mid = get_last_deleted(DB_FILE)
    if not mid:
        await temp_notice(context.bot, "ℹ️ No hay nada para deshacer.", ttl=5);  return
    restore_draft(DB_FILE, mid)
    if STATS["cancelados"] > 0:
        STATS["cancelados"] -= 1
    restantes = len(list_drafts(DB_FILE))
    await temp_notice(context.bot, f"↩️ Restaurado id:{mid}. Ahora hay {restantes} en la cola.", ttl=6)

async def _cmd_preview(context: ContextTypes.DEFAULT_TYPE):
    # enviar copia a PREVIEW sin marcar como enviado (excluye bloqueados)
    import sqlite3
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    rows_full = list(cur.execute(
        "SELECT message_id, snippet, raw_json FROM drafts WHERE sent=0 AND deleted=0 ORDER BY message_id ASC"
    ).fetchall())
    con.close()
    rows = [(m, t, r) for (m, t, r) in rows_full if m not in SCHEDULED_LOCK]
    if not rows:
        await temp_notice(context.bot, "🧪 Preview: 0 mensajes.", ttl=4);  return
    ids = [m for (m, _t, _r) in rows]
    pubs, fails, _ = await publicar_ids(context, ids=ids, targets=[PREVIEW_CHAT_ID], mark_as_sent=False)
    await context.bot.send_message(SOURCE_CHAT_ID, f"🧪 Preview: enviados {pubs}, fallidos {fails}.")

async def _cmd_backup(context: ContextTypes.DEFAULT_TYPE, arg: str):
    v = (arg or "").strip().lower()
    if v in ("on", "1", "true", "si", "sí"):
        set_active_backup(True)
    elif v in ("off", "0", "false", "no"):
        set_active_backup(False)
    else:
        await context.bot.send_message(SOURCE_CHAT_ID, "Usa: /backup on|off")
        return
    await context.bot.send_message(SOURCE_CHAT_ID, text_settings(), reply_markup=kb_settings(), parse_mode="Markdown")

# ===== Callbacks (inline) =====
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    await q.answer()
    data = q.data or ""
    try:
        if data == "m:list":
            await _cmd_listar(context)
        elif data == "m:send":
            await temp_notice(context.bot, "⏳ Procesando envío…", ttl=4)
            ok, fail = await publicar_todo_activos(context)
            extras = []
            if STATS["cancelados"]:
                extras.append(f"Cancelados: {STATS['cancelados']}")
            if STATS["eliminados"]:
                extras.append(f"Eliminados: {STATS['eliminados']}")
            msg_out = f"✅ Publicados {ok}."
            if fail:
                extras.append(f"Fallidos: {fail}")
            if extras:
                msg_out += "\n📦 " + " · ".join(extras) + "."
            await context.bot.send_message(SOURCE_CHAT_ID, msg_out)
            STATS["cancelados"] = 0;  STATS["eliminados"] = 0
        elif data == "m:preview":
            await _cmd_preview(context)
        elif data == "m:sched":
            # (tu panel de programación si lo usas aquí)
            await q.edit_message_text(
                "⏰ Programación rápida disponible en tu proyecto con scheduler.py.\n"
                "Usa /programar YYYY-MM-DD HH:MM si ya lo tienes activo.",
                reply_markup=kb_settings()
            )
        elif data == "m:settings":
            await q.edit_message_text(text_settings(), reply_markup=kb_settings(), parse_mode="Markdown")
        elif data == "m:toggle_backup":
            set_active_backup(not is_active_backup())
            await q.edit_message_text(text_settings(), reply_markup=kb_settings(), parse_mode="Markdown")
        elif data == "m:back":
            await q.edit_message_text(text_main(), reply_markup=kb_main())
    except Exception as e:
        logger.exception(f"Error en callback: {e}")

# ===== Handler del canal =====
async def handle_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg or msg.chat_id != SOURCE_CHAT_ID:
        return

    txt = (msg.text or "").strip()

    if _is_command_text(txt):
        low = txt.lower()

        if low.startswith("/listar") or low.startswith("/lista"):
            await _cmd_listar(context);  return

        if low.startswith(("/cancelar", "/cancel", "/skip")):
            await _cmd_cancelar(update, context, txt);  return

        if low.startswith(("/eliminar", "/del", "/delete", "/remove", "/borrar")):
            await _cmd_eliminar(update, context, txt);  return

        if low.startswith(("/deshacer", "/undo", "/restaurar")):
            await _cmd_deshacer(update, context, txt);  return

        if low.startswith("/nuke"):
            drafts = list_drafts(DB_FILE)
            arg = (txt.split(maxsplit=1)[1] if len(txt.split()) > 1 else "")
            victims = parse_nuke_selection(arg, drafts)
            if not drafts:
                await context.bot.send_message(SOURCE_CHAT_ID, "No hay pendientes.");  return
            if not victims:
                await context.bot.send_message(
                    SOURCE_CHAT_ID,
                    "Usa: /nuke all | /nuke todos | /nuke 1,3,5 | /nuke 1-10 | /nuke N"
                );  return
            import sqlite3
            borrados = 0
            for mid in sorted(victims, reverse=True):
                try:
                    await context.bot.delete_message(chat_id=SOURCE_CHAT_ID, message_id=mid)
                except TelegramError:
                    pass
                con = sqlite3.connect(DB_FILE)
                cur = con.cursor()
                cur.execute("DELETE FROM drafts WHERE message_id=?", (mid,))
                con.commit(); con.close()
                SCHEDULED_LOCK.discard(mid)
                borrados += 1
            STATS["eliminados"] += borrados
            restantes = len(list_drafts(DB_FILE))
            await context.bot.send_message(SOURCE_CHAT_ID, f"💣 Nuke: {borrados} borrados. Quedan {restantes} en la cola.")
            return

        if low.startswith("/enviar"):
            await temp_notice(context.bot, "⏳ Procesando envío…", ttl=4)
            ok, fail = await publicar_todo_activos(context)
            extras = []
            if STATS["cancelados"]:
                extras.append(f"Cancelados: {STATS['cancelados']}")
            if STATS["eliminados"]:
                extras.append(f"Eliminados: {STATS['eliminados']}")
            msg_out = f"✅ Publicados {ok}."
            if fail:
                extras.append(f"Fallidos: {fail}")
            if extras:
                msg_out += "\n📦 " + " · ".join(extras) + "."
            await context.bot.send_message(SOURCE_CHAT_ID, msg_out)
            STATS["cancelados"] = 0;  STATS["eliminados"] = 0
            return

        if low.startswith("/preview"):
            await _cmd_preview(context);  return

        if low.startswith("/backup"):
            arg = (txt.split(maxsplit=1)[1] if len(txt.split()) > 1 else "")
            await _cmd_backup(context, arg);  return

        if low.startswith(("/comandos", "/comando", "/ayuda", "/start")):
            await context.bot.send_message(SOURCE_CHAT_ID, text_main(), reply_markup=kb_main());  return

        await context.bot.send_message(SOURCE_CHAT_ID, "Comando no reconocido. Usa /comandos.")
        return

    # ===== Borrador =====
    snippet = msg.text or msg.caption or ""
    raw_json = json.dumps(msg.to_dict(), ensure_ascii=False)
    save_draft(DB_FILE, msg.message_id, snippet, raw_json)
    logger.info(f"Guardado en borrador: {msg.message_id}")

# ===== MAIN =====
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel))
    app.add_handler(CallbackQueryHandler(handle_callback))
    logger.info("Bot iniciado 🚀 Escuchando channel_post en el BORRADOR.")
    app.run_polling(allowed_updates=["channel_post", "callback_query"], drop_pending_updates=True)

if __name__ == "__main__":
    main()
