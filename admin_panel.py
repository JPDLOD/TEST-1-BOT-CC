# -*- coding: utf-8 -*-
import logging
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import ADMIN_USER_IDS
from database import set_user_limit, set_user_subscriber, get_or_create_user, get_all_case_ids

logger = logging.getLogger(__name__)

BUTTON_PATTERN = re.compile(r'@@@\s*([^|]+?)\s*\|\s*(.+)', re.IGNORECASE)
LINK_PATTERN = re.compile(r'%%%\s*([^|]+?)\s*\|\s*(.+)', re.IGNORECASE)

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
    
    await update.message.reply_text(
        "🔐 Panel de Administración\n\nSelecciona una opción:",
        reply_markup=keyboard
    )

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
        
        await query.edit_message_text(
            f"📊 Estadísticas:\n\n"
            f"👥 Usuarios totales: {total_users}\n"
            f"⭐ Subscriptores: {subs}\n"
            f"📚 Casos disponibles: {cases}"
        )
    
    elif data == "admin_users":
        await query.edit_message_text(
            "👥 Gestión de Usuarios\n\n"
            "Comandos:\n"
            "/set_limit @user 10 - Cambiar límite\n"
            "/set_sub @user 1 - Activar subscripción\n"
            "/set_sub @user 0 - Desactivar subscripción"
        )
    
    elif data == "admin_cases":
        cases = get_all_case_ids()
        await query.edit_message_text(
            f"📚 Casos en base de datos: {len(cases)}\n\n"
            f"Primeros 10:\n" + "\n".join(cases[:10])
        )

async def cmd_set_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /set_limit @username 10")
        return
    
    username = context.args[0].replace("@", "").lower()
    limit = int(context.args[1])
    
    from database import _get_conn
    conn = _get_conn()
    
    from database import USE_POSTGRES
    if USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users WHERE LOWER(username)=%s", (username,))
            row = cur.fetchone()
    else:
        cur = conn.execute("SELECT user_id FROM users WHERE LOWER(username)=?", (username,))
        row = cur.fetchone()
    
    if not row:
        await update.message.reply_text("❌ Usuario no encontrado")
        return
    
    user_id = row['user_id'] if USE_POSTGRES else row[0]
    set_user_limit(user_id, limit)
    
    await update.message.reply_text(f"✅ Límite de @{username} actualizado a {limit}")

async def cmd_set_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /set_sub @username 1")
        return
    
    username = context.args[0].replace("@", "").lower()
    is_sub = int(context.args[1])
    
    from database import _get_conn, USE_POSTGRES
    conn = _get_conn()
    
    if USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users WHERE LOWER(username)=%s", (username,))
            row = cur.fetchone()
    else:
        cur = conn.execute("SELECT user_id FROM users WHERE LOWER(username)=?", (username,))
        row = cur.fetchone()
    
    if not row:
        await update.message.reply_text("❌ Usuario no encontrado")
        return
    
    user_id = row['user_id'] if USE_POSTGRES else row[0]
    set_user_subscriber(user_id, is_sub)
    
    status = "activada" if is_sub else "desactivada"
    await update.message.reply_text(f"✅ Subscripción de @{username} {status}")

def parse_message_with_buttons(text: str):
    buttons = []
    clean_lines = []
    
    for line in text.split("\n"):
        button_match = BUTTON_PATTERN.match(line)
        link_match = LINK_PATTERN.match(line)
        
        if button_match:
            label = button_match.group(1).strip()
            url = button_match.group(2).strip()
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url
            buttons.append(InlineKeyboardButton(label, url=url))
        elif link_match:
            label = link_match.group(1).strip()
            url = link_match.group(2).strip()
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url
            clean_lines.append(f"[{label}]({url})")
        else:
            clean_lines.append(line)
    
    clean_text = "\n".join(clean_lines)
    keyboard = InlineKeyboardMarkup([buttons]) if buttons else None
    
    return clean_text, keyboard
