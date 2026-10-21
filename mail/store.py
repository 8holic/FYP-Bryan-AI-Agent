import os
import sqlite3

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_FILE = os.path.join(HERE, "mail.db")


def _connect():
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS messages ("
        "id TEXT PRIMARY KEY, sender TEXT, subject TEXT, "
        "date TEXT, body TEXT)"
    )
    return conn


def upsert(mid, sender, subject, date, body):
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO messages (id, sender, subject, date, body) "
            "VALUES (?, ?, ?, ?, ?)",
            (mid, sender, subject, date, body),
        )
        conn.commit()
    finally:
        conn.close()


def known():
    conn = _connect()
    try:
        return {row[0] for row in conn.execute("SELECT id FROM messages")}
    finally:
        conn.close()


def count():
    conn = _connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    finally:
        conn.close()


def search(query, limit=5):
    conn = _connect()
    try:
        like = f"%{query}%"
        return conn.execute(
            "SELECT id, sender, subject, date, substr(body, 1, 300) FROM messages "
            "WHERE sender LIKE ? OR subject LIKE ? OR body LIKE ? "
            "ORDER BY rowid DESC LIMIT ?",
            (like, like, like, int(limit)),
        ).fetchall()
    finally:
        conn.close()
