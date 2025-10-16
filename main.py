# -*- coding: utf-8 -*-
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional, Set

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, ContextTypes, CallbackQueryHandler, PollHandler, PollAnswerHandler, filters
from telegram.error import TelegramError

from config import (
    BOT_TOKEN, DB_FILE, TZNAME, TZ,
    SOURCE_CHAT_ID, TARGET_CHAT_ID, PREVIEW_CHAT_ID, BACKUP_CHAT_ID
)
from database import (
    init_db, save_draft, get_unsent_drafts, list_drafts,
    add_button, clear_buttons
)
from publisher import publicar_todo_activos, publicar_ids, get_active_targets, STATS, SCHEDULED_LOCK
from publisher import handle_poll_update, handle_poll_answer_update, detect_voted_polls_on_save
from scheduler import schedule_ids, cmd_programar, cmd_programados, cmd_desprogramar, SCHEDULES
from core_utils import temp_notice, extract_id_from_text, deep_link_for_channel_message, parse_shortcut_line

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

init_db(DB_FILE)
logger.info(
    f"SQLite listo. BORRADOR={SOURCE_CHAT_ID}  PRINCIPAL={TARGET_CHAT_ID}  "
    f"BACKUP={BACKUP_CHAT_ID} (SIEMPRE ON)  PREVIEW={PREVIEW_CHAT_ID}  TZ={TZNAME}"
)

def _is_command_text(txt: Optional[str]) -> bool:
    return bool(txt and txt.strip().startswith("/"))

async def _delete_user_command_if_possible(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update and update.channel_post:
            await context.bot.delete_message(chat_id=SOURCE_CHAT_ID, message_id=update.channel_post.message_id)
    except TelegramError:
        pass

def parse_nuke_args(arg: str, drafts_count: int) -> Set[int]:
    from typing import Set
    arg = arg.strip().lower()
    result: Set[int] = set()
    drafts = list_drafts(DB_FILE)
    ids_in_order = [did for (did, _snip) in drafts]
    
    if not arg or not drafts:
        return result
    
    if arg in ("all", "todos"):
        return set(ids_in_order)
    
    last_match = re.match(r'^(?:last|l|últimos?|u)(\d+)$', arg)
    if last_match:
        n = int(last_match.group(1))
        return set(ids_in_order[-n:]) if n > 0 else set()
    
    if ',' in arg or '-' in arg:
        parts = arg.replace(' ', '').split(',')
        for part in parts:
            if '-' in part and part.count('-') == 1:
                try:
                    start, end = map(int, part.split('-'))
                    for pos in range(start, end + 1):
                        if 0 < pos <= len(ids_in_order):
                            result.add(ids_in_order[pos - 1])
                except:
                    pass
            else:
                try:
                    pos = int(part)
                    if 0 < pos <= len(ids_in_order):
                        result.add(ids_in_order[pos - 1])
                except:
                    pass
    else:
        try:
            pos = int(arg)
            if 0 < pos <= len(ids_in_order):
                result.add(ids_in_order[pos - 1])
        except:
            pass
    
    return result

async def _cmd_listar(context: ContextTypes.DEFAULT_TYPE):
    drafts_all = list_drafts(DB_FILE)
    drafts = [(did, snip) for (did, snip) in drafts_all if did not in SCHEDULED_LOCK]

    if not drafts:
        out = ["📭 **Sin borradores pendientes**"]
    else:
        out = ["📋 **Borradores pendientes:**"]
        for i, (did, snip) in enumerate(drafts, start=1):
            s = (snip or "").strip()
            if len(s) > 60:
                s = s[:60] + "…"
            out.append(f"• {i:>2} — {s or '[contenido]'}  (id:{did})")

    if not SCHEDULES:
        out.append("\n⏰ **Sin programaciones activas**")
    else:
        now = datetime.now(tz=TZ)
        out.append("\n🗓️ **Programaciones activas:**")
        for pid, rec in sorted(SCHEDULES.items()):
            when = rec["when"].astimezone(TZ).strftime("%Y-%m-%d %H:%M")
            ids = rec["ids"]
            out.append(f"• #{pid} — {when} ({TZNAME}) — {len(ids)} mensajes")

    await context.bot.send_message(SOURCE_CHAT_ID, "\n".join(out), parse_mode="Markdown")

async def _cmd_nuke(context: ContextTypes.DEFAULT_TYPE, arg: str):
    drafts = list_drafts(DB_FILE)
    if not drafts:
        await context.bot.send_message(SOURCE_CHAT_ID, "No hay pendientes.")
        return

    victims = parse_nuke_args(arg, len(drafts))
    
    if not victims:
        await context.bot.send_message(
            SOURCE_CHAT_ID,
            "Usa: /nuke all | /nuke 5 | /nuke last5 | /nuke 1,3,5 | /nuke 1-5"
        )
        return

    borrados = 0
    import sqlite3
    for mid in sorted(victims, reverse=True):
        try:
            await context.bot.delete_message(chat_id=SOURCE_CHAT_ID, message_id=mid)
        except TelegramError as e:
            logger.warning(f"No pude borrar del canal id:{mid} → {e}")
        
        try:
            con = sqlite3.connect(DB_FILE)
            cur = con.cursor()
            cur.execute("DELETE FROM drafts WHERE message_id = ?", (mid,))
            con.commit()
            con.close()
        except:
            pass
        
        SCHEDULED_LOCK.discard(mid)
        borrados += 1

    STATS["eliminados"] += borrados
    restantes = len(list_drafts(DB_FILE))
    await context.bot.send_message(SOURCE_CHAT_ID, f"💣 Nuke: {borrados} borrados. Quedan {restantes} en la cola.")

async def _cmd_preview(context: ContextTypes.DEFAULT_TYPE):
    rows_full = get_unsent_drafts(DB_FILE)
    rows = [(m, t, r) for (m, t, r) in rows_full if m not in SCHEDULED_LOCK]
    if not rows:
        await temp_notice(context.bot, "🧪 Preview: 0 mensajes.", ttl=4)
        return
    ids = [m for (m, _t, _r) in rows]
    pubs, fails, _ = await publicar_ids(context, ids=ids, targets=[PREVIEW_CHAT_ID], mark_as_sent=False)
    await context.bot.send_message(SOURCE_CHAT_ID, f"🧪 Preview: enviados {pubs}, fallidos {fails}.")

def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 Listar", callback_data="m:list"),
             InlineKeyboardButton("📦 Enviar", callback_data="m:send")],
            [InlineKeyboardButton("🧪 Preview", callback_data="m:preview"),
             InlineKeyboardButton("⏰ Programar", callback_data="m:sched")],
            [InlineKeyboardButton("📊 Estado", callback_data="m:status")]
        ]
    )

def text_main() -> str:
    return (
        "🛠️ **Acciones rápidas:**\n"
        "• `/listar` — muestra borradores pendientes\n"
        "• `/enviar` — publica ahora a Principal + Backup\n"
        "• `/preview` — envía a PREVIEW sin marcar enviada\n"
        "• `/programar` YYYY-MM-DD HH:MM — programa envío\n"
        "• `/programados` — ver pendientes programados\n"
        "• `/desprogramar` <id|all> — cancela programación\n"
        "• `/id` — muestra ID del mensaje\n"
        "• `/canales` — ver estado de canales\n"
        "\n💣 **Comando /nuke:**\n"
        "• `/nuke` 5 — elimina el mensaje #5\n"
        "• `/nuke` last5 — elimina los últimos 5\n"
        "• `/nuke` 1,3,5 — elimina posiciones específicas\n"
        "• `/nuke` 1-5 — elimina rango\n"
        "• `/nuke` all — elimina todos\n"
        "\n📚 **Links de justificación:**\n"
        "• Envía: `CASO #X https://t.me/ccjustificaciones/ID`\n"
        "• Se convierte en botón: `Ver justificación CASO #X`\n"
        "• Soporta múltiples: `ID,ID,ID` o `ID-ID`\n"
        "\n🔘 **Botones personalizados:**\n"
        "• `@@@ Texto | URL` — agrega botón al último borrador\n"
        "\n📝 Pulsa un botón o usa `/comandos` para ver este panel."
    )

def kb_schedule() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⏳ +5 min", callback_data="s:+5"),
             InlineKeyboardButton("⏳ +15 min", callback_data="s:+15")],
            [InlineKeyboardButton("🕗 Hoy 20:00", callback_data="s:today20"),
             InlineKeyboardButton("🌅 Mañana 07:00", callback_data="s:tom07")],
            [InlineKeyboardButton("🗒 Ver programados", callback_data="s:list"),
             InlineKeyboardButton("⌫ Cancelar todos", callback_data="s:clear")],
            [InlineKeyboardButton("✏️ Custom", callback_data="s:custom"),
             InlineKeyboardButton("⬅️ Volver", callback_data="m:back")]
        ]
    )

def text_schedule() -> str:
    return (
        "⏰ **Programar envío** de los borradores actuales.\n"
        "Elige un atajo o usa `/programar YYYY-MM-DD HH:MM`\n"
        "Formato 24h (00:00-23:59)"
    )

def text_status() -> str:
    justifications_info = ""
    try:
        from justifications_handler import JUSTIFICATIONS_CHAT_ID
        justifications_info = f"• **Justificaciones:** `{JUSTIFICATIONS_CHAT_ID}` 📚\n"
    except ImportError:
        pass
    
    return (
        f"📡 **Estado de Canales**\n\n"
        f"• **Principal:** `{TARGET_CHAT_ID}` ✅\n"
        f"• **Backup:** `{BACKUP_CHAT_ID}` ✅\n"
        f"• **Preview:** `{PREVIEW_CHAT_ID}`\n"
        f"{justifications_info}"
        f"\n💡 Todos los canales están configurados y activos."
    )

def kb_status() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Volver", callback_data="m:back")]
    ])

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
            if STATS["eliminados"]:
                extras.append(f"Eliminados: {STATS['eliminados']}")
            msg_out = f"✅ Publicados {ok}."
            if fail:
                extras.append(f"Fallidos: {fail}")
            if extras:
                msg_out += "\n📦 " + " · ".join(extras) + "."
            await context.bot.send_message(SOURCE_CHAT_ID, msg_out)
            STATS["eliminados"] = 0
        
        elif data == "m:preview":
            await _cmd_preview(context)
        
        elif data == "m:sched":
            await q.edit_message_text(text_schedule(), reply_markup=kb_schedule(), parse_mode="Markdown")
        
        elif data == "m:status":
            try:
                await q.edit_message_text(text_status(), reply_markup=kb_status(), parse_mode="Markdown")
            except TelegramError as e:
                if "Message is not modified" in str(e):
                    pass
                else:
                    raise
        
        elif data == "m:back":
            await q.edit_message_text(text_main(), reply_markup=kb_main(), parse_mode="Markdown")
        
        elif data == "m:settings":
            try:
                await q.edit_message_text(text_status(), reply_markup=kb_status(), parse_mode="Markdown")
            except TelegramError as e:
                if "Message is not modified" in str(e):
                    pass
                else:
                    raise
        
        elif data == "m:toggle_backup":
            await q.answer("⚠️ El backup está siempre activo por seguridad", show_alert=True)
            try:
                await q.edit_message_text(text_status(), reply_markup=kb_status(), parse_mode="Markdown")
            except TelegramError:
                pass
        
        elif data.startswith("s:"):
            if data == "s:custom":
                custom_kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ Volver", callback_data="m:sched")
                ]])
                await q.edit_message_text(
                    "✏️ **Formato manual:**\n"
                    "`/programar YYYY-MM-DD HH:MM`\n"
                    "Ejemplo: `/programar 2024-12-25 18:00`\n"
                    "\nUsa formato 24 horas (00:00-23:59)",
                    parse_mode="Markdown",
                    reply_markup=custom_kb
                )
            elif data == "s:list":
                await cmd_programados(context)
            elif data == "s:clear":
                await cmd_desprogramar(context, "all")
            else:
                now = datetime.now(tz=TZ)
                when = None
                
                if data == "s:+5":
                    when = now + timedelta(minutes=5)
                elif data == "s:+15":
                    when = now + timedelta(minutes=15)
                elif data == "s:today20":
                    when = now.replace(hour=20, minute=0, second=0, microsecond=0)
                    if when <= now:
                        when = when + timedelta(days=1)
                elif data == "s:tom07":
                    when = (now + timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0)
                
                if when:
                    ids = [did for (did, _snip) in list_drafts(DB_FILE)]
                    if not ids:
                        await temp_notice(context.bot, "🔭 No hay borradores para programar.", ttl=6)
                    else:
                        await schedule_ids(context, when, ids)
    
    except Exception as e:
        if "Message is not modified" not in str(e):
            logger.exception(f"Error en callback: {e}")

async def handle_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg:
        return
    if msg.chat_id != SOURCE_CHAT_ID:
        return

    txt = (msg.text or "").strip()

    if _is_command_text(txt):
        low = txt.lower()

        if low.startswith(("/listar", "/lista")):
            await _cmd_listar(context)
            await _delete_user_command_if_possible(update, context)
            return

        if low.startswith("/nuke"):
            parts = txt.split(maxsplit=1)
            arg = parts[1] if len(parts) > 1 else ""
            await _cmd_nuke(context, arg)
            await _delete_user_command_if_possible(update, context)
            return

        if low.startswith("/enviar"):
            await temp_notice(context.bot, "⏳ Procesando envío…", ttl=4)
            ok, fail = await publicar_todo_activos(context)
            msg_out = f"✅ Publicados {ok}."
            if fail:
                msg_out += f" Fallidos: {fail}."
            if STATS["eliminados"]:
                msg_out += f" Eliminados previos: {STATS['eliminados']}."
            await context.bot.send_message(SOURCE_CHAT_ID, msg_out)
            STATS["eliminados"] = 0
            await _delete_user_command_if_possible(update, context)
            return

        if low.startswith("/preview"):
            await _cmd_preview(context)
            await _delete_user_command_if_possible(update, context)
            return

        if low.startswith("/programar"):
            parts = txt.split(maxsplit=2)
            if len(parts) >= 3:
                when_str = f"{parts[1]} {parts[2]}"
                await cmd_programar(context, when_str)
            else:
                await context.bot.send_message(
                    SOURCE_CHAT_ID,
                    "Usa: `/programar YYYY-MM-DD HH:MM` (formato 24h)",
                    parse_mode="Markdown"
                )
            await _delete_user_command_if_possible(update, context)
            return

        if low.startswith("/programados"):
            await cmd_programados(context)
            await _delete_user_command_if_possible(update, context)
            return

        if low.startswith("/desprogramar"):
            parts = txt.split(maxsplit=1)
            arg = parts[1] if len(parts) > 1 else ""
            await cmd_desprogramar(context, arg)
            await _delete_user_command_if_possible(update, context)
            return

        if low.startswith("/id"):
            if update.channel_post and update.channel_post.reply_to_message and len(txt.split()) == 1:
                rid = update.channel_post.reply_to_message.message_id
                await context.bot.send_message(SOURCE_CHAT_ID, f"🆔 ID del mensaje: {rid}")
            else:
                mid = extract_id_from_text(txt)
                if not mid:
                    await context.bot.send_message(SOURCE_CHAT_ID, "Usa: /id <id> o responde con /id")
                else:
                    link = deep_link_for_channel_message(SOURCE_CHAT_ID, int(mid))
                    await context.bot.send_message(SOURCE_CHAT_ID, f"🆔 {mid}\n• Enlace: {link}")
            await _delete_user_command_if_possible(update, context)
            return

        if low.startswith(("/canales", "/targets")):
            await context.bot.send_message(SOURCE_CHAT_ID, text_status(), reply_markup=kb_main(), parse_mode="Markdown")
            await _delete_user_command_if_possible(update, context)
            return

        if low.startswith("/test_just"):
            parts = txt.split(maxsplit=1)
            if len(parts) < 2:
                await context.bot.send_message(SOURCE_CHAT_ID, "Uso: /test_just <id> o /test_just <id1,id2,id3>")
            else:
                try:
                    from justifications_handler import cmd_test_justification
                    await cmd_test_justification(update, context)
                except ImportError:
                    await context.bot.send_message(SOURCE_CHAT_ID, "❌ Módulo de justificaciones no disponible")
            await _delete_user_command_if_possible(update, context)
            return

        if low.startswith(("/comandos", "/ayuda", "/start")):
            await context.bot.send_message(SOURCE_CHAT_ID, text_main(), reply_markup=kb_main(), parse_mode="Markdown")
            await _delete_user_command_if_possible(update, context)
            return

        await context.bot.send_message(SOURCE_CHAT_ID, "Comando no reconocido. Usa /comandos")
        await _delete_user_command_if_possible(update, context)
        return

    shortcut_info = parse_shortcut_line(txt)
    if shortcut_info:
        label = shortcut_info["label"]
        url = shortcut_info["url"]
        
        drafts = list_drafts(DB_FILE)
        if drafts:
            last_draft_id = drafts[-1][0]
            clear_buttons(DB_FILE, last_draft_id)
            add_button(DB_FILE, last_draft_id, label, url)
            
            await temp_notice(
                context.bot, 
                f"✅ Botón '{label}' agregado al último borrador", 
                ttl=5
            )
            
            try:
                await context.bot.delete_message(
                    chat_id=SOURCE_CHAT_ID, 
                    message_id=msg.message_id
                )
            except:
                pass
        else:
            await temp_notice(context.bot, "⚠️ No hay borradores para agregar el botón", ttl=5)
        
        return

    snippet = msg.text or msg.caption or ""
    raw_json = json.dumps(msg.to_dict(), ensure_ascii=False)
    save_draft(DB_FILE, msg.message_id, snippet, raw_json)
    detect_voted_polls_on_save(msg.message_id, raw_json)
    logger.info(f"Guardado en borrador: {msg.message_id}")

async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para mensajes privados (fuera del canal)."""
    if not update.message:
        return
    
    # Ignorar si es del canal
    if update.message.chat.type in ['channel', 'supergroup', 'group']:
        return
    
    txt = update.message.text or ""
    
    # Comando /start en privado
    if txt.strip().lower().startswith("/start"):
        # Verificar si es un deep link de justificación
        if "just_" in txt:
            # Delegar a justifications_handler
            return
        
        # Menú principal con botones estilo Combot
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📚 Casos Aleatorios", callback_data="menu_casos")],
            [InlineKeyboardButton("ℹ️ Acerca de", callback_data="menu_about")],
            [InlineKeyboardButton("📋 Comandos", callback_data="menu_commands")]
        ])
        
        welcome_text = (
            "¡Bienvenido al Bot de Casos Clínicos! 🏥\n\n"
            "Practica con casos clínicos aleatorios del banco de preguntas.\n\n"
            "Selecciona una opción:"
        )
        
        await update.message.reply_text(
            welcome_text,
            reply_markup=keyboard
        )
        return
    
    # Otros comandos privados
    if txt.strip().startswith("/"):
        # Ya manejados por otros handlers
        return

async def handle_menu_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para los botones del menú principal."""
    query = update.callback_query
    if not query or not query.data.startswith("menu_"):
        return
    
    await query.answer()
    
    if query.data == "menu_casos":
        await query.edit_message_text(
            "📚 **Casos Aleatorios**\n\n"
            "Usa el comando:\n"
            "`/casos [cantidad]`\n\n"
            "Ejemplos:\n"
            "• `/casos` - 5 casos (default)\n"
            "• `/casos 10` - 10 casos aleatorios\n"
            "• `/casos 20` - 20 casos (máximo)\n\n"
            "El bot te enviará casos con botones A, B, C, D.\n"
            "Responde cada uno y recibirás la justificación.",
            parse_mode="Markdown"
        )
    
    elif query.data == "menu_about":
        await query.edit_message_text(
            "ℹ️ **Acerca del Bot**\n\n"
            "Bot de Casos Clínicos para preparación de exámenes médicos.\n\n"
            "• Casos aleatorios con opciones múltiples\n"
            "• Justificaciones automáticas\n"
            "• Estadísticas de respuestas\n"
            "• Sistema de tracking por usuario\n\n"
            "Desarrollado para estudiantes de medicina."
        )
    
    elif query.data == "menu_commands":
        await query.edit_message_text(
            "📋 **Comandos Disponibles**\n\n"
            "**Usuario:**\n"
            "• `/start` - Menú principal\n"
            "• `/casos [N]` - N casos aleatorios (1-20)\n"
            "• `/qbank [N]` - Alias de /casos\n\n"
            "**Administrador:**\n"
            "• Ver panel de admin en el canal BORRADOR",
            parse_mode="Markdown"
        )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Excepción no capturada", exc_info=context.error)

async def _set_bot_commands(app: Application):
    try:
        await app.bot.set_my_commands([
            ("comandos", "Ver ayuda y botones"),
            ("listar", "Mostrar borradores pendientes"),
            ("enviar", "Publicar ahora"),
            ("preview", "Enviar a PREVIEW"),
            ("programar", "Programar envío"),
            ("programados", "Ver programaciones"),
            ("desprogramar", "Cancelar programación"),
            ("nuke", "Eliminar mensajes"),
            ("id", "Mostrar ID del mensaje"),
            ("canales", "Ver estado de canales"),
        ])
    except Exception:
        pass

def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(PollHandler(handle_poll_update))
    app.add_handler(PollAnswerHandler(handle_poll_answer_update))
    
    # Command handler para /start en privado
    app.add_handler(CommandHandler("start", handle_private_message, filters=filters.ChatType.PRIVATE))
    
    try:
        from justifications_handler import add_justification_handlers
        add_justification_handlers(app)
        logger.info("✅ Sistema de justificaciones activado")
    except ImportError:
        logger.warning("⚠️ Módulo de justificaciones no encontrado")
    
    try:
        from qbank_handler import add_qbank_handlers
        add_qbank_handlers(app)
        logger.info("✅ Sistema QBank activado")
    except ImportError:
        logger.warning("⚠️ Módulo QBank no encontrado")
    
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel))
    
    # Handler para mensajes privados
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
        handle_private_message
    ))
    
    # Handler para botones del menú
    app.add_handler(CallbackQueryHandler(handle_menu_callbacks, pattern=r"^menu_"))
    
    # Handler general de callbacks (debe ir después de los específicos)
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_error_handler(on_error)

    logger.info("🚀 Bot iniciado! Escuchando en el canal BORRADOR.")
    logger.info(f"✅ Backup siempre activo en: {BACKUP_CHAT_ID}")

    app.post_init = _set_bot_commands

    app.run_polling(
        allowed_updates=["channel_post", "callback_query", "poll", "poll_answer", "message"], 
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
