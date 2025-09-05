# -*- coding: utf-8 -*-
# Bot principal - VERSIÓN CORREGIDA

import json
import logging
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
    init_db, save_draft, list_drafts
)
from publisher import publicar_todo_activos, publicar_ids, get_active_targets, STATS, SCHEDULED_LOCK
from publisher import handle_poll_update, handle_poll_answer_update, detect_voted_polls_on_save
from scheduler import schedule_ids, cmd_programar, cmd_programados, cmd_desprogramar, SCHEDULES
from core_utils import temp_notice, extract_id_from_text, deep_link_for_channel_message, human_eta

# ========= LOGGING =========
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ========= DB =========
DB_FILE = DB_FILE or "drafts.db"
init_db(DB_FILE)
logger.info(f"SQLite listo. BORRADOR={SOURCE_CHAT_ID}  PRINCIPAL={TARGET_CHAT_ID}  BACKUP={BACKUP_CHAT_ID}")

# ========= Helpers =========
def _is_command_text(txt: Optional[str]) -> bool:
    return bool(txt and txt.strip().startswith("/"))

async def _delete_user_command_if_possible(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Borra el mensaje de comando del canal si es posible."""
    try:
        if update and update.channel_post:
            await context.bot.delete_message(chat_id=SOURCE_CHAT_ID, message_id=update.channel_post.message_id)
    except TelegramError:
        pass

def parse_nuke_args(arg: str) -> Set[int]:
    """Parser para /nuke."""
    arg = arg.strip().lower()
    result: Set[int] = set()
    drafts = list_drafts(DB_FILE)
    ids_in_order = [did for (did, _snip) in drafts]
    
    if not arg or not drafts:
        return result
    
    if arg in ("all", "todos"):
        return set(ids_in_order)
    
    import re
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

# ========= Comandos =========
async def _cmd_listar(context: ContextTypes.DEFAULT_TYPE):
    """Lista borradores y programaciones."""
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
        out.append("\n🗓️ **Programaciones activas:**")
        for pid, rec in sorted(SCHEDULES.items()):
            when = rec["when"].astimezone(TZ).strftime("%Y-%m-%d %H:%M")
            ids = rec["ids"]
            out.append(f"• #{pid} — {when} ({TZNAME}) — {len(ids)} mensajes")

    await context.bot.send_message(SOURCE_CHAT_ID, "\n".join(out), parse_mode="Markdown")

async def _cmd_nuke(context: ContextTypes.DEFAULT_TYPE, arg: str):
    """Comando /nuke."""
    drafts = list_drafts(DB_FILE)
    if not drafts:
        await context.bot.send_message(SOURCE_CHAT_ID, "No hay pendientes.")
        return

    victims = parse_nuke_args(arg)
    
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
        except:
            pass
        
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
    await context.bot.send_message(SOURCE_CHAT_ID, f"💣 Nuke: {borrados} borrados. Quedan {restantes}.")

async def _cmd_preview(context: ContextTypes.DEFAULT_TYPE):
    """Preview sin marcar como enviada."""
    from database import get_unsent_drafts
    rows_full = get_unsent_drafts(DB_FILE)
    rows = [(m, t, r) for (m, t, r) in rows_full if m not in SCHEDULED_LOCK]
    if not rows:
        await temp_notice(context.bot, "🧪 Preview: 0 mensajes.", ttl=4)
        return
    ids = [m for (m, _t, _r) in rows]
    pubs, fails, _ = await publicar_ids(context, ids=ids, targets=[PREVIEW_CHAT_ID], mark_as_sent=False)
    await context.bot.send_message(SOURCE_CHAT_ID, f"🧪 Preview: enviados {pubs}, fallidos {fails}.")

# ========= UI/Menús =========
def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Listar", callback_data="m:list"),
         InlineKeyboardButton("📦 Enviar", callback_data="m:send")],
        [InlineKeyboardButton("🧪 Preview", callback_data="m:preview"),
         InlineKeyboardButton("⏰ Programar", callback_data="m:sched")],
        [InlineKeyboardButton("📊 Estado", callback_data="m:status")]
    ])

def text_main() -> str:
    return (
        "🛠️ **Comandos disponibles:**\n\n"
        "📋 **Gestión de borradores:**\n"
        "• /listar — muestra borradores pendientes\n"
        "• /enviar — publica ahora en Principal + Backup\n"
        "• /preview — envía a PREVIEW sin marcar enviada\n"
        "• /nuke — elimina mensajes (all, 5, last5, 1-5, 1,3,5)\n\n"
        "⏰ **Programación:**\n"
        "• /programar YYYY-MM-DD HH:MM — programa envío\n"
        "• /programados — ver programaciones activas\n"
        "• /desprogramar id o /desprogramar all — cancela\n\n"
        "📚 **Justificaciones:**\n"
        "• Los enlaces se convierten automáticamente\n"
        "• Redirigen al bot @clinicase_bot\n"  # CORREGIDO
        "• /test_just id — probar justificación\n\n"
        "🔘 **Otros:**\n"
        "• /id — muestra ID del mensaje\n"
        "• /canales — ver estado de canales\n"
        "• /comandos o /ayuda — muestra este menú"
    )

def kb_schedule() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏳ +5 min", callback_data="s:+5"),
         InlineKeyboardButton("⏳ +15 min", callback_data="s:+15")],
        [InlineKeyboardButton("🕗 Hoy 20:00", callback_data="s:today20"),
         InlineKeyboardButton("🌅 Mañana 07:00", callback_data="s:tom07")],
        [InlineKeyboardButton("🗒 Ver programados", callback_data="s:list"),
         InlineKeyboardButton("⌫ Cancelar todos", callback_data="s:clear")],
        [InlineKeyboardButton("✏️ Custom", callback_data="s:custom"),
         InlineKeyboardButton("⬅️ Volver", callback_data="m:back")]
    ])

def text_schedule() -> str:
    return (
        "⏰ **Programar envío**\n\n"
        "Elige un atajo o usa formato manual:\n"
        "/programar YYYY-MM-DD HH:MM\n\n"
        "Formato 24h (00:00-23:59)"
    )

def kb_status() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Volver", callback_data="m:back")]
    ])

def text_status() -> str:
    return (
        f"📡 **Estado de Canales**\n\n"
        f"• **Principal:** {TARGET_CHAT_ID} ✅\n"
        f"• **Backup:** {BACKUP_CHAT_ID} ✅\n"
        f"• **Preview:** {PREVIEW_CHAT_ID} 👁️\n"
        f"• **Borrador:** {SOURCE_CHAT_ID} 📝\n\n"
        f"📚 **Bot de Justificaciones:**\n"
        f"• @clinicase_bot ✅\n"
        f"• Solo responde a deep links\n"
        f"• Auto-elimina en 10 minutos\n\n"
        f"💡 Sistema funcionando correctamente"
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja callbacks de botones."""
    q = update.callback_query
    if not q:
        return
    
    await q.answer()
    data = q.data or ""
    
    try:
        # Menú principal
        if data == "m:list":
            await _cmd_listar(context)
        
        elif data == "m:send":
            await temp_notice(context.bot, "⏳ Procesando envío…", ttl=4)
            ok, fail = await publicar_todo_activos(context)
            msg_out = f"✅ Publicados {ok}."
            if fail:
                msg_out += f" Fallidos: {fail}."
            if STATS.get("eliminados"):
                msg_out += f" Eliminados previos: {STATS['eliminados']}."
            await context.bot.send_message(SOURCE_CHAT_ID, msg_out)
            STATS["eliminados"] = 0
        
        elif data == "m:preview":
            await _cmd_preview(context)
        
        elif data == "m:sched":
            await q.edit_message_text(text_schedule(), reply_markup=kb_schedule(), parse_mode="Markdown")
        
        elif data == "m:status":
            await q.edit_message_text(text_status(), reply_markup=kb_status(), parse_mode="Markdown")
        
        elif data == "m:back":
            await q.edit_message_text(text_main(), reply_markup=kb_main(), parse_mode="Markdown")
        
        # Programación
        elif data.startswith("s:"):
            if data == "s:custom":
                custom_kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ Volver", callback_data="m:sched")
                ]])
                custom_text = (
                    "✏️ **Formato manual:**\n\n"
                    "/programar YYYY-MM-DD HH:MM\n\n"
                    "Ejemplo: /programar 2024-12-25 18:00\n"
                    "Formato 24 horas (00:00-23:59)"
                )
                await q.edit_message_text(
                    custom_text,
                    parse_mode="Markdown",
                    reply_markup=custom_kb
                )
            
            elif data == "s:list":
                await cmd_programados(context)
            
            elif data == "s:clear":
                await cmd_desprogramar(context, "all")
            
            else:
                # Atajos de tiempo - ENVIAR MENSAJE NUEVO, NO EDITAR
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
                    ids = [did for (did, _) in list_drafts(DB_FILE)]
                    if ids:
                        await schedule_ids(context, when, ids)
                        # ENVIAR MENSAJE NUEVO EN LUGAR DE EDITAR
                        when_str = when.astimezone(TZ).strftime("%Y-%m-%d %H:%M")
                        eta = human_eta(when)
                        await context.bot.send_message(
                            SOURCE_CHAT_ID,
                            f"✅ **Programación creada**\n\n"
                            f"📅 Fecha: {when_str} ({TZNAME})\n"
                            f"⏰ {eta}\n"
                            f"📦 Mensajes: {len(ids)}",
                            parse_mode="Markdown"
                        )
                    else:
                        await q.answer("No hay borradores para programar", show_alert=True)
    
    except TelegramError as e:
        if "Message is not modified" not in str(e):
            logger.error(f"Error en callback: {e}")
    except Exception as e:
        logger.error(f"Error inesperado en callback: {e}")

# ========= Handler principal del canal =========
async def handle_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja mensajes del canal BORRADOR."""
    logger.info("Inside handle_channel")
    msg = update.channel_post
    if not msg or msg.chat_id != SOURCE_CHAT_ID:
        return

    txt = (msg.text or "").strip()

    # ========= COMANDOS =========
    if _is_command_text(txt):
        parts = txt.strip().split()
        cmd = parts[0].lower() if parts else ""
        
        # COMANDOS PRINCIPALES
        if cmd in ["/comandos", "/ayuda", "/help", "/start"]:
            await context.bot.send_message(
                SOURCE_CHAT_ID, 
                text_main(), 
                reply_markup=kb_main(), 
                parse_mode="Markdown"
            )
            await _delete_user_command_if_possible(update, context)
            return

        if cmd in ["/listar", "/lista", "/list"]:
            await _cmd_listar(context)
            await _delete_user_command_if_possible(update, context)
            return

        if cmd == "/nuke":
            arg = txt[5:].strip() if len(txt) > 5 else ""
            await _cmd_nuke(context, arg)
            await _delete_user_command_if_possible(update, context)
            return

        if cmd in ["/enviar", "/send"]:
            await temp_notice(context.bot, "⏳ Procesando envío…", ttl=4)
            ok, fail = await publicar_todo_activos(context)
            msg_out = f"✅ Publicados {ok}."
            if fail:
                msg_out += f" Fallidos: {fail}."
            if STATS.get("eliminados"):
                msg_out += f" Eliminados previos: {STATS['eliminados']}."
            await context.bot.send_message(SOURCE_CHAT_ID, msg_out)
            STATS["eliminados"] = 0
            await _delete_user_command_if_possible(update, context)
            return

        if cmd == "/preview":
            await _cmd_preview(context)
            await _delete_user_command_if_possible(update, context)
            return

        if cmd == "/programar":
            parts = txt.split(maxsplit=2)
            if len(parts) >= 3:
                when_str = f"{parts[1]} {parts[2]}"
                await cmd_programar(context, when_str)
            else:
                error_text = (
                    "❌ **Formato incorrecto**\n\n"
                    "Usa: /programar YYYY-MM-DD HH:MM\n"
                    "Ejemplo: /programar 2024-12-25 18:00\n\n"
                    "Formato 24 horas (00:00-23:59)"
                )
                await context.bot.send_message(
                    SOURCE_CHAT_ID,
                    error_text,
                    parse_mode="Markdown"
                )
            await _delete_user_command_if_possible(update, context)
            return

        if cmd == "/programados":
            await cmd_programados(context)
            await _delete_user_command_if_possible(update, context)
            return

        if cmd == "/desprogramar":
            arg = txt[13:].strip() if len(txt) > 13 else ""
            await cmd_desprogramar(context, arg)
            await _delete_user_command_if_possible(update, context)
            return

        if cmd == "/id":
            if update.channel_post and update.channel_post.reply_to_message and len(parts) == 1:
                rid = update.channel_post.reply_to_message.message_id
                link = deep_link_for_channel_message(SOURCE_CHAT_ID, rid)
                await context.bot.send_message(
                    SOURCE_CHAT_ID, 
                    f"🆔 ID: {rid}\n🔗 Link: {link}",
                    parse_mode="Markdown"
                )
            else:
                mid = extract_id_from_text(txt)
                if mid:
                    link = deep_link_for_channel_message(SOURCE_CHAT_ID, int(mid))
                    await context.bot.send_message(
                        SOURCE_CHAT_ID, 
                        f"🆔 ID: {mid}\n🔗 Link: {link}",
                        parse_mode="Markdown"
                    )
                else:
                    await context.bot.send_message(
                        SOURCE_CHAT_ID, 
                        "Usa: /id numero o responde a un mensaje con /id",
                        parse_mode="Markdown"
                    )
            await _delete_user_command_if_possible(update, context)
            return

        if cmd in ["/canales", "/channels", "/targets"]:
            await context.bot.send_message(
                SOURCE_CHAT_ID, 
                text_status(), 
                reply_markup=kb_status(), 
                parse_mode="Markdown"
            )
            await _delete_user_command_if_possible(update, context)
            return

        if cmd == "/test_just":
            # CORREGIDO para funcionar correctamente
            args = txt.split()[1:] if len(txt.split()) > 1 else []
            if not args:
                await context.bot.send_message(
                    SOURCE_CHAT_ID,
                    "❌ Uso: /test_just id\nEjemplo: /test_just 123",
                    parse_mode="Markdown"
                )
            else:
                try:
                    msg_id = int(args[0])
                    # Crear link de justificación de prueba
                    test_text = f"🧪 **PRUEBA DE JUSTIFICACIÓN**\n\n"
                    test_text += f"CASO #TEST https://t.me/ccjustificaciones/{msg_id}"
                    
                    # Procesar el texto para convertirlo
                    from justifications_handler import process_justification_links
                    cleaned_text, buttons = process_justification_links(test_text)
                    
                    if buttons:
                        from telegram import InlineKeyboardMarkup
                        keyboard = InlineKeyboardMarkup([[b] for b in buttons])
                        await context.bot.send_message(
                            SOURCE_CHAT_ID,
                            "🧪 **Test de justificación**\n\nHaz clic en el botón para recibir la justificación:",
                            reply_markup=keyboard,
                            parse_mode="Markdown"
                        )
                    else:
                        await context.bot.send_message(
                            SOURCE_CHAT_ID,
                            "❌ No se pudo generar el botón de justificación",
                            parse_mode="Markdown"
                        )
                except ValueError:
                    await context.bot.send_message(
                        SOURCE_CHAT_ID,
                        "❌ ID inválido. Usa un número: /test_just 123",
                        parse_mode="Markdown"
                    )
                except ImportError:
                    await context.bot.send_message(
                        SOURCE_CHAT_ID,
                        "❌ Módulo de justificaciones no disponible",
                        parse_mode="Markdown"
                    )
            await _delete_user_command_if_possible(update, context)
            return

        # Comando no reconocido
        await context.bot.send_message(
            SOURCE_CHAT_ID, 
            "❌ Comando no reconocido. Usa /comandos para ver la lista",
            parse_mode="Markdown"
        )
        await _delete_user_command_if_possible(update, context)
        return

    # ========= GUARDAR BORRADOR (sin @@@ ya que fue eliminado) =========
    snippet = msg.text or msg.caption or ""
    raw_json = msg.to_dict()
    if msg.poll:
        logger.info(f"Poll: {msg.poll}")
        raw_json["poll"] = msg.poll.to_dict()
    raw_json = json.dumps(raw_json, ensure_ascii=False)    
    save_draft(DB_FILE, msg.message_id, snippet, raw_json)
    detect_voted_polls_on_save(msg.message_id, raw_json)
    logger.info(f"Guardado en borrador: {msg.message_id}")

# ========= ERROR HANDLER =========
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Error no capturado", exc_info=context.error)

# ========= CONFIGURAR COMANDOS DEL BOT =========
async def _set_bot_commands(app: Application):
    """Configura los comandos visibles del bot."""
    try:
        await app.bot.set_my_commands([
            ("comandos", "Ver menú completo"),
            ("listar", "Mostrar borradores"),
            ("enviar", "Publicar ahora"),
            ("preview", "Vista previa"),
            ("programar", "Programar envío"),
            ("programados", "Ver programaciones"),
            ("desprogramar", "Cancelar programación"),
            ("nuke", "Eliminar mensajes"),
            ("test_just", "Probar justificación"),
            ("id", "Ver ID de mensaje"),
            ("canales", "Estado de canales"),
        ])
        logger.info("✅ Comandos del bot configurados")
    except Exception as e:
        logger.error(f"Error configurando comandos: {e}")

# ========= MAIN =========
def main():
    """Función principal del bot."""
    app = Application.builder().token(BOT_TOKEN).build()

    # Handlers para encuestas
    
    
    # Handlers de justificaciones
    try:
        from justifications_handler import add_justification_handlers
        add_justification_handlers(app)
        logger.info("✅ Sistema de justificaciones activado")
    except ImportError:
        logger.warning("⚠️ Justificaciones no disponibles")
    
    # Handler principal del canal
    # Polls posted in a channel
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel))

    # Poll lifecycle updates (correct answer, closed, vote counts for anonymous polls)
    app.add_handler(PollHandler(handle_poll_update))

    # Non-anonymous poll answers (user votes)
    app.add_handler(PollAnswerHandler(handle_poll_answer_update))
    # Handler para callbacks (botones)
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    # Error handler
    app.add_error_handler(on_error)
    
    # Configurar comandos al iniciar
    app.post_init = _set_bot_commands

    logger.info("=" * 50)
    logger.info("🚀 Bot principal iniciado!")
    logger.info(f"📝 Canal BORRADOR: {SOURCE_CHAT_ID}")
    logger.info(f"📢 Canal PRINCIPAL: {TARGET_CHAT_ID}")
    logger.info(f"💾 Canal BACKUP: {BACKUP_CHAT_ID}")
    logger.info(f"👁️ Canal PREVIEW: {PREVIEW_CHAT_ID}")
    logger.info("📚 Justificaciones: @clinicase_bot")
    logger.info("=" * 50)

    app.run_polling(
        allowed_updates=["channel_post", "callback_query", "poll", "poll_answer"],
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
