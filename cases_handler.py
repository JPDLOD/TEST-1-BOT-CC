# -*- coding: utf-8 -*-
import logging
import random
import re
import asyncio
from typing import Set
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import ContextTypes
from telegram.error import TelegramError, RetryAfter

from config import JUSTIFICATIONS_CHAT_ID
from database import (
    get_all_case_ids, get_user_sent_cases, get_case_by_id,
    get_daily_progress, increment_daily_progress, get_or_create_user,
    save_user_sent_case, count_cases, delete_case,
    save_user_response, increment_case_stat, update_user_stats, get_case_stats
)

logger = logging.getLogger(__name__)

CASE_PATTERN = re.compile(r'###CASE[_\s]*([A-Z0-9_-]+)', re.IGNORECASE)
CORRECT_PATTERN = re.compile(r'#([A-D])#', re.IGNORECASE)
ID_CLEANUP_PATTERN = re.compile(r'###CASE[_\s]*[A-Z0-9_-]+|#[A-D]#', re.IGNORECASE)

user_sessions = {}
deleted_cases_cache: Set[str] = set()

MAX_RETRIES = 3
RETRY_DELAY = 2

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
            f"`###CASE_0001_PED_DENGUE #C#`\n\n"
            f"📌 **Recuerda:**\n"
            f"• Usa `#LETRA#` para la respuesta\n"
            f"• Las letras son: A, B, C o D",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
        return
    
    sent_cases = get_user_sent_cases(user_id)
    available = all_cases - sent_cases
    
    if not available:
        available = all_cases
        await update.message.reply_text("🎉 ¡Completaste todos los casos! Reiniciando...")
    
    # CORREGIDO: Usar límite restante del usuario
    remaining = limit - today_solved
    cases_to_send = min(remaining, len(available))
    selected = random.sample(list(available), cases_to_send)
    
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
        logger.warning(f"⚠️ Caso {case_id} no existe en DB")
        session["current_index"] += 1
        await send_case(update, context, user_id)
        return
    
    _, message_id, correct_answer = case_data
    
    session["current_case"] = case_id
    session["correct_answer"] = correct_answer
    
    # SOLUCIÓN DEFINITIVA: Extraer file_id y enviar limpio (1 SOLA VEZ)
    tries = 0
    while tries < MAX_RETRIES:
        try:
            # 1. Forward temporal para extraer file_id
            msg = await context.bot.forward_message(
                chat_id=user_id,
                from_chat_id=JUSTIFICATIONS_CHAT_ID,
                message_id=message_id
            )
            
            # 2. Extraer datos
            text = msg.text or msg.caption or ""
            clean_text = ID_CLEANUP_PATTERN.sub('', text).strip()
            
            photo_id = msg.photo[-1].file_id if msg.photo else None
            doc_id = msg.document.file_id if msg.document else None
            
            # 3. BORRAR FORWARD INMEDIATAMENTE
            try:
                await context.bot.delete_message(user_id, msg.message_id)
            except:
                pass
            
            # 4. ENVIAR LIMPIO (UNA SOLA VEZ)
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
            
            save_user_sent_case(user_id, case_id)
            break
            
        except RetryAfter as e:
            wait = e.retry_after
            logger.warning(f"⚠️ Rate limit: esperar {wait}s")
            await asyncio.sleep(wait + 1)
            continue
            
        except TelegramError as e:
            error = str(e).lower()
            
            if "message to forward not found" in error or "message not found" in error:
                if case_id not in deleted_cases_cache:
                    logger.warning(f"⚠️ CASO ELIMINADO: {case_id} (msg_id: {message_id})")
                    deleted_cases_cache.add(case_id)
                    delete_case(case_id)
                
                session["current_index"] += 1
                await send_case(update, context, user_id)
                return
            
            tries += 1
            if tries < MAX_RETRIES:
                logger.warning(f"⚠️ Intento {tries}/{MAX_RETRIES} falló: {error}")
                await asyncio.sleep(RETRY_DELAY)
            else:
                logger.error(f"❌ TIMEOUT persistente: {case_id} - Saltando al siguiente")
                session["current_index"] += 1
                await send_case(update, context, user_id)
                return
    
    # CORREGIDO: Mostrar contador dinámico
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
    
    save_user_response(user_id, case_id, answer, 1 if is_correct else 0)
    increment_case_stat(case_id, answer)
    update_user_stats(user_id, 1 if is_correct else 0)
    
    if is_correct:
        session["correct_count"] += 1
    
    stats = get_case_stats(case_id)
    total = sum(stats.values())
    
    stats_text = "\n📊 Estadísticas:\n"
    for opt in ["A", "B", "C", "D"]:
        count = stats[opt]
        pct = (count / total * 100) if total > 0 else 0
        check = "✅" if opt == correct else ""
        stats_text += f"• {opt}: {pct:.0f}% {check}\n"
    
    result_text = "🎉 ¡CORRECTO!" if is_correct else f"❌ Incorrecto. La respuesta era: {correct}"
    
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
    percentage = int((correct / total) * 100) if total > 0 else 0
    
    # CORREGIDO: Puntaje más claro y escalable
    await context.bot.send_message(
        user_id,
        f"🏁 Sesión completada!\n\n"
        f"🎯 Puntaje: {correct}/{total} ({percentage}%)\n"
        f"✅ Correctas: {correct}\n"
        f"❌ Incorrectas: {total - correct}\n\n"
        f"🔥 ¡Vuelve mañana para más casos!",
        reply_markup=ReplyKeyboardRemove()
    )
    
    del user_sessions[user_id]
