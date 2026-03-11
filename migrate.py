import sqlite3

conn = sqlite3.connect("stockhub.db")
c = conn.cursor()

c.execute("""
    CREATE TABLE IF NOT EXISTS watchlist_strategies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        watchlist_id INTEGER NOT NULL,
        strategy_type TEXT NOT NULL CHECK(strategy_type IN ('uptrend','downtrend','consolidation')),
        is_active INTEGER DEFAULT 0,
        t1 REAL, t2 REAL, sl1 REAL, sl2 REAL,
        t1_pct REAL, t2_pct REAL, sl1_pct REAL, sl2_pct REAL,
        notify_price_targets INTEGER DEFAULT 1,
        notify_stop_loss INTEGER DEFAULT 1,
        notify_ma_crossover INTEGER DEFAULT 1,
        notify_trend_break INTEGER DEFAULT 1,
        notify_consolidation_break INTEGER DEFAULT 1,
        status TEXT DEFAULT 'Monitoring...',
        last_ma_state TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (watchlist_id) REFERENCES watchlist(id) ON DELETE CASCADE,
        UNIQUE(watchlist_id, strategy_type)
    )
""")

conn.commit()

# Create strategy rows for all existing stocks
stocks = conn.execute("SELECT id, entry_price FROM watchlist").fetchall()
conn.row_factory = sqlite3.Row

DEFAULTS = {
    "uptrend":       {"t1_pct": 5.0,  "t2_pct": 10.0, "sl1_pct": -3.0, "sl2_pct": -6.0},
    "downtrend":     {"t1_pct": -5.0, "t2_pct": -10.0,"sl1_pct":  3.0, "sl2_pct":  5.0},
    "consolidation": {"t1_pct": 3.0,  "t2_pct":  6.0, "sl1_pct": -2.0, "sl2_pct": -4.0},
}

for stock in stocks:
    sid   = stock[0]
    price = stock[1] or 100.0
    for stype, pcts in DEFAULTS.items():
        t1  = round(price * (1 + pcts["t1_pct"]  / 100), 2)
        t2  = round(price * (1 + pcts["t2_pct"]  / 100), 2)
        sl1 = round(price * (1 + pcts["sl1_pct"] / 100), 2)
        sl2 = round(price * (1 + pcts["sl2_pct"] / 100), 2)
        c.execute("""
            INSERT OR IGNORE INTO watchlist_strategies
            (watchlist_id, strategy_type, t1, t2, sl1, sl2, t1_pct, t2_pct, sl1_pct, sl2_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (sid, stype, t1, t2, sl1, sl2,
              pcts["t1_pct"], pcts["t2_pct"], pcts["sl1_pct"], pcts["sl2_pct"]))

conn.commit()
conn.close()
print(f"Migration complete. Strategies created for {len(stocks)} existing stocks.")