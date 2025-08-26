# -*- coding: utf-8 -*-
"""
Sistema de Justificaciones Protegidas (deep-link “tipo BotFather”).

• Detecta enlaces a mensajes del Canal de Justificaciones para reemplazarlos por un botón
  "Ver justificación 🔒" (opcional; lo usas desde publisher.py).
• Maneja el deep-link /start just_<MESSAGE_ID> para copiar ESA justificación al usuario.
• copy_message con protect_content=True y autodestrucción opcional.

Requiere:
- JUSTIFICATIONS_CHAT_ID (o JUST_CHANNEL_ID) con el ID numérico del canal de justificaciones.
- Opcional: JUSTIFICATIONS_CHANNEL_USERNAME si el canal es público y quieres detectar por alias.
- Opcional: AUTO_DELETE_MINUTES > 0 para borrar los mensajes enviados en privado luego de N minutos.
"""

import json
import logging
import os
import re
from datetime import datetime
from typing import Optional, Tuple

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logger = logging.getLogger(__name__)

# =========================
# Carga de configuración (con fallbacks)
# =========================

# Canal de justificaciones (numérico -100xxxxxxxxxx)
_JCHAT = None
try:
    from config import JUSTIFICATIONS_CHAT_ID as _JCHAT  # type: ignore
except Exception:
    _JCHAT = None

if _JCHAT is None:
    try:
        from config import JUST_CHANNEL_ID as _JCHAT  # type: ignore
    except Exception:
        _JCHAT = None

if _JCHAT is None:
    try:
        _JCHAT = int(os.getenv("JUSTIFICATIONS_CHAT_ID", "0").strip() or "0")
        if _JCHAT == 0:
            raise ValueError
    except Exception:
        try:
            _JCHAT = int(os.getenv("JUST_CHANNEL_ID", "0").strip() or "0")
            if _JCHAT == 0:
                raise ValueError
        except Exception:
            pass

if not isinstance(_JCHAT, int):
    raise RuntimeError(
        "No se pudo determinar el ID del canal de justificaciones. "
        "Define JUSTIFICATIONS_CHAT_ID (o JUST_CHANNEL_ID) en config.py o en variables de entorno."
    )

JUSTIFICATIONS_CHAT_ID: int = _JCHAT

# Autodestrucción de mensajes privados (minutos)
try:
    from config import AUTO_DELETE_MINUTES  # type: ignore
except Exception:
    try:
        AUTO_DELETE_MINUTES = int(os.getenv("AUTO_DELETE_MINUTES", "10"))
    except Exception:
        AUTO_DELETE_MINUTES = 10

# Alias público opcional del canal (si existe)
try:
    from config import JUSTIFICATIONS_CHANNEL_USERNAME  # type: ignore
except Exception:
    JUSTIFICATIONS_CHANNEL_USERNAME = os.getenv("JUSTIFICATIONS_CHANNEL_USERNAME", "").strip() or None

# =========================
# Utils
# =========================

def _normalize_channel_clean_id(chat_id: int) -> str:
    """
    Para enlaces tipo t.me/c/<clean_id>/<msg_id>, Telegram usa el ID del canal sin el prefijo -100.
    """
    s = str(chat_id)
    if s.startswith("-100"):
        return s[4:]
    return s

def make_deeplink(bot_username: str, message_id: int) -> str:
    return f"https://t.me/{bot_username}?start=just_{message_id}"

def build_just_button_from_username(bot_username: str, message_id: int) -> InlineKeyboardMarkup:
    deep_link = make_deeplink(bot_username, message_id)
    kb = [[InlineKeyboardButton("Ver justificación 🔒", url=deep_link)]]
    return InlineKeyboardMarkup(kb)

async def get_bot_username(context: ContextTypes.DEFAULT_TYPE) -> str:
    me = await context.bot.get_me()
    return me.username

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
        return True
    except Exception as e:
        logger.exception(
            f"Error copiando justificación {justification_id} para user {user_id}: {e}"
        )
        return False

# =========================
# Deep-link: /start just_<ID>
# =========================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Maneja:
        /start
        /start just_<MESSAGE_ID>
    """
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Hola 👋. Usa el botón 'Ver justificación 🔒' o envía /start just_<ID>."
        )
        return

    payload = args[0]
    if not payload.startswith("just_"):
        await update.message.reply_text(
            "Formato no reconocido. Usa /start just_<ID>."
        )
        return

    try:
        justification_id = int(payload.replace("just_", ""))
    except ValueError:
        await update.message.reply_text("❌ Link inválido. Vuelve a tocar el botón.")
        return

    user_id = update.message.from_user.id
    processing = await update.message.reply_text(
        "🔄 Obteniendo justificación...", disable_notification=True
    )

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
        await update.message.reply_text(
            "❌ No pude traer esa justificación. Revisa el enlace o inténtalo de nuevo."
        )

# Acepta también el mensaje literal por si /start llega como texto completo
async def handle_start_text_regex(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text.startswith("/start just_"):
        return
    try:
        justification_id = int(text.replace("/start just_", ""))
    except ValueError:
        await update.message.reply_text("❌ Link inválido. Vuelve a tocar el botón.")
        return
    user_id = update.message.from_user.id
    processing = await update.message.reply_text(
        "🔄 Obteniendo justificación...", disable_notification=True
    )
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
        await update.message.reply_text(
            "❌ No pude traer esa justificación. Revisa el enlace o inténtalo de nuevo."
        )

# =========================
# Detección de enlaces de justificación en borradores (opcional)
# Para reemplazar el link por un botón deep-link en tus posts
# =========================

def extract_justification_id_from_text(text: str) -> Optional[int]:
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

    # Alternativa: alias público del canal (si existe)
    if JUSTIFICATIONS_CHANNEL_USERNAME:
        pat_u = re.compile(
            rf"https?://t\.me/{re.escape(JUSTIFICATIONS_CHANNEL_USERNAME)}/(\d+)",
            re.IGNORECASE,
        )
        m2 = pat_u.search(text)
        if m2:
            try:
                return int(m2.group(1))
            except ValueError:
                pass

    return None

def remove_justification_link_from_text(text: str, justification_id: int) -> str:
    """
    Elimina del texto/caption el enlace a la justificación detectado.
    """
    if not text:
        return text

    clean_id = _normalize_channel_clean_id(JUSTIFICATIONS_CHAT_ID)
    # Por numeric clean_id
    numeric_pattern = re.compile(
        rf"(?:https?://)?t\.me/c/{re.escape(clean_id)}/{justification_id}/?(?:\s|$)",
        re.IGNORECASE,
    )
    new_text = re.sub(numeric_pattern, "", text).strip()

    # Por alias público, si existe
    if JUSTIFICATIONS_CHANNEL_USERNAME:
        username_pattern = re.compile(
            rf"(?:https?://)?t\.me/{re.escape(JUSTIFICATIONS_CHANNEL_USERNAME)}/{justification_id}/?(?:\s|$)",
            re.IGNORECASE,
        )
        new_text = re.sub(username_pattern, "", new_text).strip()

    # Limpia múltiples saltos
    new_text = re.sub(r"\n\s*\n\s*\n+", "\n\n", new_text)
    return new_text

def process_message_with_justification_json(
    raw_json: str, bot_username: str
) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """
    Si el JSON del borrador incluye un link a justificación, lo remueve
    y devuelve (json_modificado, teclado_de_boton_o_None).
    """
    try:
        data = json.loads(raw_json)

        # Determina si es texto o caption (para fotos, etc.)
        text_field = "text" if ("text" in data and data["text"]) else ("caption" if "caption" in data else None)
        if not text_field:
            return raw_json, None

        original_text = data[text_field] or ""
        just_id = extract_justification_id_from_text(original_text)
        if not just_id:
            return raw_json, None

        clean_text = remove_justification_link_from_text(original_text, just_id)
        data[text_field] = clean_text

        kb = build_just_button_from_username(bot_username, just_id)
        modified_json = json.dumps(data, ensure_ascii=False)
        return modified_json, kb
    except Exception as e:
        logger.exception(f"process_message_with_justification_json error: {e}")
        return raw_json, None

# =========================
# Comandos utilitarios (tests / métricas sencillas)
# =========================

_sent_registry = {}  # {(user_id, justification_id): datetime}

async def cmd_test_just(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /test_just <message_id> — envía esa justificación al usuario que lo ejecuta.
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
    if ok:
        _sent_registry[(update.effective_user.id, mid)] = datetime.now()
    await update.message.reply_text("OK ✅" if ok else "Falló ❌")

async def cmd_just_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /just_stats — muestra conteo simple de justificaciones enviadas en esta sesión.
    """
    total = len(_sent_registry)
    lines = [f"📊 Justificaciones enviadas (sesión): {total}"]
    for (uid, jid), ts in list(_sent_registry.items())[:10]:
        hhmmss = ts.strftime("%H:%M:%S")
        lines.append(f"• user {uid} ← just {jid} ({hhmmss})")
    if total > 10:
        lines.append(f"... y {total-10} más")
    await update.message.reply_text("\n".join(lines))

# =========================
# API pública para integrar en main/publisher
# =========================

def add_justification_handlers(app: Application):
    """
    Registra:
      - /start just_<ID> (deep-link)
      - /test_just y /just_stats (opcionales)
      - handler regex adicional por si /start llega como texto completo
    """
    app.add_handler(CommandHandler("start", cmd_start), group=0)
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^/start just_\d+$"), handle_start_text_regex), group=0)
    app.add_handler(CommandHandler("test_just", cmd_test_just))
    app.add_handler(CommandHandler("just_stats", cmd_just_stats))
    logger.info("✅ Handlers de justificaciones registrados")

async def process_draft_for_justifications(
    context: ContextTypes.DEFAULT_TYPE,
    raw_json: str
) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """
    Llama desde publisher: recibe JSON bruto del borrador,
    y devuelve (json_modificado_sin_link, teclado_con_boton) si detecta justificación.
    """
    bot_username = await get_bot_username(context)
    return process_message_with_justification_json(raw_json, bot_username)
