import yfinance as yf
import requests
import smtplib
import json
from email.mime.text import MIMEText
from datetime import datetime
import pandas as pd
import database as db
import os

TELEGRAM_TOKEN     = os.environ.get("TELEGRAM_TOKEN", "")
GMAIL_ADDRESS      = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_PASSWORD     = os.environ.get("GMAIL_PASSWORD", "")
DEVIATION_THRESHOLD = 5.0

def convert_ticker(ticker):
    ticker = ticker.strip().upper()
    special = {"INDEXNSE:NIFTY_50": "^NSEI", "INDEXBOM:SENSEX": "^BSESN"}
    if ticker in special:
        return special[ticker]
    prefixes = {"NASDAQ:": "", "NYSE:": "", "NYSEARCA:": "", "OTCMKTS:": "",
                "NSE:": ".NS", "BSE:": ".BO"}
    for prefix, suffix in prefixes.items():
        if ticker.startswith(prefix):
            return ticker[len(prefix):] + suffix
    return ticker

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

def _compute_ma(closes, period, ma_type):
    if len(closes) < period:
        return None
    if ma_type == "EMA":
        val = closes.ewm(span=period, adjust=False).mean().iloc[-1]
    else:
        val = closes.tail(period).mean()
    return round(float(val), 2)

def get_moving_averages(ticker, ma_type="SMA"):
    try:
        t    = yf.Ticker(convert_ticker(ticker))
        hist = t.history(period="1y")
        if hist.empty or len(hist) < 10:
            return None, None, None, None
        closes = hist["Close"]
        return (_compute_ma(closes, 10, ma_type), _compute_ma(closes, 20, ma_type),
                _compute_ma(closes, 50, ma_type), _compute_ma(closes, 200, ma_type))
    except Exception as e:
        print(f"  MA fetch failed {ticker}: {e}")
        return None, None, None, None

def get_indicators(ticker, ma_type="SMA"):
    try:
        t    = yf.Ticker(convert_ticker(ticker))
        hist = t.history(period="1y")
        if hist.empty or len(hist) < 20:
            return None, None, None, None, None, None, None, None, None, None
        closes = hist["Close"]
        high   = hist["High"]
        low    = hist["Low"]
        volume = hist["Volume"]
        ma10  = _compute_ma(closes, 10,  ma_type)
        ma20  = _compute_ma(closes, 20,  ma_type)
        ma50  = _compute_ma(closes, 50,  ma_type)
        ma200 = _compute_ma(closes, 200, ma_type)
        delta    = closes.diff()
        gain     = delta.clip(lower=0)
        loss     = (-delta).clip(lower=0)
        avg_gain = gain.ewm(com=13, adjust=False).mean()
        avg_loss = loss.ewm(com=13, adjust=False).mean()
        rs       = avg_gain / avg_loss.replace(0, 1e-10)
        rsi_val  = round(float((100 - (100 / (1 + rs))).iloc[-1]), 1)
        prev_close = closes.shift(1)
        tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        raw_dmp = high.diff();  raw_dmm = (-low.diff())
        dmp = raw_dmp.where((raw_dmp > 0) & (raw_dmp > raw_dmm), 0)
        dmm = raw_dmm.where((raw_dmm > 0) & (raw_dmm > raw_dmp), 0)
        atr = tr.ewm(com=13, adjust=False).mean()
        sdi_p = 100 * dmp.ewm(com=13, adjust=False).mean() / atr.replace(0, 1e-10)
        sdi_m = 100 * dmm.ewm(com=13, adjust=False).mean() / atr.replace(0, 1e-10)
        di_plus_val  = round(float(sdi_p.iloc[-1]), 1)
        di_minus_val = round(float(sdi_m.iloc[-1]), 1)
        dx = (sdi_p - sdi_m).abs() / (sdi_p + sdi_m).replace(0, 1e-10) * 100
        adx_val = round(float(dx.ewm(com=13, adjust=False).mean().iloc[-1]), 1)
        vol_avg = volume.tail(20).mean()
        vol_ratio = round(float(volume.iloc[-1] / vol_avg), 2) if vol_avg > 0 else None
        return ma10, ma20, ma50, ma200, rsi_val, adx_val, di_plus_val, di_minus_val, vol_ratio, hist
    except Exception as e:
        print(f"  Indicator fetch failed {ticker}: {e}")
        return None, None, None, None, None, None, None, None, None, None

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

def rsi_signal(rsi):
    if rsi is None:
        return "—", "", 0
    if rsi >= 80:
        return "EXTREMELY OVERBOUGHT", "Consider taking profits. Exhaustion likely.", 2
    if rsi >= 70:
        return "OVERBOUGHT", "Momentum may be peaking. Watch for reversal.", 1
    if rsi >= 60:
        return "BULLISH", "Strong upward momentum continuing.", 0
    if rsi >= 50:
        return "NEUTRAL-BULLISH", "Mild bullish bias. No strong signal.", 0
    if rsi >= 40:
        return "NEUTRAL-BEARISH", "Selling pressure building.", 1
    if rsi >= 30:
        return "BEARISH", "Weak momentum. Prepare stop loss review.", 2
    if rsi >= 20:
        return "OVERSOLD", "Approaching reversal zone but still in downtrend.", 2
    return "EXTREMELY OVERSOLD", "Potential reversal. High risk.", 3

def adx_signal(adx, di_plus, di_minus):
    if adx is None:
        return "—", "", 0
    direction = "bullish" if (di_plus and di_minus and di_plus > di_minus) else "bearish"
    if adx < 20:
        return "NO TREND", "Market ranging. Avoid trend-following strategies.", 1
    if adx < 25:
        return f"WEAK {direction.upper()} TREND", "Trend forming but not confirmed.", 1
    if adx < 40:
        return f"TRENDING {direction.upper()}", "Clear directional move. Trade with the trend.", 0
    return f"STRONG {direction.upper()} TREND", "Powerful trend. Ride it with tight trailing stop.", 0

def prediction_score(price, entry_price, rsi, adx, di_plus, di_minus, ma_state):
    score = 0; reasons = []
    if ma_state == "uptrend":
        score += 1; reasons.append("MAs aligned bullish (uptrend)")
    elif ma_state == "downtrend":
        score -= 1; reasons.append("MAs aligned bearish (downtrend)")
    if rsi is not None:
        if rsi > 70:
            score -= 1; reasons.append(f"RSI {rsi} — overbought, momentum may reverse")
        elif rsi > 50:
            score += 1; reasons.append(f"RSI {rsi} — bullish momentum zone")
        elif rsi < 30:
            score -= 1; reasons.append(f"RSI {rsi} — oversold but dangerous territory")
        else:
            score -= 1; reasons.append(f"RSI {rsi} — below 50, bearish bias")
    if adx is not None and di_plus is not None and di_minus is not None:
        if adx >= 25:
            if di_plus > di_minus:
                score += 1; reasons.append(f"ADX {adx} with +DI > -DI — strong bullish trend")
            else:
                score -= 1; reasons.append(f"ADX {adx} with -DI > +DI — strong bearish trend")
        else:
            reasons.append(f"ADX {adx} — below 25, weak/no trend")
    if price and entry_price:
        pnl = (price - entry_price) / entry_price * 100
        if pnl >= 10:
            score += 1; reasons.append(f"Position up {pnl:.1f}% from entry")
        elif pnl <= -8:
            score -= 1; reasons.append(f"Position down {pnl:.1f}% from entry")
    score = max(-3, min(3, score))
    label_map = {3:"STRONG BUY",2:"BUY",1:"MILD BUY",0:"NEUTRAL",-1:"MILD SELL",-2:"SELL",-3:"STRONG SELL"}
    return {"signal": label_map[score], "score": score, "reasons": reasons}

def generate_chart_data(ticker, period="1mo", ma_type="SMA", include_indicators=True):
    try:
        t    = yf.Ticker(convert_ticker(ticker))
        hist = t.history(period=period)
        if hist.empty:
            return None
        hist = hist.reset_index()
        if "Datetime" in hist.columns:
            hist = hist.rename(columns={"Datetime": "Date"})
        hist["Date"] = pd.to_datetime(hist["Date"]).dt.strftime("%Y-%m-%d")
        ohlcv = []
        for _, row in hist.iterrows():
            ohlcv.append({"date": row["Date"], "open": round(float(row["Open"]), 2),
                          "high": round(float(row["High"]), 2), "low": round(float(row["Low"]), 2),
                          "close": round(float(row["Close"]), 2), "volume": int(row["Volume"])})
        closes = hist["Close"]
        result = {"ticker": ticker, "period": period, "ma_type": ma_type,
                  "ohlcv": ohlcv, "dates": list(hist["Date"]),
                  "closes": [round(float(c), 2) for c in closes]}
        if include_indicators:
            def ma_series(p):
                vals = closes.ewm(span=p, adjust=False).mean() if ma_type == "EMA" else closes.rolling(p).mean()
                return [round(float(v), 2) if not pd.isna(v) else None for v in vals]
            result["ma10"] = ma_series(10)
            result["ma20"] = ma_series(20)
            result["ma50"] = ma_series(50) if len(closes) >= 50 else None
            delta = closes.diff(); gain = delta.clip(lower=0); loss = (-delta).clip(lower=0)
            avg_gain = gain.ewm(com=13, adjust=False).mean()
            avg_loss = loss.ewm(com=13, adjust=False).mean()
            rs = avg_gain / avg_loss.replace(0, 1e-10)
            rsi_s = 100 - (100 / (1 + rs))
            result["rsi_series"] = [round(float(v), 1) if not pd.isna(v) else None for v in rsi_s]
            high = hist["High"]; low_h = hist["Low"]; prev_c = closes.shift(1)
            tr = pd.concat([(high - low_h), (high - prev_c).abs(), (low_h - prev_c).abs()], axis=1).max(axis=1)
            raw_dmp = high.diff(); raw_dmm = (-low_h.diff())
            dmp = raw_dmp.where((raw_dmp > 0) & (raw_dmp > raw_dmm), 0)
            dmm = raw_dmm.where((raw_dmm > 0) & (raw_dmm > raw_dmp), 0)
            atr = tr.ewm(com=13, adjust=False).mean()
            di_p = 100 * dmp.ewm(com=13, adjust=False).mean() / atr.replace(0, 1e-10)
            di_m = 100 * dmm.ewm(com=13, adjust=False).mean() / atr.replace(0, 1e-10)
            dx   = (di_p - di_m).abs() / (di_p + di_m).replace(0, 1e-10) * 100
            adx_s = dx.ewm(com=13, adjust=False).mean()
            result["adx_series"]     = [round(float(v), 1) if not pd.isna(v) else None for v in adx_s]
            result["di_plus_series"] = [round(float(v), 1) if not pd.isna(v) else None for v in di_p]
            result["di_minus_series"]= [round(float(v), 1) if not pd.isna(v) else None for v in di_m]
            result["volume_colors"]  = ["#00ff88" if r["close"] >= r["open"] else "#ff3b5c" for r in ohlcv]
        return result
    except Exception as e:
        print(f"  Chart data failed {ticker}: {e}")
        return None

def get_live_ma_snapshot(ticker, ma_type="SMA"):
    price = get_price(ticker)
    ma10, ma20, ma50, ma200, rsi, adx, di_plus, di_minus, vol_ratio, hist = get_indicators(ticker, ma_type)
    state = detect_ma_state(price, ma10, ma20, ma50, ma200)
    rsi_lbl, rsi_note, _ = rsi_signal(rsi)
    adx_lbl, adx_note, _ = adx_signal(adx, di_plus, di_minus)
    return {"price": price, "ma_type": ma_type,
            "ma10": ma10, "ma20": ma20, "ma50": ma50, "ma200": ma200,
            "state": state, "rsi": rsi, "rsi_label": rsi_lbl, "rsi_note": rsi_note,
            "adx": adx, "adx_label": adx_lbl, "adx_note": adx_note,
            "di_plus": di_plus, "di_minus": di_minus, "volume_ratio": vol_ratio}

def hard_refresh_stock(stock_id, user_entry_price=None):
    conn  = db.get_db()
    stock = conn.execute("SELECT * FROM watchlist WHERE id=?", (stock_id,)).fetchone()
    conn.close()
    if not stock:
        return False, "Stock not found"
    ma_type = stock["ma_type"] if stock["ma_type"] else "SMA"
    entry   = user_entry_price or get_price(stock["ticker"])
    if not entry:
        return False, "Could not determine entry price"
    t1 = round(entry * 1.02, 2); t2 = round(entry * 1.05, 2)
    sl1= round(entry * 0.98, 2); sl2= round(entry * 0.95, 2)
    db.update_stock_levels(stock_id, entry, t1, t2, sl1, sl2)
    existing = db.get_strategies(stock_id)
    if existing:
        db.refresh_strategy_levels(stock_id, entry)
    else:
        db.create_strategies_for_stock(stock_id, entry)
    return True, {"price": entry, "t1": t1, "t2": t2, "sl1": sl1, "sl2": sl2}

def hard_refresh_user(user_id):
    stocks  = db.get_watchlist(user_id)
    results = []
    for stock in stocks:
        ok, _ = hard_refresh_stock(stock["id"])
        results.append({"ticker": stock["ticker"], "status": "refreshed" if ok else "failed"})
    return results

def get_portfolio_snapshot(user_id):
    stocks = db.get_watchlist(user_id)
    result = []
    for stock in stocks:
        ma_type = stock["ma_type"] if stock["ma_type"] else "SMA"
        price   = get_price(stock["ticker"])
        ma10, ma20, ma50, ma200, rsi, adx, di_plus, di_minus, vol_ratio, hist = get_indicators(stock["ticker"], ma_type)
        item = dict(stock)
        item["live_price"] = price
        item["ma10"] = ma10; item["ma20"] = ma20; item["ma50"] = ma50; item["ma200"] = ma200
        item["ma_state"]    = detect_ma_state(price, ma10, ma20, ma50, ma200)
        item["rsi"] = rsi; item["adx"] = adx
        item["di_plus"] = di_plus; item["di_minus"] = di_minus
        item["volume_ratio"] = vol_ratio
        item["strategies"]  = [dict(s) for s in db.get_strategies(stock["id"])]
        item["pnl_pct"]     = round(((price - stock["entry_price"]) / stock["entry_price"]) * 100, 2) if price and stock["entry_price"] else None
        result.append(item)
    return result

STRATEGY_LABELS = {"uptrend": "📈 Uptrend", "downtrend": "📉 Downtrend", "consolidation": "➡️ Consolidation"}

def run_price_alert_engine(strategies, today):
    for row in strategies:
        if not row["is_active"] or row["status"] == "FULL EXIT":
            continue
        ticker = row["ticker"]; company = row["company_name"]
        stype  = row["strategy_type"]; status = row["status"]
        t1, t2 = row["t1"], row["t2"]; sl1, sl2 = row["sl1"], row["sl2"]
        price = get_price(ticker)
        if not price or not t1:
            continue
        t1_up = round(((t1 - price) / price) * 100, 1)
        t2_up = round(((t2 - price) / price) * 100, 1)
        msg = ""; new_status = None
        if stype == "uptrend":
            if price >= t2:
                if row["notify_price_targets"]: msg = build_message(f"🚀 TARGET 2 HIT | {today}", stype, company, ticker, price, t1, t2, sl2, t1_up, t2_up, "T2 achieved. Consider full exit.")
                new_status = "FULL EXIT"
            elif price >= t1 and status == "Monitoring...":
                if row["notify_price_targets"]: msg = build_message(f"✅ TARGET 1 HIT | {today}", stype, company, ticker, price, t1, t2, sl1, t1_up, t2_up, "T1 achieved. Book 50%, ride to T2.")
                new_status = "T1 HIT - Hold 50%"
            elif price <= sl2 and status != "SL1 - Sell 50%":
                if row["notify_stop_loss"]: msg = build_message(f"🔴 CRITICAL STOP LOSS | {today}", stype, company, ticker, price, t1, t2, sl2, t1_up, t2_up, "Critical SL breached. Full exit.")
                new_status = "FULL EXIT"
            elif price <= sl1 and status == "Monitoring...":
                if row["notify_stop_loss"]: msg = build_message(f"⚠️ STOP LOSS HIT | {today}", stype, company, ticker, price, t1, t2, sl1, t1_up, t2_up, "SL1 hit. Sell 50% of holdings.")
                new_status = "SL1 - Sell 50%"
        elif stype == "downtrend":
            if price <= t2:
                if row["notify_price_targets"]: msg = build_message(f"📉 DOWNSIDE T2 HIT | {today}", stype, company, ticker, price, t1, t2, sl2, t1_up, t2_up, "Downside T2 reached. Cover short / exit.")
                new_status = "FULL EXIT"
            elif price <= t1 and status == "Monitoring...":
                if row["notify_price_targets"]: msg = build_message(f"📉 DOWNSIDE T1 HIT | {today}", stype, company, ticker, price, t1, t2, sl1, t1_up, t2_up, "Downside T1 reached. Book 50% of short.")
                new_status = "T1 HIT - Hold 50%"
            elif price >= sl2 and status != "SL1 - Sell 50%":
                if row["notify_stop_loss"]: msg = build_message(f"🔴 CRITICAL STOP LOSS | {today}", stype, company, ticker, price, t1, t2, sl2, t1_up, t2_up, "Stop loss hit on short. Exit immediately.")
                new_status = "FULL EXIT"
            elif price >= sl1 and status == "Monitoring...":
                if row["notify_stop_loss"]: msg = build_message(f"⚠️ STOP LOSS HIT | {today}", stype, company, ticker, price, t1, t2, sl1, t1_up, t2_up, "SL1 hit on short position.")
                new_status = "SL1 - Sell 50%"
        elif stype == "consolidation":
            if price >= t2:
                if row["notify_price_targets"]: msg = build_message(f"🚀 BREAKOUT T2 | {today}", stype, company, ticker, price, t1, t2, sl2, t1_up, t2_up, "Strong breakout confirmed. T2 achieved.")
                new_status = "FULL EXIT"
            elif price >= t1 and status == "Monitoring...":
                if row["notify_price_targets"]: msg = build_message(f"📈 BREAKOUT T1 | {today}", stype, company, ticker, price, t1, t2, sl1, t1_up, t2_up, "Range breakout. Book 50%, target T2.")
                new_status = "T1 HIT - Hold 50%"
            elif price <= sl2 and status != "SL1 - Sell 50%":
                if row["notify_stop_loss"]: msg = build_message(f"🔴 BREAKDOWN SL2 | {today}", stype, company, ticker, price, t1, t2, sl2, t1_up, t2_up, "Range breakdown confirmed. Full exit.")
                new_status = "FULL EXIT"
            elif price <= sl1 and status == "Monitoring...":
                if row["notify_stop_loss"]: msg = build_message(f"⚠️ BREAKDOWN SL1 | {today}", stype, company, ticker, price, t1, t2, sl1, t1_up, t2_up, "Range breaking down. Sell 50%.")
                new_status = "SL1 - Sell 50%"
        if new_status:
            db.update_strategy_status(row["id"], new_status)
        if msg:
            dispatch(msg, row["telegram_chat_id"], row["email"], company, row["user_id"], ticker, stype.upper(), price)

def run_transition_engine(strategies, today):
    stocks_seen = {}
    for row in strategies:
        wid = row["watchlist_id"]
        if wid not in stocks_seen:
            stocks_seen[wid] = row
    for wid, row in stocks_seen.items():
        ticker = row["ticker"]; company = row["company_name"]
        conn = db.get_db()
        w = conn.execute("SELECT ma_type FROM watchlist WHERE id=?", (wid,)).fetchone()
        conn.close()
        ma_type = (w["ma_type"] if w and w["ma_type"] else "SMA")
        price = get_price(ticker)
        if not price:
            continue
        ma10, ma20, ma50, ma200 = get_moving_averages(ticker, ma_type)
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
            for s in all_strats: db.update_strategy_ma_state(s["id"], current_state)
            continue
        if current_state == "consolidation":
            msg = build_ma_message(f"➡️ ENTERING CONSOLIDATION | {today}", f"From {last_state.title()} → Consolidation ({ma_type})", company, ticker, price, ma10, ma20, ma50, "20MA and 50MA converging within 2%. Wait for breakout direction.")
        elif current_state == "uptrend" and last_state == "consolidation":
            msg = build_transition_message(f"📈 BREAKOUT — UPTREND FORMING | {today}", company, ticker, price, ma10, ma20, ma50, last_state, current_state, ma_type)
        elif current_state == "uptrend" and last_state == "downtrend":
            msg = build_transition_message(f"📈 FULL REVERSAL — UPTREND | {today}", company, ticker, price, ma10, ma20, ma50, last_state, current_state, ma_type)
        elif current_state == "downtrend" and last_state == "consolidation":
            msg = build_transition_message(f"📉 BREAKDOWN — DOWNTREND FORMING | {today}", company, ticker, price, ma10, ma20, ma50, last_state, current_state, ma_type)
        elif current_state == "downtrend" and last_state == "uptrend":
            msg = build_transition_message(f"📉 TREND REVERSAL — DOWNTREND | {today}", company, ticker, price, ma10, ma20, ma50, last_state, current_state, ma_type)
        else:
            icons = {"uptrend": "📈", "downtrend": "📉", "consolidation": "➡️"}
            msg = build_transition_message(f"{icons.get(current_state,'🔄')} STRATEGY TRANSITION | {today}", company, ticker, price, ma10, ma20, ma50, last_state, current_state, ma_type)
        dispatch(msg, row["telegram_chat_id"], row["email"], company, row["user_id"], ticker, "TRANSITION", price)
        for s in all_strats: db.update_strategy_ma_state(s["id"], current_state)

def run_deviation_engine(strategies, today):
    for row in strategies:
        if not row.get("user_overridden"):
            continue
        user_t1 = row["t1"]; engine_t1 = row.get("engine_t1")
        user_sl1= row["sl1"]; engine_sl1= row.get("engine_sl1")
        if not all([user_t1, user_sl1, engine_t1, engine_sl1]):
            continue
        t1_dev  = abs(user_t1  - engine_t1)  / engine_t1  * 100
        sl1_dev = abs(user_sl1 - engine_sl1) / engine_sl1 * 100
        if t1_dev > DEVIATION_THRESHOLD or sl1_dev > DEVIATION_THRESHOLD:
            ticker = row["ticker"]; company = row["company_name"]; stype = row["strategy_type"]
            msg  = f"<b>⚠️ CUSTOM LEVELS DEVIATION WARNING | {today}</b>\n\n"
            msg += f"<b>{company}</b>  |  <code>{ticker}</code>\n"
            msg += f"📊 <b>Strategy:</b> {STRATEGY_LABELS.get(stype, stype.title())}\n\n"
            if t1_dev > DEVIATION_THRESHOLD:
                msg += f"🎯 <b>T1 Deviation:</b> {t1_dev:.1f}%  —  Yours: <b>{user_t1:.2f}</b>  Engine: <b>{engine_t1:.2f}</b>\n\n"
            if sl1_dev > DEVIATION_THRESHOLD:
                msg += f"🛡 <b>SL1 Deviation:</b> {sl1_dev:.1f}%  —  Yours: <b>{user_sl1:.2f}</b>  Engine: <b>{engine_sl1:.2f}</b>\n\n"
            msg += f"<i>Your levels remain active. This is a suggestion only.</i>\n\n"
            msg += f"⚠️ <b>Do your own research before acting.</b>"
            dispatch(msg, row["telegram_chat_id"], row["email"], company, row["user_id"], ticker, "DEVIATION_WARNING", get_price(ticker) or 0)

def run_indicator_engine(today):
    conn   = db.get_db()
    stocks = conn.execute("SELECT w.*, u.telegram_chat_id, u.email FROM watchlist w JOIN users u ON w.user_id = u.id").fetchall()
    conn.close()
    for stock in stocks:
        ticker = stock["ticker"]; company = stock["company_name"]
        stock_id = stock["id"]; entry_price = stock["entry_price"]
        ma_type  = stock["ma_type"] if stock["ma_type"] else "SMA"
        price = get_price(ticker)
        if not price:
            continue
        ma10, ma20, ma50, ma200, rsi, adx, di_plus, di_minus, vol_ratio, hist = get_indicators(ticker, ma_type)
        ma_state = detect_ma_state(price, ma10, ma20, ma50, ma200)
        pred     = prediction_score(price, entry_price, rsi, adx, di_plus, di_minus, ma_state)
        engine_notes = f"{pred['signal']} | " + " · ".join(pred["reasons"][:2])
        try:
            from portfolio_service import upsert_engine_analysis
            volatility = "high" if (adx and adx > 35) else ("low" if (adx and adx < 15) else "normal")
            momentum   = "bullish" if (rsi and rsi > 55) else ("bearish" if (rsi and rsi < 45) else "neutral")
            upsert_engine_analysis(stock_id, ma_state, momentum, volatility, rsi, adx,
                                   di_plus, di_minus, vol_ratio, max(0, -pred["score"]), engine_notes)
        except Exception as e:
            print(f"  Analysis cache write failed: {e}")
        conn2 = db.get_db()
        conn2.execute("UPDATE watchlist SET rsi=?, adx=?, di_plus=?, di_minus=?, volume_ratio=?, indicators_updated=CURRENT_TIMESTAMP WHERE id=?",
                      (rsi, adx, di_plus, di_minus, vol_ratio, stock_id))
        conn2.commit(); conn2.close()
        rsi_lbl, rsi_note, rsi_urgency = rsi_signal(rsi)
        if rsi_urgency >= 2 and rsi and (rsi < 30 or rsi > 75):
            icon = "🚨" if rsi < 30 else "⚡"
            msg  = f"<b>{icon} RSI ALERT — {rsi_lbl} | {today}</b>\n\n"
            msg += f"<b>{company}</b>  |  <code>{ticker}</code>\n\n"
            msg += f"💰 <b>CMP:</b> {price:.2f}\n📊 <b>RSI({ma_type}):</b> <b>{rsi}</b> — {rsi_lbl}\n\n"
            msg += f"📋 <b>Interpretation:</b> <i>{rsi_note}</i>\n\n"
            adx_lbl, adx_note, _ = adx_signal(adx, di_plus, di_minus)
            msg += f"📐 <b>ADX:</b> {adx} — {adx_lbl}\n     +DI: {di_plus}  |  -DI: {di_minus}\n\n"
            msg += f"🎯 <b>Prediction: {pred['signal']}</b>\n"
            for r in pred["reasons"]: msg += f"   · {r}\n"
            msg += f"\n⚠️ <b>Do your own research before acting.</b>"
            dispatch(msg, stock["telegram_chat_id"], stock["email"], company, stock["user_id"], ticker, "RSI_ALERT", price)
        if adx is not None and adx >= 30 and di_minus and di_plus and di_minus > di_plus and ma_state != "uptrend":
            adx_lbl, adx_note, _ = adx_signal(adx, di_plus, di_minus)
            msg  = f"<b>📉 STRONG BEARISH TREND CONFIRMED | {today}</b>\n\n"
            msg += f"<b>{company}</b>  |  <code>{ticker}</code>\n\n"
            msg += f"💰 <b>CMP:</b> {price:.2f}\n📐 <b>ADX:</b> {adx} — {adx_lbl}\n     -DI ({di_minus}) > +DI ({di_plus})\n\n"
            msg += f"📊 <b>RSI:</b> {rsi}\n\n📋 <b>Action:</b> <i>{adx_note}</i>\n\n"
            msg += f"⚠️ <b>Do your own research before acting.</b>"
            dispatch(msg, stock["telegram_chat_id"], stock["email"], company, stock["user_id"], ticker, "ADX_BEARISH", price)

def run_alert_engine():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Alert engine running...")
    today     = datetime.now().strftime("%d %b %Y")
    strategies= db.get_all_strategies_for_engine()
    if not strategies:
        print("  No strategies found.")
        return
    run_price_alert_engine(strategies, today)
    run_transition_engine(strategies, today)
    run_deviation_engine(strategies, today)
    run_indicator_engine(today)
    print(f"  Done.")

def build_message(header, strategy, company, ticker, price, t1, t2, sl, t1_up, t2_up, note):
    msg  = f"<b>{header}</b>\n\n<b>{company}</b>  |  <code>{ticker}</code>\n"
    msg += f"📊 <b>Strategy:</b> {STRATEGY_LABELS.get(strategy, strategy.title())}\n\n"
    msg += f"💰 <b>CMP:</b>  {price:.2f}\n\n🎯 <b>TARGETS</b>\n"
    msg += f"     T1:  <b>{t1:.2f}</b>  <i>(+{t1_up}%)</i>\n     T2:  <b>{t2:.2f}</b>  <i>(+{t2_up}%)</i>\n\n"
    msg += f"🛡 <b>STOP LOSS:  {sl:.2f}</b>\n\n📋 <b>SIGNAL</b>\n     <i>{note}</i>\n\n"
    msg += f"⚠️ <b>Do your own research before acting.</b>"
    return msg

def build_ma_message(header, label, company, ticker, price, ma10, ma20, ma50, note):
    msg  = f"<b>{header}</b>\n\n<b>{company}</b>  |  <code>{ticker}</code>\n"
    msg += f"📊 <b>Signal:</b> {label}\n\n💰 <b>CMP:</b>  {price:.2f}\n\n"
    msg += f"📉 <b>MOVING AVERAGES</b>\n     10MA:  <b>{ma10:.2f}</b>\n     20MA:  <b>{ma20:.2f}</b>\n     50MA:  <b>{ma50:.2f}</b>\n\n"
    msg += f"📋 <b>SIGNAL</b>\n     <i>{note}</i>\n\n⚠️ <b>Do your own research before acting.</b>"
    return msg

def build_transition_message(header, company, ticker, price, ma10, ma20, ma50, from_state, to_state, ma_type="SMA"):
    msg  = f"<b>{header}</b>\n\n<b>{company}</b>  |  <code>{ticker}</code>\n\n💰 <b>CMP:</b>  {price:.2f}\n\n"
    msg += f"🔄 <b>TRANSITION ({ma_type})</b>\n     From:  <b>{STRATEGY_LABELS.get(from_state, from_state)}</b>\n"
    msg += f"     To:    <b>{STRATEGY_LABELS.get(to_state, to_state)}</b>\n\n"
    msg += f"📉 <b>MOVING AVERAGES</b>\n     10MA:  <b>{ma10:.2f}</b>\n     20MA:  <b>{ma20:.2f}</b>\n     50MA:  <b>{ma50:.2f}</b>\n\n"
    msg += f"📋 <b>SIGNAL</b>\n     <i>Market conditions shifted. Review your active strategy.</i>\n\n"
    msg += f"⚠️ <b>Do your own research before acting.</b>"
    return msg

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