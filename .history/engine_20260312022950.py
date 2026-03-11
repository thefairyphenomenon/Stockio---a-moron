import yfinance as yf
import requests
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
import database as db
import os

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
GMAIL_ADDRESS  = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_PASSWORD = os.environ.get("GMAIL_PASSWORD", "")

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

# ── MOVING AVERAGES ───────────────────────────────────
def get_moving_averages(ticker):
    try:
        t = yf.Ticker(convert_ticker(ticker))
        hist = t.history(period="1y")
        if hist.empty or len(hist) < 10:
            return None, None, None, None
        closes = hist["Close"]
        ma10  = round(closes.tail(10).mean(),  2) if len(closes) >= 10  else None
        ma20  = round(closes.tail(20).mean(),  2) if len(closes) >= 20  else None
        ma50  = round(closes.tail(50).mean(),  2) if len(closes) >= 50  else None
        ma200 = round(closes.tail(200).mean(), 2) if len(closes) >= 200 else None
        return ma10, ma20, ma50, ma200
    except Exception as e:
        print(f"  MA fetch failed {ticker}: {e}")
        return None, None, None, None

# ── DETECT MA STATE ───────────────────────────────────
def detect_ma_state(price, ma10, ma20, ma50, ma200):
    if not all([price, ma10, ma20, ma50]):
        return "unknown"
    if ma20 and ma50:
        spread = abs(ma20 - ma50) / ma50 * 100
        if spread <= 2.0:
            return "consolidation"
    if price > ma20 and ma20 > ma50:
        return "uptrend"
    if price < ma20 and ma20 < ma50:
        return "downtrend"
    return "uptrend" if price > ma50 else "downtrend"

# ── HARD REFRESH ──────────────────────────────────────
def hard_refresh_stock(stock_id):
    conn = db.get_db()
    stock = conn.execute("SELECT * FROM watchlist WHERE id=?", (stock_id,)).fetchone()
    conn.close()
    if not stock:
        return False, "Stock not found"
    price = get_price(stock["ticker"])
    if not price:
        return False, "Could not fetch price"
    t1  = round(price * 1.02, 2)
    t2  = round(price * 1.05, 2)
    sl1 = round(price * 0.98, 2)
    sl2 = round(price * 0.95, 2)
    db.update_stock_levels(stock_id, price, t1, t2, sl1, sl2)
    existing = db.get_strategies(stock_id)
    if existing:
        db.refresh_strategy_levels(stock_id, price)
    else:
        db.create_strategies_for_stock(stock_id, price)
    return True, {"price": price, "t1": t1, "t2": t2, "sl1": sl1, "sl2": sl2}

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
        ma10, ma20, ma50, ma200 = get_moving_averages(stock["ticker"])
        ma_state   = detect_ma_state(price, ma10, ma20, ma50, ma200)
        strategies = db.get_strategies(stock["id"])
        item = dict(stock)
        item["live_price"]  = price
        item["ma10"]        = ma10
        item["ma20"]        = ma20
        item["ma50"]        = ma50
        item["ma200"]       = ma200
        item["ma_state"]    = ma_state
        item["strategies"]  = [dict(s) for s in strategies]
        item["pnl_pct"]     = round(((price - stock["entry_price"]) / stock["entry_price"]) * 100, 2) if price and stock["entry_price"] else None
        result.append(item)
    return result

# ═══════════════════════════════════════════════════════
# ENGINE 1 — ACTIVE STRATEGY PRICE ALERTS
# ═══════════════════════════════════════════════════════
def run_price_alert_engine(strategies, today):
    for row in strategies:
        if not row["is_active"]:
            continue
        ticker   = row["ticker"]
        company  = row["company_name"]
        stype    = row["strategy_type"]
        status   = row["status"]
        t1, t2   = row["t1"], row["t2"]
        sl1, sl2 = row["sl1"], row["sl2"]
        price    = get_price(ticker)
        if not price or not t1:
            continue

        t1_up = round(((t1 - price) / price) * 100, 1)
        t2_up = round(((t2 - price) / price) * 100, 1)
        msg = ""
        new_status = None

        if stype == "uptrend":
            if price >= t2 and status != "FULL EXIT":
                if row["notify_price_targets"]:
                    msg = build_message(f"🚀 TARGET 2 HIT | {today}", stype, company, ticker, price, t1, t2, sl2, t1_up, t2_up, "T2 achieved. Consider full exit.")
                new_status = "FULL EXIT"
            elif price >= t1 and status == "Monitoring...":
                if row["notify_price_targets"]:
                    msg = build_message(f"✅ TARGET 1 HIT | {today}", stype, company, ticker, price, t1, t2, sl1, t1_up, t2_up, "T1 achieved. Book 50%, ride to T2.")
                new_status = "T1 HIT - Hold 50%"
            elif price <= sl2:
                if row["notify_stop_loss"]:
                    msg = build_message(f"🔴 CRITICAL STOP LOSS | {today}", stype, company, ticker, price, t1, t2, sl2, t1_up, t2_up, "Critical SL breached. Full exit.")
                new_status = "FULL EXIT"
            elif price <= sl1 and status == "Monitoring...":
                if row["notify_stop_loss"]:
                    msg = build_message(f"⚠️ STOP LOSS HIT | {today}", stype, company, ticker, price, t1, t2, sl1, t1_up, t2_up, "SL1 hit. Sell 50% of holdings.")
                new_status = "SL1 - Sell 50%"

        elif stype == "downtrend":
            if price <= t2 and status != "FULL EXIT":
                if row["notify_price_targets"]:
                    msg = build_message(f"📉 DOWNSIDE T2 HIT | {today}", stype, company, ticker, price, t1, t2, sl2, t1_up, t2_up, "Downside T2 reached. Cover short / exit.")
                new_status = "FULL EXIT"
            elif price <= t1 and status == "Monitoring...":
                if row["notify_price_targets"]:
                    msg = build_message(f"📉 DOWNSIDE T1 HIT | {today}", stype, company, ticker, price, t1, t2, sl1, t1_up, t2_up, "Downside T1 reached. Book 50% of short.")
                new_status = "T1 HIT - Hold 50%"
            elif price >= sl2:
                if row["notify_stop_loss"]:
                    msg = build_message(f"🔴 CRITICAL STOP LOSS | {today}", stype, company, ticker, price, t1, t2, sl2, t1_up, t2_up, "Stop loss hit on short. Exit immediately.")
                new_status = "FULL EXIT"
            elif price >= sl1 and status == "Monitoring...":
                if row["notify_stop_loss"]:
                    msg = build_message(f"⚠️ STOP LOSS HIT | {today}", stype, company, ticker, price, t1, t2, sl1, t1_up, t2_up, "SL1 hit on short position.")
                new_status = "SL1 - Sell 50%"

        elif stype == "consolidation":
            if price >= t2 and status != "FULL EXIT":
                if row["notify_price_targets"]:
                    msg = build_message(f"🚀 BREAKOUT T2 | {today}", stype, company, ticker, price, t1, t2, sl2, t1_up, t2_up, "Strong breakout confirmed. T2 achieved.")
                new_status = "FULL EXIT"
            elif price >= t1 and status == "Monitoring...":
                if row["notify_price_targets"]:
                    msg = build_message(f"📈 BREAKOUT T1 | {today}", stype, company, ticker, price, t1, t2, sl1, t1_up, t2_up, "Range breakout. Book 50%, target T2.")
                new_status = "T1 HIT - Hold 50%"
            elif price <= sl2:
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
# ENGINE 2 — MA CROSSOVER + TRANSITION DETECTION
# ═══════════════════════════════════════════════════════
def run_transition_engine(strategies, today):
    # One fetch per stock, not per strategy row
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

        # Inconclusive MA data — skip entirely
        if current_state == "unknown":
            continue

        # Get last stored state
        all_strats = db.get_strategies(wid)
        strat_map  = {s["strategy_type"]: s for s in all_strats}
        ref        = strat_map.get("uptrend") or list(strat_map.values())[0]
        last_state = ref["last_ma_state"] or ""

        # ── THE CORE RULE ──────────────────────────────
        # If state hasn't changed → do nothing, fire nothing
        if current_state == last_state:
            continue
        # ───────────────────────────────────────────────

        # State has changed — determine what kind of change and fire ONE alert
        icons = {"uptrend": "📈", "downtrend": "📉", "consolidation": "➡️"}

        if last_state == "":
            # First time we're seeing this stock — just record state, no alert
            for s in all_strats:
                db.update_strategy_ma_state(s["id"], current_state)
            continue

        if current_state == "consolidation":
            # Something moved INTO consolidation — MAs compressing
            note = "20MA and 50MA converging within 2%. Market entering consolidation phase."
            msg  = build_ma_message(
                f"➡️ ENTERING CONSOLIDATION | {today}",
                f"From {last_state.title()} → Consolidation",
                company, ticker, price, ma10, ma20, ma50, note
            )

        elif current_state == "uptrend" and last_state == "consolidation":
            note = "Price and MAs breaking upward from consolidation. Uptrend forming."
            msg  = build_transition_message(
                f"📈 BREAKOUT — UPTREND FORMING | {today}",
                company, ticker, price, ma10, ma20, ma50, last_state, current_state
            )

        elif current_state == "uptrend" and last_state == "downtrend":
            note = "Full reversal. Price and MAs confirming shift from downtrend to uptrend."
            msg  = build_transition_message(
                f"📈 FULL REVERSAL — UPTREND | {today}",
                company, ticker, price, ma10, ma20, ma50, last_state, current_state
            )

        elif current_state == "downtrend" and last_state == "consolidation":
            note = "Price and MAs breaking downward from consolidation. Downtrend forming."
            msg  = build_transition_message(
                f"📉 BREAKDOWN — DOWNTREND FORMING | {today}",
                company, ticker, price, ma10, ma20, ma50, last_state, current_state
            )

        elif current_state == "downtrend" and last_state == "uptrend":
            note = "Trend reversal confirmed. Uptrend has broken down."
            msg  = build_transition_message(
                f"📉 TREND REVERSAL — DOWNTREND | {today}",
                company, ticker, price, ma10, ma20, ma50, last_state, current_state
            )

        else:
            # Catch-all for any other state change
            msg = build_transition_message(
                f"{icons.get(current_state,'🔄')} STRATEGY TRANSITION | {today}",
                company, ticker, price, ma10, ma20, ma50, last_state, current_state
            )

        # Dispatch ONE alert, then update stored state
        dispatch(msg, row["telegram_chat_id"], row["email"], company,
                 row["user_id"], ticker, "TRANSITION", price)

        for s in all_strats:
            db.update_strategy_ma_state(s["id"], current_state)

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
    print(f"  Done.")

# ── MESSAGE BUILDERS ──────────────────────────────────
STRATEGY_LABELS = {"uptrend": "📈 Uptrend", "downtrend": "📉 Downtrend", "consolidation": "➡️ Consolidation"}

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

def build_ma_message(header, label, company, ticker, price, ma10, ma20, ma50, note):
    msg  = f"<b>{header}</b>\n\n"
    msg += f"<b>{company}</b>  |  <code>{ticker}</code>\n"
    msg += f"📊 <b>Signal:</b> {label}\n\n"
    msg += f"💰 <b>CMP:</b>  {price:.2f}\n\n"
    msg += f"📉 <b>MOVING AVERAGES</b>\n"
    msg += f"     10MA:  <b>{ma10:.2f}</b>\n"
    msg += f"     20MA:  <b>{ma20:.2f}</b>\n"
    msg += f"     50MA:  <b>{ma50:.2f}</b>\n\n"
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
