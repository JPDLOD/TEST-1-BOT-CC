#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Justifications Bot - CORREGIDO
- Receives deep links like /start just_<id> (supports multiple: just_123_456)
- Copies the referenced message(s) from a private channel to the user's DM
- Auto-deletes helper messages after N minutes
"""

import os
import logging
import asyncio
import base64
from typing import Dict, List, Optional
import random

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.error import TelegramError

# Import message bank
from justification_messages import get_random_message

# ---------- Logging ----------
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("justifications_bot")

# ---------- Env - CORREGIDO ----------
# CAMBIO CRÍTICO: Usar JUST_BOT_TOKEN en lugar de BOT_TOKEN
JUST_BOT_TOKEN = os.getenv("JUST_BOT_TOKEN", "").strip()

# CAMBIO CRÍTICO: Asegurar que se lea correctamente el CHAT_ID
JUSTIFICATIONS_CHAT_ID = int(os.getenv("JUSTIFICATIONS_CHAT_ID", "-1003058530208"))

# Variables opcionales con valores por defecto
JUST_AUTO_DELETE_MINUTES = int(os.getenv("JUST_AUTO_DELETE_MINUTES", "10"))

# Admin IDs - procesar correctamente
admin_ids_str = os.getenv("JUST_ADMIN_IDS", "231090224")
JUST_ADMIN_IDS = []
if admin_ids_str:
    for id_str in admin_ids_str.split(","):
        id_str = id_str.strip()
        if id_str.isdigit():
            JUST_ADMIN_IDS.append(int(id_str))

# Logging de configuración
logger.info("=== CONFIGURACIÓN DEL BOT ===")
logger.info(f"Token presente: {bool(JUST_BOT_TOKEN)}")
logger.info(f"Canal de justificaciones: {JUSTIFICATIONS_CHAT_ID}")
logger.info(f"Auto-delete minutos: {JUST_AUTO_DELETE_MINUTES}")
logger.info(f"Admin IDs: {JUST_ADMIN_IDS}")

# Validación
if not JUST_BOT_TOKEN:
    raise SystemExit("❌ JUST_BOT_TOKEN no está configurado!")

if JUSTIFICATIONS_CHAT_ID == 0:
    raise SystemExit("❌ JUSTIFICATIONS_CHAT_ID no está configurado correctamente!")

# ---------- Session state for auto-delete ----------
user_sessions: Dict[int, Dict[str, List[int] | Optional[asyncio.Task]]] = {}

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

    # Buscar payload después de '/start '
    if " " in text:
        payload = text.split(" ", 1)[1].strip()
    else:
        payload = ""

    if not payload:
        return parts

    payload = payload.strip()
    logger.info(f"📝 Procesando payload: {payload}")

    if payload.startswith("just_"):
        tail = payload[len("just_"):]
        # Permitir separadores _ , -
        for token in filter(None, [t.strip() for t in tail.replace(",", "_").replace("-", "_").split("_")]):
            if token.isdigit():
                parts.append(int(token))
                logger.info(f"✅ ID extraído: {token}")
        return parts

    if payload.startswith("b64_"):
        b64 = payload[len("b64_"):]
        mid = _b64url_decode_int(b64)
        if mid is not None:
            parts.append(mid)
            logger.info(f"✅ ID desde b64: {mid}")
        return parts

    # Fallback: si el payload es solo dígitos
    if payload.isdigit():
        parts.append(int(payload))
        logger.info(f"✅ ID directo: {payload}")
    
    return parts

def _allow_user(user_id: int) -> bool:
    """Verifica si el usuario tiene permiso."""
    # Si no hay restricciones de admin, permitir a todos
    if not JUST_ADMIN_IDS:
        return True
    # Si hay restricciones, verificar si está en la lista
    return user_id in JUST_ADMIN_IDS

async def _send_case(context: ContextTypes.DEFAULT_TYPE, user_id: int, message_id: int) -> bool:
    """Envía el caso al usuario con mensaje aleatorio."""
    try:
        logger.info(f"📤 Enviando mensaje {message_id} desde canal {JUSTIFICATIONS_CHAT_ID} al usuario {user_id}")
        
        # Copiar el mensaje protegido
        sent_msg = await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=JUSTIFICATIONS_CHAT_ID,
            message_id=message_id,
            protect_content=True,
        )
        
        # Enviar mensaje motivacional aleatorio
        motivational = get_random_message()
        await context.bot.send_message(
            chat_id=user_id,
            text=motivational,
            parse_mode=None  # Sin formato para evitar errores
        )
        
        logger.info(f"✅ Mensaje {message_id} enviado exitosamente")
        return True
        
    except TelegramError as e:
        logger.error(f"❌ Error enviando mensaje {message_id}: {e}")
        if "message to copy not found" in str(e).lower():
            logger.error("El mensaje no existe en el canal de origen")
        elif "chat not found" in str(e).lower():
            logger.error("El canal de justificaciones no es accesible")
        return False
    except Exception as e:
        logger.exception(f"❌ Error inesperado enviando mensaje {message_id}: {e}")
        return False

async def _auto_delete(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Auto-elimina mensajes helper después de N minutos."""
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
    logger.info(f"🗑️ Auto-deleted helper messages for user {user_id}")

# ---------- Handlers ----------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja el comando /start con deep links."""
    if not update.message:
        return
    
    user = update.message.from_user
    user_id = user.id
    username = user.username or "Sin username"
    text = update.message.text or ""
    
    logger.info(f"👤 Usuario {username} (ID: {user_id}) ejecutó: {text}")
    
    # Verificar permisos
    if not _allow_user(user_id):
        await update.message.reply_text("⛔ No tienes autorización para usar este bot.")
        logger.warning(f"⚠️ Usuario no autorizado: {user_id}")
        return
    
    # Parsear payload
    ids = _parse_start_payload(text)
    
    if not ids:
        # Mensaje de ayuda
        help_txt = (
            "🩺 **Bot de Justificaciones**\n\n"
            "Haz clic en el enlace de justificación del canal para recibir el contenido.\n\n"
            "También puedes usar:\n"
            "• `/get <id>` si conoces el ID del mensaje\n"
            "• `/start just_<id>` con el enlace directo"
        )
        await update.message.reply_text(help_txt, parse_mode="Markdown")
        return
    
    logger.info(f"📋 IDs a enviar: {ids}")
    
    # Limpiar mensajes helper anteriores si existen
    if user_id in user_sessions:
        task = user_sessions[user_id].get("task")
        if isinstance(task, asyncio.Task):
            task.cancel()
        
        for mid in user_sessions[user_id].get("messages", []):
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=mid)
            except Exception:
                pass
        
        user_sessions[user_id] = {"messages": [], "task": None}
    else:
        user_sessions[user_id] = {"messages": [], "task": None}
    
    # Mensaje de procesamiento
    processing = await update.message.reply_text(
        "🔄 Obteniendo justificación..." if len(ids) == 1 else f"🔄 Obteniendo {len(ids)} justificaciones..."
    )
    
    # Enviar cada justificación
    ok_count = 0
    failed: List[int] = []
    
    for mid in ids:
        if await _send_case(context, user_id, mid):
            ok_count += 1
            await asyncio.sleep(0.3)  # Pequeña pausa entre envíos
        else:
            failed.append(mid)
    
    # Eliminar mensaje de procesamiento
    try:
        await processing.delete()
    except Exception:
        pass
    
    # Resultado
    if ok_count > 0:
        if JUST_AUTO_DELETE_MINUTES > 0:
            note = await update.message.reply_text(
                f"✅ ¡Justificación{'es' if ok_count > 1 else ''} enviada{'s' if ok_count > 1 else ''}!\n"
                f"_Este mensaje se eliminará en {JUST_AUTO_DELETE_MINUTES} minutos_",
                parse_mode="Markdown"
            )
            user_sessions[user_id]["messages"].append(note.message_id)
            user_sessions[user_id]["task"] = asyncio.create_task(_auto_delete(context, user_id))
        logger.info(f"✅ Enviadas {ok_count} justificaciones a {username}")
    else:
        await update.message.reply_text(
            "❌ No se pudieron recuperar las justificaciones.\n"
            "Por favor, verifica el enlace o contacta al administrador."
        )
        logger.error(f"❌ No se pudieron enviar justificaciones a {username}. IDs fallidos: {failed}")

async def get_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comando /get para obtener justificación por ID."""
    if not update.message:
        return
    
    user_id = update.message.from_user.id
    
    if not _allow_user(user_id):
        await update.message.reply_text("⛔ No autorizado.")
        return
    
    if not context.args:
        await update.message.reply_text("Uso: `/get <id>`", parse_mode="Markdown")
        return
    
    try:
        mid = int(context.args[0])
    except Exception:
        await update.message.reply_text("❌ ID inválido.")
        return
    
    logger.info(f"📥 Comando /get para ID {mid}")
    
    if await _send_case(context, user_id, mid):
        if JUST_AUTO_DELETE_MINUTES > 0:
            note = await update.message.reply_text("✅ ¡Justificación enviada!")
            session = user_sessions.setdefault(user_id, {"messages": [], "task": None})
            session["messages"].append(note.message_id)
            session["task"] = asyncio.create_task(_auto_delete(context, user_id))
    else:
        await update.message.reply_text("❌ No se pudo obtener la justificación con ese ID.")

async def test_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comando /test para verificar configuración (solo admins)."""
    if not update.message:
        return
    
    user_id = update.message.from_user.id
    
    # Solo para admins
    if user_id not in JUST_ADMIN_IDS:
        return
    
    test_msg = (
        "🧪 **Test de configuración**\n\n"
        f"✅ Bot funcionando\n"
        f"📚 Canal justificaciones: `{JUSTIFICATIONS_CHAT_ID}`\n"
        f"⏱️ Auto-delete: {JUST_AUTO_DELETE_MINUTES} min\n"
        f"👮 Admins: {len(JUST_ADMIN_IDS)}\n"
        f"🤖 Bot username: @{context.bot.username}"
    )
    await update.message.reply_text(test_msg, parse_mode="Markdown")

def main():
    """Función principal."""
    logger.info("=" * 50)
    logger.info("🚀 Iniciando Bot de Justificaciones...")
    logger.info("=" * 50)
    
    # Crear aplicación con el token correcto
    app = Application.builder().token(JUST_BOT_TOKEN).build()
    
    # Agregar handlers
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("get", get_cmd))
    app.add_handler(CommandHandler("test", test_cmd))
    
    # También capturar '/start' como mensaje de texto por si acaso
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^/start"), start_cmd))
    
    logger.info("✅ Bot configurado correctamente")
    logger.info(f"📚 Escuchando justificaciones desde: {JUSTIFICATIONS_CHAT_ID}")
    logger.info("🟢 Bot listo y funcionando!")
    
    # Iniciar bot
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
