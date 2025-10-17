# -*- coding: utf-8 -*-
import logging
import re
import asyncio
import io
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config import JUSTIFICATIONS_CHAT_ID
from database import get_justifications_for_case, increment_daily_progress

logger = logging.getLogger(__name__)

JUST_PATTERN = re.compile(r'###JUST[_\s]*(\d{3,})', re.IGNORECASE)
JUST_CLEANUP_PATTERN = re.compile(r'###JUST[_\s]*\d{3,}', re.IGNORECASE)

async def detect_justification_from_message(message_id: int, text: str):
    just_match = JUST_PATTERN.search(text)
    if not just_match:
        return
    
    case_id = f"#{just_match.group(1)}"
    
    from database import save_justification
    save_justification(case_id, message_id)
    
    logger.info(f"Justificación detectada: {case_id} → {message_id}")

async def handle_justification_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if not data.startswith("just_"):
        return
    
    case_id = data.replace("just_", "")
    user_id = query.from_user.id
    
    justification_ids = get_justifications_for_case(case_id)
    
    if not justification_ids:
        await query.edit_message_text("❌ Justificación no disponible")
        return
    
    for just_id in justification_ids:
        try:
            logger.info(f"🔄 Descargando justificación {just_id} DIRECTO (sin forward visible)")
            
            # PASO 1: Forward temporal SOLO para obtener info
            original_msg = await context.bot.forward_message(
                chat_id=user_id,
                from_chat_id=JUSTIFICATIONS_CHAT_ID,
                message_id=just_id
            )
            
            # PASO 2: Extraer datos del forward temporal
            text = original_msg.text or original_msg.caption or ""
            clean_text = JUST_CLEANUP_PATTERN.sub('', text).strip()
            
            # PASO 3: BORRAR EL FORWARD TEMPORAL INMEDIATAMENTE
            try:
                await context.bot.delete_message(user_id, original_msg.message_id)
                logger.info(f"🗑️ Forward temporal BORRADO antes de enviar limpio")
            except Exception as del_err:
                logger.warning(f"⚠️ No se pudo borrar forward temporal: {del_err}")
            
            # PASO 4: ENVIAR LIMPIO (descarga y reenvío sin "Reenviado de")
            sent_clean = False
            
            if original_msg.photo:
                logger.info(f"📸 Enviando foto LIMPIA")
                photo = original_msg.photo[-1]
                file = await context.bot.get_file(photo.file_id)
                
                photo_bytes = io.BytesIO()
                await file.download_to_memory(photo_bytes)
                photo_bytes.seek(0)
                
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=photo_bytes,
                    caption=clean_text if clean_text else None,
                    protect_content=True
                )
                sent_clean = True
                logger.info(f"✅ Foto enviada SIN REENVIAR")
            
            elif original_msg.document:
                logger.info(f"📄 Enviando documento LIMPIO")
                doc = original_msg.document
                file = await context.bot.get_file(doc.file_id)
                
                doc_bytes = io.BytesIO()
                await file.download_to_memory(doc_bytes)
                doc_bytes.seek(0)
                
                await context.bot.send_document(
                    chat_id=user_id,
                    document=doc_bytes,
                    caption=clean_text if clean_text else None,
                    filename=doc.file_name or "justificacion.pdf",
                    protect_content=True
                )
                sent_clean = True
                logger.info(f"✅ Documento enviado SIN REENVIAR")
            
            elif original_msg.video:
                logger.info(f"🎥 Enviando video LIMPIO")
                video = original_msg.video
                file = await context.bot.get_file(video.file_id)
                
                video_bytes = io.BytesIO()
                await file.download_to_memory(video_bytes)
                video_bytes.seek(0)
                
                await context.bot.send_video(
                    chat_id=user_id,
                    video=video_bytes,
                    caption=clean_text if clean_text else None,
                    protect_content=True
                )
                sent_clean = True
                logger.info(f"✅ Video enviado SIN REENVIAR")
            
            elif original_msg.audio:
                logger.info(f"🎵 Enviando audio LIMPIO")
                audio = original_msg.audio
                file = await context.bot.get_file(audio.file_id)
                
                audio_bytes = io.BytesIO()
                await file.download_to_memory(audio_bytes)
                audio_bytes.seek(0)
                
                await context.bot.send_audio(
                    chat_id=user_id,
                    audio=audio_bytes,
                    caption=clean_text if clean_text else None,
                    protect_content=True
                )
                sent_clean = True
                logger.info(f"✅ Audio enviado SIN REENVIAR")
            
            elif clean_text:
                logger.info(f"💬 Enviando texto LIMPIO")
                await context.bot.send_message(
                    chat_id=user_id,
                    text=clean_text,
                    protect_content=True
                )
                sent_clean = True
                logger.info(f"✅ Texto enviado SIN REENVIAR")
            
            await asyncio.sleep(0.3)
            
        except TelegramError as e:
            logger.error(f"❌ Error enviando justificación {just_id}: {e}")
    
    try:
        from justification_messages import get_weighted_random_message
        motivational_text = get_weighted_random_message()
    except:
        motivational_text = "📚 Justificación enviada"
    
    from cases_handler import user_sessions
    session = user_sessions.get(user_id)
    
    if session:
        session["current_index"] += 1
        increment_daily_progress(user_id)
        
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Siguiente caso ➡️", callback_data="next_case")
        ]])
        
        await context.bot.send_message(
            user_id,
            motivational_text,
            reply_markup=keyboard
        )
    else:
        await context.bot.send_message(user_id, motivational_text)

async def handle_next_case(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    from cases_handler import send_case
    await send_case(update, context, user_id)
    
    try:
        await query.message.delete()
    except:
        pass
