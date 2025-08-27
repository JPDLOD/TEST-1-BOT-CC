#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Bot de Justificaciones - VERSIÓN FINAL QUE FUNCIONA
Token actualizado y funcionamiento garantizado
"""

import os
import logging
import asyncio
from typing import Dict, List

from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.error import TelegramError

# Importar mensajes desde el archivo existente
try:
    from justification_messages import get_random_message
except:
    def get_random_message():
        return "📚 ¡Justificación enviada!"

# ---------- Logging mejorado ----------
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.DEBUG  # DEBUG para ver TODO
)
logger = logging.getLogger("JUST_BOT")

# ---------- Configuración ----------
# NUEVO TOKEN
JUST_BOT_TOKEN = os.getenv("JUST_BOT_TOKEN", "8475270102:AAGgnAj7PCgYyGILuxSHrLrdOqCqP3E-5iU").strip()
JUSTIFICATIONS_CHAT_ID = int(os.getenv("JUSTIFICATIONS_CHAT_ID", "-1003058530208"))
JUST_AUTO_DELETE_MINUTES = int(os.getenv("JUST_AUTO_DELETE_MINUTES", "10"))

logger.info("=" * 60)
logger.info("🤖 BOT DE JUSTIFICACIONES v3.0")
logger.info(f"📚 Canal: {JUSTIFICATIONS_CHAT_ID}")
logger.info(f"⏱️ Auto-delete: {JUST_AUTO_DELETE_MINUTES} min")
logger.info(f"🔑 Token: {JUST_BOT_TOKEN[:20]}...")
logger.info("=" * 60)

if not JUST_BOT_TOKEN:
    raise SystemExit("❌ FALTA JUST_BOT_TOKEN!")

# ---------- Estado global ----------
user_messages: Dict[int, List[int]] = {}
bot_info = None

# ---------- Verificar acceso al canal ----------
async def verify_channel_access(bot: Bot) -> bool:
    """Verifica que el bot tenga acceso al canal de justificaciones."""
    try:
        chat = await bot.get_chat(JUSTIFICATIONS_CHAT_ID)
        logger.info(f"✅ Acceso verificado al canal: {chat.title or 'Sin título'}")
        return True
    except Exception as e:
        logger.error(f"❌ NO TENGO ACCESO AL CANAL {JUSTIFICATIONS_CHAT_ID}: {e}")
        return False

# ---------- Parser mejorado ----------
def parse_payload(text: str) -> List[int]:
    """
    Parsea diferentes formatos de payload.
    /start just_8 -> [8]
    /start 8 -> [8]
    /start just_8_9_10 -> [8, 9, 10]
    """
    if not text:
        return []
    
    logger.debug(f"Texto completo recibido: '{text}'")
    
    # Obtener el payload después de /start
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        logger.debug("No hay payload después de /start")
        return []
    
    payload = parts[1].strip()
    logger.info(f"📝 Payload extraído: '{payload}'")
    
    ids = []
    
    # Si empieza con just_
    if payload.startswith("just_"):
        id_string = payload[5:]  # Remover "just_"
        logger.debug(f"ID string después de 'just_': '{id_string}'")
        
        # Puede ser just_8 o just_8_9_10
        for part in id_string.split("_"):
            part = part.strip()
            if part.isdigit():
                ids.append(int(part))
                logger.debug(f"ID encontrado: {part}")
    
    # Si es solo números
    elif payload.isdigit():
        ids.append(int(payload))
        logger.debug(f"ID directo encontrado: {payload}")
    
    logger.info(f"✅ IDs parseados: {ids}")
    return ids

# ---------- Función principal de envío ----------
async def send_justification(bot: Bot, user_id: int, message_id: int) -> bool:
    """Envía una justificación al usuario."""
    try:
        logger.info(f"📤 Intentando enviar justificación {message_id} al usuario {user_id}")
        
        # Verificar que el mensaje existe
        try:
            # Intentar obtener el mensaje primero
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=JUSTIFICATIONS_CHAT_ID,
                message_id=message_id,
                protect_content=True
            )
            logger.info(f"✅ Mensaje {message_id} copiado exitosamente")
        except TelegramError as e:
            if "message to copy not found" in str(e).lower():
                logger.error(f"❌ El mensaje {message_id} no existe en el canal")
                return False
            elif "chat not found" in str(e).lower():
                logger.error(f"❌ No tengo acceso al canal {JUSTIFICATIONS_CHAT_ID}")
                return False
            else:
                logger.error(f"❌ Error de Telegram: {e}")
                return False
        
        # Enviar mensaje motivacional
        try:
            msg = await bot.send_message(
                chat_id=user_id,
                text=get_random_message()
            )
            
            # Guardar para auto-delete
            if user_id not in user_messages:
                user_messages[user_id] = []
            user_messages[user_id].append(msg.message_id)
            
        except Exception as e:
            logger.warning(f"No pude enviar mensaje motivacional: {e}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Error enviando justificación: {e}")
        return False

# ---------- Auto-delete ----------
async def auto_delete(bot: Bot, user_id: int, message_ids: List[int]):
    """Auto-elimina mensajes helper después de X minutos."""
    if JUST_AUTO_DELETE_MINUTES <= 0:
        return
    
    await asyncio.sleep(JUST_AUTO_DELETE_MINUTES * 60)
    
    for mid in message_ids:
        try:
            await bot.delete_message(chat_id=user_id, message_id=mid)
        except:
            pass
    
    if user_id in user_messages:
        del user_messages[user_id]

# ---------- Comando /start ----------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para /start con deep links."""
    if not update.message:
        return
    
    user = update.message.from_user
    user_id = user.id
    username = user.username or f"User_{user_id}"
    text = update.message.text or ""
    
    logger.info(f"=" * 40)
    logger.info(f"👤 Usuario: {username} (ID: {user_id})")
    logger.info(f"📨 Comando: {text}")
    
    # Parsear IDs
    ids = parse_payload(text)
    
    if not ids:
        # Mensaje de ayuda
        help_msg = (
            "🩺 **Bot de Justificaciones**\n\n"
            "Este bot entrega las justificaciones de los casos clínicos.\n\n"
            "Usa el enlace del canal para recibir justificaciones,\n"
            "o usa: `/get <id>` si conoces el ID del mensaje.\n\n"
            f"📊 Estado: ✅ Funcionando\n"
            f"📚 Canal: `{JUSTIFICATIONS_CHAT_ID}`"
        )
        await update.message.reply_text(help_msg, parse_mode="Markdown")
        return
    
    # Procesar justificaciones
    success_count = 0
    failed_ids = []
    
    for message_id in ids:
        if await send_justification(context.bot, user_id, message_id):
            success_count += 1
            await asyncio.sleep(0.2)  # Pequeña pausa
        else:
            failed_ids.append(message_id)
    
    # Resultado
    if success_count > 0:
        logger.info(f"✅ Enviadas {success_count} justificaciones a {username}")
        
        # Programar auto-delete
        if user_id in user_messages and user_messages[user_id]:
            asyncio.create_task(
                auto_delete(context.bot, user_id, user_messages[user_id].copy())
            )
    
    if failed_ids:
        error_msg = (
            f"❌ No se pudieron obtener las justificaciones:\n"
            f"IDs fallidos: {', '.join(map(str, failed_ids))}\n\n"
            f"Posibles causas:\n"
            f"• El mensaje no existe\n"
            f"• El bot no tiene acceso al canal\n"
            f"• Error de conexión"
        )
        await update.message.reply_text(error_msg)
        logger.error(f"❌ Fallo al enviar IDs {failed_ids} a {username}")

# ---------- Comando /get ----------
async def get_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Obtener justificación por ID directo."""
    if not update.message:
        return
    
    user_id = update.message.from_user.id
    
    if not context.args:
        await update.message.reply_text(
            "Uso: `/get <id>`\n"
            "Ejemplo: `/get 123`",
            parse_mode="Markdown"
        )
        return
    
    try:
        message_id = int(context.args[0])
    except:
        await update.message.reply_text("❌ ID inválido. Debe ser un número.")
        return
    
    logger.info(f"📥 Comando /get para ID {message_id}")
    
    if await send_justification(context.bot, user_id, message_id):
        # Auto-delete
        if user_id in user_messages and user_messages[user_id]:
            asyncio.create_task(
                auto_delete(context.bot, user_id, user_messages[user_id].copy())
            )
    else:
        await update.message.reply_text(
            f"❌ No se pudo obtener la justificación {message_id}\n"
            f"Verifica que el ID sea correcto."
        )

# ---------- Comando /test ----------
async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando para probar el bot."""
    if not update.message:
        return
    
    # Verificar acceso al canal
    has_access = await verify_channel_access(context.bot)
    
    status_msg = (
        "🧪 **TEST DEL BOT**\n\n"
        f"🤖 Bot: @{bot_info.username if bot_info else 'Unknown'}\n"
        f"📚 Canal: `{JUSTIFICATIONS_CHAT_ID}`\n"
        f"🔑 Acceso al canal: {'✅ SÍ' if has_access else '❌ NO'}\n"
        f"⏱️ Auto-delete: {JUST_AUTO_DELETE_MINUTES} min\n"
        f"👥 Usuarios activos: {len(user_messages)}\n"
        f"\n{'✅ Todo funcionando correctamente' if has_access else '❌ AGREGAR BOT AL CANAL'}"
    )
    
    await update.message.reply_text(status_msg, parse_mode="Markdown")

# ---------- Handler para mensajes directos ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja mensajes directos al bot."""
    if not update.message:
        return
    
    text = update.message.text or ""
    
    # Si envían solo un número, tratarlo como ID
    if text.isdigit():
        message_id = int(text)
        user_id = update.message.from_user.id
        
        if await send_justification(context.bot, user_id, message_id):
            if user_id in user_messages and user_messages[user_id]:
                asyncio.create_task(
                    auto_delete(context.bot, user_id, user_messages[user_id].copy())
                )
        else:
            await update.message.reply_text(f"❌ No se pudo obtener la justificación {message_id}")
    else:
        await update.message.reply_text(
            "Envíame el ID de la justificación (solo el número)\n"
            "o usa `/get <id>`",
            parse_mode="Markdown"
        )

# ---------- Main ----------
async def post_init(app: Application) -> None:
    """Se ejecuta después de inicializar el bot."""
    global bot_info
    bot_info = await app.bot.get_me()
    logger.info(f"✅ Bot iniciado: @{bot_info.username}")
    
    # Verificar acceso al canal
    has_access = await verify_channel_access(app.bot)
    if not has_access:
        logger.error("⚠️ ¡ADVERTENCIA! El bot NO tiene acceso al canal de justificaciones")
        logger.error(f"⚠️ Agrega el bot @{bot_info.username} al canal {JUSTIFICATIONS_CHAT_ID}")

def main():
    """Función principal."""
    logger.info("🚀 Iniciando bot de justificaciones...")
    
    # Crear aplicación
    app = Application.builder().token(JUST_BOT_TOKEN).build()
    
    # Post-init
    app.post_init = post_init
    
    # Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("get", get_command))
    app.add_handler(CommandHandler("test", test_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("✅ Handlers configurados")
    logger.info("🟢 Bot listo para recibir comandos")
    
    # Iniciar
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
