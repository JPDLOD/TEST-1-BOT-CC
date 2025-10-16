# -*- coding: utf-8 -*-
import os
from zoneinfo import ZoneInfo

BOT_TOKEN = os.environ["BOT_TOKEN"]

JUSTIFICATIONS_CHAT_ID = int(os.environ.get("JUSTIFICATIONS_CHAT_ID", "-1003058530208"))

FREE_CHANNEL_ID = int(os.environ.get("FREE_CHANNEL_ID", "-1002717125281"))
SUBS_CHANNEL_ID = int(os.environ.get("SUBS_CHANNEL_ID", "-1003042227035"))

ADMIN_USER_IDS = list(map(int, os.environ.get("ADMIN_USER_IDS", "").split(","))) if os.environ.get("ADMIN_USER_IDS") else []

DB_FILE = os.environ.get("DB_FILE", "clinicas.db")

DAILY_CASE_LIMIT = int(os.environ.get("DAILY_CASE_LIMIT", "5"))

TZNAME = os.environ.get("TIMEZONE", "America/Bogota")
TZ = ZoneInfo(TZNAME)

PAUSE = float(os.environ.get("PAUSE", "0.3"))
