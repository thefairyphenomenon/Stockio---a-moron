import sqlite3
import hashlib
import os

DB_PATH = "stockhub.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            telegram_chat_id TEXT,
            is_admin INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            company_name TEXT NOT NULL,
            entry_price REAL,
            t1 REAL,
            t2 REAL,
            sl1 REAL,
            sl2 REAL,
            status TEXT DEFAULT 'Monitoring...',
            added_by_admin INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS alert_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            price REAL,
            message TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Create default admin if not exists
    admin_password = hash_password("admin123")
    c.execute("""
        INSERT OR IGNORE INTO users (name, email, password, is_admin)
        VALUES (?, ?, ?, 1)
    """, ("Admin", "admin@stockhub.com", admin_password))

    conn.commit()
    conn.close()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def get_user_by_email(email):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    return user

def get_user_by_id(user_id):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return user

def create_user(name, email, password, telegram_chat_id=""):
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO users (name, email, password, telegram_chat_id)
            VALUES (?, ?, ?, ?)
        """, (name, email, hash_password(password), telegram_chat_id))
        conn.commit()
        return True, "User created"
    except sqlite3.IntegrityError:
        return False, "Email already exists"
    finally:
        conn.close()

def get_all_users():
    conn = get_db()
    users = conn.execute("SELECT id, name, email, telegram_chat_id, is_admin, created_at FROM users ORDER BY created_at DESC").fetchall()
    conn.close()
    return users

def get_watchlist(user_id):
    conn = get_db()
    stocks = conn.execute("""
        SELECT * FROM watchlist WHERE user_id = ? ORDER BY created_at DESC
    """, (user_id,)).fetchall()
    conn.close()
    return stocks

def get_all_watchlist():
    conn = get_db()
    stocks = conn.execute("""
        SELECT w.*, u.name as user_name, u.email as user_email
        FROM watchlist w JOIN users u ON w.user_id = u.id
        ORDER BY w.created_at DESC
    """).fetchall()
    conn.close()
    return stocks

def add_stock(user_id, ticker, company_name, added_by_admin=0):
    conn = get_db()
    # Check if ticker already exists for this user
    existing = conn.execute(
        "SELECT id FROM watchlist WHERE user_id = ? AND ticker = ?",
        (user_id, ticker.upper())
    ).fetchone()
    if existing:
        conn.close()
        return False, "Ticker already in watchlist"
    conn.execute("""
        INSERT INTO watchlist (user_id, ticker, company_name, status, added_by_admin)
        VALUES (?, ?, ?, 'Pending Refresh', ?)
    """, (user_id, ticker.upper(), company_name, added_by_admin))
    conn.commit()
    conn.close()
    return True, "Stock added"

def update_stock_levels(stock_id, entry_price, t1, t2, sl1, sl2):
    conn = get_db()
    conn.execute("""
        UPDATE watchlist SET entry_price=?, t1=?, t2=?, sl1=?, sl2=?, status='Monitoring...'
        WHERE id=?
    """, (entry_price, t1, t2, sl1, sl2, stock_id))
    conn.commit()
    conn.close()

def update_stock_status(stock_id, status):
    conn = get_db()
    conn.execute("UPDATE watchlist SET status=? WHERE id=?", (status, stock_id))
    conn.commit()
    conn.close()

def delete_stock(stock_id, user_id=None):
    conn = get_db()
    if user_id:
        conn.execute("DELETE FROM watchlist WHERE id=? AND user_id=?", (stock_id, user_id))
    else:
        conn.execute("DELETE FROM watchlist WHERE id=?", (stock_id,))
    conn.commit()
    conn.close()

def log_alert(user_id, ticker, alert_type, price, message):
    conn = get_db()
    conn.execute("""
        INSERT INTO alert_log (user_id, ticker, alert_type, price, message)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, ticker, alert_type, price, message))
    conn.commit()
    conn.close()

def get_alert_log(user_id=None, limit=50):
    conn = get_db()
    if user_id:
        logs = conn.execute("""
            SELECT a.*, u.name as user_name FROM alert_log a
            JOIN users u ON a.user_id = u.id
            WHERE a.user_id = ? ORDER BY a.sent_at DESC LIMIT ?
        """, (user_id, limit)).fetchall()
    else:
        logs = conn.execute("""
            SELECT a.*, u.name as user_name FROM alert_log a
            JOIN users u ON a.user_id = u.id
            ORDER BY a.sent_at DESC LIMIT ?
        """, (limit,)).fetchall()
    conn.close()
    return logs
