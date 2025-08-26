import os
from zoneinfo import ZoneInfo

BOT_TOKEN = os.environ["BOT_TOKEN"]
SOURCE_CHAT_ID = int(os.environ.get("SOURCE_CHAT_ID", "-1002859784457"))
TARGET_CHAT_ID = int(os.environ.get("TARGET_CHAT_ID", "-1002679848195"))
BACKUP_CHAT_ID = int(os.environ.get("BACKUP_CHAT_ID", "-1002717125281"))
PREVIEW_CHAT_ID = int(os.environ.get("PREVIEW_CHAT_ID", "-1003042227035"))

# JUSTIFICACIONES
JUSTIFICATIONS_CHAT_ID = int(os.environ.get("JUSTIFICATIONS_CHAT_ID", "-1003058530208"))
JUSTIFICATIONS_CHANNEL_USERNAME = os.environ.get("JUSTIFICATIONS_CHANNEL_USERNAME", "ccjustificaciones")
AUTO_DELETE_MINUTES = int(os.environ.get("AUTO_DELETE_MINUTES", "10"))

DB_FILE = os.environ.get("DB_FILE", "drafts.db")
PAUSE = float(os.environ.get("PAUSE", "0.6"))
TZNAME = os.environ.get("TIMEZONE", "America/Bogota")
TZ = ZoneInfo(TZNAME)
