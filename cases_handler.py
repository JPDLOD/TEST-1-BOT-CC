# -*- coding: utf-8 -*-
import logging
import random
import re
import asyncio
from typing import Set
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import ContextTypes
from telegram.error import TelegramError, RetryAfter

from database import (
    get_all_case_ids, get_user_sent_cases, get_case_by_id,
    get_daily_progress, increment_daily_progress, get_or_create_user,
    save_user_sent_case, count_cases, delete_case,
    save_user_response, increment_case_stat, update_user_stats, get_case_stats
)

logger = logging.getLogger(__name__)

user_sessions = {}
deleted_cases_cache: Set[str] = set()

MAX_RETRIES = 3
RETRY_DELAY = 2

async def cmd_random_cases(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """VERSIÓN CON DEBUG EXTREMO"""
    try:
        logger.info("🔥 COMANDO /random_cases EJECUTADO")
        await update.message.reply_text("🔄 Procesando solicitud...")
        
        user_id = update.effective_user.id
        username = update.effective_user.username or ""
        first_name = update.effective_user.first_name or ""
        
        logger.info(f"👤 Usuario: {user_id} (@{username})")
        
        user = get_or_create_user(user_id, username, first_name)
        logger.info(f"✅ Usuario creado/recuperado: {user}")
        
        today_solved = get_daily_progress(user_id)
        limit = user["daily_limit"]
        
        logger.info(f"📊 Progreso hoy: {today_solved}/{limit}")
        
        if today_solved >= limit:
            await update.message.reply_text(
                f"🔥 Ya completaste tus {limit} casos de hoy.\n"
                f"Vuelve mañana a las 12:00 AM para más.",
                reply_markup=ReplyKeyboardRemove()
            )
            return
        
        # VERIFICACIÓN EXHAUSTIVA DE CASOS
        total_in_db = count_cases()
        logger.info(f"📚 Total casos en BD: {total_in_db}")
        await update.message.reply_text(f"📚 Total casos en BD: {total_in_db}")
        
        all_cases = set(get_all_case_ids())
        logger.info(f"📋 Casos recuperados: {len(all_cases)}")
        await update.message.reply_text(f"📋 Casos recuperados: {len(all_cases)}")
        
        if all_cases:
            logger.info(f"🔍 Primeros 5 casos: {list(all_cases)[:5]}")
            await update.message.reply_text(f"🔍 Muestra: {list(all_cases)[:5]}")
        
        if not all_cases:
            await update.message.reply_text(
                f"❌ No hay casos disponibles\n\n"
                f"📊 Total en BD: {total_in_db}\n"
                f"🔍 IDs recuperados: {len(all_cases)}",
                reply_markup=ReplyKeyboardRemove()
            )
            return
        
        sent_cases = get_user_sent_cases(user_id)
        logger.info(f"📤 Casos ya enviados al usuario: {len(sent_cases)}")
        
        available = all_cases - sent_cases
        logger.info(f"✅ Casos disponibles: {len(available)}")
        await update.message.reply_text(f"✅ Casos disponibles para ti: {len(available)}")
        
        # Si completó todos, resetear
        if not available:
            from database import _get_conn, USE_POSTGRES
            conn = _get_conn()
            if USE_POSTGRES:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM user_sent_cases WHERE user_id=%s", (user_id,))
            else:
                conn.execute("DELETE FROM user_sent_cases WHERE user_id=?", (user_id,))
                conn.commit()
            
            await update.message.reply_text("🎉 ¡Completaste todos los casos! 🔄 Reiniciando catálogo...")
            available = all_cases
        
        if not available:
            await update.message.reply_text(
                "❌ No hay casos disponibles después del reset.\n\n"
                "Contacta al administrador.",
                reply_markup=ReplyKeyboardRemove()
            )
            return
        
        remaining = limit - today_solved
        cases_to_send = min(remaining, len(available))
        selected = random.sample(list(available), cases_to_send)
        
        logger.info(f"🎯 Casos seleccionados: {selected}")
        await update.message.reply_text(f"🎯 Enviando {len(selected)} casos...")
        
        user_sessions[user_id] = {
            "cases": selected,
            "current_index": 0,
            "correct_count": 0
        }
        
        await send_case(update, context, user_id)
        
    except Exception as e:
        logger.exception(f"💥 ERROR CRÍTICO en cmd_random_cases: {e}")
        await update.message.reply_text(f"💥 ERROR: {str(e)}")

async def send_case(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    session = user_sessions.get(user_id)
    if not session:
        await context.bot.send_message(user_id, "❌ Sesión no encontrada")
        return
    
    idx = session["current_index"]
    cases = session["cases"]
    
    if idx >= len(cases):
        await finish_session(update, context, user_id)
        return
    
    case_id = cases[idx]
    logger.info(f"📤 Intentando enviar caso: {case_id}")
    
    case_data = get_case_by_id(case_id)
    
    if not case_data:
        logger.warning(f"⚠️ Caso {case_id} no existe en DB")
        await context.bot.send_message(user_id, f"⚠️ Caso {case_id} no existe")
        session["current_index"] += 1
        await send_case(update, context, user_id)
        return
    
    _, file_id, file_type, caption, correct_answer = case_data
    logger.info(f"✅ Caso encontrado: tipo={file_type}, respuesta={correct_answer}")
    
    session["current_case"] = case_id
    session["correct_answer"] = correct_answer
    
    tries = 0
    while tries < MAX_RETRIES:
        try:
            logger.info(f"📤 Enviando caso {case_id} ({file_type})")
            
            if file_type == "document":
                await context.bot.send_document(chat_id=user_id, document=file_id, caption=caption if caption else None)
            elif file_type == "photo":
                await context.bot.send_photo(chat_id=user_id, photo=file_id, caption=caption if caption else None)
            elif file_type == "video":
                await context.bot.send_video(chat_id=user_id, video=file_id, caption=caption if caption else None)
            elif file_type == "audio":
                await context.bot.send_audio(chat_id=user_id, audio=file_id, caption=caption if caption else None)
            elif file_type == "voice":
                await context.bot.send_voice(chat_id=user_id, voice=file_id)
                if caption:
                    await context.bot.send_message(chat_id=user_id, text=caption)
            elif file_type == "text":
                await context.bot.send_message(chat_id=user_id, text=caption)
            
            logger.info(f"✅ Caso {case_id} enviado exitosamente")
            save_user_sent_case(user_id, case_id)
            break
            
        except RetryAfter as e:
            wait = e.retry_after
            logger.warning(f"⚠️ Rate limit: esperar {wait}s")
            await asyncio.sleep(wait + 1)
            continue
            
        except TelegramError as e:
            error = str(e).lower()
            logger.error(f"❌ Error Telegram: {error}")
            
            if "file_id" in error or "not found" in error:
                if case_id not in deleted_cases_cache:
                    logger.warning(f"⚠️ file_id inválido: {case_id}")
                    deleted_cases_cache.add(case_id)
                    delete_case(case_id)
                
                session["current_index"] += 1
                await send_case(update, context, user_id)
                return
            
            tries += 1
            if tries < MAX_RETRIES:
                logger.warning(f"⚠️ Intento {tries}/{MAX_RETRIES}")
                await asyncio.sleep(RETRY_DELAY)
            else:
                logger.error(f"❌ Saltando caso {case_id}")
                session["current_index"] += 1
                await send_case(update, context, user_id)
                return
    
    keyboard = [
        [KeyboardButton("A"), KeyboardButton("B")],
        [KeyboardButton("C"), KeyboardButton("D")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    
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
        await update.message.reply_text("❌ Sesión expirada. Usa /random_cases", reply_markup=ReplyKeyboardRemove())
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
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(f"Ver justificación 📚", callback_data=f"just_{case_id}")]])
    
    await update.message.reply_text(f"{result_text}\n{stats_text}", reply_markup=keyboard)
    
    if is_correct:
        try:
            sticker_ids = ["CAACAgIAAxkBAAEMYjZnYP5T9k7LRgABm0VZhqP-AAFU8TkAAh0AA2J5xgoj3b0zzBYmwB4E"]
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
    
    await context.bot.send_message(
        user_id,
        f"🏁 **Sesión completada!**\n\n"
        f"🎯 **Puntaje:** {correct}/{total} ({percentage}%)\n\n"
        f"✅ Correctas: {correct}\n"
        f"❌ Incorrectas: {total - correct}\n\n"
        f"🔥 ¡Vuelve mañana para más casos!",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    
    del user_sessions[user_id]
