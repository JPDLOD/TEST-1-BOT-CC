# -*- coding: utf-8 -*-
import logging
import random
import re
from typing import Optional, Tuple, List
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config import JUSTIFICATIONS_CHAT_ID
from database import (
    get_all_case_ids, get_user_sent_cases, get_case_by_id,
    get_daily_progress, increment_daily_progress, get_or_create_user,
    save_user_sent_case, count_cases
)

logger = logging.getLogger(__name__)

# Patrones actualizados
CASE_PATTERN = re.compile(r'###CASE[_\s]*([A-Z0-9_-]+)', re.IGNORECASE)
CORRECT_PATTERN = re.compile(r'#([A-D])#', re.IGNORECASE)
ID_CLEANUP_PATTERN = re.compile(r'###CASE[_\s]*[A-Z0-9_-]+|#[A-D]#', re.IGNORECASE)

user_sessions = {}

async def cmd_random_cases(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or ""
    first_name = update.effective_user.first_name or ""
    
    user = get_or_create_user(user_id, username, first_name)
    
    today_solved = get_daily_progress(user_id)
    limit = user["daily_limit"]
    
    if today_solved >= limit:
        await update.message.reply_text(
            f"🔥 Ya completaste tus {limit} casos de hoy.\n"
            f"Vuelve mañana a las 12:00 AM para más.",
            reply_markup=ReplyKeyboardRemove()
        )
        return
    
    all_cases = set(get_all_case_ids())
    
    if not all_cases:
        total = count_cases()
        await update.message.reply_text(
            f"❌ No hay casos disponibles\n\n"
            f"📊 Casos en BD: {total}\n\n"
            f"💡 **Formatos aceptados:**\n"
            f"`###CASE_0001 #A#`\n"
            f"`###CASE_0001_PED_DENGUE #C#`\n"
            f"`###CASE CASO_GYO_0008 #B#`\n\n"
            f"📌 **Recuerda:**\n"
            f"• Usa `#LETRA#` para la respuesta\n"
            f"• Las letras son: A, B, C o D\n"
            f"• El bot detecta casos automáticamente",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
        return
    
    sent_cases = get_user_sent_cases(user_id)
    available = all_cases - sent_cases
    
    if not available:
        available = all_cases
        await update.message.reply_text("🎉 ¡Completaste todos los casos! Reiniciando...")
    
    if not available:
        await update.message.reply_text(
            "❌ No hay casos disponibles.",
            reply_markup=ReplyKeyboardRemove()
        )
        return
    
    cases_to_send = min(5, limit - today_solved)
    selected = random.sample(list(available), min(cases_to_send, len(available)))
    
    user_sessions[user_id] = {
        "cases": selected,
        "current_index": 0,
        "correct_count": 0
    }
    
    await send_case(update, context, user_id)

async def send_case(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    session = user_sessions.get(user_id)
    if not session:
        return
    
    idx = session["current_index"]
    cases = session["cases"]
    
    if idx >= len(cases):
        await finish_session(update, context, user_id)
        return
    
    case_id = cases[idx]
    case_data = get_case_by_id(case_id)
    
    if not case_data:
        await context.bot.send_message(
            user_id, 
            "❌ Caso no encontrado",
            reply_markup=ReplyKeyboardRemove()
        )
        return
    
    _, message_id, correct_answer = case_data
    
    session["current_case"] = case_id
    session["correct_answer"] = correct_answer
    
    try:
        # Forward temporal para extraer info
        msg = await context.bot.forward_message(
            chat_id=user_id,
            from_chat_id=JUSTIFICATIONS_CHAT_ID,
            message_id=message_id
        )
        
        text = msg.text or msg.caption or ""
        clean_text = ID_CLEANUP_PATTERN.sub('', text).strip()
        
        # Extraer file_ids ANTES de borrar
        photo_id = msg.photo[-1].file_id if msg.photo else None
        doc_id = msg.document.file_id if msg.document else None
        
        # BORRAR forward inmediatamente
        try:
            await context.bot.delete_message(user_id, msg.message_id)
        except:
            pass
        
        # Enviar versión limpia
        if photo_id:
            await context.bot.send_photo(
                chat_id=user_id,
                photo=photo_id,
                caption=clean_text if clean_text else None
            )
        elif doc_id:
            await context.bot.send_document(
                chat_id=user_id,
                document=doc_id,
                caption=clean_text if clean_text else None
            )
        elif clean_text:
            await context.bot.send_message(chat_id=user_id, text=clean_text)
        
        # Guardar que enviamos este caso
        save_user_sent_case(user_id, case_id)
        
    except TelegramError as e:
        logger.error(f"Error enviando caso: {e}")
        return
    
    # REPLY KEYBOARD (botones en la barra inferior)
    keyboard = [
        [KeyboardButton("A"), KeyboardButton("B")],
        [KeyboardButton("C"), KeyboardButton("D")]
    ]
    reply_markup = ReplyKeyboardMarkup(
        keyboard, 
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Selecciona tu respuesta..."
    )
    
    await context.bot.send_message(
        chat_id=user_id,
        text=f"📋 Caso {idx + 1}/{len(cases)}\n\n¿Cuál es tu respuesta?",
        reply_markup=reply_markup
    )

async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja respuestas de los botones A, B, C, D"""
    user_id = update.effective_user.id
    text = update.message.text.strip().upper()
    
    if text not in ["A", "B", "C", "D"]:
        return
    
    session = user_sessions.get(user_id)
    
    if not session or "current_case" not in session:
        await update.message.reply_text(
            "❌ Sesión expirada. Usa /random_cases",
            reply_markup=ReplyKeyboardRemove()
        )
        return
    
    answer = text
    case_id = session["current_case"]
    correct = session["correct_answer"]
    
    is_correct = (answer == correct)
    
    from database import save_user_response, increment_case_stat, update_user_stats
    save_user_response(user_id, case_id, answer, 1 if is_correct else 0)
    increment_case_stat(case_id, answer)
    update_user_stats(user_id, 1 if is_correct else 0)
    
    if is_correct:
        session["correct_count"] += 1
    
    from database import get_case_stats
    stats = get_case_stats(case_id)
    total = sum(stats.values())
    
    stats_text = "\n📊 Estadísticas:\n"
    for opt in ["A", "B", "C", "D"]:
        count = stats[opt]
        pct = (count / total * 100) if total > 0 else 0
        check = "✅" if opt == correct else ""
        stats_text += f"• {opt}: {pct:.0f}% {check}\n"
    
    result_text = "🎉 ¡CORRECTO!" if is_correct else f"❌ Incorrecto. La respuesta era: {correct}"
    
    # Botón para ver justificación (inline)
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"Ver justificación 📚", callback_data=f"just_{case_id}")
    ]])
    
    await update.message.reply_text(
        f"{result_text}\n{stats_text}",
        reply_markup=keyboard
    )
    
    if is_correct:
        try:
            sticker_ids = [
                "CAACAgIAAxkBAAEMYjZnYP5T9k7LRgABm0VZhqP-AAFU8TkAAh0AA2J5xgoj3b0zzBYmwB4E",
                "CAACAgIAAxkBAAEMYjhnYP5kKQABbNf1N-WkBqCYe9fO4wACHgADYnnGCqOTU8YAAcYHNh4E"
            ]
            await context.bot.send_sticker(user_id, random.choice(sticker_ids))
        except:
            pass

async def finish_session(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    session = user_sessions.get(user_id)
    if not session:
        return
    
    total = len(session["cases"])
    correct = session["correct_count"]
    
    await context.bot.send_message(
        user_id,
        f"🏁 Sesión completada!\n\n"
        f"✅ Respondiste correctamente: {correct}/{total}\n"
        f"📈 Progreso: {'█' * correct}{'░' * (total - correct)}\n\n"
        f"🔥 Vuelve mañana para más casos!",
        reply_markup=ReplyKeyboardRemove()
    )
    
    del user_sessions[user_id]
```

---

## 4️⃣ **runtime.txt**
```
python-3.11
```

---

## 5️⃣ **requirements.txt**
```
python-telegram-bot[job-queue]==21.6
psycopg2-binary==2.9.10
```

---

## 🎯 INSTRUCCIONES

1. **Copia estos 5 archivos** exactamente como están
2. **Sube a GitHub**
3. **Deploy en Render**
4. **Configura las variables de entorno en Render:**
```
BOT_TOKEN=8364968927:AAFSDwTr9TZfkbQfpe2EWMVZEkYnTXjNCKw
DATABASE_URL=(automático)
JUSTIFICATIONS_CHAT_ID=-1003058530208
FREE_CHANNEL_ID=-1002717125281
SUBS_CHANNEL_ID=-1003042227035
ADMIN_USER_IDS=TU_USER_ID
DAILY_CASE_LIMIT=5
TIMEZONE=America/Bogota
PAUSE=0.3
```

5. **Envia un caso con este formato:**
```
###CASE_0001 #C#

Pregunta del caso aquí...
