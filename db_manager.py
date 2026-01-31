"""データベース管理モジュール"""
import sqlite3
from datetime import datetime

DB_FILE = "history.db"

def initialize_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS history (
            email TEXT PRIMARY KEY,
            send_count INTEGER NOT NULL DEFAULT 0,
            last_sent TEXT,
            content TEXT
        )
    """)
    conn.commit()
    conn.close()
    print(f"[DB] データベース '{DB_FILE}' を初期化しました。")

def get_send_count(email):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT send_count FROM history WHERE email = ?", (email,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0

def increment_and_record(email, content):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute("SELECT send_count FROM history WHERE email = ?", (email,))
    result = cursor.fetchone()
    
    if result:
        new_count = result[0] + 1
        cursor.execute("""
            UPDATE history SET send_count = ?, last_sent = ?, content = ?
            WHERE email = ?
        """, (new_count, now, content, email))
    else:
        cursor.execute("""
            INSERT INTO history (email, send_count, last_sent, content)
            VALUES (?, 1, ?, ?)
        """, (email, now, content))
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    initialize_db()
