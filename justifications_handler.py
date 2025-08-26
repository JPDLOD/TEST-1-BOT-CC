# -*- coding: utf-8 -*-
"""
Sistema de Justificaciones Protegidas - VERSIÓN CORREGIDA
Maneja deep-links para enviar justificaciones específicas desde un canal de justificaciones
"""

import logging
import asyncio
import re
from typing import Optional, Dict, Set, List
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, filters
from telegram.error import TelegramError

from config import TZ

logger = logging.getLogger(__name__)

# ========= CONFIGURACIÓN DE JUSTIFICACIONES =========
JUSTIFICATIONS_CHAT_ID = -1003058530208  # Canal de justificaciones
AUTO_DELETE_MINUTES = 10  # Tiempo antes de borrar la justificación (0 = no borrar)

# Cache para rastrear mensajes enviados y sus timers de eliminación
sent_justifications: Dict[str, Dict] = {}  # {user_id_message_id: {chat_id, message_id, timer_task}}

# ========= DETECCIÓN DE JUSTIFICACIONES EN MENSAJES =========

# Patrón para detectar enlaces de justificación en mensajes
# Ejemplo: https://t.me/c/1058530208/4 -> mensaje ID 4
JUSTIFICATION_LINK_PATTERN = re.compile(
    r'https://t\.me/c/(\d+)/(\d+)',
    re.IGNORECASE
)

def detect_justification_links(text: str) -> List[int]:
    """
    Detecta enlaces de justificación en el texto del mensaje.
    Retorna lista de IDs de mensajes de justificación.
    """
    if not text:
        return []
    
    links = JUSTIFICATION_LINK_PATTERN.findall(text)
    justification_ids = []
    
    for chat_id_part, message_id in links:
        # Convertir el chat_id del enlace al formato completo
        full_chat_id = f"-100{chat_id_part}"
        
        # Verificar si corresponde al canal de justificaciones
        if int(full_chat_id) == JUSTIFICATIONS_CHAT_ID:
            try:
                justification_ids.append(int(message_id))
                logger.info(f"📚 Link de justificación detectado: mensaje {message_id}")
            except ValueError:
                continue
    
    return justification_ids

def extract_case_name(text: str) -> str:
    """
    Extrae el nombre del caso del texto si existe.
    Busca patrones como "CASO #X", "🤣 CASO #34", etc.
    """
    if not text:
        return ''
    
    # Patrones para detectar nombres de casos
    patterns = [
        r'([🤣😂]*\s*CASO\s*#\d+[^.\n]*)',
        r'(CASO\s+[^.\n]+)',
        r'(CASP\s*#\d+[^.\n]*)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            case_name = match.group(1).strip()
            logger.info(f"📝 Nombre de caso detectado: '{case_name}'")
            return case_name
    
    return ''

# ========= FUNCIONES AUXILIARES =========

def generate_justification_deep_link(bot_username: str, justification_ids: List[int]) -> str:
    """
    Genera el deep-link para justificaciones múltiples.
    Formato: https://t.me/BotUsername?start=just_4_5_7
    """
    ids_str = "_".join(str(id) for id in justification_ids)
    return f"https://t.me/{bot_username}?start=just_{ids_str}"

def create_justification_button(bot_username: str, justification_ids: List[int], case_name: str = '') -> InlineKeyboardMarkup:
    """
    Crea el botón inline "Ver justificación 📚" con deep-link.
    """
    deep_link = generate_justification_deep_link(bot_username, justification_ids)
    
    # Personalizar texto del botón según el caso
    if case_name:
        button_text = f"Ver justificación {case_name} 📚"
    else:
        button_text = "Ver justificación 📚"
    
    button = InlineKeyboardButton(button_text, url=deep_link)
    return InlineKeyboardMarkup([[button]])

async def clean_previous_messages(context: ContextTypes.DEFAULT_TYPE, user_id: int, keep_last_n: int = 0):
    """
    Limpia mensajes anteriores del bot en el chat privado del usuario.
    FUNCIÓN CORREGIDA - era 'clean_all_previous_messages' en el error.
    """
    try:
        # Obtener historial de mensajes recientes
        # Nota: Esta es una implementación simplificada
        # En un caso real, necesitarías mantener un registro de mensajes enviados
        logger.info(f"🧹 Limpiando mensajes anteriores para usuario {user_id}")
        
        # Por ahora, solo limpiar del cache local
        keys_to_remove = []
        for key in sent_justifications.keys():
            if key.startswith(f"{user_id}_"):
                keys_to_remove.append(key)
        
        for key in keys_to_remove:
            sent_justifications.pop(key, None)
            
    except Exception as e:
        logger.warning(f"⚠️ Error limpiando mensajes anteriores: {e}")

async def send_protected_justifications(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    justification_ids: List[int]
) -> bool:
    """
    Envía múltiples justificaciones protegidas al usuario.
    FUNCIÓN CORREGIDA para manejar múltiples justificaciones.
    """
    
    try:
        logger.info(f"📋 Enviando justificaciones {justification_ids} a usuario {user_id}")
        
        # Limpiar mensajes anteriores del bot
        await clean_previous_messages(context, user_id, 0)
        
        success_count = 0
        
        for justification_id in justification_ids:
            try:
                # Copiar el mensaje desde el canal de justificaciones al usuario
                copied_message = await context.bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=JUSTIFICATIONS_CHAT_ID,
                    message_id=justification_id,
                    protect_content=True  # PROTECCIÓN: No se puede copiar/reenviar/capturar
                )
                
                if copied_message:
                    success_count += 1
                    logger.info(f"✅ Justificación {justification_id} enviada a {user_id}")
                    
                    # Programar auto-eliminación si está configurada
                    if AUTO_DELETE_MINUTES > 0:
                        await schedule_message_deletion(
                            context, 
                            user_id, 
                            copied_message.message_id, 
                            justification_id
                        )
                else:
                    logger.error(f"❌ No se pudo copiar justificación {justification_id}")
                    
                # Pequeña pausa entre envíos
                await asyncio.sleep(0.2)
                    
            except TelegramError as e:
                logger.error(f"❌ Error enviando justificación {justification_id}: {e}")
                continue
        
        return success_count > 0
        
    except Exception as e:
        logger.exception(f"❌ Error inesperado enviando justificaciones: {e}")
        return False

async def schedule_message_deletion(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    message_id: int,
    justification_id: int
):
    """
    Programa la eliminación automática de una justificación después del tiempo configurado.
    """
    
    # Crear una tarea asyncio para la eliminación
    async def delete_justification():
        try:
            # Esperar el tiempo configurado
            await asyncio.sleep(AUTO_DELETE_MINUTES * 60)
            
            # Intentar borrar el mensaje
            await context.bot.delete_message(chat_id=user_id, message_id=message_id)
            logger.info(f"🗑️ Auto-eliminada justificación {justification_id} del usuario {user_id}")
            
            # Notificar al usuario que se eliminó
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="🕐 La justificación se ha eliminado automáticamente por seguridad.",
                    disable_notification=True
                )
            except:
                pass  # Si no se puede notificar, no importa
                
        except TelegramError as e:
            if "message not found" not in str(e).lower():
                logger.warning(f"⚠️ No se pudo auto-eliminar justificación: {e}")
        except Exception as e:
            logger.error(f"❌ Error en auto-eliminación: {e}")
        finally:
            # Limpiar del cache
            cache_key = f"{user_id}_{message_id}"
            sent_justifications.pop(cache_key, None)
    
    # Crear y guardar la tarea
    deletion_task = asyncio.create_task(delete_justification())
    cache_key = f"{user_id}_{message_id}"
    
    sent_justifications[cache_key] = {
        "user_id": user_id,
        "message_id": message_id,
        "justification_id": justification_id,
        "sent_at": datetime.now(tz=TZ),
        "deletion_task": deletion_task
    }
    
    logger.info(f"⏰ Programada auto-eliminación de justificación {justification_id} en {AUTO_DELETE_MINUTES} minutos")

async def handle_justification_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Maneja las solicitudes de justificación que llegan vía deep-link /start just_ID1_ID2_ID3
    FUNCIÓN CORREGIDA para manejar múltiples IDs.
    """
    
    if not update.message or not update.message.text:
        return False
    
    text = update.message.text.strip()
    user_id = update.message.from_user.id
    
    # Verificar si es una solicitud de justificación
    if not text.startswith("/start just_"):
        return False
    
    # Extraer los IDs de las justificaciones
    try:
        justification_ids_str = text.replace("/start just_", "")
        justification_ids = [int(id_str) for id_str in justification_ids_str.split("_")]
    except ValueError:
        logger.warning(f"⚠️ IDs de justificación inválidos: {text}")
        await update.message.reply_text(
            "❌ Link de justificación inválido. Verifica que el enlace sea correcto."
        )
        return True
    
    logger.info(f"🔍 Solicitud de justificaciones {justification_ids} por usuario {user_id}")
    
    # Enviar mensaje de "procesando"
    processing_msg = await update.message.reply_text(
        "🔄 Obteniendo justificaciones...",
        disable_notification=True
    )
    
    # Intentar enviar las justificaciones
    success = await send_protected_justifications(context, user_id, justification_ids)
    
    # Borrar el mensaje de "procesando"
    try:
        await processing_msg.delete()
    except:
        pass
    
    if success:
        # Mensaje de éxito con información adicional
        count = len(justification_ids)
        success_text = f"✅ {count} justificación(es) enviada(s) con protección anti-copia."
        if AUTO_DELETE_MINUTES > 0:
            success_text += f"\n🕐 Se eliminarán automáticamente en {AUTO_DELETE_MINUTES} minutos."
        
        await update.message.reply_text(
            success_text,
            disable_notification=True
        )
    else:
        await update.message.reply_text(
            "❌ No se pudo obtener las justificaciones. Puede que el enlace sea inválido o haya un problema temporal.",
            disable_notification=True
        )
    
    return True

# ========= COMANDOS ADMINISTRATIVOS =========

async def cmd_test_justification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Comando para probar el sistema de justificaciones.
    Uso: /test_just <message_id>
    """
    
    if not context.args:
        await update.message.reply_text("Uso: /test_just <message_id>")
        return
    
    try:
        message_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID de mensaje inválido")
        return
    
    user_id = update.message.from_user.id
    success = await send_protected_justifications(context, user_id, [message_id])
    
    if success:
        await update.message.reply_text(f"✅ Justificación {message_id} enviada como prueba")
    else:
        await update.message.reply_text(f"❌ No se pudo enviar justificación {message_id}")

async def cmd_justification_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Muestra estadísticas del sistema de justificaciones.
    """
    
    active_justifications = len(sent_justifications)
    
    stats_text = f"""
📊 **Estadísticas de Justificaciones**

🔒 Justificaciones activas: {active_justifications}
🕐 Auto-eliminación: {'ON' if AUTO_DELETE_MINUTES > 0 else 'OFF'}
📁 Canal justificaciones: `{JUSTIFICATIONS_CHAT_ID}`

⏰ Tiempo de auto-eliminación: {AUTO_DELETE_MINUTES} minutos
"""
    
    if active_justifications > 0:
        stats_text += "\n📋 **Activas actualmente:**\n"
        for cache_key, info in list(sent_justifications.items())[:5]:  # Mostrar solo las primeras 5
            sent_time = info['sent_at'].strftime("%H:%M:%S")
            stats_text += f"• Usuario {info['user_id']} - Justif {info['justification_id']} ({sent_time})\n"
        
        if active_justifications > 5:
            stats_text += f"... y {active_justifications - 5} más\n"
    
    await update.message.reply_text(stats_text, parse_mode="Markdown")

# ========= FUNCIÓN PARA INTEGRAR CON EL BOT PRINCIPAL =========

def add_justification_handlers(application):
    """
    Agrega los handlers de justificaciones al bot principal.
    Llamar esta función desde main.py después de crear la aplicación.
    """
    
    from telegram.ext import CommandHandler
    
    # Handler para /start just_ID (debe ir ANTES del handler general de /start)
    application.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r"^/start just_[\d_]+$"), 
        handle_justification_request
    ), group=0)  # Grupo 0 para que tenga prioridad
    
    # Comandos administrativos
    application.add_handler(CommandHandler("test_just", cmd_test_justification))
    application.add_handler(CommandHandler("just_stats", cmd_justification_stats))
    
    logger.info("✅ Handlers de justificaciones agregados al bot")

# ========= FUNCIÓN PARA DETECTAR Y PROCESAR JUSTIFICACIONES =========

async def process_justification_links(
    context: ContextTypes.DEFAULT_TYPE,
    message_id: int,
    text: str,
    previous_message_id: Optional[int] = None
) -> Optional[InlineKeyboardMarkup]:
    """
    Procesa los enlaces de justificación en un mensaje y retorna el botón inline.
    
    Args:
        context: Contexto del bot
        message_id: ID del mensaje actual
        text: Texto del mensaje donde buscar enlaces
        previous_message_id: ID del mensaje anterior (para casos donde el enlace va al mensaje previo)
    
    Returns:
        InlineKeyboardMarkup con el botón de justificación o None
    """
    
    justification_ids = detect_justification_links(text)
    
    if not justification_ids:
        return None
    
    # Extraer nombre del caso si existe
    case_name = extract_case_name(text)
    
    logger.info(f"📚 Justificaciones detectadas: {justification_ids} con caso: '{case_name}'")
    
    # Determinar a qué mensaje agregar el botón
    target_message_id = previous_message_id if previous_message_id else message_id
    
    logger.info(f"🔗 Mensaje {message_id}: justificaciones {justification_ids}, caso: '{case_name}'")
    
    try:
        # Obtener info del bot para el deep-link
        bot_info = await context.bot.get_me()
        bot_username = bot_info.username
        
        # Crear el botón
        keyboard = create_justification_button(bot_username, justification_ids, case_name)
        
        logger.info(f"📎 Botón 'Ver justificación {case_name + ' ' if case_name else ''}📚' preparado para mensaje {target_message_id}")
        
        return keyboard
        
    except Exception as e:
        logger.error(f"❌ Error creando botón de justificación: {e}")
        return None

# ========= FUNCIÓN PARA USAR EN LAS PUBLICACIONES =========

async def add_justification_button_to_message(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    justification_ids: List[int],
    case_name: str = ''
) -> bool:
    """
    Agrega un botón de justificación a un mensaje ya publicado.
    
    Args:
        context: Contexto del bot
        chat_id: ID del chat donde está el mensaje
        message_id: ID del mensaje
        justification_ids: IDs de las justificaciones
        case_name: Nombre del caso (opcional)
    
    Returns:
        bool: True si se agregó exitosamente
    """
    try:
        # Obtener info del bot para el deep-link
        bot_info = await context.bot.get_me()
        bot_username = bot_info.username
        
        # Crear el botón
        keyboard = create_justification_button(bot_username, justification_ids, case_name)
        
        # Actualizar el mensaje con el botón
        await context.bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=keyboard
        )
        
        logger.info(f"✅ Botón de justificación agregado a mensaje {message_id} → justificaciones {justification_ids}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error agregando botón de justificación: {e}")
        return False
