# -*- coding: utf-8 -*-
import logging
from typing import List, Tuple, Optional, Set, Dict
from datetime import datetime
from config import TZ, DATABASE_URL, DB_FILE

logger = logging.getLogger(__name__)

# Decidir backend de BD
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    logger.info("🐘 Usando PostgreSQL (persistente)")
else:
    import sqlite3
    logger.info("💾 Usando SQLite (local testing)")

_conn_cache = {}

def _get_conn():
    """Obtiene conexión según el backend configurado"""
    global _conn_cache
    
    if USE_POSTGRES:
        key = "postgres"
        if key in _conn_cache:
            return _conn_cache[key]
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        conn.autocommit = True
        _conn_cache[key] = conn
        return conn
    else:
        key = "sqlite"
        if key in _conn_cache:
            return _conn_cache[key]
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        _conn_cache[key] = conn
        return conn

# Schema SQL - Compatible con ambos
_schema_postgres = """
CREATE TABLE IF NOT EXISTS clinical_cases (
  case_id TEXT PRIMARY KEY,
  message_id BIGINT NOT NULL,
  specialty TEXT,
  topic TEXT,
  subtopic TEXT,
  correct_answer TEXT,
  created_at BIGINT DEFAULT EXTRACT(EPOCH FROM NOW())
);
CREATE INDEX IF NOT EXISTS idx_cases_specialty ON clinical_cases(specialty);

CREATE TABLE IF NOT EXISTS justifications (
  id SERIAL PRIMARY KEY,
  case_id TEXT NOT NULL,
  message_id BIGINT NOT NULL,
  created_at BIGINT DEFAULT EXTRACT(EPOCH FROM NOW())
);
CREATE INDEX IF NOT EXISTS idx_just_case ON justifications(case_id);

CREATE TABLE IF NOT EXISTS users (
  user_id BIGINT PRIMARY KEY,
  username TEXT,
  first_name TEXT,
  is_subscriber INTEGER DEFAULT 0,
  daily_limit INTEGER DEFAULT 5,
  total_cases INTEGER DEFAULT 0,
  correct_answers INTEGER DEFAULT 0,
  last_interaction BIGINT,
  created_at BIGINT DEFAULT EXTRACT(EPOCH FROM NOW())
);

CREATE TABLE IF NOT EXISTS user_responses (
  id SERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  case_id TEXT NOT NULL,
  answer TEXT,
  is_correct INTEGER,
  timestamp BIGINT DEFAULT EXTRACT(EPOCH FROM NOW())
);
CREATE INDEX IF NOT EXISTS idx_resp_user ON user_responses(user_id);
CREATE INDEX IF NOT EXISTS idx_resp_case ON user_responses(case_id);

CREATE TABLE IF NOT EXISTS user_sent_cases (
  user_id BIGINT NOT NULL,
  case_id TEXT NOT NULL,
  sent_at BIGINT DEFAULT EXTRACT(EPOCH FROM NOW()),
  PRIMARY KEY (user_id, case_id)
);
CREATE INDEX IF NOT EXISTS idx_sent_user ON user_sent_cases(user_id);

CREATE TABLE IF NOT EXISTS case_stats (
  case_id TEXT,
  answer TEXT,
  count INTEGER DEFAULT 0,
  PRIMARY KEY (case_id, answer)
);

CREATE TABLE IF NOT EXISTS daily_progress (
  user_id BIGINT,
  date TEXT,
  cases_solved INTEGER DEFAULT 0,
  PRIMARY KEY (user_id, date)
);
"""

_schema_sqlite = """
CREATE TABLE IF NOT EXISTS clinical_cases (
  case_id TEXT PRIMARY KEY,
  message_id INTEGER NOT NULL,
  specialty TEXT,
  topic TEXT,
  subtopic TEXT,
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

CREATE TABLE IF NOT EXISTS user_sent_cases (
  user_id INTEGER NOT NULL,
  case_id TEXT NOT NULL,
  sent_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
  PRIMARY KEY (user_id, case_id)
);
CREATE INDEX IF NOT EXISTS idx_sent_user ON user_sent_cases(user_id);

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

def init_db():
    """Inicializa la base de datos"""
    conn = _get_conn()
    schema = _schema_postgres if USE_POSTGRES else _schema_sqlite
    
    if USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute(schema)
    else:
        conn.executescript(schema)
        conn.commit()
    
    logger.info("✅ Base de datos inicializada")

def parse_case_id(case_id: str) -> Dict[str, str]:
    """
    Parsea un case_id con subcategorías
    Ejemplo: ###CASE_0000_PED_DENGUE_0001
    Retorna: {
        'full_id': '###CASE_0000_PED_DENGUE_0001',
        'general_id': '0000',
        'specialty': 'PED',
        'topic': 'DENGUE',
        'subtopic': '0001'
    }
    """
    parts = case_id.replace("###CASE_", "").split("_")
    
    result = {
        'full_id': case_id,
        'general_id': parts[0] if len(parts) > 0 else '',
        'specialty': parts[1] if len(parts) > 1 else '',
        'topic': parts[2] if len(parts) > 2 else '',
        'subtopic': parts[3] if len(parts) > 3 else ''
    }
    
    return result

def save_case(case_id: str, message_id: int, correct_answer: str = ""):
    """Guarda un caso con sus subcategorías parseadas"""
    parsed = parse_case_id(case_id)
    
    conn = _get_conn()
    
    if USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO clinical_cases(case_id, message_id, specialty, topic, subtopic, correct_answer) 
                   VALUES (%s, %s, %s, %s, %s, %s) 
                   ON CONFLICT (case_id) DO UPDATE SET 
                   message_id=EXCLUDED.message_id, 
                   correct_answer=EXCLUDED.correct_answer""",
                (case_id, message_id, parsed['specialty'], parsed['topic'], parsed['subtopic'], correct_answer)
            )
    else:
        conn.execute(
            """INSERT OR REPLACE INTO clinical_cases(case_id, message_id, specialty, topic, subtopic, correct_answer) 
               VALUES (?,?,?,?,?,?)""",
            (case_id, message_id, parsed['specialty'], parsed['topic'], parsed['subtopic'], correct_answer)
        )
        conn.commit()
    
    logger.info(f"💾 Caso guardado: {case_id} → Esp:{parsed['specialty']} Topic:{parsed['topic']}")

def save_justification(case_id: str, message_id: int):
    conn = _get_conn()
    if USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO justifications(case_id, message_id) VALUES (%s, %s)", (case_id, message_id))
    else:
        conn.execute("INSERT INTO justifications(case_id, message_id) VALUES (?,?)", (case_id, message_id))
        conn.commit()

def get_all_case_ids() -> List[str]:
    conn = _get_conn()
    if USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute("SELECT case_id FROM clinical_cases ORDER BY case_id")
            return [row['case_id'] for row in cur.fetchall()]
    else:
        cur = conn.execute("SELECT case_id FROM clinical_cases ORDER BY case_id")
        return [row[0] for row in cur.fetchall()]

def get_case_by_id(case_id: str) -> Optional[Tuple]:
    conn = _get_conn()
    if USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute("SELECT case_id, message_id, correct_answer FROM clinical_cases WHERE case_id=%s", (case_id,))
            row = cur.fetchone()
            return (row['case_id'], row['message_id'], row['correct_answer']) if row else None
    else:
        cur = conn.execute("SELECT case_id, message_id, correct_answer FROM clinical_cases WHERE case_id=?", (case_id,))
        return cur.fetchone()

def get_justifications_for_case(case_id: str) -> List[int]:
    conn = _get_conn()
    if USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute("SELECT message_id FROM justifications WHERE case_id=%s ORDER BY id", (case_id,))
            return [row['message_id'] for row in cur.fetchall()]
    else:
        cur = conn.execute("SELECT message_id FROM justifications WHERE case_id=? ORDER BY id", (case_id,))
        return [row[0] for row in cur.fetchall()]

def get_user_sent_cases(user_id: int) -> Set[str]:
    conn = _get_conn()
    if USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute("SELECT case_id FROM user_sent_cases WHERE user_id=%s", (user_id,))
            return {row['case_id'] for row in cur.fetchall()}
    else:
        cur = conn.execute("SELECT case_id FROM user_sent_cases WHERE user_id=?", (user_id,))
        return {row[0] for row in cur.fetchall()}

def save_user_sent_case(user_id: int, case_id: str):
    conn = _get_conn()
    if USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_sent_cases(user_id, case_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (user_id, case_id)
            )
    else:
        conn.execute("INSERT OR IGNORE INTO user_sent_cases(user_id, case_id) VALUES (?,?)", (user_id, case_id))
        conn.commit()

def save_user_response(user_id: int, case_id: str, answer: str, is_correct: int):
    conn = _get_conn()
    if USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_responses(user_id, case_id, answer, is_correct) VALUES (%s, %s, %s, %s)",
                (user_id, case_id, answer, is_correct)
            )
    else:
        conn.execute(
            "INSERT INTO user_responses(user_id, case_id, answer, is_correct) VALUES (?,?,?,?)",
            (user_id, case_id, answer, is_correct)
        )
        conn.commit()

def increment_case_stat(case_id: str, answer: str):
    conn = _get_conn()
    if USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO case_stats(case_id, answer, count) VALUES (%s, %s, 1) 
                   ON CONFLICT(case_id, answer) DO UPDATE SET count=case_stats.count+1""",
                (case_id, answer)
            )
    else:
        conn.execute(
            """INSERT INTO case_stats(case_id, answer, count) VALUES (?,?,1) 
               ON CONFLICT(case_id, answer) DO UPDATE SET count=count+1""",
            (case_id, answer)
        )
        conn.commit()

def get_case_stats(case_id: str) -> dict:
    conn = _get_conn()
    stats = {"A": 0, "B": 0, "C": 0, "D": 0}
    
    if USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute("SELECT answer, count FROM case_stats WHERE case_id=%s", (case_id,))
            for row in cur.fetchall():
                stats[row['answer']] = row['count']
    else:
        cur = conn.execute("SELECT answer, count FROM case_stats WHERE case_id=?", (case_id,))
        for answer, count in cur.fetchall():
            stats[answer] = count
    
    return stats

def get_or_create_user(user_id: int, username: str = "", first_name: str = "") -> dict:
    conn = _get_conn()
    
    if USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, username, first_name, is_subscriber, daily_limit, total_cases, correct_answers FROM users WHERE user_id=%s",
                (user_id,)
            )
            row = cur.fetchone()
            if row:
                return dict(row)
            
            cur.execute(
                "INSERT INTO users(user_id, username, first_name) VALUES (%s, %s, %s)",
                (user_id, username, first_name)
            )
    else:
        cur = conn.execute(
            "SELECT user_id, username, first_name, is_subscriber, daily_limit, total_cases, correct_answers FROM users WHERE user_id=?",
            (user_id,)
        )
        row = cur.fetchone()
        if row:
            return {
                "user_id": row[0], "username": row[1], "first_name": row[2],
                "is_subscriber": row[3], "daily_limit": row[4],
                "total_cases": row[5], "correct_answers": row[6]
            }
        
        conn.execute("INSERT INTO users(user_id, username, first_name) VALUES (?,?,?)", (user_id, username, first_name))
        conn.commit()
    
    return {
        "user_id": user_id, "username": username, "first_name": first_name,
        "is_subscriber": 0, "daily_limit": 5, "total_cases": 0, "correct_answers": 0
    }

def get_daily_progress(user_id: int) -> int:
    today = datetime.now(tz=TZ).strftime("%Y-%m-%d")
    conn = _get_conn()
    
    if USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute("SELECT cases_solved FROM daily_progress WHERE user_id=%s AND date=%s", (user_id, today))
            row = cur.fetchone()
            return row['cases_solved'] if row else 0
    else:
        cur = conn.execute("SELECT cases_solved FROM daily_progress WHERE user_id=? AND date=?", (user_id, today))
        row = cur.fetchone()
        return row[0] if row else 0

def increment_daily_progress(user_id: int):
    today = datetime.now(tz=TZ).strftime("%Y-%m-%d")
    conn = _get_conn()
    
    if USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO daily_progress(user_id, date, cases_solved) VALUES (%s, %s, 1) 
                   ON CONFLICT(user_id, date) DO UPDATE SET cases_solved=daily_progress.cases_solved+1""",
                (user_id, today)
            )
    else:
        conn.execute(
            """INSERT INTO daily_progress(user_id, date, cases_solved) VALUES (?,?,1) 
               ON CONFLICT(user_id, date) DO UPDATE SET cases_solved=cases_solved+1""",
            (user_id, today)
        )
        conn.commit()

def set_user_limit(user_id: int, limit: int):
    conn = _get_conn()
    if USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET daily_limit=%s WHERE user_id=%s", (limit, user_id))
    else:
        conn.execute("UPDATE users SET daily_limit=? WHERE user_id=?", (limit, user_id))
        conn.commit()

def set_user_subscriber(user_id: int, is_sub: int):
    conn = _get_conn()
    if USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET is_subscriber=%s WHERE user_id=%s", (is_sub, user_id))
    else:
        conn.execute("UPDATE users SET is_subscriber=? WHERE user_id=?", (is_sub, user_id))
        conn.commit()

def update_user_stats(user_id: int, is_correct: int):
    conn = _get_conn()
    if USE_POSTGRES:
        with conn.cursor() as cur:
            if is_correct:
                cur.execute("UPDATE users SET total_cases=total_cases+1, correct_answers=correct_answers+1 WHERE user_id=%s", (user_id,))
            else:
                cur.execute("UPDATE users SET total_cases=total_cases+1 WHERE user_id=%s", (user_id,))
    else:
        if is_correct:
            conn.execute("UPDATE users SET total_cases=total_cases+1, correct_answers=correct_answers+1 WHERE user_id=?", (user_id,))
        else:
            conn.execute("UPDATE users SET total_cases=total_cases+1 WHERE user_id=?", (user_id,))
        conn.commit()

def get_all_users() -> List[int]:
    conn = _get_conn()
    if USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users")
            return [row['user_id'] for row in cur.fetchall()]
    else:
        cur = conn.execute("SELECT user_id FROM users")
        return [row[0] for row in cur.fetchall()]

def get_subscribers() -> List[int]:
    conn = _get_conn()
    if USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users WHERE is_subscriber=1")
            return [row['user_id'] for row in cur.fetchall()]
    else:
        cur = conn.execute("SELECT user_id FROM users WHERE is_subscriber=1")
        return [row[0] for row in cur.fetchall()]

def count_cases() -> int:
    """Cuenta cuántos casos hay en la BD"""
    conn = _get_conn()
    if USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) as cnt FROM clinical_cases")
            return cur.fetchone()['cnt']
    else:
        cur = conn.execute("SELECT COUNT(*) FROM clinical_cases")
        return cur.fetchone()[0]
