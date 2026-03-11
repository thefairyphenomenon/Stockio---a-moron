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
            t1 REAL, t2 REAL, sl1 REAL, sl2 REAL,
            status TEXT DEFAULT 'Monitoring...',
            added_by_admin INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS watchlist_strategies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            watchlist_id INTEGER NOT NULL,
            strategy_type TEXT NOT NULL CHECK(strategy_type IN ('uptrend','downtrend','consolidation')),
            is_active INTEGER DEFAULT 0,
            -- User's levels (what the engine actually alerts on)
            t1 REAL, t2 REAL, sl1 REAL, sl2 REAL,
            t1_pct REAL, t2_pct REAL, sl1_pct REAL, sl2_pct REAL,
            -- Engine's suggested levels (stored separately, never overwritten by user)
            engine_t1 REAL, engine_t2 REAL, engine_sl1 REAL, engine_sl2 REAL,
            -- Flag: has the user manually edited their levels?
            user_overridden INTEGER DEFAULT 0,
            -- Notification toggles
            notify_price_targets INTEGER DEFAULT 1,
            notify_stop_loss INTEGER DEFAULT 1,
            notify_ma_crossover INTEGER DEFAULT 1,
            notify_trend_break INTEGER DEFAULT 1,
            notify_consolidation_break INTEGER DEFAULT 1,
            -- State tracking
            status TEXT DEFAULT 'Monitoring...',
            last_ma_state TEXT DEFAULT '',
            deviation_warned INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (watchlist_id) REFERENCES watchlist(id) ON DELETE CASCADE,
            UNIQUE(watchlist_id, strategy_type)
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
    stocks = conn.execute("SELECT * FROM watchlist WHERE user_id = ? ORDER BY created_at DESC", (user_id,)).fetchall()
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

def add_stock(user_id, ticker, company_name, entry_price=None, added_by_admin=0):
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM watchlist WHERE user_id = ? AND ticker = ?",
        (user_id, ticker.upper())
    ).fetchone()
    if existing:
        conn.close()
        return False, "Ticker already in watchlist"
    conn.execute("""
        INSERT INTO watchlist (user_id, ticker, company_name, entry_price, status, added_by_admin)
        VALUES (?, ?, ?, ?, 'Pending Refresh', ?)
    """, (user_id, ticker.upper(), company_name, entry_price, added_by_admin))
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

def update_stock_entry_price(stock_id, entry_price):
    conn = get_db()
    conn.execute("UPDATE watchlist SET entry_price=? WHERE id=?", (entry_price, stock_id))
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

# ── STRATEGY FUNCTIONS ────────────────────────────────

STRATEGY_DEFAULTS = {
    "uptrend":       {"t1_pct": 5.0,  "t2_pct": 10.0, "sl1_pct": -3.0, "sl2_pct": -6.0},
    "downtrend":     {"t1_pct": -5.0, "t2_pct": -10.0,"sl1_pct":  3.0, "sl2_pct":  5.0},
    "consolidation": {"t1_pct": 3.0,  "t2_pct":  6.0, "sl1_pct": -2.0, "sl2_pct": -4.0},
}

def create_strategies_for_stock(watchlist_id, entry_price):
    conn = get_db()
    for stype, pcts in STRATEGY_DEFAULTS.items():
        t1  = round(entry_price * (1 + pcts["t1_pct"]  / 100), 2)
        t2  = round(entry_price * (1 + pcts["t2_pct"]  / 100), 2)
        sl1 = round(entry_price * (1 + pcts["sl1_pct"] / 100), 2)
        sl2 = round(entry_price * (1 + pcts["sl2_pct"] / 100), 2)
        conn.execute("""
            INSERT OR IGNORE INTO watchlist_strategies
            (watchlist_id, strategy_type,
             t1, t2, sl1, sl2, t1_pct, t2_pct, sl1_pct, sl2_pct,
             engine_t1, engine_t2, engine_sl1, engine_sl2)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (watchlist_id, stype,
              t1, t2, sl1, sl2,
              pcts["t1_pct"], pcts["t2_pct"], pcts["sl1_pct"], pcts["sl2_pct"],
              t1, t2, sl1, sl2))
    conn.commit()
    conn.close()

def get_strategies(watchlist_id):
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM watchlist_strategies WHERE watchlist_id = ?
        ORDER BY CASE strategy_type
            WHEN 'uptrend' THEN 1
            WHEN 'consolidation' THEN 2
            WHEN 'downtrend' THEN 3
        END
    """, (watchlist_id,)).fetchall()
    conn.close()
    return rows

def get_all_strategies_for_engine():
    conn = get_db()
    rows = conn.execute("""
        SELECT
            ws.*,
            w.ticker, w.company_name, w.user_id, w.entry_price,
            u.email, u.telegram_chat_id, u.name as user_name
        FROM watchlist_strategies ws
        JOIN watchlist w ON ws.watchlist_id = w.id
        JOIN users u ON w.user_id = u.id
    """).fetchall()
    conn.close()
    return rows

def set_strategy_active(watchlist_id, strategy_type):
    conn = get_db()
    conn.execute("UPDATE watchlist_strategies SET is_active=0 WHERE watchlist_id=?", (watchlist_id,))
    conn.execute("""
        UPDATE watchlist_strategies SET is_active=1
        WHERE watchlist_id=? AND strategy_type=?
    """, (watchlist_id, strategy_type))
    conn.commit()
    conn.close()

def update_strategy_toggles(strategy_id, toggles: dict):
    conn = get_db()
    conn.execute("""
        UPDATE watchlist_strategies SET
            notify_price_targets=?, notify_stop_loss=?,
            notify_ma_crossover=?, notify_trend_break=?,
            notify_consolidation_break=?
        WHERE id=?
    """, (
        toggles.get("notify_price_targets", 1),
        toggles.get("notify_stop_loss", 1),
        toggles.get("notify_ma_crossover", 1),
        toggles.get("notify_trend_break", 1),
        toggles.get("notify_consolidation_break", 1),
        strategy_id
    ))
    conn.commit()
    conn.close()

def update_strategy_user_levels(strategy_id, t1, t2, sl1, sl2):
    """User manually sets their own T1/T2/SL. Sets user_overridden=1. Resets deviation_warned."""
    conn = get_db()
    # Recalculate pct relative to entry price
    stock = conn.execute("""
        SELECT w.entry_price FROM watchlist w
        JOIN watchlist_strategies ws ON ws.watchlist_id = w.id
        WHERE ws.id=?
    """, (strategy_id,)).fetchone()
    entry = stock["entry_price"] if stock and stock["entry_price"] else None
    t1_pct  = round(((t1  - entry) / entry) * 100, 2) if entry else None
    t2_pct  = round(((t2  - entry) / entry) * 100, 2) if entry else None
    sl1_pct = round(((sl1 - entry) / entry) * 100, 2) if entry else None
    sl2_pct = round(((sl2 - entry) / entry) * 100, 2) if entry else None
    conn.execute("""
        UPDATE watchlist_strategies
        SET t1=?, t2=?, sl1=?, sl2=?,
            t1_pct=?, t2_pct=?, sl1_pct=?, sl2_pct=?,
            user_overridden=1, deviation_warned=0, status='Monitoring...'
        WHERE id=?
    """, (t1, t2, sl1, sl2, t1_pct, t2_pct, sl1_pct, sl2_pct, strategy_id))
    conn.commit()
    conn.close()

def update_engine_suggestion(strategy_id, engine_t1, engine_t2, engine_sl1, engine_sl2):
    """Engine updates its suggested levels without touching user's levels."""
    conn = get_db()
    conn.execute("""
        UPDATE watchlist_strategies
        SET engine_t1=?, engine_t2=?, engine_sl1=?, engine_sl2=?
        WHERE id=?
    """, (engine_t1, engine_t2, engine_sl1, engine_sl2, strategy_id))
    conn.commit()
    conn.close()

def reset_deviation_warned(strategy_id):
    conn = get_db()
    conn.execute("UPDATE watchlist_strategies SET deviation_warned=0 WHERE id=?", (strategy_id,))
    conn.commit()
    conn.close()

def update_strategy_status(strategy_id, status):
    conn = get_db()
    conn.execute("UPDATE watchlist_strategies SET status=? WHERE id=?", (status, strategy_id))
    conn.commit()
    conn.close()

def update_strategy_ma_state(strategy_id, ma_state):
    conn = get_db()
    conn.execute("UPDATE watchlist_strategies SET last_ma_state=? WHERE id=?", (ma_state, strategy_id))
    conn.commit()
    conn.close()

def refresh_strategy_levels(watchlist_id, entry_price):
    """Recalculate engine suggestions. Only update user levels if NOT user_overridden."""
    conn = get_db()
    for stype, pcts in STRATEGY_DEFAULTS.items():
        t1  = round(entry_price * (1 + pcts["t1_pct"]  / 100), 2)
        t2  = round(entry_price * (1 + pcts["t2_pct"]  / 100), 2)
        sl1 = round(entry_price * (1 + pcts["sl1_pct"] / 100), 2)
        sl2 = round(entry_price * (1 + pcts["sl2_pct"] / 100), 2)
        # Always update engine suggestion columns
        conn.execute("""
            UPDATE watchlist_strategies
            SET engine_t1=?, engine_t2=?, engine_sl1=?, engine_sl2=?
            WHERE watchlist_id=? AND strategy_type=?
        """, (t1, t2, sl1, sl2, watchlist_id, stype))
        # Only update user-facing levels if not overridden
        conn.execute("""
            UPDATE watchlist_strategies
            SET t1=?, t2=?, sl1=?, sl2=?,
                t1_pct=?, t2_pct=?, sl1_pct=?, sl2_pct=?,
                status='Monitoring...'
            WHERE watchlist_id=? AND strategy_type=? AND user_overridden=0
        """, (t1, t2, sl1, sl2,
              pcts["t1_pct"], pcts["t2_pct"], pcts["sl1_pct"], pcts["sl2_pct"],
              watchlist_id, stype))
    conn.commit()
    conn.close()
