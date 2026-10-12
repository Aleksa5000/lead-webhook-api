import os
import sqlite3
from datetime import datetime, timedelta, timezone

SCHEMA = """CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT,
    message TEXT NOT NULL, source TEXT NOT NULL,
    score INTEGER NOT NULL, created_at TEXT NOT NULL)"""


def connect() -> sqlite3.Connection:
    # Path is read per call so tests (and deployments) can change it via env.
    conn = sqlite3.connect(os.getenv("DATABASE_PATH", "leads.db"))
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    return conn


def now() -> datetime:
    return datetime.now(timezone.utc)


def is_duplicate(email: str, hours: int = 24) -> bool:
    since = (now() - timedelta(hours=hours)).isoformat()
    conn = connect()
    try:
        return conn.execute(
            "SELECT 1 FROM leads WHERE email = ? AND created_at >= ?", (email, since)
        ).fetchone() is not None
    finally:
        conn.close()


def insert_lead(name, email, phone, message, source, score) -> dict:
    conn = connect()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO leads (name,email,phone,message,source,score,created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (name, email, phone, message, source, score, now().isoformat()),
            )
        return dict(conn.execute("SELECT * FROM leads WHERE id = ?", (cur.lastrowid,)).fetchone())
    finally:
        conn.close()


def list_leads(min_score: int = 0) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM leads WHERE score >= ? ORDER BY id DESC", (min_score,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
