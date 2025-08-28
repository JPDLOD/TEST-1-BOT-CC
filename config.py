# -*- coding: utf-8 -*-
import os
from zoneinfo import ZoneInfo

# ====== BOT PRINCIPAL (reenviador) ======
BOT_TOKEN = os.environ["BOT_TOKEN"]  # obligatorio
SOURCE_CHAT_ID = int(os.environ.get("SOURCE_CHAT_ID", "-1002918387207"))
TARGET_CHAT_ID = int(os.environ.get("TARGET_CHAT_ID", "-1003048176186"))
BACKUP_CHAT_ID = int(os.environ.get("BACKUP_CHAT_ID", "-1002923147603"))
PREVIEW_CHAT_ID = int(os.environ.get("PREVIEW_CHAT_ID", "-1002953653419"))

# ====== BOT DE JUSTIFICACIONES (privado) ======
JUST_BOT_TOKEN = os.environ.get("JUST_BOT_TOKEN", "")
JUSTIFICATIONS_CHAT_ID = int(os.environ.get("JUSTIFICATIONS_CHAT_ID", "-1003058530208"))
JUSTIFICATIONS_BOT_USERNAME = "clinicase_bot"  # Username del bot de justificaciones

# Admin IDs para el bot de justificaciones
JUST_ADMIN_IDS = set()
admin_ids_str = os.environ.get("JUST_ADMIN_IDS", "")
if admin_ids_str:
    for id_str in admin_ids_str.replace(",", " ").split():
        try:
            JUST_ADMIN_IDS.add(int(id_str.strip()))
        except:
            pass

JUST_AUTO_DELETE_MINUTES = int(os.environ.get("JUST_AUTO_DELETE_MINUTES", "10"))
AUTO_DELETE_MINUTES = int(os.environ.get("AUTO_DELETE_MINUTES", "10"))

# ====== General ======
PAUSE = float(os.environ.get("PAUSE", "0.6"))
DB_FILE = os.environ.get("DB_FILE", "drafts.db")

# ====== Zona horaria ======
TZNAME = os.environ.get("TIMEZONE", "America/Bogota")
TZ = ZoneInfo(TZNAME)
