# -*- coding: utf-8 -*-
import os
from zoneinfo import ZoneInfo

# OBLIGATORIOS
BOT_TOKEN = os.environ["BOT_TOKEN"]

# ====== CAMBIO PRINCIPAL ======
# ANTES: JUSTIFICATIONS_CHAT_ID (canal)
# AHORA: CASES_UPLOADER_ID (usuario que envía casos y justificaciones)
CASES_UPLOADER_ID = int(os.environ["CASES_UPLOADER_ID"])

FREE_CHANNEL_ID = int(os.environ.get("FREE_CHANNEL_ID", "0"))
SUBS_CHANNEL_ID = int(os.environ.get("SUBS_CHANNEL_ID", "0"))
ADMIN_USER_IDS = [int(x) for x in os.environ.get("ADMIN_USER_IDS", str(os.environ.get("CASES_UPLOADER_ID", "0"))).split(",")]
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Opcionales
DAILY_CASE_LIMIT = int(os.environ.get("DAILY_CASE_LIMIT", "5"))
TZNAME = os.environ.get("TIMEZONE", "America/Bogota")
TZ = ZoneInfo(TZNAME)
PAUSE = float(os.environ.get("PAUSE", "0.3"))
