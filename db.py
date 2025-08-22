import sqlite3
from typing import Optional, List, Tuple

DB_PATH = 'bot_data.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        approved INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        phone TEXT,
        chat_id INTEGER,
        transfer_user_id INTEGER,
        session_string TEXT,
        active INTEGER DEFAULT 0,
        FOREIGN KEY(user_id) REFERENCES users(user_id)
    )''')
    conn.commit()
    conn.close()

def add_user(user_id: int, username: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO users (user_id, username, approved) VALUES (?, ?, 0)', (user_id, username))
    conn.commit()
    conn.close()

def approve_user(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE users SET approved=1 WHERE user_id=?', (user_id,))
    conn.commit()
    conn.close()

def unapprove_user(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE users SET approved=0 WHERE user_id=?', (user_id,))
    conn.commit()
    conn.close()

def is_user_approved(user_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT approved FROM users WHERE user_id=?', (user_id,))
    row = c.fetchone()
    conn.close()
    return bool(row and row[0])

def add_account(user_id: int, phone: str, chat_id: int, transfer_user_id: int, session_string: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT INTO accounts (user_id, phone, chat_id, transfer_user_id, session_string, active) VALUES (?, ?, ?, ?, ?, 0)''',
              (user_id, phone, chat_id, transfer_user_id, session_string))
    conn.commit()
    conn.close()

def get_accounts(user_id: int) -> List[Tuple]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, phone, chat_id, transfer_user_id, session_string, active FROM accounts WHERE user_id=?', (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def set_account_active(account_id: int, active: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE accounts SET active=? WHERE id=?', (active, account_id))
    conn.commit()
    conn.close()

def remove_account(account_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM accounts WHERE id=?', (account_id,))
    conn.commit()
    conn.close()

def get_all_approved_users() -> List[Tuple]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT user_id, username FROM users WHERE approved=1')
    rows = c.fetchall()
    conn.close()
    return rows

def get_user_by_id(user_id: int) -> Optional[Tuple]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT user_id, username, approved FROM users WHERE user_id=?', (user_id,))
    row = c.fetchone()
    conn.close()
    return row 