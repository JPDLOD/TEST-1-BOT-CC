# -*- coding: utf-8 -*-
import os
import re
from zoneinfo import ZoneInfo

def _to_int(v, default=None):
    try:
        return int(str(v).strip())
    except Exception:
        return default

def _to_float(v, default=None):
    try:
        return float(str(v).strip())
    except Exception:
        return default

def _parse_ids(s: str):
    if not s:
        return set()
    parts = re.split(r"[,\s;]+", str(s))
    out = set()
    for p in parts:
        p = p.strip()
        if p.isdigit() or (p.startswith("-") and p[1:].isdigit()):
            try:
                out.add(int(p))
            except Exception:
                pass
    return out

# ====== BOT PRINCIPAL (reenviador) ======
BOT_TOKEN = os.environ["BOT_TOKEN"]  # obligatorio
SOURCE_CHAT_ID = _to_int(os.environ.get("SOURCE_CHAT_ID"))
TARGET_CHAT_ID = _to_int(os.environ.get("TARGET_CHAT_ID"))
BACKUP_CHAT_ID = _to_int(os.environ.get("BACKUP_CHAT_ID"))
PREVIEW_CHAT_ID = _to_int(os.environ.get("PREVIEW_CHAT_ID"))

PAUSE = _to_float(os.environ.get("PAUSE"), 0.6)

# ====== BOT DE JUSTIFICACIONES (privado) ======
JUST_BOT_TOKEN = os.environ.get("JUST_BOT_TOKEN")  # obligatorio para ese bot
JUSTIFICATIONS_CHAT_ID = _to_int(os.environ.get("JUSTIFICATIONS_CHAT_ID"))
JUST_ADMIN_IDS = _parse_ids(os.environ.get("JUST_ADMIN_IDS", ""))
JUST_AUTO_DELETE_MINUTES = _to_int(os.environ.get("JUST_AUTO_DELETE_MINUTES"), 0)

# ====== AUTO-DELETE del principal (si lo usas en algún handler) ======
AUTO_DELETE_MINUTES = _to_int(os.environ.get("AUTO_DELETE_MINUTES"), 0)

# ====== Zona horaria ======
TZNAME = os.environ.get("TIMEZONE", "America/Bogota")
TZ = ZoneInfo(TZNAME)