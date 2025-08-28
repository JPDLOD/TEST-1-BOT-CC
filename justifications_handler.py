#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Handler de justificaciones para el bot PRINCIPAL
Solo convierte enlaces a deep links del bot @clinicase_bot
"""

import re
import logging
from typing import Optional, Tuple, List
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)

# Configuración del bot de justificaciones
JUSTIFICATIONS_BOT_USERNAME = "clinicase_bot"  # Bot CLINICASE_BOT
JUSTIFICATIONS_CHAT_ID = -1003058530208

def process_justification_links(text: str) -> Tuple[str, List[InlineKeyboardButton]]:
    """
    Detecta enlaces de justificación y crea botones con deep links.
    
    Returns:
        (texto_limpio, lista_de_botones)
    """
    if not text:
        return text, []
    
    # Patrones para detectar enlaces
    patterns = [
        # Formato: https://t.me/c/3058530208/123
        r'(?:CASO\s*#?\s*\d+\s*)?https://t\.me/c/3058530208/(\d+)',
        # Formato: https://t.me/ccjustificaciones/123
        r'(?:CASO\s*#?\s*\d+\s*)?https://t\.me/ccjustificaciones/(\d+)',
    ]
    
    buttons = []
    cleaned_text = text
    
    for pattern in patterns:
        matches = list(re.finditer(pattern, text, re.IGNORECASE))
        
        for match in matches:
            full_match = match.group(0)
            message_id = match.group(1)
            
            # Extraer nombre del caso si existe
            case_match = re.search(r'CASO\s*#?\s*(\d+)', full_match, re.IGNORECASE)
            if case_match:
                case_num = case_match.group(1)
                button_text = f"📚 Ver justificación CASO #{case_num}"
            else:
                button_text = f"📚 Ver justificación"
            
            # Crear deep link al bot de justificaciones
            deep_link = f"https://t.me/{JUSTIFICATIONS_BOT_USERNAME}?start=jst_{message_id}"
            
            # Crear botón
            button = InlineKeyboardButton(button_text, url=deep_link)
            buttons.append(button)
            
            # Limpiar el texto del enlace
            cleaned_text = cleaned_text.replace(full_match, "")
            
            logger.info(f"✅ Justificación detectada: ID {message_id} → {button_text}")
    
    # Limpiar espacios múltiples
    cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()
    
    return cleaned_text, buttons

def create_justification_keyboard(buttons: List[InlineKeyboardButton]) -> Optional[InlineKeyboardMarkup]:
    """
    Crea un teclado con los botones de justificación.
    """
    if not buttons:
        return None
    
    # Organizar botones en columnas de 1
    keyboard = [[button] for button in buttons]
    return InlineKeyboardMarkup(keyboard)

async def cmd_test_justification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Comando de prueba para verificar el sistema de justificaciones.
    Uso: /test_just 123 o /test_just 123,456,789
    """
    if not update.channel_post:
        return
    
    from config import SOURCE_CHAT_ID
    
    args = context.args
    if not args:
        await context.bot.send_message(
            SOURCE_CHAT_ID,
            "❌ Uso: `/test_just <id>` o `/test_just <id1,id2,id3>`\n"
            "Ejemplo: `/test_just 123`",
            parse_mode="Markdown"
        )
        return
    
    # Parsear IDs
    ids_text = args[0]
    ids = []
    
    # Soportar múltiples IDs separados por comas
    for part in ids_text.split(','):
        part = part.strip()
        if part.isdigit():
            ids.append(part)
    
    if not ids:
        await context.bot.send_message(SOURCE_CHAT_ID, "❌ No se encontraron IDs válidos")
        return
    
    # Crear mensaje de prueba
    if len(ids) == 1:
        test_text = f"🧪 **PRUEBA DE JUSTIFICACIÓN**\n\nCASO #TEST https://t.me/ccjustificaciones/{ids[0]}"
    else:
        test_text = "🧪 **PRUEBA DE JUSTIFICACIONES MÚLTIPLES**\n\n"
        for i, msg_id in enumerate(ids, 1):
            test_text += f"CASO #{i} https://t.me/ccjustificaciones/{msg_id}\n"
    
    # Procesar enlaces
    cleaned_text, buttons = process_justification_links(test_text)
    
    if buttons:
        keyboard = create_justification_keyboard(buttons)
        
        # Agregar texto informativo
        final_text = (
            "🧪 **PRUEBA DE JUSTIFICACIÓN**\n\n"
            f"Se detectaron {len(buttons)} justificacion(es).\n"
            "Haz clic en el botón para recibirla:"
        )
        
        await context.bot.send_message(
            SOURCE_CHAT_ID,
            final_text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        
        logger.info(f"✅ Test enviado con {len(buttons)} botón(es)")
    else:
        await context.bot.send_message(
            SOURCE_CHAT_ID,
            "❌ No se detectaron justificaciones válidas"
        )

def add_justification_handlers(app: Application):
    """
    Agrega los handlers de justificaciones al bot principal.
    """
    app.add_handler(CommandHandler("test_just", cmd_test_justification))
    logger.info("✅ Handlers de justificaciones agregados")

# Función helper para publisher.py
def should_process_justifications(text: str) -> bool:
    """
    Verifica si un texto contiene enlaces de justificación.
    """
    if not text:
        return False
    
    patterns = [
        r'https://t\.me/c/3058530208/\d+',
        r'https://t\.me/ccjustificaciones/\d+',
    ]
    
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    
    return False
