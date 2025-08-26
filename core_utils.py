# -*- coding: utf-8 -*-
import asyncio
import re
from datetime import datetime
from typing import Optional, Dict

from config import TZ, SOURCE_CHAT_ID

# ========= Helpers genéricos =========
async def safe_sleep(seconds: float):
    try:
        await asyncio.sleep(max(0.0, seconds))
    except Exception:
        pass

async def temp_notice(bot, text: str, ttl: int = 6):
    """Envía un aviso temporal y lo borra pasado ttl segundos."""
    try:
        m = await bot.send_message(SOURCE_CHAT_ID, text, disable_notification=True)
    except Exception:
        return
    async def _auto_del():
        await safe_sleep(ttl)
        try:
            await bot.delete_message(SOURCE_CHAT_ID, m.message_id)
        except Exception:
            pass
    asyncio.create_task(_auto_del())

def human_eta(target_dt: datetime, now: Optional[datetime] = None) -> str:
    """Calcula tiempo restante en formato humano."""
    now = now or datetime.now(tz=TZ)
    sec = max(0, int((target_dt - now).total_seconds()))
    mins = sec // 60
    if mins < 60:
        return f"en {mins} min"
    hours = mins // 60
    mins = mins % 60
    if hours < 24:
        if mins:
            return f"en {hours} h {mins} m"
        return f"en {hours} h"
    days = hours // 24
    hours = hours % 24
    if hours:
        return f"en {days} d {hours} h"
    return f"en {days} d"

def extract_id_from_text(txt: str) -> Optional[int]:
    """Extrae ID del texto de comando."""
    parts = (txt or "").split()
    for p in parts[1:]:
        if p.isdigit():
            return int(p)
        if p.lower().startswith("id:"):
            n = p.split(":", 1)[1]
            if n.isdigit():
                return int(n)
    return None

def deep_link_for_channel_message(chat_id: int, mid: int) -> str:
    """Genera enlace directo a mensaje del canal."""
    cid = str(chat_id)
    if cid.startswith("-100"):
        cid = cid[4:]
    return f"https://t.me/c/{cid}/{mid}"

# ========= ATAJO @@@ =========
# Formato: "@@@ TÍTULO | URL"
_AT_SHORTCUT = re.compile(r"^\s*@@@\s*(?P<label>[^|]+?)\s*\|\s*(?P<url>.+)\s*$", re.IGNORECASE)

def parse_shortcut_line(text: str) -> Optional[Dict[str, str]]:
    """Detecta y parsea líneas con formato @@@ texto | url."""
    if not text:
        return None
    m = _AT_SHORTCUT.match(text.strip())
    if not m:
        return None
    label = m.group("label").strip()
    url = m.group("url").strip()
    
    # Si la URL no tiene protocolo, agregar https://
    if not url.startswith(('http://', 'https://', 'tg://')):
        if url.startswith('t.me/'):
            url = 'https://' + url
        elif '.' in url:
            url = 'https://' + url
    
    return {"label": label, "url": url}
