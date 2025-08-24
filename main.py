# -*- coding: utf-8 -*-
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, CallbackQueryHandler, filters
from telegram.error import TelegramError

from config import BOT_TOKEN, SOURCE_CHAT_ID, PREVIEW_CHAT_ID, TZ, TZNAME
from database import init_db, save_draft, get_unsent_drafts, list_drafts, mark_deleted, restore_draft
from keyboards import kb_main, text_main, kb_settings, text_settings
from publisher import (
    publicar_todo_activos, publicar_rows, publicar_ids, get_active_targets,
    STATS, SCHEDULED_LOCK, set_active_backup
)
from scheduler import schedule_ids, cmd_programar, cmd_programados, cmd_desprogramar, SCHEDULES
from core_utils import temp_notice, extract_id_from_text, deep_link_for_channel_message, parse_nuke_selection, parse_shortcut_line

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

init_db()  # tu database.py ya conoce el path por defecto

# -------- helpers ----------
def _is_command_text(txt: Optional[str]) -> bool:
    return bool(txt and txt.strip().startswith("/"))

async def _delete_user_command_if_possible(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update and update.channel_post:
            await context.bot.delete_message(chat_id=SOURCE_CHAT_ID, message_id=update.channel_post.message_id)
    except TelegramError:
        pass

# -------- comandos ----------
async def _cmd_listar(context: ContextTypes.DEFAULT_TYPE):
    drafts_all = list_drafts()
    drafts = [(did, snip) for (did, snip) in drafts_all if did not in SCHEDULED_LOCK]
    if not drafts:
        out = ["📋 Borradores pendientes: 0"]
    else:
        out = ["📋 Borradores pendientes:"]
        for i, (did, snip) in enumerate(drafts, start=1):
            s = (snip or "").strip()
            if len(s) > 60: s = s[:60] + "…"
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

async def _cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE, txt: str):
    mid = extract_id_from_text(txt)
    if not mid and update.channel_post and update.channel_post.reply_to_message:
        mid = update.channel_post.reply_to_message.message_id
    if not mid:
        await context.bot.send_message(SOURCE_CHAT_ID, "❌ Usa: /cancelar <id> o responde al mensaje a cancelar.")
        return
    mark_deleted(mid)
    SCHEDULED_LOCK.discard(mid)
    STATS["cancelados"] += 1
    restantes = len(list_drafts())
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
    except TelegramError:
        ok_del = False
    # hard delete en DB
    import sqlite3, os
    from config import DB_FILE
    con = sqlite3.connect(DB_FILE)
    con.execute("DELETE FROM drafts WHERE message_id=?", (mid,))
    con.commit(); con.close()
    SCHEDULED_LOCK.discard(mid)
    STATS["eliminados"] += 1
    restantes = len(list_drafts())
    txt_ok = "🗑️ Eliminado del canal y de la cola." if ok_del else "🗑️ Quitado de la cola (no pude borrar en el canal)."
    await temp_notice(context.bot, f"{txt_ok} id:{mid}. Quedan {restantes} en la cola.", ttl=7)

async def _cmd_deshacer(update: Update, context: ContextTypes.DEFAULT_TYPE, txt: str):
    from database import get_last_deleted
    mid = extract_id_from_text(txt)
    if not mid and update.channel_post and update.channel_post.reply_to_message:
        mid = update.channel_post.reply_to_message.message_id
    if not mid:
        mid = get_last_deleted()
    if not mid:
        await temp_notice(context.bot, "ℹ️ No hay nada para deshacer.", ttl=5);  return
    restore_draft(mid)
    if STATS["cancelados"] > 0: STATS["cancelados"] -= 1
    restantes = len(list_drafts())
    await temp_notice(context.bot, f"↩️ Restaurado id:{mid}. Ahora hay {restantes} en la cola.", ttl=6)

async def _cmd_preview(context: ContextTypes.DEFAULT_TYPE):
    rows_full = get_unsent_drafts()
    rows = [(m, t, r) for (m, t, r) in rows_full if m not in SCHEDULED_LOCK]
    if not rows:
        await temp_notice(context.bot, "🧪 Preview: 0 mensajes.", ttl=4);  return
    pubs, fails, _ = await publicar_rows(context, rows=rows, targets=[PREVIEW_CHAT_ID], mark_as_sent=False)
    await context.bot.send_message(SOURCE_CHAT_ID, f"🧪 Preview: enviados {pubs}, fallidos {fails}.")

async def _cmd_backup(context: ContextTypes.DEFAULT_TYPE, arg: str):
    v = (arg or "").strip().lower()
    if v in ("on", "1", "true", "si", "sí"):
        set_active_backup(True)
    elif v in ("off", "0", "false", "no"):
        set_active_backup(False)
    else:
        await context.bot.send_message(SOURCE_CHAT_ID, "Usa: /backup on|off");  return
    await context.bot.send_message(SOURCE_CHAT_ID, text_settings(), reply_markup=kb_settings(), parse_mode="Markdown")

# -------- callbacks ----------
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return
    await q.answer()
    data = q.data or ""
    try:
        if data == "m:list":
            await _cmd_listar(context)
        elif data == "m:send":
            await temp_notice(context.bot, "⏳ Procesando envío…", ttl=4)
            ok, fail = await publicar_todo_activos(context)
            extras = []
            if STATS["cancelados"]: extras.append(f"Cancelados: {STATS['cancelados']}")
            if STATS["eliminados"]: extras.append(f"Eliminados: {STATS['eliminados']}")
            msg_out = f"✅ Publicados {ok}."
            if fail: extras.append(f"Fallidos: {fail}")
            if extras: msg_out += "\n📦 " + " · ".join(extras) + "."
            await context.bot.send_message(SOURCE_CHAT_ID, msg_out)
            STATS["cancelados"] = 0;  STATS["eliminados"] = 0
        elif data == "m:preview":
            await _cmd_preview(context)
        elif data == "m:sched":
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            text = (
                "⏰ Programar envío de **los borradores actuales**.\n"
                "Elige un atajo o usa `/programar YYYY-MM-DD HH:MM` (formato 24h).\n"
                "⚠️ Si no hay borradores, no se programa nada."
            )
            kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("⏳ +5 min", callback_data="s:+5"),
                     InlineKeyboardButton("⏳ +15 min", callback_data="s:+15")],
                    [InlineKeyboardButton("🕗 Hoy 20:00", callback_data="s:today20"),
                     InlineKeyboardButton("🌅 Mañana 07:00", callback_data="s:tom07")],
                    [InlineKeyboardButton("🗒 Ver programados", callback_data="s:list"),
                     InlineKeyboardButton("❌ Cancelar todos", callback_data="s:clear")],
                    [InlineKeyboardButton("✍️ Custom", callback_data="s:custom"),
                     InlineKeyboardButton("⬅️ Volver", callback_data="m:back")]
                ]
            )
            await q.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        elif data == "m:settings":
            await q.edit_message_text(text_settings(), reply_markup=kb_settings(), parse_mode="Markdown")
        elif data == "m:toggle_backup":
            # simple: alternar y refrescar
            from publisher import is_active_backup
            set_active_backup(not is_active_backup())
            await q.edit_message_text(text_settings(), reply_markup=kb_settings(), parse_mode="Markdown")
        elif data == "m:back":
            await q.edit_message_text(text_main(), reply_markup=kb_main())

        # Programación rápida
        elif data.startswith("s:"):
            now = datetime.now(tz=TZ); when = None
            if data == "s:+5": when = now + timedelta(minutes=5)
            elif data == "s:+15": when = now + timedelta(minutes=15)
            elif data == "s:today20":
                when = now.replace(hour=20, minute=0, second=0, microsecond=0)
                if when <= now: when = when + timedelta(days=1)
            elif data == "s:tom07":
                when = (now + timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0)
            elif data == "s:list":
                await cmd_programados(context)
            elif data == "s:clear":
                await cmd_desprogramar(context, "all")
            elif data == "s:custom":
                await q.edit_message_text(
                    "✍️ Formato manual:\n`/programar YYYY-MM-DD HH:MM` (formato 24 h)\n\n⬅️ Usa *Volver* para regresar.",
                    parse_mode="Markdown"
                )
            if when:
                ids = [did for (did, _snip) in list_drafts()]
                if not ids:
                    await temp_notice(context.bot, "📭 No hay borradores para programar.", ttl=6)
                else:
                    await schedule_ids(context, when, ids)
    except Exception as e:
        logger.exception(f"Error en callback: {e}")

# -------- handler canal borrador ----------
async def handle_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg or msg.chat_id != SOURCE_CHAT_ID:
        return

    txt = (msg.text or "").strip()

    # COMANDOS
    if _is_command_text(txt):
        low = txt.lower()
        if low.startswith("/listar") or low.startswith("/lista"):
            await _cmd_listar(context);  await _delete_user_command_if_possible(update, context);  return
        if low.startswith(("/cancelar", "/cancel", "/skip")):
            await _cmd_cancelar(update, context, txt);  await _delete_user_command_if_possible(update, context);  return
        if low.startswith(("/eliminar", "/del", "/delete", "/remove", "/borrar")):
            await _cmd_eliminar(update, context, txt);  await _delete_user_command_if_possible(update, context);  return
        if low.startswith(("/deshacer", "/undo", "/restaurar")):
            await _cmd_deshacer(update, context, txt);  await _delete_user_command_if_possible(update, context);  return
        if low.startswith("/nuke"):
            drafts = list_drafts()
            parts = (txt or "").split(maxsplit=1)
            arg = parts[1] if len(parts) > 1 else ""
            victims = parse_nuke_selection(arg, drafts)
            if not drafts:
                await context.bot.send_message(SOURCE_CHAT_ID, "No hay pendientes.");  await _delete_user_command_if_possible(update, context);  return
            if not victims:
                await context.bot.send_message(SOURCE_CHAT_ID, "Usa: /nuke all | /nuke todos | /nuke 1,3,5 | /nuke 1-10 | /nuke N");  await _delete_user_command_if_possible(update, context);  return
            import sqlite3
            from config import DB_FILE
            borrados = 0
            for mid in sorted(victims, reverse=True):
                try: await context.bot.delete_message(chat_id=SOURCE_CHAT_ID, message_id=mid)
                except TelegramError: pass
                con = sqlite3.connect(DB_FILE);  con.execute("DELETE FROM drafts WHERE message_id=?", (mid,));  con.commit(); con.close()
                SCHEDULED_LOCK.discard(mid);  borrados += 1
            STATS["eliminados"] += borrados
            restantes = len(list_drafts())
            await context.bot.send_message(SOURCE_CHAT_ID, f"💣 Nuke: {borrados} borrados. Quedan {restantes} en la cola.")
            await _delete_user_command_if_possible(update, context);  return
        if low.startswith("/enviar"):
            await temp_notice(context.bot, "⏳ Procesando envío…", ttl=4)
            ok, fail = await publicar_todo_activos(context)
            extras = []
            if STATS["cancelados"]: extras.append(f"Cancelados: {STATS['cancelados']}")
            if STATS["eliminados"]: extras.append(f"Eliminados: {STATS['eliminados']}")
            msg_out = f"✅ Publicados {ok}."
            if fail: extras.append(f"Fallidos: {fail}")
            if extras: msg_out += "\n📦 " + " · ".join(extras) + "."
            await context.bot.send_message(SOURCE_CHAT_ID, msg_out)
            STATS["cancelados"] = 0;  STATS["eliminados"] = 0
            await _delete_user_command_if_possible(update, context);  return
        if low.startswith("/preview"):
            await _cmd_preview(context);  await _delete_user_command_if_possible(update, context);  return
        if low.startswith("/programar"):
            parts = txt.split(maxsplit=2)
            if len(parts) >= 3:
                when_str = f"{parts[1]} {parts[2]}"
                await cmd_programar(context, when_str)
            else:
                await context.bot.send_message(SOURCE_CHAT_ID, "Usa: `/programar YYYY-MM-DD HH:MM` (24h).", parse_mode="Markdown")
            await _delete_user_command_if_possible(update, context);  return
        if low.startswith("/programados"):
            await cmd_programados(context);  await _delete_user_command_if_possible(update, context);  return
        if low.startswith("/desprogramar"):
            parts = txt.split(maxsplit=1);  arg = parts[1] if len(parts) > 1 else ""
            await cmd_desprogramar(context, arg);  await _delete_user_command_if_possible(update, context);  return
        if low.startswith("/id"):
            if msg.reply_to_message and len((txt or "").split()) == 1:
                rid = msg.reply_to_message.message_id
                await context.bot.send_message(SOURCE_CHAT_ID, f"🆔 ID del mensaje: {rid}")
            else:
                mid = extract_id_from_text(txt) or (txt.split()[1] if len(txt.split()) > 1 and txt.split()[1].isdigit() else None)
                if not mid:
                    await context.bot.send_message(SOURCE_CHAT_ID, "Usa: /id <id> o responde a un mensaje con /id.")
                else:
                    mid = int(mid);  link = deep_link_for_channel_message(SOURCE_CHAT_ID, mid)
                    await context.bot.send_message(SOURCE_CHAT_ID, f"🆔 {mid}\n• Enlace: {link}")
            await _delete_user_command_if_possible(update, context);  return
        if low.startswith(("/canales", "/targets", "/where")):
            await context.bot.send_message(SOURCE_CHAT_ID, text_settings(), reply_markup=kb_settings(), parse_mode="Markdown")
            await _delete_user_command_if_possible(update, context);  return
        if low.startswith("/backup"):
            parts = txt.split(maxsplit=1);  arg = parts[1] if len(parts) > 1 else ""
            await _cmd_backup(context, arg);  await _delete_user_command_if_possible(update, context);  return
        if low.startswith(("/comandos", "/comando", "/ayuda", "/start")):
            await context.bot.send_message(SOURCE_CHAT_ID, text_main(), reply_markup=kb_main())
            await _delete_user_command_if_possible(update, context);  return
        await context.bot.send_message(SOURCE_CHAT_ID, "Comando no reconocido. Usa /comandos.")
        await _delete_user_command_if_possible(update, context);  return

    # NO COMANDO → ¿atajo @@@ o borrador normal?
    sc = parse_shortcut_line(txt)
    if sc:
        # borrar del canal para que no quede basura
        try:
            await context.bot.delete_message(chat_id=SOURCE_CHAT_ID, message_id=msg.message_id)
        except TelegramError:
            pass
        # guardamos un draft "especial" con payload shortcut
        raw_json = json.dumps({"shortcut": sc}, ensure_ascii=False)
        save_draft(msg.message_id, f"@@@ {sc['label']}", raw_json)
        logger.info(f"Guardado atajo @@@: {msg.message_id}")
        return

    # Draft normal
    snippet = msg.text or msg.caption or ""
    raw_json = json.dumps(msg.to_dict(), ensure_ascii=False)
    save_draft(msg.message_id, snippet, raw_json)
    logger.info(f"Guardado en borrador: {msg.message_id}")

# -------- main --------
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel))
    app.add_handler(CallbackQueryHandler(handle_callback))
    logger.info("Bot iniciado 🚀 Escuchando channel_post en el BORRADOR.")
    app.run_polling(allowed_updates=["channel_post", "callback_query"], drop_pending_updates=True)

if __name__ == "__main__":
    main()
