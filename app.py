from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from functools import wraps
import database as db
import engine
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "stockhub_secret_change_in_production")

# ── SCHEDULER ─────────────────────────────────────────
scheduler = BackgroundScheduler()
scheduler.add_job(func=engine.run_alert_engine, trigger="interval", minutes=5, id="alert_engine")
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

# ── AUTH DECORATORS ───────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        user = db.get_user_by_id(session["user_id"])
        if not user or not user["is_admin"]:
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated

# ── AUTH ROUTES ───────────────────────────────────────
@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email    = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        user = db.get_user_by_email(email)
        if user and user["password"] == db.hash_password(password):
            session["user_id"]   = user["id"]
            session["user_name"] = user["name"]
            session["is_admin"]  = user["is_admin"]
            return redirect(url_for("admin_panel") if user["is_admin"] else url_for("dashboard"))
        flash("Invalid email or password", "error")
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name     = request.form.get("name", "").strip()
        email    = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        tg_id    = request.form.get("telegram_chat_id", "").strip()
        ok, msg  = db.create_user(name, email, password, tg_id)
        if ok:
            flash("Account created. Please log in.", "success")
            return redirect(url_for("login"))
        flash(msg, "error")
    return render_template("register.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ── DASHBOARD ─────────────────────────────────────────
@app.route("/dashboard")
@login_required
def dashboard():
    user   = db.get_user_by_id(session["user_id"])
    stocks = db.get_watchlist(session["user_id"])
    logs   = db.get_alert_log(session["user_id"], limit=20)
    enriched = []
    for stock in stocks:
        item = dict(stock)
        item["strategies"] = [dict(s) for s in db.get_strategies(stock["id"])]
        item["ma10"] = None; item["ma20"] = None
        item["ma50"] = None; item["ma200"] = None
        item["ma_state"] = "unknown"; item["pnl_pct"] = None
        enriched.append(item)
    return render_template("dashboard.html", user=user, stocks=enriched, logs=logs)

# ── API: LIVE MA FOR DASHBOARD INSTANT FEEDBACK ───────
@app.route("/api/live_ma")
@login_required
def api_live_ma():
    ticker = request.args.get("ticker", "").strip()
    if not ticker:
        return jsonify({"error": "No ticker"}), 400
    data = engine.get_live_ma_snapshot(ticker)
    return jsonify(data)

@app.route("/api/portfolio")
@login_required
def api_portfolio():
    data = engine.get_portfolio_snapshot(session["user_id"])
    return jsonify(data)

# ── ADD STOCK ─────────────────────────────────────────
@app.route("/add_stock", methods=["POST"])
@login_required
def add_stock():
    ticker       = request.form.get("ticker", "").strip().upper()
    company      = request.form.get("company_name", "").strip()
    entry_price  = request.form.get("entry_price", "").strip()

    if not ticker or not company:
        flash("Ticker and company name are required", "error")
        return redirect(url_for("dashboard"))

    # Parse entry price if provided
    ep = None
    if entry_price:
        try:
            ep = float(entry_price)
        except ValueError:
            flash("Invalid entry price", "error")
            return redirect(url_for("dashboard"))

    ok, msg = db.add_stock(session["user_id"], ticker, company, entry_price=ep)
    if ok:
        conn = db.get_db()
        stock = conn.execute(
            "SELECT id FROM watchlist WHERE user_id=? AND ticker=?",
            (session["user_id"], ticker)
        ).fetchone()
        conn.close()
        if stock:
            engine.hard_refresh_stock(stock["id"], user_entry_price=ep)
        flash(f"{company} added. Targets auto-filled — edit them if needed.", "success")
    else:
        flash(msg, "error")
    return redirect(url_for("dashboard"))

# ── DELETE / REFRESH STOCK ────────────────────────────
@app.route("/delete_stock/<int:stock_id>", methods=["POST"])
@login_required
def delete_stock(stock_id):
    db.delete_stock(stock_id, session["user_id"])
    flash("Stock removed", "success")
    return redirect(url_for("dashboard"))

@app.route("/refresh_stock/<int:stock_id>", methods=["POST"])
@login_required
def refresh_stock(stock_id):
    # Allow user to also pass a new entry price on refresh
    entry_price = request.form.get("entry_price", "").strip()
    ep = None
    if entry_price:
        try:
            ep = float(entry_price)
        except ValueError:
            pass
    ok, result = engine.hard_refresh_stock(stock_id, user_entry_price=ep)
    if ok:
        flash(f"Engine suggestions refreshed from entry: {result['price']}", "success")
    else:
        flash(result, "error")
    return redirect(url_for("dashboard"))

# ── STRATEGY ROUTES ───────────────────────────────────
@app.route("/strategy/activate", methods=["POST"])
@login_required
def activate_strategy():
    watchlist_id  = request.form.get("watchlist_id")
    strategy_type = request.form.get("strategy_type")
    conn = db.get_db()
    stock = conn.execute("SELECT id FROM watchlist WHERE id=? AND user_id=?",
        (watchlist_id, session["user_id"])).fetchone()
    conn.close()
    if not stock:
        flash("Unauthorized", "error")
        return redirect(url_for("dashboard"))
    db.set_strategy_active(int(watchlist_id), strategy_type)
    flash(f"{strategy_type.title()} strategy activated.", "success")
    return redirect(url_for("dashboard"))

@app.route("/strategy/update_levels", methods=["POST"])
@login_required
def update_strategy_levels():
    """User manually saves their own T1/T2/SL1/SL2 for a strategy."""
    strategy_id = request.form.get("strategy_id")
    try:
        t1  = float(request.form.get("t1",  0))
        t2  = float(request.form.get("t2",  0))
        sl1 = float(request.form.get("sl1", 0))
        sl2 = float(request.form.get("sl2", 0))
    except (ValueError, TypeError):
        flash("Invalid level values", "error")
        return redirect(url_for("dashboard"))

    # Verify ownership
    conn = db.get_db()
    row = conn.execute("""
        SELECT w.user_id FROM watchlist_strategies ws
        JOIN watchlist w ON ws.watchlist_id = w.id
        WHERE ws.id=?
    """, (strategy_id,)).fetchone()
    conn.close()
    if not row or row["user_id"] != session["user_id"]:
        flash("Unauthorized", "error")
        return redirect(url_for("dashboard"))

    db.update_strategy_user_levels(int(strategy_id), t1, t2, sl1, sl2)
    flash("Your custom levels saved. Engine will still suggest — you'll be warned if they diverge.", "success")
    return redirect(url_for("dashboard"))

@app.route("/strategy/reset_levels", methods=["POST"])
@login_required
def reset_strategy_levels():
    """Reset user levels back to engine suggestion."""
    strategy_id = request.form.get("strategy_id")
    conn = db.get_db()
    row = conn.execute("""
        SELECT ws.*, w.user_id, w.entry_price FROM watchlist_strategies ws
        JOIN watchlist w ON ws.watchlist_id = w.id
        WHERE ws.id=?
    """, (strategy_id,)).fetchone()
    conn.close()
    if not row or row["user_id"] != session["user_id"]:
        flash("Unauthorized", "error")
        return redirect(url_for("dashboard"))
    # Copy engine suggestion back to user levels, clear override flag
    conn = db.get_db()
    conn.execute("""
        UPDATE watchlist_strategies
        SET t1=engine_t1, t2=engine_t2, sl1=engine_sl1, sl2=engine_sl2,
            user_overridden=0, deviation_warned=0, status='Monitoring...'
        WHERE id=?
    """, (strategy_id,))
    conn.commit()
    conn.close()
    flash("Levels reset to engine suggestion.", "success")
    return redirect(url_for("dashboard"))

@app.route("/strategy/toggles", methods=["POST"])
@login_required
def update_toggles():
    strategy_id = int(request.form.get("strategy_id"))
    toggles = {
        "notify_price_targets":       1 if request.form.get("notify_price_targets")       else 0,
        "notify_stop_loss":           1 if request.form.get("notify_stop_loss")           else 0,
        "notify_ma_crossover":        1 if request.form.get("notify_ma_crossover")        else 0,
        "notify_trend_break":         1 if request.form.get("notify_trend_break")         else 0,
        "notify_consolidation_break": 1 if request.form.get("notify_consolidation_break") else 0,
    }
    db.update_strategy_toggles(strategy_id, toggles)
    flash("Notification preferences saved.", "success")
    return redirect(url_for("dashboard"))

# ── ADMIN ─────────────────────────────────────────────
@app.route("/admin")
@admin_required
def admin_panel():
    users  = db.get_all_users()
    stocks = db.get_all_watchlist()
    logs   = db.get_alert_log(limit=50)
    return render_template("admin.html", users=users, stocks=stocks, logs=logs)

@app.route("/admin/add_stock", methods=["POST"])
@admin_required
def admin_add_stock():
    user_id     = request.form.get("user_id")
    ticker      = request.form.get("ticker", "").strip().upper()
    company     = request.form.get("company_name", "").strip()
    entry_price = request.form.get("entry_price", "").strip()
    if not user_id or not ticker or not company:
        flash("All fields required", "error")
        return redirect(url_for("admin_panel"))
    ep = None
    if entry_price:
        try:
            ep = float(entry_price)
        except ValueError:
            pass
    ok, msg = db.add_stock(int(user_id), ticker, company, entry_price=ep, added_by_admin=1)
    if ok:
        conn = db.get_db()
        stock = conn.execute("SELECT id FROM watchlist WHERE user_id=? AND ticker=?", (int(user_id), ticker)).fetchone()
        conn.close()
        if stock:
            engine.hard_refresh_stock(stock["id"], user_entry_price=ep)
        flash(f"{company} added for user.", "success")
    else:
        flash(msg, "error")
    return redirect(url_for("admin_panel"))

@app.route("/admin/delete_stock/<int:stock_id>", methods=["POST"])
@admin_required
def admin_delete_stock(stock_id):
    db.delete_stock(stock_id)
    flash("Stock removed", "success")
    return redirect(url_for("admin_panel"))

@app.route("/admin/refresh_all/<int:user_id>", methods=["POST"])
@admin_required
def admin_refresh_all(user_id):
    results = engine.hard_refresh_user(user_id)
    refreshed = sum(1 for r in results if r.get("status") == "refreshed")
    flash(f"Refreshed {refreshed}/{len(results)} stocks", "success")
    return redirect(url_for("admin_panel"))

@app.route("/admin/create_user", methods=["POST"])
@admin_required
def admin_create_user():
    name     = request.form.get("name", "").strip()
    email    = request.form.get("email", "").strip()
    password = request.form.get("password", "").strip()
    tg_id    = request.form.get("telegram_chat_id", "").strip()
    ok, msg  = db.create_user(name, email, password, tg_id)
    flash(msg if not ok else f"User {name} created", "success" if ok else "error")
    return redirect(url_for("admin_panel"))

@app.route("/admin/run_engine", methods=["POST"])
@admin_required
def admin_run_engine():
    engine.run_alert_engine()
    flash("Alert engine ran manually", "success")
    return redirect(url_for("admin_panel"))

if __name__ == "__main__":
    db.init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
