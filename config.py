# -*- coding: utf-8 -*-
import os
from zoneinfo import ZoneInfo

# =========================
# CONFIG DESDE ENV
# =========================
BOT_TOKEN = os.environ["BOT_TOKEN"]  # obligatorio

# Canales por defecto
SOURCE_CHAT_ID = int(os.environ.get("SOURCE_CHAT_ID", "-1002859784457"))  # BORRADOR
TARGET_CHAT_ID = int(os.environ.get("TARGET_CHAT_ID", "-1002679848195"))  # PRINCIPAL

# Fallbacks
BACKUP_FALLBACK = -1002717125281
PREVIEW_FALLBACK = -1003042227035

BACKUP_CHAT_ID = int(os.environ.get("BACKUP_CHAT_ID", str(BACKUP_FALLBACK)))
PREVIEW_CHAT_ID = int(os.environ.get("PREVIEW_CHAT_ID", str(PREVIEW_FALLBACK)))

# =========================
# CONFIG JUSTIFICACIONES
# =========================
JUSTIFICATIONS_CHAT_ID = int(os.environ.get("JUSTIFICATIONS_CHAT_ID", "-1003058530208"))
JUSTIFICATIONS_CHANNEL_USERNAME = os.environ.get("JUSTIFICATIONS_CHANNEL_USERNAME", "ccjustificaciones")
AUTO_DELETE_MINUTES = int(os.environ.get("AUTO_DELETE_MINUTES", "10"))

# =========================
# CONFIG GENERAL
# =========================
DB_FILE = os.environ.get("DB_FILE", "drafts.db")
PAUSE = float(os.environ.get("PAUSE", "0.6"))
TZNAME = os.environ.get("TIMEZONE", "America/Bogota")
TZ = ZoneInfo(TZNAME)
