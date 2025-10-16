# -*- coding: utf-8 -*-
import logging
import random
import re
from typing import Optional, Tuple, List
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config import DB_FILE, JUSTIFICATIONS_CHAT_ID
from database import (
    get_all_case_ids, get_user_sent_cases, get_case_by_id,
    get_daily_progress, increment_daily_progress, get_or_create_user
)

logger = logging.getLogger(__name__)

CASE_PATTERN = re.compile(r'(?:CASO[_\s]*(?:\w+[_\s]*)?)?#(\d{3,})', re.IGNORECASE)
CORRECT_PATTERN = re.compile(r'#([A-D])#', re.IGNORECASE)
ID_CLEANUP_PATTERN = re.compile(r'(?:CASO[_\s]*\w+[_\s]*)?#\d{3,}|#[A-D]#', re.IGNORECASE)

user_sessions = {}

async def detect_case_from_message(message_id: int, text: str):
    case_match = CASE_PATTERN.search(text)
    if not case_match:
        return
    
    case_id = f"#{case_match.group(1)}"
    
    correct_match = CORRECT_PATTERN.search(text)
    correct_answer = correct_match.group(1).upper() if correct_match else "A"
    
    from database import save_case
    save_case(DB_FILE, case_id, message_id, correct_answer=correct_answer)
    
    logger.info(f"Caso detectado: {case_id} → {message_id}, respuesta: {correct_answer}")

async def cmd_random_cases(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or ""
    first_name = update.effective_user.first_name or ""
    
    user = get_or_create_user(DB_FILE, user_id, username, first_name)
    
    today_solved = get_daily_progress(DB_FILE, user_id)
    limit = user["daily_limit"]
    
    if today_solved >= limit:
        await update.message.reply_text(
            f"🔥 Ya completaste tus {limit} casos de hoy.\n"
            f"Vuelve mañana a las 12:00 AM para más."
        )
        return
    
    all_cases = set(get_all_case_ids(DB_FILE))
    sent_cases = get_user_sent_cases(DB_FILE, user_id)
    
    available = all_cases - sent_cases
    
    if not available:
        available = all_cases
        await update.message.reply_text("🎉 ¡Completaste todos los casos! Reiniciando...")
    
    if not available:
        await update.message.reply_text("❌ No hay casos disponibles.")
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
    case_data = get_case_by_id(DB_FILE, case_id)
    
    if not case_data:
        await context.bot.send_message(user_id, "❌ Caso no encontrado")
        return
    
    _, message_id, correct_answer = case_data
    
    session["current_case"] = case_id
    session["correct_answer"] = correct_answer
    
    try:
        # Hacer forward temporal para extraer info
        msg = await context.bot.forward_message(
            chat_id=user_id,
            from_chat_id=JUSTIFICATIONS_CHAT_ID,
            message_id=message_id
        )
        
        text = msg.text or msg.caption or ""
        clean_text = ID_CLEANUP_PATTERN.sub('', text).strip()
        
        # Extraer file_id ANTES de borrar
        photo_id = msg.photo[-1].file_id if msg.photo else None
        doc_id = msg.document.file_id if msg.document else None
        
        # BORRAR INMEDIATAMENTE el forward
        try:
            await context.bot.delete_message(user_id, msg.message_id)
        except:
            pass
        
        # AHORA enviar versión limpia directamente
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
        from database import save_user_sent_case
        save_user_sent_case(DB_FILE, user_id, case_id)
        
    except TelegramError as e:
        logger.error(f"Error enviando caso: {e}")
        return
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("A", callback_data=f"ans_A"),
         InlineKeyboardButton("B", callback_data=f"ans_B")],
        [InlineKeyboardButton("C", callback_data=f"ans_C"),
         InlineKeyboardButton("D", callback_data=f"ans_D")]
    ])
    
    await context.bot.send_message(
        chat_id=user_id,
        text=f"📋 Caso {idx + 1}/{len(cases)}\n\n¿Cuál es tu respuesta?",
        reply_markup=keyboard
    )

async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    session = user_sessions.get(user_id)
    
    if not session or "current_case" not in session:
        await query.edit_message_text("❌ Sesión expirada. Usa /random_cases")
        return
    
    answer = query.data.split("_")[1]
    case_id = session["current_case"]
    correct = session["correct_answer"]
    
    is_correct = (answer == correct)
    
    from database import save_user_response, increment_case_stat, update_user_stats
    save_user_response(DB_FILE, user_id, case_id, answer, 1 if is_correct else 0)
    increment_case_stat(DB_FILE, case_id, answer)
    update_user_stats(DB_FILE, user_id, 1 if is_correct else 0)
    
    if is_correct:
        session["correct_count"] += 1
    
    from database import get_case_stats
    stats = get_case_stats(DB_FILE, case_id)
    total = sum(stats.values())
    
    stats_text = "\n📊 Estadísticas:\n"
    for opt in ["A", "B", "C", "D"]:
        count = stats[opt]
        pct = (count / total * 100) if total > 0 else 0
        check = "✅" if opt == correct else ""
        stats_text += f"• {opt}: {pct:.0f}% {check}\n"
    
    result_text = "🎉 ¡CORRECTO!" if is_correct else f"❌ Incorrecto. La respuesta era: {correct}"
    
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"Ver justificación 📚", callback_data=f"just_{case_id}")
    ]])
    
    await query.edit_message_text(
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
        f"🔥 Vuelve mañana para más casos!"
    )
    
    del user_sessions[user_id]
