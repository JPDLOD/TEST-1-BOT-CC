# -*- coding: utf-8 -*-

“””
Sistema de Justificaciones Protegidas
Versión mejorada con soporte múltiple y limpieza automática
“””

import logging
import asyncio
import re
from typing import Optional, Dict, Set, List, Tuple
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config import TZ

logger = logging.getLogger(**name**)

# ========= CONFIGURACIÓN DE JUSTIFICACIONES =========

JUSTIFICATIONS_CHAT_ID = -1003058530208  # Canal de justificaciones
AUTO_DELETE_MINUTES = 10  # Tiempo antes de borrar la justificación (0 = no borrar)

# Cache para rastrear mensajes enviados

sent_justifications: Dict[str, Dict] = {}  # {user_id: {message_ids: [], timer_task}}
user_joke_messages: Dict[int, List[int]] = {}  # {user_id: [message_ids]}

# ========= FUNCIONES AUXILIARES =========

def parse_justification_links(text: str) -> Tuple[List[int], str]:
“””
Extrae múltiples IDs de justificación y el nombre del caso del texto.
Soporta formatos:
- “CASO #3 https://t.me/ccjustificaciones/11”
- “https://t.me/ccjustificaciones/11,12,13”
- “https://t.me/ccjustificaciones/11-15”
- Múltiples links: “https://t.me/ccjustificaciones/11 https://t.me/ccjustificaciones/12”

```
Returns:
    (lista_de_ids, nombre_del_caso)
"""
justification_ids = []
case_name = ""

# Buscar nombre del caso (CASO #X o cualquier texto antes del primer link)
case_pattern = re.search(r'^(.*?)(?=https://)', text)
if case_pattern:
    potential_case = case_pattern.group(1).strip()
    if potential_case:
        # Limpiar emojis comunes y caracteres
        case_name = potential_case.replace("📚", "").replace("*", "").replace("_", "").strip()

# Patrón para detectar todos los formatos de links
# Soporta: /11  /11,12,13  /11-15  y múltiples links separados
link_pattern = re.compile(r'https?://t\.me/ccjustificaciones/(\d+(?:[,\-]\d+)*)', re.IGNORECASE)

# Encontrar todos los matches
for match in link_pattern.finditer(text):
    id_string = match.group(1)
    
    # Procesar rangos y comas
    parts = id_string.split(',')
    for part in parts:
        if '-' in part:
            # Es un rango
            try:
                start, end = map(int, part.split('-'))
                justification_ids.extend(range(start, end + 1))
            except:
                pass
        else:
            # Es un ID simple
            try:
                justification_ids.append(int(part))
            except:
                pass

# Eliminar duplicados y ordenar
justification_ids = sorted(list(set(justification_ids)))

return justification_ids, case_name
```

def generate_justification_deep_link(bot_username: str, message_ids: List[int]) -> str:
“””
Genera el deep-link para una o múltiples justificaciones.
Formato: https://t.me/BotUsername?start=just_ID1_ID2_ID3
“””
ids_string = “*”.join(map(str, message_ids))
return f”https://t.me/{bot_username}?start=just*{ids_string}”

def create_justification_button(bot_username: str, message_ids: List[int], case_name: str = “”) -> InlineKeyboardMarkup:
“””
Crea el botón inline con el nombre del caso si está disponible.
“””
deep_link = generate_justification_deep_link(bot_username, message_ids)

```
# Personalizar el texto del botón según el caso
if case_name:
    # Limpiar el nombre del caso de caracteres especiales
    clean_case = case_name.replace("*", "").replace("_", "").strip()
    button_text = f"Ver justificación {clean_case} 📚"
else:
    button_text = "Ver justificación 📚"

button = InlineKeyboardButton(button_text, url=deep_link)
return InlineKeyboardMarkup([[button]])
```

async def clean_previous_messages(context: ContextTypes.DEFAULT_TYPE, user_id: int):
“””
Limpia TODOS los mensajes previos del usuario (justificaciones, chistes, comandos).
“””
# Limpiar justificaciones previas
user_key = str(user_id)
if user_key in sent_justifications:
user_data = sent_justifications[user_key]

```
    # Cancelar timer si existe
    if "timer_task" in user_data and user_data["timer_task"]:
        user_data["timer_task"].cancel()
    
    # Borrar mensajes de justificación
    for msg_id in user_data.get("message_ids", []):
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
        except:
            pass
    
    # Limpiar del cache
    del sent_justifications[user_key]

# Limpiar mensajes de chistes y comandos
if user_id in user_joke_messages:
    for msg_id in user_joke_messages[user_id]:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
        except:
            pass
    del user_joke_messages[user_id]

# Intentar borrar mensajes de comando /start recientes (últimos 10 mensajes)
try:
    # Borrar hasta 10 mensajes previos para limpiar comandos
    for offset in range(1, 11):
        try:
            # Intentar borrar mensaje por offset desde el actual
            await context.bot.delete_message(
                chat_id=user_id, 
                message_id=update.message.message_id - offset
            )
        except:
            pass
except:
    pass
```

async def send_protected_justifications(
context: ContextTypes.DEFAULT_TYPE,
user_id: int,
justification_message_ids: List[int]
) -> bool:
“””
Envía múltiples justificaciones protegidas al usuario.
“””
try:
# Limpiar mensajes previos ANTES de enviar nuevas justificaciones
await clean_all_previous_messages(context, user_id, 0)

```
    sent_messages = []
    
    for justification_id in justification_message_ids:
        try:
            logger.info(f"📋 Enviando justificación {justification_id} a usuario {user_id}")
            
            # Copiar el mensaje desde el canal de justificaciones
            copied_message = await context.bot.copy_message(
                chat_id=user_id,
                from_chat_id=JUSTIFICATIONS_CHAT_ID,
                message_id=justification_id,
                protect_content=True
            )
            
            if copied_message:
                sent_messages.append(copied_message.message_id)
                logger.info(f"✅ Justificación {justification_id} enviada")
            
            # Pequeña pausa entre mensajes
            if len(justification_message_ids) > 1:
                await asyncio.sleep(0.3)
                
        except TelegramError as e:
            logger.error(f"❌ Error enviando justificación {justification_id}: {e}")
            continue
    
    if not sent_messages:
        return False
    
    # Guardar referencias y programar eliminación
    user_key = str(user_id)
    sent_justifications[user_key] = {
        "message_ids": sent_messages,
        "sent_at": datetime.now(tz=TZ),
        "timer_task": None
    }
    
    # Programar auto-eliminación si está configurada
    if AUTO_DELETE_MINUTES > 0:
        await schedule_messages_deletion(context, user_id, sent_messages)
    
    return True
    
except Exception as e:
    logger.exception(f"❌ Error inesperado enviando justificaciones: {e}")
    return False
```

async def schedule_messages_deletion(
context: ContextTypes.DEFAULT_TYPE,
user_id: int,
message_ids: List[int]
):
“””
Programa la eliminación automática de múltiples mensajes.
“””
async def delete_messages():
try:
await asyncio.sleep(AUTO_DELETE_MINUTES * 60)

```
        # Borrar todas las justificaciones
        for msg_id in message_ids:
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
            except:
                pass
        
        # Borrar mensajes de chistes asociados
        if user_id in user_joke_messages:
            for msg_id in user_joke_messages[user_id]:
                try:
                    await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
                except:
                    pass
            del user_joke_messages[user_id]
        
        logger.info(f"🗑️ Auto-eliminadas justificaciones del usuario {user_id}")
        
    except Exception as e:
        logger.error(f"❌ Error en auto-eliminación: {e}")
    finally:
        # Limpiar del cache
        user_key = str(user_id)
        if user_key in sent_justifications:
            del sent_justifications[user_key]

# Crear y guardar la tarea
deletion_task = asyncio.create_task(delete_messages())

user_key = str(user_id)
if user_key in sent_justifications:
    sent_justifications[user_key]["timer_task"] = deletion_task

logger.info(f"⏰ Programada auto-eliminación en {AUTO_DELETE_MINUTES} minutos")
```

async def handle_justification_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
“””
Maneja las solicitudes de justificación con soporte para múltiples IDs.
“””
if not update.message or not update.message.text:
return False

```
text = update.message.text.strip()
user_id = update.message.from_user.id

# Verificar si es una solicitud de justificación
if not text.startswith("/start just_"):
    return False

# Extraer los IDs de justificación (pueden ser múltiples)
try:
    ids_string = text.replace("/start just_", "")
    justification_ids = [int(id_str) for id_str in ids_string.split("_") if id_str.isdigit()]
    
    if not justification_ids:
        raise ValueError("No se encontraron IDs válidos")
        
except ValueError:
    logger.warning(f"⚠️ IDs de justificación inválidos: {text}")
    await update.message.reply_text(
        "❌ Link de justificación inválido. Verifica que el enlace sea correcto."
    )
    return True

logger.info(f"🔍 Solicitud de justificaciones {justification_ids} por usuario {user_id}")

# Enviar mensaje de "procesando"
processing_msg = await update.message.reply_text(
    "🔄 Obteniendo justificación..." if len(justification_ids) == 1 else f"🔄 Obteniendo {len(justification_ids)} justificaciones...",
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
    # Importar mensajes creativos
    try:
        from justification_messages import get_random_message
        success_text = get_random_message()
    except ImportError:
        import random
        fallback_messages = [
            "📚 ¡Justificación lista! Revisa con calma.",
            "✨ Material de estudio enviado.",
            "🎯 ¡Justificación disponible!",
            "📖 Contenido académico listo para revisar.",
        ]
        success_text = random.choice(fallback_messages)
    
    joke_msg = await update.message.reply_text(
        success_text,
        disable_notification=True
    )
    
    # Guardar referencia del mensaje de chiste
    if user_id not in user_joke_messages:
        user_joke_messages[user_id] = []
    user_joke_messages[user_id].append(joke_msg.message_id)
    
else:
    await update.message.reply_text(
        "❌ No se pudo obtener la justificación. Puede que el enlace sea inválido o haya un problema temporal.",
        disable_notification=True
    )

return True
```

# ========= INTEGRACIÓN CON PUBLISHER PARA DETECTAR CASOS =========

def extract_justification_info(text: str) -> Tuple[List[int], str]:
“””
Función helper para el publisher.
Extrae IDs de justificación y el nombre del caso.
“””
return parse_justification_links(text)

# ========= COMANDOS ADMINISTRATIVOS =========

async def cmd_test_justification(update: Update, context: ContextTypes.DEFAULT_TYPE):
“””
Comando para probar el sistema de justificaciones.
Uso: /test_just <message_id> o /test_just <id1,id2,id3>
“””
if not context.args:
await update.message.reply_text(“Uso: /test_just <message_id> o /test_just <id1,id2,id3>”)
return

```
try:
    # Soportar múltiples IDs separados por comas
    ids_str = context.args[0]
    if ',' in ids_str:
        message_ids = [int(id.strip()) for id in ids_str.split(',')]
    else:
        message_ids = [int(ids_str)]
        
except ValueError:
    await update.message.reply_text("❌ ID(s) de mensaje inválido(s)")
    return

user_id = update.message.from_user.id
success = await send_protected_justifications(context, user_id, message_ids)

if success:
    await update.message.reply_text(f"✅ Justificación(es) {message_ids} enviada(s) como prueba")
else:
    await update.message.reply_text(f"❌ No se pudieron enviar justificaciones {message_ids}")
```

async def cmd_justification_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
“””
Muestra estadísticas del sistema de justificaciones.
“””
active_justifications = len(sent_justifications)
total_messages = sum(len(data.get(“message_ids”, [])) for data in sent_justifications.values())

```
stats_text = f"""
```

📊 **Estadísticas de Justificaciones**

🔒 Usuarios con justificaciones activas: {active_justifications}
📝 Total de mensajes enviados: {total_messages}
🕐 Auto-eliminación: {‘ON’ if AUTO_DELETE_MINUTES > 0 else ‘OFF’}
📁 Canal justificaciones: `{JUSTIFICATIONS_CHAT_ID}`
⏰ Tiempo de auto-eliminación: {AUTO_DELETE_MINUTES} minutos
“””

```
if active_justifications > 0:
    stats_text += "\n📋 **Usuarios activos:**\n"
    for user_key, info in list(sent_justifications.items())[:5]:
        sent_time = info['sent_at'].strftime("%H:%M:%S")
        num_msgs = len(info.get('message_ids', []))
        stats_text += f"• Usuario {user_key}: {num_msgs} mensajes ({sent_time})\n"
    
    if active_justifications > 5:
        stats_text += f"... y {active_justifications - 5} usuarios más\n"

await update.message.reply_text(stats_text, parse_mode="Markdown")
```

# ========= FUNCIÓN PARA INTEGRAR CON EL BOT PRINCIPAL =========

def add_justification_handlers(application):
“””
Agrega los handlers de justificaciones al bot principal.
“””
from telegram.ext import CommandHandler, MessageHandler, filters

```
# Handler para /start just_ID
application.add_handler(MessageHandler(
    filters.TEXT & filters.Regex(r"^/start just_\d+"), 
    handle_justification_request
), group=0)

# Comandos administrativos
application.add_handler(CommandHandler("test_just", cmd_test_justification))
application.add_handler(CommandHandler("just_stats", cmd_justification_stats))

logger.info("✅ Handlers de justificaciones agregados al bot")
```