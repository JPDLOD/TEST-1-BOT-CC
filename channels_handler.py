# -*- coding: utf-8 -*-
import logging
import re
from telegram import Update
from telegram.ext import ContextTypes

from config import CASES_UPLOADER_ID
from database import save_case, save_justification, count_cases, get_case_by_id, delete_case, get_all_case_ids

logger = logging.getLogger(__name__)

CASE_PATTERN = re.compile(r'###CASE[_\s]*([A-Z0-9_-]+)', re.IGNORECASE)
CORRECT_PATTERN = re.compile(r'#([A-D])#', re.IGNORECASE)
JUST_PATTERN = re.compile(r'###JUST[_\s]*([A-Z0-9_-]+)', re.IGNORECASE)

async def handle_uploader_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Procesa mensajes del CASES_UPLOADER_ID.
    Detecta casos (###CASE) y justificaciones (###JUST).
    """
    msg = update.message
    user_id = update.effective_user.id
    
    # FILTRO: Solo procesar si es del uploader autorizado
    if user_id != CASES_UPLOADER_ID:
        return
    
    text = msg.text or msg.caption or ""
    
    # DETECTAR CASO
    case_match = CASE_PATTERN.search(text)
    if case_match:
        case_id = f"###CASE_{case_match.group(1)}"
        
        # Extraer respuesta correcta
        correct_match = CORRECT_PATTERN.search(text)
        correct_answer = correct_match.group(1).upper() if correct_match else "A"
        
        # Limpiar texto (quitar marcadores)
        clean_text = CASE_PATTERN.sub('', text)
        clean_text = CORRECT_PATTERN.sub('', clean_text).strip()
        
        # Extraer file_id según tipo
        file_id = None
        file_type = None
        
        if msg.document:
            file_id = msg.document.file_id
            file_type = "document"
        elif msg.photo:
            file_id = msg.photo[-1].file_id
            file_type = "photo"
        elif msg.video:
            file_id = msg.video.file_id
            file_type = "video"
        elif msg.audio:
            file_id = msg.audio.file_id
            file_type = "audio"
        elif msg.voice:
            file_id = msg.voice.file_id
            file_type = "voice"
        elif clean_text:
            # Solo texto, crear un "file" ficticio
            file_id = f"text_{case_id}"
            file_type = "text"
        
        if file_id and file_type:
            save_case(case_id, file_id, file_type, clean_text, correct_answer)
            logger.info(f"✅ Caso guardado: {case_id} ({file_type}) → Respuesta: {correct_answer}")
            
            await msg.reply_text(
                f"✅ **Caso guardado**\n\n"
                f"• ID: `{case_id}`\n"
                f"• Tipo: {file_type}\n"
                f"• Respuesta correcta: {correct_answer}",
                parse_mode="Markdown"
            )
        else:
            await msg.reply_text("❌ No se pudo detectar contenido válido")
        
        return
    
    # DETECTAR JUSTIFICACIÓN
    just_match = JUST_PATTERN.search(text)
    if just_match:
        case_id = f"###CASE_{just_match.group(1)}"
        
        # Limpiar texto
        clean_text = JUST_PATTERN.sub('', text).strip()
        
        # Extraer file_id
        file_id = None
        file_type = None
        
        if msg.document:
            file_id = msg.document.file_id
            file_type = "document"
        elif msg.photo:
            file_id = msg.photo[-1].file_id
            file_type = "photo"
        elif msg.video:
            file_id = msg.video.file_id
            file_type = "video"
        elif msg.audio:
            file_id = msg.audio.file_id
            file_type = "audio"
        elif clean_text:
            file_id = f"text_{case_id}_just"
            file_type = "text"
        
        if file_id and file_type:
            save_justification(case_id, file_id, file_type, clean_text)
            logger.info(f"✅ Justificación guardada para: {case_id} ({file_type})")
            
            await msg.reply_text(
                f"✅ **Justificación guardada**\n\n"
                f"• Para caso: `{case_id}`\n"
                f"• Tipo: {file_type}",
                parse_mode="Markdown"
            )
        else:
            await msg.reply_text("❌ No se pudo detectar contenido válido")
        
        return

async def cmd_refresh_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from admin_panel import is_admin
    
    if not is_admin(update.effective_user.id):
        return
    
    msg = await update.message.reply_text("🔄 Verificando catálogo...")
    
    total = count_cases()
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
        response += "`###CASE_0001 #A#`\n\n"
        response += "**Ejemplos válidos:**\n"
        response += "• `###CASE_0001 #A#`\n"
        response += "• `###CASE_0001_PED_DENGUE #C#`\n"
    
    await msg.edit_text(response, parse_mode="Markdown")

async def cmd_replace_caso(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from admin_panel import is_admin
    
    if not is_admin(update.effective_user.id):
        return
    
    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "**Uso:** `/replace_caso ###CASE_0001`\n\n"
            "Esto eliminará el caso de la BD.\n"
            "Luego puedes enviar el nuevo caso con el mismo ID.",
            parse_mode="Markdown"
        )
        return
    
    case_id = context.args[0]
    caso = get_case_by_id(case_id)
    
    if not caso:
        await update.message.reply_text(f"❌ Caso `{case_id}` no existe en BD", parse_mode="Markdown")
        return
    
    delete_case(case_id)
    
    await update.message.reply_text(
        f"✅ Caso `{case_id}` eliminado de la BD\n\n"
        f"Ahora puedes enviar el nuevo caso con el mismo ID.",
        parse_mode="Markdown"
    )
