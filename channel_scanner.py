# -*- coding: utf-8 -*-
"""
Scanner del canal de justificaciones para reconstruir el catálogo de casos.
Se ejecuta al iniciar el bot si la BD está vacía.
"""
import logging
import re
import asyncio
from telegram.ext import ContextTypes

from config import JUSTIFICATIONS_CHAT_ID
from database import save_case, save_justification, count_cases

logger = logging.getLogger(__name__)

CASE_PATTERN = re.compile(r'###CASE[_\s]*([A-Z0-9_-]+)', re.IGNORECASE)
CORRECT_PATTERN = re.compile(r'#([A-D])#', re.IGNORECASE)
JUST_PATTERN = re.compile(r'###JUST[_\s]*([A-Z0-9_-]+)', re.IGNORECASE)

async def scan_channel_for_cases(context: ContextTypes.DEFAULT_TYPE, limit: int = 2000):
    """
    Escanea el canal de justificaciones para detectar casos y justificaciones.
    Se ejecuta al iniciar si la BD está vacía.
    """
    try:
        logger.info(f"🔍 Escaneando canal {JUSTIFICATIONS_CHAT_ID} (últimos {limit} mensajes)...")
        
        cases_found = 0
        justs_found = 0
        
        try:
            chat = await context.bot.get_chat(JUSTIFICATIONS_CHAT_ID)
            logger.info(f"📡 Canal encontrado: {chat.title}")
        except Exception as e:
            logger.error(f"❌ No se pudo acceder al canal: {e}")
            return
        
        logger.warning("⚠️ Scanner automático limitado por API de Telegram")
        logger.info("💡 Usa /refresh_catalog después de agregar casos al canal")
        
    except Exception as e:
        logger.exception(f"Error escaneando canal: {e}")

async def cmd_refresh_catalog(update, context: ContextTypes.DEFAULT_TYPE):
    """
    Comando admin para refrescar el catálogo manualmente.
    Los casos deben estar en el canal con formato correcto.
    """
    from admin_panel import is_admin
    
    if not is_admin(update.effective_user.id):
        return
    
    msg = await update.message.reply_text("🔄 Verificando catálogo...")
    
    total = count_cases()
    
    from database import get_all_case_ids
    all_ids = get_all_case_ids()
    
    response = f"✅ Catálogo actualizado\n\n📊 **Estado:**\n"
    response += f"• Total de casos: {total}\n\n"
    
    if all_ids:
        response += "📋 **Últimos 10 casos:**\n"
        for case_id in all_ids[-10:]:
            response += f"• `{case_id}`\n"
    else:
        response += "⚠️ **No hay casos en la BD**\n\n"
        response += "💡 **Formato esperado:**\n"
        response += "`###CASE_ID #RESPUESTA#`\n\n"
        response += "**Ejemplos válidos:**\n"
        response += "• `###CASE_0001 #A#`\n"
        response += "• `###CASE_0001_PED_DENGUE_0001 #C#`\n"
        response += "• `###CASE CASO_GYO_0008 #B#`\n"
    
    await msg.edit_text(response, parse_mode="Markdown")

async def process_message_for_catalog(message_id: int, text: str):
    """
    Procesa un mensaje del canal para detectar casos/justificaciones.
    Se llama desde handle_justifications_channel en main.py
    """
    if not text:
        return
    
    # Detectar CASO
    case_match = CASE_PATTERN.search(text)
    if case_match:
        case_id = f"###CASE_{case_match.group(1)}"
        
        # Buscar respuesta correcta
        correct_match = CORRECT_PATTERN.search(text)
        correct_answer = correct_match.group(1).upper() if correct_match else "A"
        
        save_case(case_id, message_id, correct_answer)
        logger.info(f"✅ Caso detectado: {case_id} → Respuesta: {correct_answer}")
    
    # Detectar JUSTIFICACIÓN
    just_match = JUST_PATTERN.search(text)
    if just_match:
        case_id = f"###CASE_{just_match.group(1)}"
        save_justification(case_id, message_id)
        logger.info(f"✅ Justificación detectada para: {case_id}")
