import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "scalehack.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                firm_id TEXT NOT NULL,
                firm_name TEXT NOT NULL,
                sender_name TEXT NOT NULL,
                sender_email TEXT DEFAULT '',
                text TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS firm_memories (
                firm_id TEXT PRIMARY KEY,
                firm_name TEXT NOT NULL,
                summary TEXT DEFAULT '',
                tier TEXT DEFAULT 'cold',
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS processed_emails (
                gmail_id TEXT PRIMARY KEY,
                relevant INTEGER DEFAULT 0,
                processed_at TEXT DEFAULT (datetime('now'))
            );
        """)

def is_email_processed(gmail_id):
    with get_db() as conn:
        row = conn.execute("SELECT 1 FROM processed_emails WHERE gmail_id = ?", (gmail_id,)).fetchone()
        return row is not None

def mark_email_processed(gmail_id, relevant):
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO processed_emails (gmail_id, relevant) VALUES (?, ?)",
            (gmail_id, 1 if relevant else 0)
        )

def add_message(firm_id, firm_name, sender_name, text, sender_email=""):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO messages (firm_id, firm_name, sender_name, sender_email, text) VALUES (?, ?, ?, ?, ?)",
            (firm_id, firm_name, sender_name, sender_email, text)
        )
        conn.execute(
            "INSERT OR IGNORE INTO firm_memories (firm_id, firm_name) VALUES (?, ?)",
            (firm_id, firm_name)
        )

def get_all_firms():
    with get_db() as conn:
        rows = conn.execute("""
            SELECT
                m.firm_id,
                m.firm_name,
                COUNT(m.id) as message_count,
                MAX(m.created_at) as last_message_at,
                (SELECT text FROM messages WHERE firm_id = m.firm_id ORDER BY created_at DESC LIMIT 1) as latest_text,
                (SELECT sender_name FROM messages WHERE firm_id = m.firm_id ORDER BY created_at DESC LIMIT 1) as latest_sender,
                fm.summary,
                fm.tier,
                fm.updated_at as memory_updated_at
            FROM messages m
            LEFT JOIN firm_memories fm ON m.firm_id = fm.firm_id
            GROUP BY m.firm_id
            ORDER BY last_message_at DESC
        """).fetchall()
        return [dict(r) for r in rows]

def get_firm_messages(firm_id, limit=20):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE firm_id = ? ORDER BY created_at DESC LIMIT ?",
            (firm_id, limit)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

def update_firm_memory(firm_id, firm_name, summary, tier):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO firm_memories (firm_id, firm_name, summary, tier, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(firm_id) DO UPDATE SET
                summary = excluded.summary,
                tier = excluded.tier,
                updated_at = excluded.updated_at
        """, (firm_id, firm_name, summary, tier))

def get_setting(key, default=None):
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row['value'] if row else default

def set_setting(key, value):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )
