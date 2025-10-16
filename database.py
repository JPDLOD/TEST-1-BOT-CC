# -*- coding: utf-8 -*-
import sqlite3
from typing import List, Tuple, Optional, Set
from datetime import datetime
from config import TZ

_conn_cache = {}

def _conn(path: str) -> sqlite3.Connection:
    conn = _conn_cache.get(path)
    if conn:
        return conn
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    _conn_cache[path] = conn
    return conn

_schema = """
CREATE TABLE IF NOT EXISTS clinical_cases (
  case_id TEXT PRIMARY KEY,
  message_id INTEGER NOT NULL,
  specialty TEXT,
  topic TEXT,
  correct_answer TEXT,
  created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_cases_specialty ON clinical_cases(specialty);

CREATE TABLE IF NOT EXISTS justifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id TEXT NOT NULL,
  message_id INTEGER NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_just_case ON justifications(case_id);

CREATE TABLE IF NOT EXISTS users (
  user_id INTEGER PRIMARY KEY,
  username TEXT,
  first_name TEXT,
  is_subscriber INTEGER DEFAULT 0,
  daily_limit INTEGER DEFAULT 5,
  total_cases INTEGER DEFAULT 0,
  correct_answers INTEGER DEFAULT 0,
  last_interaction INTEGER,
  created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS user_responses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  case_id TEXT NOT NULL,
  answer TEXT,
  is_correct INTEGER,
  timestamp INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_resp_user ON user_responses(user_id);
CREATE INDEX IF NOT EXISTS idx_resp_case ON user_responses(case_id);

CREATE TABLE IF NOT EXISTS case_stats (
  case_id TEXT,
  answer TEXT,
  count INTEGER DEFAULT 0,
  PRIMARY KEY (case_id, answer)
);

CREATE TABLE IF NOT EXISTS daily_progress (
  user_id INTEGER,
  date TEXT,
  cases_solved INTEGER DEFAULT 0,
  PRIMARY KEY (user_id, date)
);
"""

def init_db(path: str):
    c = _conn(path)
    c.executescript(_schema)
    c.commit()

def save_case(path: str, case_id: str, message_id: int, specialty: str = "", topic: str = "", correct_answer: str = ""):
    c = _conn(path)
    c.execute(
        "INSERT OR REPLACE INTO clinical_cases(case_id, message_id, specialty, topic, correct_answer) VALUES (?,?,?,?,?)",
        (case_id, message_id, specialty, topic, correct_answer)
    )
    c.commit()

def save_justification(path: str, case_id: str, message_id: int):
    c = _conn(path)
    c.execute("INSERT INTO justifications(case_id, message_id) VALUES (?,?)", (case_id, message_id))
    c.commit()

def get_all_case_ids(path: str) -> List[str]:
    c = _conn(path)
    cur = c.execute("SELECT case_id FROM clinical_cases ORDER BY case_id")
    return [row[0] for row in cur.fetchall()]

def get_case_by_id(path: str, case_id: str) -> Optional[Tuple]:
    c = _conn(path)
    cur = c.execute("SELECT case_id, message_id, correct_answer FROM clinical_cases WHERE case_id=?", (case_id,))
    return cur.fetchone()

def get_justifications_for_case(path: str, case_id: str) -> List[int]:
    c = _conn(path)
    cur = c.execute("SELECT message_id FROM justifications WHERE case_id=? ORDER BY id", (case_id,))
    return [row[0] for row in cur.fetchall()]

def get_user_sent_cases(path: str, user_id: int) -> Set[str]:
    c = _conn(path)
    cur = c.execute("SELECT DISTINCT case_id FROM user_responses WHERE user_id=?", (user_id,))
    return {row[0] for row in cur.fetchall()}

def save_user_response(path: str, user_id: int, case_id: str, answer: str, is_correct: int):
    c = _conn(path)
    c.execute(
        "INSERT INTO user_responses(user_id, case_id, answer, is_correct) VALUES (?,?,?,?)",
        (user_id, case_id, answer, is_correct)
    )
    c.commit()

def increment_case_stat(path: str, case_id: str, answer: str):
    c = _conn(path)
    c.execute(
        "INSERT INTO case_stats(case_id, answer, count) VALUES (?,?,1) ON CONFLICT(case_id, answer) DO UPDATE SET count=count+1",
        (case_id, answer)
    )
    c.commit()

def get_case_stats(path: str, case_id: str) -> dict:
    c = _conn(path)
    cur = c.execute("SELECT answer, count FROM case_stats WHERE case_id=?", (case_id,))
    stats = {"A": 0, "B": 0, "C": 0, "D": 0}
    for answer, count in cur.fetchall():
        stats[answer] = count
    return stats

def get_or_create_user(path: str, user_id: int, username: str = "", first_name: str = "") -> dict:
    c = _conn(path)
    cur = c.execute("SELECT user_id, username, first_name, is_subscriber, daily_limit, total_cases, correct_answers FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if row:
        return {
            "user_id": row[0],
            "username": row[1],
            "first_name": row[2],
            "is_subscriber": row[3],
            "daily_limit": row[4],
            "total_cases": row[5],
            "correct_answers": row[6]
        }
    c.execute(
        "INSERT INTO users(user_id, username, first_name) VALUES (?,?,?)",
        (user_id, username, first_name)
    )
    c.commit()
    return {
        "user_id": user_id,
        "username": username,
        "first_name": first_name,
        "is_subscriber": 0,
        "daily_limit": 5,
        "total_cases": 0,
        "correct_answers": 0
    }

def get_daily_progress(path: str, user_id: int) -> int:
    today = datetime.now(tz=TZ).strftime("%Y-%m-%d")
    c = _conn(path)
    cur = c.execute("SELECT cases_solved FROM daily_progress WHERE user_id=? AND date=?", (user_id, today))
    row = cur.fetchone()
    return row[0] if row else 0

def increment_daily_progress(path: str, user_id: int):
    today = datetime.now(tz=TZ).strftime("%Y-%m-%d")
    c = _conn(path)
    c.execute(
        "INSERT INTO daily_progress(user_id, date, cases_solved) VALUES (?,?,1) ON CONFLICT(user_id, date) DO UPDATE SET cases_solved=cases_solved+1",
        (user_id, today)
    )
    c.commit()

def set_user_limit(path: str, user_id: int, limit: int):
    c = _conn(path)
    c.execute("UPDATE users SET daily_limit=? WHERE user_id=?", (limit, user_id))
    c.commit()

def set_user_subscriber(path: str, user_id: int, is_sub: int):
    c = _conn(path)
    c.execute("UPDATE users SET is_subscriber=? WHERE user_id=?", (is_sub, user_id))
    c.commit()

def update_user_stats(path: str, user_id: int, is_correct: int):
    c = _conn(path)
    if is_correct:
        c.execute("UPDATE users SET total_cases=total_cases+1, correct_answers=correct_answers+1 WHERE user_id=?", (user_id,))
    else:
        c.execute("UPDATE users SET total_cases=total_cases+1 WHERE user_id=?", (user_id,))
    c.commit()

def get_all_users(path: str) -> List[int]:
    c = _conn(path)
    cur = c.execute("SELECT user_id FROM users")
    return [row[0] for row in cur.fetchall()]

def get_subscribers(path: str) -> List[int]:
    c = _conn(path)
    cur = c.execute("SELECT user_id FROM users WHERE is_subscriber=1")
    return [row[0] for row in cur.fetchall()]
