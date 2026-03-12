import yfinance as yf
import requests
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
import database as db
import os

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
GMAIL_ADDRESS  = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_PASSWORD = os.environ.get("GMAIL_PASSWORD", "")

DEVIATION_THRESHOLD = 5.0

# ── TICKER CONVERSION ─────────────────────────────────
def convert_ticker(ticker):
    ticker = ticker.strip().upper()
    special = {"INDEXNSE:NIFTY_50": "^NSEI", "INDEXBOM:SENSEX": "^BSESN"}
    if ticker in special:
        return special[ticker]
    prefixes = {"NASDAQ:": "", "NYSE:": "", "NYSEARCA:": "", "OTCMKTS:": "", "NSE:": ".NS", "BSE:": ".BO"}
    for prefix, suffix in prefixes.items():
        if ticker.startswith(prefix):
            return ticker[len(prefix):] + suffix
    return ticker

# ── PRICE FETCH ───────────────────────────────────────
def get_price(ticker):
    try:
        t = yf.Ticker(convert_ticker(ticker))
        price = t.fast_info.last_price
        if price and price > 0:
            return round(price, 2)
        hist = t.history(period="1d")
        if not hist.empty:
            return round(hist["Close"].iloc[-1], 2)
    except Exception as e:
        print(f"  Price fetch failed {ticker}: {e}")
    return None

# ── FULL INDICATOR FETCH ──────────────────────────────
# Returns: ma10, ma20, ma50, ma200, rsi, adx, di_plus, di_minus, volume_ratio, hist
# hist is passed through so callers can do death cross checks without re-fetching
def get_indicators(ticker):
    try:
        t    = yf.Ticker(convert_ticker(ticker))
        hist = t.history(period="1y")
        if hist.empty or len(hist) < 20:
            return None, None, None, None, None, None, None, None, None, None

        closes = hist["Close"]
        high   = hist["High"]
        low    = hist["Low"]
        volume = hist["Volume"]

        # ── Moving Averages ──
        ma10  = round(closes.tail(10).mean(),  2) if len(closes) >= 10  else None
        ma20  = round(closes.tail(20).mean(),  2) if len(closes) >= 20  else None
        ma50  = round(closes.tail(50).mean(),  2) if len(closes) >= 50  else None
        ma200 = round(closes.tail(200).mean(), 2) if len(closes) >= 200 else None

        # ── RSI(14) ──────────────────────────────────
        # From Excel: <30 = EMERGENCY, <40 = EXIT, <50 = WEAK
        delta    = closes.diff()
        gain     = delta.clip(lower=0)
        loss     = (-delta).clip(lower=0)
        avg_gain = gain.ewm(com=13, adjust=False).mean()   # Wilder smoothing
        avg_loss = loss.ewm(com=13, adjust=False).mean()
        rs       = avg_gain / avg_loss.replace(0, 1e-10)
        rsi_val  = round(float(100 - (100 / (1 + rs.iloc[-1]))), 1)

        # ── ADX(14) + DI+/DI- ────────────────────────
        # From Excel: ADX>25 + -DI>+DI = "STRONG DOWN"
        import pandas as pd
        prev_close = closes.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low  - prev_close).abs()
        ], axis=1).max(axis=1)

        dm_plus  = high.diff().clip(lower=0)
        dm_minus = (-low.diff()).clip(lower=0)
        # Only keep the dominant direction
        mask_plus  = dm_plus  >= dm_minus
        mask_minus = dm_minus >  dm_plus
        dm_plus  = dm_plus.where(mask_plus,  0)
        dm_minus = dm_minus.where(mask_minus, 0)

        atr14    = tr.ewm(com=13, adjust=False).mean()
        di_plus  = round(float(100 * dm_plus.ewm(com=13, adjust=False).mean().iloc[-1]  / max(atr14.iloc[-1], 0.001)), 1)
        di_minus = round(float(100 * dm_minus.ewm(com=13, adjust=False).mean().iloc[-1] / max(atr14.iloc[-1], 0.001)), 1)
        dx       = abs(di_plus - di_minus) / max(di_plus + di_minus, 0.001) * 100
        # ADX = smoothed DX — approximate with EWM on last values
        dx_series = (
            (100 * (dm_plus.ewm(com=13, adjust=False).mean() - dm_minus.ewm(com=13, adjust=False).mean()).abs())
            / (100 * (dm_plus.ewm(com=13, adjust=False).mean() + dm_minus.ewm(com=13, adjust=False).mean()).abs().replace(0, 1e-10))
        )
        adx_val = round(float(dx_series.ewm(com=13, adjust=False).mean().iloc[-1] * 100), 1)

        # ── Volume Ratio ─────────────────────────────
        # From Excel checklist: volume > 1.5x 20-day avg = confirmed breakout
        vol_avg_20  = volume.tail(20).mean()
        vol_ratio   = round(float(volume.iloc[-1] / vol_avg_20), 2) if vol_avg_20 > 0 else None

        return ma10, ma20, ma50, ma200, rsi_val, adx_val, di_plus, di_minus, vol_ratio, hist

    except Exception as e:
        print(f"  Indicator fetch failed {ticker}: {e}")
        return None, None, None, None, None, None, None, None, None, None

# ── BACK-COMPAT: callers that only need MAs ───────────
def get_moving_averages(ticker):
    ma10, ma20, ma50, ma200, *_ = get_indicators(ticker)
    return ma10, ma20, ma50, ma200

# ── DETECT MA STATE ───────────────────────────────────
def detect_ma_state(price, ma10, ma20, ma50, ma200=None):
    if not all([price, ma10, ma20, ma50]):
        return "unknown"
    spread = abs(ma20 - ma50) / ma50 * 100
    if spread <= 2.0:
        return "consolidation"
    if price > ma20 and ma20 > ma50:
        return "uptrend"
    if price < ma20 and ma20 < ma50:
        return "downtrend"
    return "uptrend" if price > ma50 else "downtrend"

# ── RSI LABEL (from Excel thresholds) ─────────────────
def rsi_label(rsi):
    if rsi is None: return "—"
    if rsi < 30:    return "EMERGENCY"
    if rsi < 40:    return "EXIT"
    if rsi < 50:    return "WEAK"
    return "OK"

# ── ADX LABEL (from Excel thresholds) ─────────────────
def adx_label(adx, di_plus, di_minus):
    if adx is None: return "—"
    bearish = di_minus > di_plus if (di_plus and di_minus) else False
    if adx > 25 and bearish: return "STRONG DOWN"
    if bearish:               return "NEGATIVE"
    return "OK"

# ── EXIT SCORE (from Excel Sheet 3 logic) ─────────────
# Counts how many bearish signals are firing. Score drives urgency.
# 1 = Monitor / 2 = Tighten Stop / 3-4 = Exit 50% / 5+ = Emergency Exit
def compute_exit_score(price, entry_price, ma10, ma20, ma50,
                       rsi, adx, di_plus, di_minus, hist):
    signals  = []

    # Signal 1: price below all 3 MAs
    if price and ma10 and ma20 and ma50:
        if price < ma10 and price < ma20 and price < ma50:
            signals.append("Below ALL MAs")

    # Signal 2: 10-DMA crossed below 20-DMA (death cross — today vs yesterday)
    if hist is not None and len(hist) >= 2:
        closes = hist["Close"]
        m10    = closes.rolling(10).mean()
        m20    = closes.rolling(20).mean()
        if (m10.iloc[-2] > m20.iloc[-2]) and (m10.iloc[-1] < m20.iloc[-1]):
            signals.append("Death Cross")

    # Signal 3: RSI < 40
    if rsi and rsi < 40:
        signals.append(f"RSI {rsi} (<40)")

    # Signal 4: RSI < 30 (emergency — counts as 2 per Excel)
    if rsi and rsi < 30:
        signals.append("RSI EMERGENCY (<30)")

    # Signal 5: ADX bearish and strong
    if adx and di_plus and di_minus:
        if adx > 25 and di_minus > di_plus:
            signals.append("ADX Strong Down")

    # Signal 6: price at -8% from entry (Excel Level 1 exit trigger)
    if price and entry_price:
        pnl_pct = (price - entry_price) / entry_price * 100
        if pnl_pct <= -8:
            signals.append(f"Down {pnl_pct:.1f}% from entry")

    # Signal 7: -DI > +DI (bearish momentum confirmed)
    if di_plus and di_minus and di_minus > di_plus:
        signals.append("-DI > +DI")

    return len(signals), ", ".join(signals)

# ── LIVE SNAPSHOT (for dashboard API /api/live_ma) ────
def get_live_ma_snapshot(ticker):
    price = get_price(ticker)
    ma10, ma20, ma50, ma200, rsi, adx, di_plus, di_minus, vol_ratio, hist = get_indicators(ticker)
    state = detect_ma_state(price, ma10, ma20, ma50, ma200)
    return {
        "price":       price,
        "ma10":  ma10, "ma20": ma20, "ma50": ma50, "ma200": ma200,
        "state":       state,
        "rsi":         rsi,
        "rsi_label":   rsi_label(rsi),
        "adx":         adx,
        "di_plus":     di_plus,
        "di_minus":    di_minus,
        "adx_label":   adx_label(adx, di_plus, di_minus),
        "volume_ratio": vol_ratio,
    }

# ── HARD REFRESH ──────────────────────────────────────
def hard_refresh_stock(stock_id, user_entry_price=None):
    conn = db.get_db()
    stock = conn.execute("SELECT * FROM watchlist WHERE id=?", (stock_id,)).fetchone()
    conn.close()
    if not stock:
        return False, "Stock not found"

    entry = user_entry_price or get_price(stock["ticker"])
    if not entry:
        return False, "Could not determine entry price"

    t1  = round(entry * 1.02, 2)
    t2  = round(entry * 1.05, 2)
    sl1 = round(entry * 0.98, 2)
    sl2 = round(entry * 0.95, 2)

    db.update_stock_levels(stock_id, entry, t1, t2, sl1, sl2)

    existing = db.get_strategies(stock_id)
    if existing:
        db.refresh_strategy_levels(stock_id, entry)
    else:
        db.create_strategies_for_stock(stock_id, entry)

    return True, {"price": entry, "t1": t1, "t2": t2, "sl1": sl1, "sl2": sl2}

def hard_refresh_user(user_id):
    stocks = db.get_watchlist(user_id)
    results = []
    for stock in stocks:
        ok, _ = hard_refresh_stock(stock["id"])
        results.append({"ticker": stock["ticker"], "status": "refreshed" if ok else "failed"})
    return results

# ── PORTFOLIO SNAPSHOT ────────────────────────────────
def get_portfolio_snapshot(user_id):
    stocks = db.get_watchlist(user_id)
    result = []
    for stock in stocks:
        price = get_price(stock["ticker"])
        ma10, ma20, ma50, ma200, rsi, adx, di_plus, di_minus, vol_ratio, _ = get_indicators(stock["ticker"])
        item = dict(stock)
        item["live_price"]   = price
        item["ma10"]  = ma10;  item["ma20"]  = ma20
        item["ma50"]  = ma50;  item["ma200"] = ma200
        item["ma_state"]     = detect_ma_state(price, ma10, ma20, ma50, ma200)
        item["rsi"]          = rsi
        item["rsi_label"]    = rsi_label(rsi)
        item["adx"]          = adx
        item["di_plus"]      = di_plus
        item["di_minus"]     = di_minus
        item["adx_label"]    = adx_label(adx, di_plus, di_minus)
        item["volume_ratio"] = vol_ratio
        item["strategies"]   = [dict(s) for s in db.get_strategies(stock["id"])]
        item["pnl_pct"]      = round(((price - stock["entry_price"]) / stock["entry_price"]) * 100, 2) if price and stock["entry_price"] else None
        result.append(item)
    return result

# ═══════════════════════════════════════════════════════
# ENGINE 1 — ACTIVE STRATEGY PRICE & SL ALERTS
# ═══════════════════════════════════════════════════════
def run_price_alert_engine(strategies, today):
    for row in strategies:
        if not row["is_active"] or row["status"] == "FULL EXIT":
            continue

        ticker   = row["ticker"]
        company  = row["company_name"]
        stype    = row["strategy_type"]
        status   = row["status"]
        t1, t2   = row["t1"], row["t2"]
        sl1, sl2 = row["sl1"], row["sl2"]

        price = get_price(ticker)
        if not price or not t1:
            continue

        t1_up = round(((t1 - price) / price) * 100, 1)
        t2_up = round(((t2 - price) / price) * 100, 1)
        msg        = ""
        new_status = None

        if stype == "uptrend":
            if price >= t2:
                if row["notify_price_targets"]:
                    msg = build_message(f"🚀 TARGET 2 HIT | {today}", stype, company, ticker, price, t1, t2, sl2, t1_up, t2_up, "T2 achieved. Consider full exit.")
                new_status = "FULL EXIT"
            elif price >= t1 and status == "Monitoring...":
                if row["notify_price_targets"]:
                    msg = build_message(f"✅ TARGET 1 HIT | {today}", stype, company, ticker, price, t1, t2, sl1, t1_up, t2_up, "T1 achieved. Book 50%, ride to T2.")
                new_status = "T1 HIT - Hold 50%"
            elif price <= sl2 and status != "SL1 - Sell 50%":
                if row["notify_stop_loss"]:
                    msg = build_message(f"🔴 CRITICAL STOP LOSS | {today}", stype, company, ticker, price, t1, t2, sl2, t1_up, t2_up, "Critical SL breached. Full exit.")
                new_status = "FULL EXIT"
            elif price <= sl1 and status == "Monitoring...":
                if row["notify_stop_loss"]:
                    msg = build_message(f"⚠️ STOP LOSS HIT | {today}", stype, company, ticker, price, t1, t2, sl1, t1_up, t2_up, "SL1 hit. Sell 50% of holdings.")
                new_status = "SL1 - Sell 50%"

        elif stype == "downtrend":
            if price <= t2:
                if row["notify_price_targets"]:
                    msg = build_message(f"📉 DOWNSIDE T2 HIT | {today}", stype, company, ticker, price, t1, t2, sl2, t1_up, t2_up, "Downside T2 reached. Cover short / exit.")
                new_status = "FULL EXIT"
            elif price <= t1 and status == "Monitoring...":
                if row["notify_price_targets"]:
                    msg = build_message(f"📉 DOWNSIDE T1 HIT | {today}", stype, company, ticker, price, t1, t2, sl1, t1_up, t2_up, "Downside T1 reached. Book 50% of short.")
                new_status = "T1 HIT - Hold 50%"
            elif price >= sl2 and status != "SL1 - Sell 50%":
                if row["notify_stop_loss"]:
                    msg = build_message(f"🔴 CRITICAL STOP LOSS | {today}", stype, company, ticker, price, t1, t2, sl2, t1_up, t2_up, "Stop loss hit on short. Exit immediately.")
                new_status = "FULL EXIT"
            elif price >= sl1 and status == "Monitoring...":
                if row["notify_stop_loss"]:
                    msg = build_message(f"⚠️ STOP LOSS HIT | {today}", stype, company, ticker, price, t1, t2, sl1, t1_up, t2_up, "SL1 hit on short position.")
                new_status = "SL1 - Sell 50%"

        elif stype == "consolidation":
            if price >= t2:
                if row["notify_price_targets"]:
                    msg = build_message(f"🚀 BREAKOUT T2 | {today}", stype, company, ticker, price, t1, t2, sl2, t1_up, t2_up, "Strong breakout confirmed. T2 achieved.")
                new_status = "FULL EXIT"
            elif price >= t1 and status == "Monitoring...":
                if row["notify_price_targets"]:
                    msg = build_message(f"📈 BREAKOUT T1 | {today}", stype, company, ticker, price, t1, t2, sl1, t1_up, t2_up, "Range breakout. Book 50%, target T2.")
                new_status = "T1 HIT - Hold 50%"
            elif price <= sl2 and status != "SL1 - Sell 50%":
                if row["notify_stop_loss"]:
                    msg = build_message(f"🔴 BREAKDOWN SL2 | {today}", stype, company, ticker, price, t1, t2, sl2, t1_up, t2_up, "Range breakdown confirmed. Full exit.")
                new_status = "FULL EXIT"
            elif price <= sl1 and status == "Monitoring...":
                if row["notify_stop_loss"]:
                    msg = build_message(f"⚠️ BREAKDOWN SL1 | {today}", stype, company, ticker, price, t1, t2, sl1, t1_up, t2_up, "Range breaking down. Sell 50%.")
                new_status = "SL1 - Sell 50%"

        if new_status:
            db.update_strategy_status(row["id"], new_status)
        if msg:
            dispatch(msg, row["telegram_chat_id"], row["email"], company, row["user_id"], ticker, stype.upper(), price)

# ═══════════════════════════════════════════════════════
# ENGINE 2 — MA TRANSITION ALERTS
# ═══════════════════════════════════════════════════════
def run_transition_engine(strategies, today):
    stocks_seen = {}
    for row in strategies:
        wid = row["watchlist_id"]
        if wid not in stocks_seen:
            stocks_seen[wid] = row

    for wid, row in stocks_seen.items():
        ticker  = row["ticker"]
        company = row["company_name"]

        price = get_price(ticker)
        if not price:
            continue

        ma10, ma20, ma50, ma200 = get_moving_averages(ticker)
        if not ma10 or not ma20 or not ma50:
            continue

        current_state = detect_ma_state(price, ma10, ma20, ma50, ma200)
        if current_state == "unknown":
            continue

        all_strats = db.get_strategies(wid)
        strat_map  = {s["strategy_type"]: s for s in all_strats}
        ref        = strat_map.get("uptrend") or list(strat_map.values())[0]
        last_state = ref["last_ma_state"] or ""

        if current_state == last_state:
            continue

        if last_state == "":
            for s in all_strats:
                db.update_strategy_ma_state(s["id"], current_state)
            continue

        if current_state == "consolidation":
            msg = build_transition_message(f"➡️ ENTERING CONSOLIDATION | {today}", company, ticker, price, ma10, ma20, ma50, last_state, current_state)
        elif current_state == "uptrend" and last_state == "consolidation":
            msg = build_transition_message(f"📈 BREAKOUT — UPTREND FORMING | {today}", company, ticker, price, ma10, ma20, ma50, last_state, current_state)
        elif current_state == "uptrend" and last_state == "downtrend":
            msg = build_transition_message(f"📈 FULL REVERSAL — UPTREND | {today}", company, ticker, price, ma10, ma20, ma50, last_state, current_state)
        elif current_state == "downtrend" and last_state == "consolidation":
            msg = build_transition_message(f"📉 BREAKDOWN — DOWNTREND FORMING | {today}", company, ticker, price, ma10, ma20, ma50, last_state, current_state)
        elif current_state == "downtrend" and last_state == "uptrend":
            msg = build_transition_message(f"📉 TREND REVERSAL — DOWNTREND | {today}", company, ticker, price, ma10, ma20, ma50, last_state, current_state)
        else:
            msg = build_transition_message(f"🔄 STRATEGY TRANSITION | {today}", company, ticker, price, ma10, ma20, ma50, last_state, current_state)

        dispatch(msg, row["telegram_chat_id"], row["email"], company, row["user_id"], ticker, "TRANSITION", price)

        for s in all_strats:
            db.update_strategy_ma_state(s["id"], current_state)

# ═══════════════════════════════════════════════════════
# ENGINE 3 — DEVIATION WARNINGS
# ═══════════════════════════════════════════════════════
def run_deviation_engine(strategies, today):
    for row in strategies:
        if not row["user_overridden"]:
            continue

        user_t1   = row["t1"];      engine_t1  = row["engine_t1"]
        user_sl1  = row["sl1"];     engine_sl1 = row["engine_sl1"]

        if not all([user_t1, user_sl1, engine_t1, engine_sl1]):
            continue

        t1_dev  = abs(user_t1  - engine_t1)  / engine_t1  * 100
        sl1_dev = abs(user_sl1 - engine_sl1) / engine_sl1 * 100

        if t1_dev > DEVIATION_THRESHOLD or sl1_dev > DEVIATION_THRESHOLD:
            ticker  = row["ticker"];  company = row["company_name"];  stype = row["strategy_type"]
            msg  = f"<b>⚠️ CUSTOM LEVELS DEVIATION WARNING | {today}</b>\n\n"
            msg += f"<b>{company}</b>  |  <code>{ticker}</code>\n"
            msg += f"📊 <b>Strategy:</b> {STRATEGY_LABELS.get(stype, stype.title())}\n\n"
            msg += f"Your custom levels differ significantly from engine suggestion:\n\n"
            if t1_dev > DEVIATION_THRESHOLD:
                msg += f"🎯 <b>T1 Deviation:</b> {t1_dev:.1f}%  —  Your: <b>{user_t1:.2f}</b>  |  Engine: <b>{engine_t1:.2f}</b>\n\n"
            if sl1_dev > DEVIATION_THRESHOLD:
                msg += f"🛡 <b>SL1 Deviation:</b> {sl1_dev:.1f}%  —  Your: <b>{user_sl1:.2f}</b>  |  Engine: <b>{engine_sl1:.2f}</b>\n\n"
            msg += f"<i>Your levels remain active. This is a suggestion only.</i>\n\n"
            msg += f"⚠️ <b>Do your own research before acting.</b>"
            dispatch(msg, row["telegram_chat_id"], row["email"], company, row["user_id"], ticker, "DEVIATION_WARNING", get_price(ticker) or 0)

# ═══════════════════════════════════════════════════════
# ENGINE 4 — RSI / ADX / EXIT SCORE / DEATH CROSS / TIME EXITS
# From Excel: Sheet 3 (Exit Signal Monitor) + Golden Rules + Time-based exits
# ═══════════════════════════════════════════════════════
def run_indicator_engine(today):
    """
    Runs once per engine cycle across ALL watchlist stocks.
    Fetches RSI, ADX, volume ratio, computes exit score,
    checks death cross and time-based stagnation — fires Telegram alerts.
    """
    conn   = db.get_db()
    stocks = conn.execute("""
        SELECT w.*, u.telegram_chat_id, u.email, u.name as user_name
        FROM watchlist w
        JOIN users u ON w.user_id = u.id
    """).fetchall()
    conn.close()

    for stock in stocks:
        ticker      = stock["ticker"]
        company     = stock["company_name"]
        entry_price = stock["entry_price"]
        stock_id    = stock["id"]
        created_at  = stock["created_at"]

        price = get_price(ticker)
        if not price:
            continue

        ma10, ma20, ma50, ma200, rsi, adx, di_plus, di_minus, vol_ratio, hist = get_indicators(ticker)

        # ── Save indicators to DB ──────────────────────
        conn = db.get_db()
        conn.execute("""
            UPDATE watchlist
            SET rsi=?, adx=?, di_plus=?, di_minus=?, volume_ratio=?, last_indicators_update=CURRENT_TIMESTAMP
            WHERE id=?
        """, (rsi, adx, di_plus, di_minus, vol_ratio, stock_id))

        # ── Exit Score ─────────────────────────────────
        # From Excel Sheet 3: count bearish signals, score drives action
        score, detail = compute_exit_score(price, entry_price, ma10, ma20, ma50, rsi, adx, di_plus, di_minus, hist)
        conn.execute("UPDATE watchlist SET exit_score=?, exit_score_detail=? WHERE id=?", (score, detail, stock_id))
        conn.commit()
        conn.close()

        if not entry_price:
            continue

        pnl_pct = (price - entry_price) / entry_price * 100

        # ── RSI Alerts ────────────────────────────────
        # From Excel Golden Rule #8 and Sheet 3: RSI<30 is emergency
        if rsi and rsi < 30:
            msg  = f"<b>🚨 RSI EMERGENCY | {today}</b>\n\n"
            msg += f"<b>{company}</b>  |  <code>{ticker}</code>\n\n"
            msg += f"💰 <b>CMP:</b> {price:.2f}   📊 <b>RSI:</b> {rsi}\n\n"
            msg += f"⚡ RSI below 30 — historically preceded every major collapse\n"
            msg += f"📋 <b>ACTION:</b> <i>EXIT 100% immediately. RSI emergency.</i>\n\n"
            msg += _indicator_footer(rsi, adx, di_plus, di_minus, vol_ratio, pnl_pct)
            dispatch(msg, stock["telegram_chat_id"], stock["email"], company, stock["user_id"], ticker, "RSI_EMERGENCY", price)

        elif rsi and rsi < 40:
            msg  = f"<b>⚠️ RSI EXIT SIGNAL | {today}</b>\n\n"
            msg += f"<b>{company}</b>  |  <code>{ticker}</code>\n\n"
            msg += f"💰 <b>CMP:</b> {price:.2f}   📊 <b>RSI:</b> {rsi}\n\n"
            msg += f"📋 <b>ACTION:</b> <i>Tighten stop-loss. Prepare 50% exit.</i>\n\n"
            msg += _indicator_footer(rsi, adx, di_plus, di_minus, vol_ratio, pnl_pct)
            dispatch(msg, stock["telegram_chat_id"], stock["email"], company, stock["user_id"], ticker, "RSI_EXIT", price)

        # ── Death Cross Alert ─────────────────────────
        # From Excel Golden Rule #7: first 10/20-DMA cross = BEST exit signal
        # Fired once per cross using death_cross_alerted flag on strategies
        if hist is not None and len(hist) >= 2 and ma10 and ma20:
            closes = hist["Close"]
            m10    = closes.rolling(10).mean()
            m20    = closes.rolling(20).mean()
            if (len(m10) >= 2 and
                m10.iloc[-2] > m20.iloc[-2] and
                m10.iloc[-1] < m20.iloc[-1]):

                # Check if already alerted for this cross on any strategy
                strats = db.get_strategies(stock_id)
                already_alerted = any(s.get("death_cross_alerted", 0) for s in strats)
                if not already_alerted:
                    msg  = f"<b>💀 DEATH CROSS | {today}</b>\n\n"
                    msg += f"<b>{company}</b>  |  <code>{ticker}</code>\n\n"
                    msg += f"💰 <b>CMP:</b> {price:.2f}\n\n"
                    msg += f"📉 10-DMA ({ma10:.2f}) crossed BELOW 20-DMA ({ma20:.2f})\n\n"
                    msg += f"⚡ <i>In 24/24 historical cases this cross preceded the major decline.</i>\n"
                    msg += f"📋 <b>ACTION:</b> <i>SELL 50% immediately. Tighten stop on remainder.</i>\n\n"
                    msg += _indicator_footer(rsi, adx, di_plus, di_minus, vol_ratio, pnl_pct)
                    dispatch(msg, stock["telegram_chat_id"], stock["email"], company, stock["user_id"], ticker, "DEATH_CROSS", price)
                    # Mark all strategies as alerted
                    conn = db.get_db()
                    conn.execute("UPDATE watchlist_strategies SET death_cross_alerted=1 WHERE watchlist_id=?", (stock_id,))
                    conn.commit()
                    conn.close()
            else:
                # Reset flag when cross reverses (golden cross)
                if ma10 and ma20 and ma10 > ma20:
                    conn = db.get_db()
                    conn.execute("UPDATE watchlist_strategies SET death_cross_alerted=0 WHERE watchlist_id=?", (stock_id,))
                    conn.commit()
                    conn.close()

        # ── Exit Score Alert ─────────────────────────
        # From Excel Sheet 3: 3+ signals = high risk, 5+ = emergency
        if score >= 5:
            msg  = f"<b>🚨 EMERGENCY EXIT SIGNAL | {today}</b>\n\n"
            msg += f"<b>{company}</b>  |  <code>{ticker}</code>\n\n"
            msg += f"💰 <b>CMP:</b> {price:.2f}   🔴 <b>Exit Score: {score}/7</b>\n\n"
            msg += f"🚨 <b>Signals firing:</b>\n<i>{detail}</i>\n\n"
            msg += f"📋 <b>ACTION:</b> <i>EXIT 100% IMMEDIATELY.</i>\n\n"
            msg += _indicator_footer(rsi, adx, di_plus, di_minus, vol_ratio, pnl_pct)
            dispatch(msg, stock["telegram_chat_id"], stock["email"], company, stock["user_id"], ticker, "EXIT_EMERGENCY", price)

        elif score >= 3:
            msg  = f"<b>⚠️ HIGH RISK — EXIT 50% | {today}</b>\n\n"
            msg += f"<b>{company}</b>  |  <code>{ticker}</code>\n\n"
            msg += f"💰 <b>CMP:</b> {price:.2f}   🟠 <b>Exit Score: {score}/7</b>\n\n"
            msg += f"🔶 <b>Signals firing:</b>\n<i>{detail}</i>\n\n"
            msg += f"📋 <b>ACTION:</b> <i>EXIT 50%. High risk position.</i>\n\n"
            msg += _indicator_footer(rsi, adx, di_plus, di_minus, vol_ratio, pnl_pct)
            dispatch(msg, stock["telegram_chat_id"], stock["email"], company, stock["user_id"], ticker, "EXIT_HIGH_RISK", price)

        # ── Time-Based Stagnation Alerts ─────────────
        # From Excel Golden Rule #10 + time-alert columns
        if created_at:
            try:
                buy_date = datetime.strptime(str(created_at)[:10], "%Y-%m-%d")
                days     = (datetime.now() - buy_date).days
                conn     = db.get_db()
                row_d    = conn.execute("SELECT * FROM watchlist WHERE id=?", (stock_id,)).fetchone()
                conn.close()

                def _time_alert(day_threshold, col, condition_met, action_text, urgency_icon):
                    if days >= day_threshold and condition_met:
                        existing = row_d[col] if row_d and row_d[col] else ""
                        if existing != "ALERTED":
                            msg  = f"<b>{urgency_icon} TIME-BASED EXIT | Day {days} | {today}</b>\n\n"
                            msg += f"<b>{company}</b>  |  <code>{ticker}</code>\n\n"
                            msg += f"💰 <b>CMP:</b> {price:.2f}   📅 <b>Days held:</b> {days}\n"
                            msg += f"📊 <b>P&L:</b> {pnl_pct:+.1f}%\n\n"
                            msg += f"📋 <b>ACTION:</b> <i>{action_text}</i>\n\n"
                            msg += f"⚠️ <b>Do your own research before acting.</b>"
                            dispatch(msg, stock["telegram_chat_id"], stock["email"], company, stock["user_id"], ticker, f"TIME_{day_threshold}D", price)
                            conn2 = db.get_db()
                            conn2.execute(f"UPDATE watchlist SET {col}='ALERTED' WHERE id=?", (stock_id,))
                            conn2.commit()
                            conn2.close()

                # Day 7: flat (<3% up) → REVIEW
                _time_alert(7,  "days_alert_7",  pnl_pct < 3,
                    "7 days held. Less than 3% gain. Review thesis — reduce 25% or tighten stop.", "🕐")
                # Day 15: no progress (<5%) → EXIT 50% (Excel: "EXIT DEAD $")
                _time_alert(15, "days_alert_15", pnl_pct < 5,
                    "15 days held. No meaningful progress. EXIT 50%. Dead money costs opportunity.", "⏰")
                # Day 30: stagnant (within ±5%) → EXIT 100%
                _time_alert(30, "days_alert_30", abs(pnl_pct) < 5,
                    "30 days held. Price within ±5% of entry. EXIT 100%. Stagnant position.", "🔔")
                # Day 60: mandatory re-evaluate
                _time_alert(60, "days_alert_60", True,
                    "60 days held. Full re-evaluation required. Does the thesis still hold?", "🚨")
            except Exception as e:
                print(f"  Time alert error {ticker}: {e}")

# ── MAIN RUNNER ───────────────────────────────────────
def run_alert_engine():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Alert engine running...")
    today      = datetime.now().strftime("%d %b %Y")
    strategies = db.get_all_strategies_for_engine()
    if not strategies:
        print("  No strategies found.")
        return
    run_price_alert_engine(strategies, today)
    run_transition_engine(strategies, today)
    run_deviation_engine(strategies, today)
    run_indicator_engine(today)           # ← ENGINE 4 (new)
    print(f"  Done.")

# ── MESSAGE BUILDERS ──────────────────────────────────
STRATEGY_LABELS = {
    "uptrend":       "📈 Uptrend",
    "downtrend":     "📉 Downtrend",
    "consolidation": "➡️ Consolidation"
}

def _indicator_footer(rsi, adx, di_plus, di_minus, vol_ratio, pnl_pct):
    """Compact indicator summary appended to alert messages."""
    lines = []
    if rsi      is not None: lines.append(f"RSI: <b>{rsi}</b> ({rsi_label(rsi)})")
    if adx      is not None: lines.append(f"ADX: <b>{adx}</b> ({adx_label(adx, di_plus, di_minus)})")
    if vol_ratio is not None: lines.append(f"Vol: <b>{vol_ratio:.1f}x</b> avg")
    if pnl_pct  is not None: lines.append(f"P&L: <b>{pnl_pct:+.1f}%</b>")
    footer = "   ".join(lines)
    return f"{footer}\n\n⚠️ <b>Do your own research before acting.</b>"

def build_message(header, strategy, company, ticker, price, t1, t2, sl, t1_up, t2_up, note):
    msg  = f"<b>{header}</b>\n\n"
    msg += f"<b>{company}</b>  |  <code>{ticker}</code>\n"
    msg += f"📊 <b>Strategy:</b> {STRATEGY_LABELS.get(strategy, strategy.title())}\n\n"
    msg += f"💰 <b>CMP:</b>  {price:.2f}\n\n"
    msg += f"🎯 <b>TARGETS</b>\n"
    msg += f"     T1:  <b>{t1:.2f}</b>  <i>(+{t1_up}%)</i>\n"
    msg += f"     T2:  <b>{t2:.2f}</b>  <i>(+{t2_up}%)</i>\n\n"
    msg += f"🛡 <b>STOP LOSS:  {sl:.2f}</b>\n\n"
    msg += f"📋 <b>SIGNAL</b>\n"
    msg += f"     <i>{note}</i>\n\n"
    msg += f"⚠️ <b>Do your own research before acting.</b>"
    return msg

def build_transition_message(header, company, ticker, price, ma10, ma20, ma50, from_state, to_state):
    msg  = f"<b>{header}</b>\n\n"
    msg += f"<b>{company}</b>  |  <code>{ticker}</code>\n\n"
    msg += f"💰 <b>CMP:</b>  {price:.2f}\n\n"
    msg += f"🔄 <b>TRANSITION</b>\n"
    msg += f"     From:  <b>{STRATEGY_LABELS.get(from_state, from_state)}</b>\n"
    msg += f"     To:    <b>{STRATEGY_LABELS.get(to_state, to_state)}</b>\n\n"
    msg += f"📉 <b>MOVING AVERAGES</b>\n"
    msg += f"     10MA:  <b>{ma10:.2f}</b>\n"
    msg += f"     20MA:  <b>{ma20:.2f}</b>\n"
    msg += f"     50MA:  <b>{ma50:.2f}</b>\n\n"
    msg += f"📋 <b>SIGNAL</b>\n"
    msg += f"     <i>Market conditions shifted. Review your active strategy.</i>\n\n"
    msg += f"⚠️ <b>Do your own research before acting.</b>"
    return msg

# ── DISPATCH ──────────────────────────────────────────
def dispatch(msg, chat_id, email, company, user_id, ticker, alert_type, price):
    send_telegram(msg, chat_id)
    send_email(email, company, msg)
    db.log_alert(user_id, ticker, alert_type, price, msg)
    print(f"  Dispatched: {ticker} → {alert_type}")

def send_telegram(msg, chat_id):
    if not TELEGRAM_TOKEN or not chat_id:
        return
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"}, timeout=10)
        if r.status_code != 200:
            print(f"  Telegram error: {r.text}")
    except Exception as e:
        print(f"  Telegram failed: {e}")

def send_email(to_email, company, msg):
    if not GMAIL_ADDRESS or not GMAIL_PASSWORD or not to_email:
        return
    try:
        import re
        plain = re.sub(r"<[^>]+>", "", msg)
        message = MIMEText(plain)
        message["Subject"] = f"Stock Alert: {company}"
        message["From"]    = GMAIL_ADDRESS
        message["To"]      = to_email
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, to_email, message.as_string())
    except Exception as e:
        print(f"  Email failed: {e}")