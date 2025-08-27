#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Bot de Justificaciones CLINICASE_BOT (@clinicase_bot)
Versión Final Funcionando
"""

import os
import logging
import asyncio
from typing import Dict, List

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, filters

# IMPORTAR MENSAJES DESDE EL ARCHIVO QUE YA EXISTE
from justification_messages import get_random_message

# ---------- Logging ----------
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("JUST_BOT")

# ---------- Config ----------
JUST_BOT_TOKEN = os.getenv("JUST_BOT_TOKEN", "8475270102:AAGgnAj7PCgYyGILuxSHrLrdOqCqP3E-5iU").strip()
JUSTIFICATIONS_CHAT_ID = int(os.getenv("JUSTIFICATIONS_CHAT_ID", "-1003058530208"))
JUST_AUTO_DELETE_MINUTES = int(os.getenv("JUST_AUTO_DELETE_MINUTES", "10"))

logger.info("=" * 60)
logger.info("🤖 BOT CLINICASE (@clinicase_bot)")
logger.info(f"📚 Canal: {JUSTIFICATIONS_CHAT_ID}")
logger.info(f"⏱️ Auto-delete: {JUST_AUTO_DELETE_MINUTES} min")
logger.info("🔓 SIN RESTRICCIONES - RESPONDE A TODOS")
logger.info("=" * 60)

if not JUST_BOT_TOKEN:
    raise SystemExit("❌ FALTA JUST_BOT_TOKEN!")

# ---------- Estado ----------
user_messages: Dict[int, List[int]] = {}

# ---------- Funciones ----------
def parse_start_payload(text: str) -> List[int]:
    """
    Extrae IDs del comando /start
    Acepta: /start just_8 -> [8]
    """
    if not text:
        return []
    
    parts = text.split()
    if len(parts) < 2:
        return []
    
    payload = parts[1]
    logger.info(f"📝 Payload recibido: {payload}")
    
    if payload.startswith("just_"):
        id_part = payload[5:]  # Quitar "just_"
        if id_part.isdigit():
            return [int(id_part)]
    
    return []

async def send_justification(context, user_id: int, message_id: int) -> bool:
    """Envía justificación al usuario."""
    try:
        logger.info(f"📤 Enviando justificación {message_id} al usuario {user_id}")
        
        # Copiar mensaje protegido
        await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=JUSTIFICATIONS_CHAT_ID,
            message_id=message_id,
            protect_content=True
        )
        
        # Enviar mensaje motivacional
        msg = await context.bot.send_message(
            chat_id=user_id,
            text=get_random_message()
        )
        
        # Guardar ID para auto-delete
        if user_id not in user_messages:
            user_messages[user_id] = []
        user_messages[user_id].append(msg.message_id)
        
        logger.info(f"✅ Enviado exitosamente")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        return False

async def auto_delete(context, user_id: int, message_ids: List[int]):
    """Auto-elimina mensajes después de X minutos."""
    if JUST_AUTO_DELETE_MINUTES <= 0:
        return
    
    await asyncio.sleep(JUST_AUTO_DELETE_MINUTES * 60)
    
    for mid in message_ids:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=mid)
        except:
            pass
    
    if user_id in user_messages:
        user_messages.pop(user_id, None)

# ---------- Handlers ----------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para /start y deep links."""
    if not update.message:
        return
    
    user = update.message.from_user
    user_id = user.id
    text = update.message.text or ""
    
    logger.info(f"👤 Usuario {user.username or user_id} ejecutó: {text}")
    
    # Parsear IDs del payload
    ids = parse_start_payload(text)
    
    if not ids:
        # Mensaje de ayuda
        await update.message.reply_text(
            "🩺 **CLINICASE - Bot de Justificaciones**\n\n"
            "Usa el enlace del canal para recibir justificaciones.\n"
            "También puedes usar: /get <id>",
            parse_mode="Markdown"
        )
        return
    
    # Procesar cada ID
    success = 0
    failed = []
    
    for message_id in ids:
        if await send_justification(context, user_id, message_id):
            success += 1
        else:
            failed.append(message_id)
    
    # Respuesta al usuario
    if success > 0:
        # Programar auto-delete
        if user_id in user_messages:
            asyncio.create_task(
                auto_delete(context, user_id, user_messages[user_id].copy())
            )
    else:
        await update.message.reply_text(
            f"❌ No se pudo obtener la justificación.\n"
            f"ID solicitado: {ids[0]}"
        )

async def get_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /get para obtener justificación por ID."""
    if not update.message:
        return
    
    user_id = update.message.from_user.id
    
    if not context.args:
        await update.message.reply_text("Uso: /get <id>")
        return
    
    try:
        message_id = int(context.args[0])
    except:
        await update.message.reply_text("❌ ID inválido")
        return
    
    if await send_justification(context, user_id, message_id):
        if user_id in user_messages:
            asyncio.create_task(
                auto_delete(context, user_id, user_messages[user_id].copy())
            )
    else:
        await update.message.reply_text("❌ No se pudo obtener la justificación")

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /info para verificar estado."""
    if not update.message:
        return
    
    info = (
        "🤖 **CLINICASE - Estado del Bot**\n\n"
        f"✅ Bot @clinicase_bot funcionando\n"
        f"📚 Canal: `{JUSTIFICATIONS_CHAT_ID}`\n"
        f"⏱️ Auto-delete: {JUST_AUTO_DELETE_MINUTES} min\n"
        f"🔓 Acceso: TODOS los usuarios\n"
        f"👥 Usuarios activos: {len(user_messages)}"
    )
    await update.message.reply_text(info, parse_mode="Markdown")

# ---------- Main ----------
def main():
    """Función principal."""
    logger.info("🚀 Iniciando bot...")
    
    # Crear aplicación
    app = Application.builder().token(JUST_BOT_TOKEN).build()
    
    # Agregar handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("get", get_command))
    app.add_handler(CommandHandler("info", info_command))
    
    logger.info("✅ Bot configurado y listo")
    logger.info("🟢 Escuchando comandos...")
    
    # Iniciar
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
if not JUST_BOT_TOKEN:
    raise SystemExit("❌ FALTA JUST_BOT_TOKEN!")

# ---------- Estado ----------
user_messages: Dict[int, List[int]] = {}

# ---------- Funciones ----------
def parse_start_payload(text: str) -> List[int]:
    """
    Extrae IDs del comando /start
    Acepta: /start just_8 -> [8]
    """
    if not text:
        return []
    
    parts = text.split()
    if len(parts) < 2:
        return []
    
    payload = parts[1]
    logger.info(f"📝 Payload recibido: {payload}")
    
    if payload.startswith("just_"):
        id_part = payload[5:]  # Quitar "just_"
        if id_part.isdigit():
            return [int(id_part)]
    
    return []

async def send_justification(context, user_id: int, message_id: int) -> bool:
    """Envía justificación al usuario."""
    try:
        logger.info(f"📤 Enviando justificación {message_id} al usuario {user_id}")
        
        # Copiar mensaje protegido
        await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=JUSTIFICATIONS_CHAT_ID,
            message_id=message_id,
            protect_content=True
        )
        
        # Enviar mensaje motivacional
        msg = await context.bot.send_message(
            chat_id=user_id,
            text=get_random_message()
        )
        
        # Guardar ID para auto-delete
        if user_id not in user_messages:
            user_messages[user_id] = []
        user_messages[user_id].append(msg.message_id)
        
        logger.info(f"✅ Enviado exitosamente")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        return False

async def auto_delete(context, user_id: int, message_ids: List[int]):
    """Auto-elimina mensajes después de X minutos."""
    if JUST_AUTO_DELETE_MINUTES <= 0:
        return
    
    await asyncio.sleep(JUST_AUTO_DELETE_MINUTES * 60)
    
    for mid in message_ids:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=mid)
        except:
            pass
    
    if user_id in user_messages:
        user_messages.pop(user_id, None)

# ---------- Handlers ----------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para /start y deep links."""
    if not update.message:
        return
    
    user = update.message.from_user
    user_id = user.id
    text = update.message.text or ""
    
    logger.info(f"👤 Usuario {user.username or user_id} ejecutó: {text}")
    
    # Parsear IDs del payload
    ids = parse_start_payload(text)
    
    if not ids:
        # Mensaje de ayuda
        await update.message.reply_text(
            "🩺 **Bot de Justificaciones**\n\n"
            "Usa el enlace del canal para recibir justificaciones.\n"
            "También puedes usar: /get <id>",
            parse_mode="Markdown"
        )
        return
    
    # Procesar cada ID
    success = 0
    failed = []
    
    for message_id in ids:
        if await send_justification(context, user_id, message_id):
            success += 1
        else:
            failed.append(message_id)
    
    # Respuesta al usuario
    if success > 0:
        # Programar auto-delete
        if user_id in user_messages:
            asyncio.create_task(
                auto_delete(context, user_id, user_messages[user_id].copy())
            )
    else:
        await update.message.reply_text(
            f"❌ No se pudo obtener la justificación.\n"
            f"ID solicitado: {ids[0]}"
        )

async def get_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /get para obtener justificación por ID."""
    if not update.message:
        return
    
    user_id = update.message.from_user.id
    
    if not context.args:
        await update.message.reply_text("Uso: /get <id>")
        return
    
    try:
        message_id = int(context.args[0])
    except:
        await update.message.reply_text("❌ ID inválido")
        return
    
    if await send_justification(context, user_id, message_id):
        if user_id in user_messages:
            asyncio.create_task(
                auto_delete(context, user_id, user_messages[user_id].copy())
            )
    else:
        await update.message.reply_text("❌ No se pudo obtener la justificación")

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /info para verificar estado."""
    if not update.message:
        return
    
    info = (
        "🤖 **Estado del Bot**\n\n"
        f"✅ Bot funcionando\n"
        f"📚 Canal: `{JUSTIFICATIONS_CHAT_ID}`\n"
        f"⏱️ Auto-delete: {JUST_AUTO_DELETE_MINUTES} min\n"
        f"🔓 Acceso: TODOS los usuarios\n"
        f"👥 Usuarios activos: {len(user_messages)}"
    )
    await update.message.reply_text(info, parse_mode="Markdown")

# ---------- Main ----------
def main():
    """Función principal."""
    logger.info("🚀 Iniciando bot...")
    
    # Crear aplicación
    app = Application.builder().token(JUST_BOT_TOKEN).build()
    
    # Agregar handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("get", get_command))
    app.add_handler(CommandHandler("info", info_command))
    
    logger.info("✅ Bot configurado y listo")
    logger.info("🟢 Escuchando comandos...")
    
    # Iniciar
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
