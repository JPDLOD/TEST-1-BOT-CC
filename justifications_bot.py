#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Justifications Bot
- Receives deep links like /start just_<id> (supports multiple: just_123_456)
- Optionally supports /start b64_<payload> where payload is base64url of an integer id
- Copies the referenced message(s) from a private channel to the user's DM
- Auto-deletes helper messages after N minutes
Compatible with python-telegram-bot==21.6 (async Application)
"""

import os
import logging
import asyncio
import base64
from typing import Dict, List, Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.error import TelegramError

# ---------- Logging ----------
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("justifications_bot")

# ---------- Env ----------
BOT_TOKEN = os.getenv("JUST_BOT_TOKEN", "").strip()
JUSTIFICATIONS_CHAT_ID = int(os.getenv("JUSTIFICATIONS_CHAT_ID", "0") or "0")
JUST_AUTO_DELETE_MINUTES = int(os.getenv("JUST_AUTO_DELETE_MINUTES", "10") or "10")
JUST_ADMIN_IDS = [
    int(x.strip()) for x in (os.getenv("JUST_ADMIN_IDS", "") or "").split(",") if x.strip().isdigit()
]

if not BOT_TOKEN:
    raise SystemExit("JUST_BOT_TOKEN is required")
if JUSTIFICATIONS_CHAT_ID == 0:
    logger.warning("JUSTIFICATIONS_CHAT_ID is 0 or missing; copy_message will fail until set.")

# ---------- Session state for auto-delete ----------
user_sessions: Dict[int, Dict[str, List[int] | Optional[asyncio.Task]]] = {}
# Structure: user_sessions[user_id] = {"messages": [ids...], "task": asyncio.Task | None}

# ---------- Helpers ----------
def _b64url_decode_int(s: str) -> Optional[int]:
    s = s.strip()
    if not s:
        return None
    pad = "=" * (-len(s) % 4)
    try:
        raw = base64.urlsafe_b64decode(s + pad)
        return int(raw.decode("utf-8"))
    except Exception as e:
        logger.warning("b64 decode failed for %r: %s", s, e)
        return None

def _parse_start_payload(text: str) -> List[int]:
    """
    Accepts:
      /start just_123
      /start just_123_456_789
      /start b64_<base64urlInt>
    Returns list of message_ids (ints).
    """
    text = text or ""
    parts: List[int] = []

    # Prefer args after '/start '
    if " " in text:
        payload = text.split(" ", 1)[1].strip()
    else:
        payload = ""

    if not payload:
        return parts

    payload = payload.strip()

    if payload.startswith("just_"):
        tail = payload[len("just_"):]
        # allow separators _ , -
        for token in filter(None, [t.strip() for t in tail.replace(",", "_").replace("-", "_").split("_")]):
            if token.isdigit():
                parts.append(int(token))
        return parts

    if payload.startswith("b64_"):
        b64 = payload[len("b64_"):]
        mid = _b64url_decode_int(b64)
        if mid is not None:
            parts.append(mid)
        return parts

    # Fallback: if payload is digits only, accept it
    if payload.isdigit():
        parts.append(int(payload))
    return parts

def _allow_user(user_id: int) -> bool:
    return (not JUST_ADMIN_IDS) or (user_id in JUST_ADMIN_IDS)

async def _send_case(context: ContextTypes.DEFAULT_TYPE, user_id: int, message_id: int) -> bool:
    try:
        await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=JUSTIFICATIONS_CHAT_ID,
            message_id=message_id,
            protect_content=True,
        )
        return True
    except TelegramError as e:
        logger.error("copy_message failed for %s -> %s: %s", JUSTIFICATIONS_CHAT_ID, user_id, e)
        return False
    except Exception as e:
        logger.exception("Unexpected error sending message_id %s: %s", message_id, e)
        return False

async def _auto_delete(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    minutes = max(0, JUST_AUTO_DELETE_MINUTES)
    if minutes == 0:
        return
    await asyncio.sleep(minutes * 60)
    session = user_sessions.get(user_id)
    if not session:
        return
    msg_ids = session.get("messages", [])
    for mid in msg_ids:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=mid)
        except Exception:
            pass
    user_sessions.pop(user_id, None)
    logger.info("Auto-deleted helper messages for user %s", user_id)

# ---------- Handlers ----------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    user_id = update.message.from_user.id
    text = update.message.text or ""

    if not _allow_user(user_id):
        await update.message.reply_text("⛔ No autorizado.")
        return

    ids = _parse_start_payload(text)
    if not ids:
        # Help
        help_txt = (
            "🩺 *Justifications Bot*\n\n"
            "Send the link with `/start just_<id>` from the anchor in the channel, "
            "or use `/get <id>` if you know the message ID.\n"
        )
        await update.message.reply_text(help_txt, parse_mode="Markdown")
        return

    # Clean previous helper messages
    if user_id in user_sessions:
        # cancel existing auto-delete task
        task = user_sessions[user_id].get("task")
        if isinstance(task, asyncio.Task):
            task.cancel()
        # delete helper messages
        for mid in user_sessions[user_id].get("messages", []):
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=mid)
            except Exception:
                pass
        user_sessions[user_id] = {"messages": [], "task": None}
    else:
        user_sessions[user_id] = {"messages": [], "task": None}

    # Processing note
    processing = await update.message.reply_text(
        "🔄 Obteniendo justificación..." if len(ids) == 1 else f"🔄 Obteniendo {len(ids)} justificaciones..."
    )

    ok_count = 0
    failed: List[int] = []
    for mid in ids:
        if await _send_case(context, user_id, mid):
            ok_count += 1
            await asyncio.sleep(0.2)
        else:
            failed.append(mid)

    try:
        await processing.delete()
    except Exception:
        pass

    if ok_count:
        sent = await update.message.reply_text("📚 ¡Justificación enviada!")
        user_sessions[user_id]["messages"].append(sent.message_id)
        if JUST_AUTO_DELETE_MINUTES > 0:
            user_sessions[user_id]["task"] = asyncio.create_task(_auto_delete(context, user_id))
    else:
        await update.message.reply_text(
            "❌ No se pudieron recuperar las justificaciones.\n"
            "Verifica el ID y que el bot tenga acceso al canal privado."
        )

async def get_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    user_id = update.message.from_user.id
    if not _allow_user(user_id):
        await update.message.reply_text("⛔ No autorizado.")
        return
    if not context.args:
        await update.message.reply_text("Uso: /get <id>")
        return
    try:
        mid = int(context.args[0])
    except Exception:
        await update.message.reply_text("ID inválido.")
        return
    if await _send_case(context, update.effective_chat.id, mid):
        if JUST_AUTO_DELETE_MINUTES > 0:
            # Optional helper message
            note = await update.message.reply_text("📨 Enviado.")
            session = user_sessions.setdefault(user_id, {"messages": [], "task": None})
            session["messages"].append(note.message_id)
            session["task"] = asyncio.create_task(_auto_delete(context, user_id))
    else:
        await update.message.reply_text("❌ No se pudo obtener la justificación.")

def main():
    logger.info("Starting Justifications Bot…")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("get", get_cmd))
    # Also catch raw '/start ...' as text in case client sends it that way
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^/start"), start_cmd))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
