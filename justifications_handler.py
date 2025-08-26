# =========================
# JUSTIFICACIONES (DEEP-LINK + COPIA PROTEGIDA)
# =========================
from typing import Optional, Tuple
import re
import logging
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, filters, CommandHandler

from config import JUSTIFICATIONS_CHAT_ID, AUTO_DELETE_MINUTES

logger = logging.getLogger(__name__)

# Cache opcional para estadísticas
sent_justifications = {}  # {(user_id, justification_id): {"sent_at": datetime}}

def _normalize_channel_clean_id(chat_id: int) -> str:
    """
    Para enlaces tipo t.me/c/<clean_id>/<msg_id>, Telegram usa el ID del canal sin el prefijo -100.
    """
    s = str(chat_id)
    if s.startswith("-100"):
        return s[4:]
    return s

def generate_justification_deep_link(bot_username: str, message_id: int) -> str:
    return f"https://t.me/{bot_username}?start=just_{message_id}"

def create_justification_button(bot_username: str, message_id: int) -> InlineKeyboardMarkup:
    url = generate_justification_deep_link(bot_username, message_id)
    kb = [[InlineKeyboardButton("Ver justificación 🔒", url=url)]]
    return InlineKeyboardMarkup(kb)

async def schedule_message_deletion(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    """
    Programa el borrado del mensaje en AUTO_DELETE_MINUTES, si está configurado > 0.
    """
    if AUTO_DELETE_MINUTES and AUTO_DELETE_MINUTES > 0:
        seconds = AUTO_DELETE_MINUTES * 60
        await context.job_queue.run_once(
            lambda ctx: ctx.bot.delete_message(chat_id, message_id),
            when=seconds,
            name=f"del_{chat_id}_{message_id}",
        )

async def send_protected_justification(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    justification_id: int,
) -> bool:
    """
    Copia la justificación desde el canal de repositorio al usuario final, con protección.
    """
    try:
        sent = await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=JUSTIFICATIONS_CHAT_ID,
            message_id=justification_id,
            protect_content=True,
        )
        await schedule_message_deletion(context, sent.chat.id, sent.message_id)
        # stats
        sent_justifications[(user_id, justification_id)] = {"sent_at": datetime.now()}
        return True
    except Exception as e:
        logger.exception(f"Error copiando justificación {justification_id} para user {user_id}: {e}")
        return False

async def handle_justification_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Maneja deep-links: /start just_<MESSAGE_ID>
    """
    if not update.message or not update.message.text:
        return False

    text = update.message.text.strip()
    if not text.startswith("/start just_"):
        return False

    try:
        justification_id = int(text.replace("/start just_", ""))
    except ValueError:
        await update.message.reply_text("❌ Link inválido. Vuelve a tocar el botón.")
        return True

    user_id = update.message.from_user.id
    processing = await update.message.reply_text("🔄 Obteniendo justificación...", disable_notification=True)

    ok = await send_protected_justification(context, user_id, justification_id)

    try:
        await processing.delete()
    except Exception:
        pass

    if ok:
        msg = "✅ Justificación enviada en privado."
        if AUTO_DELETE_MINUTES and AUTO_DELETE_MINUTES > 0:
            msg += f" Se eliminará en {AUTO_DELETE_MINUTES} min."
        await update.message.reply_text(msg, disable_notification=True)
    else:
        await update.message.reply_text("❌ No pude traer esa justificación. Revisa el enlace o inténtalo de nuevo.")

    return True

def extract_justification_link(text: str) -> Optional[int]:
    """
    Busca un enlace al mensaje del canal de justificaciones en el texto/caption y devuelve el message_id.
    Acepta:
      - https://t.me/c/<clean_id>/<message_id>
      - https://t.me/<username>/<message_id>  (si usas alias público)
    """
    if not text:
        return None

    # t.me/c/<clean_id>/<message_id>
    clean_id = _normalize_channel_clean_id(JUSTIFICATIONS_CHAT_ID)
    pat_c = re.compile(rf"https?://t\.me/c/{re.escape(clean_id)}/(\d+)", re.IGNORECASE)

    m = pat_c.search(text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass

    # Alternativa por alias público del canal de justificaciones (si existe)
    # Si usas alias, p.ej. @ccjustificaciones, ponlo en tu config como JUSTIFICATIONS_CHANNEL_USERNAME (opcional).
    try:
        from config import JUSTIFICATIONS_CHANNEL_USERNAME
        if JUSTIFICATIONS_CHANNEL_USERNAME:
            pat_u = re.compile(rf"https?://t\.me/{re.escape(JUSTIFICATIONS_CHANNEL_USERNAME)}/(\d+)", re.IGNORECASE)
            m2 = pat_u.search(text)
            if m2:
                return int(m2.group(1))
    except Exception:
        pass

    return None

def remove_justification_link_from_text(text: str, justification_id: int) -> str:
    """
    Borra del texto/caption el enlace a la justificación detectado, para que el post final quede limpio.
    """
    if not text:
        return text

    clean_id = _normalize_channel_clean_id(JUSTIFICATIONS_CHAT_ID)
    username = None
    try:
        from config import JUSTIFICATIONS_CHANNEL_USERNAME
        username = JUSTIFICATIONS_CHANNEL_USERNAME
    except Exception:
        pass

    # patrón por clean_id
    username_pattern = None
    if username:
        username_pattern = rf"(?:https?://)?t\.me/{re.escape(username)}/{justification_id}/?(?:\s|$)"
    numeric_pattern = rf"(?:https?://)?t\.me/c/{re.escape(clean_id)}/{justification_id}/?(?:\s|$)"

    new_text = re.sub(numeric_pattern, "", text, flags=re.IGNORECASE).strip()
    if username_pattern:
        new_text = re.sub(username_pattern, "", new_text, flags=re.IGNORECASE).strip()

    # Limpia saltos múltiples
    new_text = re.sub(r"\n\s*\n\s*\n+", "\n\n", new_text)
    return new_text

def process_message_with_justification(raw_json: str, bot_username: str) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """
    Si el JSON del borrador incluye un link al mensaje del canal de justificaciones,
    lo remueve del texto y devuelve un teclado de 'Ver justificación 🔒' con deep-link.
    """
    try:
        import json
        data = json.loads(raw_json)

        # Busca campo de texto/caption
        text_field = "text" if "text" in data and data["text"] else ("caption" if "caption" in data else None)
        if not text_field:
            return raw_json, None

        original_text = data[text_field] or ""
        just_id = extract_justification_link(original_text)
        if not just_id:
            return raw_json, None

        clean_text = remove_justification_link_from_text(original_text, just_id)
        data[text_field] = clean_text

        kb = create_justification_button(bot_username, just_id)
        modified_json = json.dumps(data, ensure_ascii=False)
        return modified_json, kb
    except Exception as e:
        logger.exception(f"process_message_with_justification error: {e}")
        return raw_json, None

async def cmd_test_justification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /test_just <message_id> — te envía esa justificación a ti, para probar rápido.
    """
    if not context.args:
        await update.message.reply_text("Uso: /test_just <message_id>")
        return
    try:
        mid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID inválido.")
        return

    ok = await send_protected_justification(context, update.effective_user.id, mid)
    await update.message.reply_text("OK ✅" if ok else "Falló ❌")

async def cmd_justification_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /just_stats — muestra conteo simple de justificaciones enviadas en esta sesión.
    """
    total = len(sent_justifications)
    lines = [f"📊 Justificaciones enviadas (sesión): {total}"]
    for (uid, jid), info in list(sent_justifications.items())[:10]:
        ts = info["sent_at"].strftime("%H:%M:%S")
        lines.append(f"• user {uid} ← just {jid} ({ts})")
    if total > 10:
        lines.append(f"... y {total-10} más")
    await update.message.reply_text("\n".join(lines))

async def get_bot_username(context: ContextTypes.DEFAULT_TYPE) -> str:
    me = await context.bot.get_me()
    return me.username

def add_justification_handlers(application):
    """
    Registra:
      - Deep-link /start just_<ID>
      - Comandos admin /test_just y /just_stats
    NOTA: usa group=0 para que tu propio handler de /start NO se lo coma antes.
    """
    application.add_handler(
        MessageHandler(filters.TEXT & filters.Regex(r"^/start just_\d+$"), handle_justification_request),
        group=0,
    )
    application.add_handler(CommandHandler("test_just", cmd_test_justification))
    application.add_handler(CommandHandler("just_stats", cmd_justification_stats))
    logger.info("✅ Handlers de justificaciones registrados")

# --------- Integración para publisher.py ----------
async def process_draft_for_justifications(
    context: ContextTypes.DEFAULT_TYPE,
    raw_json: str
) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """
    Llama desde publisher: recibe el JSON bruto del borrador,
    y devuelve (json_modificado_sin_link, teclado_con_boton) si detecta una justificación.
    """
    bot_username = await get_bot_username(context)
    return process_message_with_justification(raw_json, bot_username)
