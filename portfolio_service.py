"""
portfolio_service.py
All portfolio + asset card CRUD.
Calls database.py functions wherever they exist.
Does NOT touch existing watchlist, alert, or engine logic.
"""
import sqlite3
from database import get_db

# ── PORTFOLIOS ────────────────────────────────────────

def get_user_portfolios(user_id):
    conn = get_db()
    rows = conn.execute("""
        SELECT p.*,
               COUNT(pa.id) as asset_count
        FROM portfolios p
        LEFT JOIN portfolio_assets pa ON pa.portfolio_id = p.id
        WHERE p.user_id = ?
        GROUP BY p.id
        ORDER BY p.created_at ASC
    """, (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_portfolio(portfolio_id, user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM portfolios WHERE id=? AND user_id=?",
        (portfolio_id, user_id)
    ).fetchone()
    conn.close()
    return dict(row) if row else None

def create_portfolio(user_id, name, description="", color="#00d4ff", icon="📊"):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO portfolios (user_id, name, description, color, icon)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, name.strip(), description.strip(), color, icon))
    portfolio_id = c.lastrowid
    conn.commit()
    conn.close()
    return portfolio_id

def update_portfolio(portfolio_id, user_id, name=None, description=None, color=None, icon=None):
    conn = get_db()
    row  = conn.execute("SELECT * FROM portfolios WHERE id=? AND user_id=?",
                        (portfolio_id, user_id)).fetchone()
    if not row:
        conn.close()
        return False
    n = name        if name        is not None else row["name"]
    d = description if description is not None else row["description"]
    c = color       if color       is not None else row["color"]
    i = icon        if icon        is not None else row["icon"]
    conn.execute("""
        UPDATE portfolios SET name=?, description=?, color=?, icon=? WHERE id=?
    """, (n, d, c, i, portfolio_id))
    conn.commit()
    conn.close()
    return True

def delete_portfolio(portfolio_id, user_id):
    conn = get_db()
    conn.execute("DELETE FROM portfolios WHERE id=? AND user_id=?",
                 (portfolio_id, user_id))
    conn.commit()
    conn.close()

# ── PORTFOLIO ASSETS (KANBAN CARDS) ──────────────────

def get_portfolio_assets(portfolio_id):
    """Return all assets in a portfolio, enriched with watchlist data + engine analysis."""
    conn = get_db()
    rows = conn.execute("""
        SELECT
            pa.*,
            w.ticker, w.company_name, w.entry_price, w.status,
            w.asset_type, w.exchange, w.created_at as watchlist_created_at,
            ea.trend_state, ea.momentum, ea.volatility_lvl,
            ea.rsi, ea.adx, ea.di_plus, ea.di_minus,
            ea.volume_ratio, ea.exit_score, ea.engine_notes,
            ea.last_updated as analysis_updated
        FROM portfolio_assets pa
        JOIN watchlist w   ON pa.watchlist_id = w.id
        LEFT JOIN engine_analysis ea ON ea.watchlist_id = w.id
        WHERE pa.portfolio_id = ?
        ORDER BY pa.kanban_column, pa.card_order ASC
    """, (portfolio_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_portfolio_assets_by_column(portfolio_id):
    """Returns dict keyed by column name for kanban rendering."""
    assets = get_portfolio_assets(portfolio_id)
    board  = {"uptrend": [], "consolidation": [], "downtrend": []}
    for a in assets:
        col = a.get("kanban_column", "consolidation")
        board.setdefault(col, []).append(a)
    return board

def add_asset_to_portfolio(portfolio_id, watchlist_id, kanban_column="consolidation",
                            buy_price=None, user_remarks="", exit_expectations="",
                            deviation_tolerance=5.0):
    conn = get_db()
    # Verify the watchlist entry exists
    stock = conn.execute("SELECT id FROM watchlist WHERE id=?", (watchlist_id,)).fetchone()
    if not stock:
        conn.close()
        return False, "Stock not found in watchlist"

    # Count current cards in column for ordering
    order = conn.execute(
        "SELECT COUNT(*) FROM portfolio_assets WHERE portfolio_id=? AND kanban_column=?",
        (portfolio_id, kanban_column)
    ).fetchone()[0]

    try:
        conn.execute("""
            INSERT INTO portfolio_assets
            (portfolio_id, watchlist_id, kanban_column, card_order,
             buy_price, user_remarks, exit_expectations, deviation_tolerance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (portfolio_id, watchlist_id, kanban_column, order,
              buy_price, user_remarks, exit_expectations, deviation_tolerance))
        conn.commit()
        conn.close()
        return True, "Asset added to portfolio"
    except sqlite3.IntegrityError:
        conn.close()
        return False, "Asset already in this portfolio"

def remove_asset_from_portfolio(portfolio_asset_id, portfolio_id):
    """Removes from portfolio only — does NOT delete from watchlist."""
    conn = get_db()
    conn.execute("""
        DELETE FROM portfolio_assets WHERE id=? AND portfolio_id=?
    """, (portfolio_asset_id, portfolio_id))
    conn.commit()
    conn.close()

def move_asset_column(portfolio_asset_id, portfolio_id, new_column, new_order=None):
    """User drags card to a new kanban column. Engine never calls this."""
    if new_column not in ("uptrend", "consolidation", "downtrend"):
        return False
    conn = get_db()
    if new_order is None:
        new_order = conn.execute(
            "SELECT COUNT(*) FROM portfolio_assets WHERE portfolio_id=? AND kanban_column=?",
            (portfolio_id, new_column)
        ).fetchone()[0]
    conn.execute("""
        UPDATE portfolio_assets
        SET kanban_column=?, card_order=?
        WHERE id=? AND portfolio_id=?
    """, (new_column, new_order, portfolio_asset_id, portfolio_id))
    conn.commit()
    conn.close()
    return True

def update_asset_card(portfolio_asset_id, portfolio_id, **kwargs):
    """Update any combination of user-defined card fields."""
    allowed = {"user_remarks", "exit_expectations", "deviation_tolerance",
               "user_expected_trend", "buy_price", "buy_date"}
    fields  = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return False
    conn = get_db()
    set_clause = ", ".join(f"{k}=?" for k in fields)
    values     = list(fields.values()) + [portfolio_asset_id, portfolio_id]
    conn.execute(f"""
        UPDATE portfolio_assets SET {set_clause}
        WHERE id=? AND portfolio_id=?
    """, values)
    conn.commit()
    conn.close()
    return True

def add_remark(portfolio_asset_id, remark_text):
    conn = get_db()
    conn.execute("""
        INSERT INTO asset_remarks (portfolio_asset_id, remark_text)
        VALUES (?, ?)
    """, (portfolio_asset_id, remark_text.strip()))
    conn.commit()
    conn.close()

def get_remarks(portfolio_asset_id):
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM asset_remarks
        WHERE portfolio_asset_id=?
        ORDER BY created_at DESC LIMIT 20
    """, (portfolio_asset_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ── ENGINE ANALYSIS CACHE ─────────────────────────────

def upsert_engine_analysis(watchlist_id, trend_state, momentum, volatility_lvl,
                            rsi, adx, di_plus, di_minus, volume_ratio,
                            exit_score, engine_notes):
    conn = get_db()
    conn.execute("""
        INSERT INTO engine_analysis
            (watchlist_id, trend_state, momentum, volatility_lvl,
             rsi, adx, di_plus, di_minus, volume_ratio,
             exit_score, engine_notes, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(watchlist_id) DO UPDATE SET
            trend_state=excluded.trend_state,
            momentum=excluded.momentum,
            volatility_lvl=excluded.volatility_lvl,
            rsi=excluded.rsi, adx=excluded.adx,
            di_plus=excluded.di_plus, di_minus=excluded.di_minus,
            volume_ratio=excluded.volume_ratio,
            exit_score=excluded.exit_score,
            engine_notes=excluded.engine_notes,
            last_updated=CURRENT_TIMESTAMP
    """, (watchlist_id, trend_state, momentum, volatility_lvl,
          rsi, adx, di_plus, di_minus, volume_ratio,
          exit_score, engine_notes))
    conn.commit()
    conn.close()

def get_engine_analysis(watchlist_id):
    conn = get_db()
    row  = conn.execute(
        "SELECT * FROM engine_analysis WHERE watchlist_id=?", (watchlist_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else {}

def get_watchlist_for_user(user_id):
    """Return all watchlist stocks for the asset picker in portfolio builder."""
    conn = get_db()
    rows = conn.execute("""
        SELECT w.id, w.ticker, w.company_name, w.asset_type, w.entry_price,
               w.status, w.created_at
        FROM watchlist w
        WHERE w.user_id = ?
        ORDER BY w.company_name ASC
    """, (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_engine_suggestions(portfolio_id):
    """
    Returns engine suggestions for kanban cards in this portfolio.
    Engine may SUGGEST a move but never executes it.
    """
    conn = get_db()
    rows = conn.execute("""
        SELECT pa.id as portfolio_asset_id, pa.kanban_column as current_column,
               ea.trend_state as suggested_column,
               w.ticker, w.company_name,
               ea.engine_notes, ea.last_updated
        FROM portfolio_assets pa
        JOIN watchlist w ON pa.watchlist_id = w.id
        LEFT JOIN engine_analysis ea ON ea.watchlist_id = w.id
        WHERE pa.portfolio_id = ?
          AND ea.trend_state IS NOT NULL
          AND ea.trend_state != pa.kanban_column
          AND ea.trend_state != 'unknown'
    """, (portfolio_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
