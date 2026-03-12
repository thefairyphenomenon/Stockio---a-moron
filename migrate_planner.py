"""
Migration: Planner System
- 4 new tables: portfolios, portfolio_assets, asset_remarks, engine_analysis
- 2 new columns on watchlist: asset_type, exchange
Run once: python migrate_planner.py
Safe to run multiple times.
"""
import sqlite3

conn = sqlite3.connect("stockhub.db")
c    = conn.cursor()

# ── 1. portfolios ─────────────────────────────────────
c.execute("""
    CREATE TABLE IF NOT EXISTS portfolios (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL,
        name        TEXT NOT NULL,
        description TEXT DEFAULT '',
        color       TEXT DEFAULT '#00d4ff',
        icon        TEXT DEFAULT '📊',
        created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
""")
print("  ✓ portfolios table")

# ── 2. portfolio_assets (the kanban card link) ────────
# watchlist_id → the actual stock data (not duplicated)
# this table adds portfolio-specific context on top
c.execute("""
    CREATE TABLE IF NOT EXISTS portfolio_assets (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        portfolio_id        INTEGER NOT NULL,
        watchlist_id        INTEGER NOT NULL,
        kanban_column       TEXT DEFAULT 'consolidation'
                                 CHECK(kanban_column IN ('uptrend','consolidation','downtrend')),
        card_order          INTEGER DEFAULT 0,
        user_expected_trend TEXT DEFAULT '',
        user_remarks        TEXT DEFAULT '',
        deviation_tolerance REAL DEFAULT 5.0,
        buy_date            TEXT DEFAULT '',
        buy_price           REAL,
        exit_expectations   TEXT DEFAULT '',
        created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (portfolio_id)  REFERENCES portfolios(id)  ON DELETE CASCADE,
        FOREIGN KEY (watchlist_id)  REFERENCES watchlist(id)   ON DELETE CASCADE,
        UNIQUE(portfolio_id, watchlist_id)
    )
""")
print("  ✓ portfolio_assets table")

# ── 3. asset_remarks (remarks history log) ────────────
c.execute("""
    CREATE TABLE IF NOT EXISTS asset_remarks (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        portfolio_asset_id  INTEGER NOT NULL,
        remark_text         TEXT NOT NULL,
        created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (portfolio_asset_id) REFERENCES portfolio_assets(id) ON DELETE CASCADE
    )
""")
print("  ✓ asset_remarks table")

# ── 4. engine_analysis (cached per ticker, written by Engine 5) ──
c.execute("""
    CREATE TABLE IF NOT EXISTS engine_analysis (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        watchlist_id    INTEGER NOT NULL UNIQUE,
        trend_state     TEXT DEFAULT 'unknown',
        momentum        TEXT DEFAULT 'neutral',
        volatility_lvl  TEXT DEFAULT 'normal',
        rsi             REAL,
        adx             REAL,
        di_plus         REAL,
        di_minus        REAL,
        volume_ratio    REAL,
        exit_score      INTEGER DEFAULT 0,
        engine_notes    TEXT DEFAULT '',
        last_updated    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (watchlist_id) REFERENCES watchlist(id) ON DELETE CASCADE
    )
""")
print("  ✓ engine_analysis table")

# ── 5. Add columns to watchlist ───────────────────────
for col, coltype in [("asset_type", "TEXT DEFAULT 'stock'"),
                     ("exchange",   "TEXT DEFAULT ''")]:
    try:
        c.execute(f"ALTER TABLE watchlist ADD COLUMN {col} {coltype}")
        print(f"  ✓ watchlist.{col} added")
    except sqlite3.OperationalError:
        print(f"  – watchlist.{col} already exists")

conn.commit()
conn.close()
print("\n✅ Planner migration complete.")
