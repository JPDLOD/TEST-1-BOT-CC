# -*- coding: utf-8 -*-
import os
from zoneinfo import ZoneInfo

# =========================
# CONFIG DESDE ENV
# =========================
BOT_TOKEN = os.environ["BOT_TOKEN"]  # obligatorio

# Canales principales
SOURCE_CHAT_ID = int(os.environ.get("SOURCE_CHAT_ID", "-1002859784457"))  # BORRADOR
TARGET_CHAT_ID = int(os.environ.get("TARGET_CHAT_ID", "-1002679848195"))  # PRINCIPAL

# Canales opcionales
BACKUP_CHAT_ID = int(os.environ.get("BACKUP_CHAT_ID", "-1002717125281"))
PREVIEW_CHAT_ID = int(os.environ.get("PREVIEW_CHAT_ID", "-1003042227035"))

# =========================
# CONFIG JUSTIFICACIONES
# =========================
# Canal donde están almacenadas las justificaciones protegidas
JUSTIFICATIONS_CHAT_ID = int(os.environ.get("JUSTIFICATIONS_CHAT_ID", "-1003058530208"))

# Tiempo en minutos antes de auto-eliminar justificaciones enviadas (0 = no eliminar)
AUTO_DELETE_MINUTES = int(os.environ.get("AUTO_DELETE_MINUTES", "10"))

# =========================
# CONFIG GENERAL
# =========================
DB_FILE = os.environ.get("DB_FILE", "drafts.db")

# Pausa base entre envíos (seg) para no rozar el flood control
PAUSE = float(os.environ.get("PAUSE", "0.6"))

# Zona horaria (24h)
TZNAME = os.environ.get("TIMEZONE", "America/Bogota")
TZ = ZoneInfo(TZNAME)
