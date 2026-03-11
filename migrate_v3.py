"""
Migration v3 — adds user_overridden and engine suggestion columns
Run once: python migrate_v3.py
Safe to run multiple times.
"""
import sqlite3

conn = sqlite3.connect("stockhub.db")
c    = conn.cursor()

new_columns = [
    ("engine_t1",       "REAL"),
    ("engine_t2",       "REAL"),
    ("engine_sl1",      "REAL"),
    ("engine_sl2",      "REAL"),
    ("user_overridden", "INTEGER DEFAULT 0"),
    ("deviation_warned","INTEGER DEFAULT 0"),
]

for col, coltype in new_columns:
    try:
        c.execute(f"ALTER TABLE watchlist_strategies ADD COLUMN {col} {coltype}")
        print(f"  Added column: {col}")
    except sqlite3.OperationalError:
        print(f"  Column already exists: {col}")

# Backfill engine columns from existing user levels
c.execute("""
    UPDATE watchlist_strategies
    SET engine_t1=t1, engine_t2=t2, engine_sl1=sl1, engine_sl2=sl2
    WHERE engine_t1 IS NULL
""")

# Add entry_price column to watchlist if missing
try:
    c.execute("ALTER TABLE watchlist ADD COLUMN entry_price REAL")
    print("  Added entry_price to watchlist")
except sqlite3.OperationalError:
    print("  entry_price already exists in watchlist")

conn.commit()
conn.close()
print("\nMigration v3 complete.")
