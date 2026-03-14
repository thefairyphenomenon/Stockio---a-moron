"""
Migration v5 — three new features:
1. MA type preference (SMA vs EMA) stored per watchlist stock
2. RSI/ADX/indicators cached on watchlist row for fast dashboard reads
3. chart_uploads table for user-uploaded or engine-generated bar charts
Run once: python migrate_v5.py
Safe to run multiple times.
"""
import sqlite3

conn = sqlite3.connect("stockhub.db")
c    = conn.cursor()

# ── 1. watchlist: MA type + cached indicators ─────────
watchlist_cols = [
    ("ma_type",          "TEXT DEFAULT 'SMA'"),   # 'SMA' or 'EMA'
    ("rsi",              "REAL"),
    ("adx",              "REAL"),
    ("di_plus",          "REAL"),
    ("di_minus",         "REAL"),
    ("volume_ratio",     "REAL"),
    ("exit_score",       "INTEGER DEFAULT 0"),
    ("indicators_updated", "TIMESTAMP"),
]
for col, coltype in watchlist_cols:
    try:
        c.execute(f"ALTER TABLE watchlist ADD COLUMN {col} {coltype}")
        print(f"  ✓ watchlist.{col}")
    except sqlite3.OperationalError:
        print(f"  – watchlist.{col} already exists")

# ── 2. watchlist_strategies: engine suggestion columns ─
# (These were planned in v3 but the live DB never got them)
strategy_cols = [
    ("engine_t1",        "REAL"),
    ("engine_t2",        "REAL"),
    ("engine_sl1",       "REAL"),
    ("engine_sl2",       "REAL"),
    ("user_overridden",  "INTEGER DEFAULT 0"),
    ("deviation_warned", "INTEGER DEFAULT 0"),
]
for col, coltype in strategy_cols:
    try:
        c.execute(f"ALTER TABLE watchlist_strategies ADD COLUMN {col} {coltype}")
        print(f"  ✓ watchlist_strategies.{col}")
    except sqlite3.OperationalError:
        print(f"  – watchlist_strategies.{col} already exists")

# ── 3. chart_uploads table ────────────────────────────
c.execute("""
    CREATE TABLE IF NOT EXISTS chart_uploads (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL,
        watchlist_id INTEGER,
        ticker       TEXT NOT NULL,
        chart_type   TEXT DEFAULT 'candlestick',  -- 'candlestick', 'bar', 'uploaded'
        timeframe    TEXT DEFAULT '1mo',
        filename     TEXT,           -- stored file path for uploaded images
        chart_data   TEXT,           -- JSON OHLCV payload for generated charts
        label        TEXT DEFAULT '',
        created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id)      REFERENCES users(id)      ON DELETE CASCADE,
        FOREIGN KEY (watchlist_id) REFERENCES watchlist(id)  ON DELETE SET NULL
    )
""")
print("  ✓ chart_uploads table")

conn.commit()
conn.close()
print("\n✅ Migration v5 complete.")