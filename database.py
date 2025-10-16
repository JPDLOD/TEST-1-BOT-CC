# -*- coding: utf-8 -*-
import sqlite3
from typing import List, Tuple, Optional, Dict

_schema = """
CREATE TABLE IF NOT EXISTS drafts (
  message_id INTEGER PRIMARY KEY,
  snippet    TEXT,
  raw_json   TEXT,
  sent       INTEGER NOT NULL DEFAULT 0,
  deleted    INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_drafts_sent_deleted ON drafts(sent, deleted);

-- Botones por mensaje (permite varios botones por draft)
CREATE TABLE IF NOT EXISTS buttons (
  message_id INTEGER NOT NULL,
  label      TEXT NOT NULL,
  url        TEXT NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_buttons_message ON buttons(message_id);

-- Casos entregados (para persistencia tras reinicio)
CREATE TABLE IF NOT EXISTS delivered_cases (
  user_id INTEGER NOT NULL,
  case_code TEXT NOT NULL,
  message_id INTEGER,
  delivered_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
  PRIMARY KEY (user_id, case_code)
);
CREATE INDEX IF NOT EXISTS idx_delivered_user ON delivered_cases(user_id);
CREATE INDEX IF NOT EXISTS idx_delivered_code ON delivered_cases(case_code);
"""

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

def init_db(path: str):
    c = _conn(path)
    c.executescript(_schema)
    c.commit()

def save_draft(path: str, message_id: int, snippet: str, raw_json: str):
    c = _conn(path)
    c.execute(
        "INSERT OR IGNORE INTO drafts(message_id, snippet, raw_json) VALUES (?,?,?)",
        (message_id, snippet or "", raw_json or "")
    )
    c.commit()

def get_unsent_drafts(path: str) -> List[Tuple[int, str, str]]:
    c = _conn(path)
    cur = c.execute(
        "SELECT message_id, snippet, raw_json FROM drafts WHERE sent=0 AND deleted=0 ORDER BY message_id ASC"
    )
    return list(cur.fetchall())

def mark_sent(path: str, ids: List[int]):
    if not ids:
        return
    c = _conn(path)
    q = "UPDATE drafts SET sent=1 WHERE message_id IN (%s)" % ",".join("?" * len(ids))
    c.execute(q, ids)
    c.commit()

def list_drafts(path: str) -> List[Tuple[int, str]]:
    c = _conn(path)
    cur = c.execute(
        "SELECT message_id, COALESCE(snippet,'') FROM drafts WHERE sent=0 AND deleted=0 ORDER BY message_id ASC"
    )
    return list(cur.fetchall())

def mark_deleted(path: str, message_id: int):
    c = _conn(path)
    c.execute("UPDATE drafts SET deleted=1 WHERE message_id=?", (message_id,))
    c.commit()

def restore_draft(path: str, message_id: int):
    c = _conn(path)
    c.execute("UPDATE drafts SET deleted=0 WHERE message_id=?", (message_id,))
    c.commit()

def get_last_deleted(path: str) -> Optional[int]:
    c = _conn(path)
    cur = c.execute(
        "SELECT message_id FROM drafts WHERE sent=0 AND deleted=1 ORDER BY created_at DESC LIMIT 1"
    )
    row = cur.fetchone()
    return int(row[0]) if row else None

def count_deleted_unsent(path: str) -> int:
    c = _conn(path)
    cur = c.execute("SELECT COUNT(*) FROM drafts WHERE sent=0 AND deleted=1")
    row = cur.fetchone()
    return int(row[0] or 0)

def get_draft_snippet(path: str, message_id: int) -> Optional[str]:
    c = _conn(path)
    cur = c.execute("SELECT snippet FROM drafts WHERE message_id=?", (message_id,))
    row = cur.fetchone()
    return row[0] if row else None

# =========================
# BOTONES (para @@@)
# =========================
def add_button(path: str, message_id: int, label: str, url: str):
    """Agrega un botón a un mensaje específico."""
    if not (message_id and label and url):
        return
    c = _conn(path)
    c.execute(
        "INSERT INTO buttons(message_id, label, url) VALUES (?,?,?)",
        (int(message_id), str(label), str(url))
    )
    c.commit()

def get_buttons_for_message(path: str, message_id: int) -> List[Tuple[str, str]]:
    """Obtiene todos los botones para un mensaje específico."""
    c = _conn(path)
    cur = c.execute(
        "SELECT label, url FROM buttons WHERE message_id=? ORDER BY created_at ASC",
        (message_id,)
    )
    return list(cur.fetchall())

def get_buttons_map_for_ids(path: str, ids: List[int]) -> Dict[int, List[Tuple[str, str]]]:
    """Devuelve {message_id: [(label, url), ...]} solo para los ids dados."""
    out: Dict[int, List[Tuple[str, str]]] = {i: [] for i in ids}
    if not ids:
        return out
    c = _conn(path)
    q = "SELECT message_id, label, url FROM buttons WHERE message_id IN (%s) ORDER BY created_at ASC" % ",".join("?"*len(ids))
    for mid, label, url in c.execute(q, ids).fetchall():
        out.setdefault(int(mid), []).append((label, url))
    return out

def clear_buttons(path: str, message_id: int):
    """Elimina todos los botones de un mensaje específico."""
    c = _conn(path)
    c.execute("DELETE FROM buttons WHERE message_id=?", (message_id,))
    c.commit()

def count_buttons_for_message(path: str, message_id: int) -> int:
    """Cuenta cuántos botones tiene un mensaje."""
    c = _conn(path)
    cur = c.execute("SELECT COUNT(*) FROM buttons WHERE message_id=?", (message_id,))
    row = cur.fetchone()
    return int(row[0] or 0)

# =========================
# CASOS ENTREGADOS (persistencia)
# =========================
def save_delivered_case(path: str, user_id: int, case_code: str, message_id: Optional[int] = None):
    """Registra un caso entregado a un usuario."""
    c = _conn(path)
    c.execute(
        "INSERT OR REPLACE INTO delivered_cases(user_id, case_code, message_id) VALUES (?,?,?)",
        (user_id, case_code, message_id)
    )
    c.commit()

def is_case_delivered(path: str, user_id: int, case_code: str) -> bool:
    """Verifica si un caso ya fue entregado a un usuario."""
    c = _conn(path)
    cur = c.execute(
        "SELECT 1 FROM delivered_cases WHERE user_id=? AND case_code=? LIMIT 1",
        (user_id, case_code)
    )
    return cur.fetchone() is not None

def get_delivered_cases_for_user(path: str, user_id: int) -> List[str]:
    """Obtiene todos los casos entregados a un usuario."""
    c = _conn(path)
    cur = c.execute(
        "SELECT case_code FROM delivered_cases WHERE user_id=? ORDER BY delivered_at ASC",
        (user_id,)
    )
    return [row[0] for row in cur.fetchall()]

def get_all_delivered_codes(path: str) -> List[str]:
    """Obtiene todos los códigos de casos que han sido entregados."""
    c = _conn(path)
    cur = c.execute("SELECT DISTINCT case_code FROM delivered_cases ORDER BY case_code ASC")
    return [row[0] for row in cur.fetchall()]

def count_deliveries(path: str) -> int:
    """Cuenta el total de entregas realizadas."""
    c = _conn(path)
    cur = c.execute("SELECT COUNT(*) FROM delivered_cases")
    row = cur.fetchone()
    return int(row[0] or 0)

def get_delivery_stats(path: str) -> Dict:
    """Obtiene estadísticas de entregas."""
    c = _conn(path)
    
    # Total de entregas
    cur = c.execute("SELECT COUNT(*) FROM delivered_cases")
    total = int(cur.fetchone()[0] or 0)
    
    # Usuarios únicos
    cur = c.execute("SELECT COUNT(DISTINCT user_id) FROM delivered_cases")
    users = int(cur.fetchone()[0] or 0)
    
    # Casos únicos entregados
    cur = c.execute("SELECT COUNT(DISTINCT case_code) FROM delivered_cases")
    cases = int(cur.fetchone()[0] or 0)
    
    return {
        "total_deliveries": total,
        "unique_users": users,
        "unique_cases": cases
    }
