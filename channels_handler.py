# -*- coding: utf-8 -*-
import logging
import re
from telegram.ext import ContextTypes

from config import JUSTIFICATIONS_CHAT_ID
from database import save_case, save_justification, count_cases, get_case_by_id, delete_case

logger = logging.getLogger(__name__)

CASE_PATTERN = re.compile(r'###CASE[_\s]*([A-Z0-9_-]+)', re.IGNORECASE)
CORRECT_PATTERN = re.compile(r'#([A-D])#', re.IGNORECASE)
JUST_PATTERN = re.compile(r'###JUST[_\s]*([A-Z0-9_-]+)', re.IGNORECASE)

async def cmd_refresh_catalog(update, context: ContextTypes.DEFAULT_TYPE):
    """
    Comando admin para refrescar el catálogo manualmente.
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
        response += "`###CASE_0001 #A#`\n\n"
        response += "**Ejemplos válidos:**\n"
        response += "• `###CASE_0001 #A#`\n"
        response += "• `###CASE_0001_PED_DENGUE #C#`\n"
    
    await msg.edit_text(response, parse_mode="Markdown")

async def cmd_replace_caso(update, context: ContextTypes.DEFAULT_TYPE):
    """
    /replace_caso ###CASE_0001
    Elimina caso viejo del canal y DB
    """
    from admin_panel import is_admin
    
    if not is_admin(update.effective_user.id):
        return
    
    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "**Uso:** `/replace_caso ###CASE_0001`\n\n"
            "Esto eliminará el caso viejo del canal y DB.\n"
            "Luego puedes enviar el nuevo caso con el mismo ID.",
            parse_mode="Markdown"
        )
        return
    
    case_id = context.args[0]
    
    # Buscar caso existente
    caso = get_case_by_id(case_id)
    
    if not caso:
        await update.message.reply_text(f"❌ Caso `{case_id}` no existe en DB", parse_mode="Markdown")
        return
    
    _, old_message_id, _ = caso
    
    # Borrar mensaje del canal
    try:
        await context.bot.delete_message(
            chat_id=JUSTIFICATIONS_CHAT_ID,
            message_id=old_message_id
        )
        logger.info(f"🗑️ Mensaje {old_message_id} borrado del canal")
    except Exception as e:
        logger.warning(f"No se pudo borrar mensaje del canal: {e}")
    
    # Eliminar de DB
    delete_case(case_id)
    
    await update.message.reply_text(
        f"✅ Caso `{case_id}` eliminado\n\n"
        f"• Mensaje `{old_message_id}` borrado del canal\n"
        f"• Registro eliminado de la base de datos\n\n"
        f"Ahora puedes enviar el nuevo caso con el mismo ID.",
        parse_mode="Markdown"
    )

async def process_message_for_catalog(message_id: int, text: str):
    """
    Procesa un mensaje del canal para detectar casos/justificaciones.
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
