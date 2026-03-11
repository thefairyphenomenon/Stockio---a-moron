import yfinance as yf
import requests
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
import database as db
import os

# ── CONFIG (set via environment variables on Railway) ──
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
GMAIL_ADDRESS    = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_PASSWORD   = os.environ.get("GMAIL_PASSWORD", "")  # App password

SKIP_TICKERS = []  # Add index tickers here if needed

# ── FETCH LIVE PRICE ──────────────────────────────────
def get_price(ticker):
    """Convert sheet-style ticker to yfinance format and fetch price."""
    yf_ticker = convert_ticker(ticker)
    try:
        t = yf.Ticker(yf_ticker)
        data = t.fast_info
        price = data.last_price
        if price and price > 0:
            return round(price, 2)
        # fallback
        hist = t.history(period="1d")
        if not hist.empty:
            return round(hist["Close"].iloc[-1], 2)
    except Exception as e:
        print(f"Price fetch failed for {ticker}: {e}")
    return None

def get_moving_averages(ticker):
    """Fetch 10, 20, 50 day moving averages."""
    yf_ticker = convert_ticker(ticker)
    try:
        t = yf.Ticker(yf_ticker)
        hist = t.history(period="3mo")
        if hist.empty or len(hist) < 10:
            return None, None, None
        closes = hist["Close"]
        ma10 = round(closes.tail(10).mean(), 2) if len(closes) >= 10 else None
        ma20 = round(closes.tail(20).mean(), 2) if len(closes) >= 20 else None
        ma50 = round(closes.tail(50).mean(), 2) if len(closes) >= 50 else None
        return ma10, ma20, ma50
    except Exception as e:
        print(f"MA fetch failed for {ticker}: {e}")
        return None, None, None

def convert_ticker(ticker):
    """Convert NASDAQ:AAPL style to AAPL, NSE:TCS to TCS.NS etc."""
    ticker = ticker.strip().upper()
    mappings = {
        "NASDAQ:": "",
        "NYSE:": "",
        "NYSEARCA:": "",
        "OTCMKTS:": "",
        "NSE:": ".NS",
        "BSE:": ".BO",
        "INDEXNSE:NIFTY_50": "^NSEI",
        "INDEXBOM:SENSEX": "^BSESN",
    }
    # Handle index tickers first
    if ticker in mappings:
        return mappings[ticker]
    for prefix, suffix in mappings.items():
        if ticker.startswith(prefix):
            symbol = ticker[len(prefix):]
            return symbol + suffix
    return ticker

# ── HARD REFRESH: lock in T1, T2, SL1, SL2 ───────────
def hard_refresh_user(user_id):
    """Recalculate and lock T1/T2/SL for all stocks of a user."""
    stocks = db.get_watchlist(user_id)
    results = []
    for stock in stocks:
        price = get_price(stock["ticker"])
        if not price:
            results.append({"ticker": stock["ticker"], "status": "failed", "reason": "Price unavailable"})
            continue
        t1  = round(price * 1.02, 2)
        t2  = round(price * 1.05, 2)
        sl1 = round(price * 0.98, 2)
        sl2 = round(price * 0.95, 2)
        db.update_stock_levels(stock["id"], price, t1, t2, sl1, sl2)
        results.append({"ticker": stock["ticker"], "price": price, "t1": t1, "t2": t2, "sl1": sl1, "sl2": sl2, "status": "refreshed"})
    return results

def hard_refresh_stock(stock_id):
    """Refresh a single stock by ID."""
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
    return True, {"price": price, "t1": t1, "t2": t2, "sl1": sl1, "sl2": sl2}

# ── MAIN ALERT ENGINE ─────────────────────────────────
def run_alert_engine():
    """Runs every 5 minutes via scheduler. Checks all active watchlists."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Running alert engine...")
    all_stocks = db.get_all_watchlist()
    today = datetime.now().strftime("%d %b %Y")

    for stock in all_stocks:
        ticker  = stock["ticker"]
        company = stock["company_name"]
        user_id = stock["user_id"]
        status  = stock["status"]
        t1      = stock["t1"]
        t2      = stock["t2"]
        sl1     = stock["sl1"]
        sl2     = stock["sl2"]

        if not t1 or status in ["FULL EXIT", "Pending Refresh"]:
            continue

        price = get_price(ticker)
        if not price:
            continue

        ma10, ma20, ma50 = get_moving_averages(ticker)
        t1_upside = round(((t1 - price) / price) * 100, 1)
        t2_upside = round(((t2 - price) / price) * 100, 1)

        alert_type = None
        new_status = None
        header = ""
        note = ""
        sl_for_msg = sl1

        # ── Price logic ──
        if price >= t2 and status != "FULL EXIT":
            alert_type = "TARGET_2"
            new_status  = "FULL EXIT"
            header      = f"🚀 TARGET 2 HIT | {today}"
            note        = "T2 achieved. Consider full exit."
            sl_for_msg  = sl2

        elif price >= t1 and status == "Monitoring...":
            alert_type = "TARGET_1"
            new_status  = "T1 HIT - Hold 50%"
            header      = f"✅ TARGET 1 HIT | {today}"
            note        = "T1 achieved. Book 50%, ride to T2."
            sl_for_msg  = sl1

        elif price <= sl2:
            alert_type = "CRITICAL_SL"
            new_status  = "FULL EXIT"
            header      = f"🔴 CRITICAL STOP LOSS | {today}"
            note        = "Critical SL breached. Full exit to protect capital."
            sl_for_msg  = sl2

        elif price <= sl1 and status == "Monitoring...":
            alert_type = "SL1"
            new_status  = "SL1 - Sell 50%"
            header      = f"⚠️ STOP LOSS HIT | {today}"
            note        = "SL1 hit. Sell 50% of holdings."
            sl_for_msg  = sl1

        # ── MA logic ──
        if ma10 and ma20 and ma50 and not alert_type:
            if ma10 < ma20 and ma20 < ma50:
                alert_type = "MA_FULL_EXIT"
                new_status  = "MA SELL 100%"
                header      = f"🔵 FULL EXIT SIGNAL | {today}"
                note        = "10MA and 20MA both below 50MA. Trend reversal confirmed."
                sl_for_msg  = sl2
            elif ma10 < ma20:
                alert_type = "MA_INFLECTION"
                new_status  = "MA SELL 50%"
                header      = f"🟡 INFLECTION POINT | {today}"
                note        = "10MA crossed below 20MA. Sell 50% of holdings."
                sl_for_msg  = sl1

        if alert_type and new_status:
            msg = build_message(header, company, ticker, price, t1, t2, sl_for_msg, t1_upside, t2_upside, note)
            user = db.get_user_by_id(user_id)
            if user:
                send_telegram(msg, user["telegram_chat_id"])
                send_email(user["email"], company, msg)
                db.update_stock_status(stock["id"], new_status)
                db.log_alert(user_id, ticker, alert_type, price, msg)
                print(f"  Alert sent: {ticker} → {alert_type} → {user['email']}")

# ── MESSAGE BUILDER ───────────────────────────────────
def build_message(header, company, ticker, price, t1, t2, sl, t1_up, t2_up, note):
    msg  = f"<b>{header}</b>\n\n"
    msg += f"<b>{company}</b>  |  <code>{ticker}</code>\n\n"
    msg += f"💰 <b>CMP:</b>  {price:.2f}\n\n"
    msg += f"🎯 <b>TARGETS</b>\n"
    msg += f"     T1:  <b>{t1:.2f}</b>  <i>(+{t1_up}%)</i>\n"
    msg += f"     T2:  <b>{t2:.2f}</b>  <i>(+{t2_up}%)</i>\n\n"
    msg += f"🛡 <b>STOP LOSS:  {sl:.2f}</b>\n\n"
    msg += f"📋 <b>SIGNAL</b>\n"
    msg += f"     <i>{note}</i>\n\n"
    msg += f"⚠️ <b>Do your own research before acting.</b>"
    return msg

# ── TELEGRAM ──────────────────────────────────────────
def send_telegram(msg, chat_id):
    if not TELEGRAM_TOKEN or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": msg, "parse_mode": "HTML"}
        r = requests.post(url, json=payload, timeout=10)
        print(f"  Telegram: {r.status_code}")
    except Exception as e:
        print(f"  Telegram failed: {e}")

# ── EMAIL ─────────────────────────────────────────────
def send_email(to_email, company, msg):
    if not GMAIL_ADDRESS or not GMAIL_PASSWORD:
        return
    try:
        # Strip HTML tags for email plain text
        import re
        plain = re.sub(r"<[^>]+>", "", msg)
        message = MIMEText(plain)
        message["Subject"] = f"Stock Alert: {company}"
        message["From"]    = GMAIL_ADDRESS
        message["To"]      = to_email
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, to_email, message.as_string())
        print(f"  Email sent to {to_email}")
    except Exception as e:
        print(f"  Email failed: {e}")

# ── LIVE PRICE SNAPSHOT FOR DASHBOARD ─────────────────
def get_portfolio_snapshot(user_id):
    """Returns enriched stock data with live prices for dashboard display."""
    stocks = db.get_watchlist(user_id)
    result = []
    for stock in stocks:
        price = get_price(stock["ticker"])
        ma10, ma20, ma50 = get_moving_averages(stock["ticker"])
        item = dict(stock)
        item["live_price"] = price
        item["ma10"] = ma10
        item["ma20"] = ma20
        item["ma50"] = ma50
        if price and stock["t1"]:
            item["pnl_pct"] = round(((price - stock["entry_price"]) / stock["entry_price"]) * 100, 2) if stock["entry_price"] else None
            item["distance_to_t1"] = round(((stock["t1"] - price) / price) * 100, 2)
            item["distance_to_sl"] = round(((price - stock["sl1"]) / price) * 100, 2)
        else:
            item["pnl_pct"] = None
            item["distance_to_t1"] = None
            item["distance_to_sl"] = None
        result.append(item)
    return result
