async def cmd_set_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /set_limit @username 10")
        return
    
    username = context.args[0].replace("@", "").lower()  # CORREGIDO: case-insensitive
    limit = int(context.args[1])
    
    from database import _get_conn
    conn = _get_conn()
    
    from database import USE_POSTGRES
    if USE_POSTGRES:
        with conn.cursor() as cur:
            # CORREGIDO: LOWER() en ambos lados
            cur.execute("SELECT user_id FROM users WHERE LOWER(username)=%s", (username,))
            row = cur.fetchone()
    else:
        # CORREGIDO: LOWER() en SQLite
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
    
    username = context.args[0].replace("@", "").lower()  # CORREGIDO: case-insensitive
    is_sub = int(context.args[1])
    
    from database import _get_conn, USE_POSTGRES
    conn = _get_conn()
    
    if USE_POSTGRES:
        with conn.cursor() as cur:
            # CORREGIDO: LOWER() en ambos lados
            cur.execute("SELECT user_id FROM users WHERE LOWER(username)=%s", (username,))
            row = cur.fetchone()
    else:
        # CORREGIDO: LOWER() en SQLite
        cur = conn.execute("SELECT user_id FROM users WHERE LOWER(username)=?", (username,))
        row = cur.fetchone()
    
    if not row:
        await update.message.reply_text("❌ Usuario no encontrado")
        return
    
    user_id = row['user_id'] if USE_POSTGRES else row[0]
    set_user_subscriber(user_id, is_sub)
    
    status = "activada" if is_sub else "desactivada"
    await update.message.reply_text(f"✅ Subscripción de @{username} {status}")
