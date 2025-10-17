# -*- coding: utf-8 -*-
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import ADMIN_USER_IDS
from database import set_user_limit, set_user_subscriber, get_or_create_user, get_all_case_ids

logger = logging.getLogger(__name__)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Estadísticas", callback_data="admin_stats")],
        [InlineKeyboardButton("👥 Gestionar usuarios", callback_data="admin_users")],
        [InlineKeyboardButton("📚 Info casos", callback_data="admin_cases")]
    ])
    
    await update.message.reply_text("🔐 Panel de Administración\n\nSelecciona una opción", reply_markup=keyboard)

async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if not is_admin(query.from_user.id):
        return
    
    data = query.data
    
    if data == "admin_stats":
        from database import get_all_users, get_subscribers
        total_users = len(get_all_users())
        subs = len(get_subscribers())
        cases = len(get_all_case_ids())
        await query.edit_message_text(f"📊 Estadísticas\n\n👥 Usuarios totales: {total_users}\n⭐ Subscriptores: {subs}\n📚 Casos disponibles: {cases}")
    
    elif data == "admin_users":
        await query.edit_message_text("👥 Gestión de Usuarios\n\nComandos:\n/set_limit USER_ID 10 - Cambiar límite\n/set_sub USER_ID 1 - Activar subscripción")
    
    elif data == "admin_cases":
        cases = get_all_case_ids()
        await query.edit_message_text(f"📚 Casos en base de datos: {len(cases)}\n\nPrimeros 10:\n" + "\n".join(cases[:10]))

async def cmd_set_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /set_limit USER_ID 10")
        return
    
    try:
        user_id = int(context.args[0])
        limit = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ USER_ID y límite deben ser números")
        return
    
    get_or_create_user(user_id, "", "Usuario")
    set_user_limit(user_id, limit)
    await update.message.reply_text(f"✅ Límite de usuario {user_id} actualizado a {limit}")

async def cmd_set_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /set_sub USER_ID 1")
        return
    
    try:
        user_id = int(context.args[0])
        is_sub = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ USER_ID y valor deben ser números")
        return
    
    get_or_create_user(user_id, "", "Usuario")
    set_user_subscriber(user_id, is_sub)
    status = "activada" if is_sub else "desactivada"
    await update.message.reply_text(f"✅ Subscripción de usuario {user_id} {status}")
