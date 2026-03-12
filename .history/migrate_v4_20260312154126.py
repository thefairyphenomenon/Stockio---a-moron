"""
Migration v4 — adds RSI, ADX, volume, exit score, time-alert columns
Run once: python migrate_v4.py
Safe to run multiple times.
"""
import sqlite3

conn = sqlite3.connect("stockhub.db")
c    = conn.cursor()

# ── watchlist: add indicator + time-alert columns ─────
watchlist_cols = [
    ("rsi",              "REAL"),
    ("adx",              "REAL"),
    ("di_plus",          "REAL"),
    ("di_minus",         "REAL"),
    ("volume_ratio",     "REAL"),   # current vol / 20-day avg vol
    ("exit_score",       "INTEGER DEFAULT 0"),
    ("exit_score_detail","TEXT DEFAULT ''"),
    ("days_alert_7",     "TEXT DEFAULT ''"),
    ("days_alert_15",    "TEXT DEFAULT ''"),
    ("days_alert_30",    "TEXT DEFAULT ''"),
    ("days_alert_60",    "TEXT DEFAULT ''"),
    ("last_indicators_update", "TIMESTAMP"),
]

for col, coltype in watchlist_cols:
    try:
        c.execute(f"ALTER TABLE watchlist ADD COLUMN {col} {coltype}")
        print(f"  Added to watchlist: {col}")
    except sqlite3.OperationalError:
        print(f"  Already exists: {col}")

# ── watchlist_strategies: add death cross tracking ────
strategy_cols = [
    ("death_cross_alerted", "INTEGER DEFAULT 0"),  # fired once per cross
]

for col, coltype in strategy_cols:
    try:
        c.execute(f"ALTER TABLE watchlist_strategies ADD COLUMN {col} {coltype}")
        print(f"  Added to watchlist_strategies: {col}")
    except sqlite3.OperationalError:
        print(f"  Already exists: {col}")

conn.commit()
conn.close()
print("\nMigration v4 complete.")