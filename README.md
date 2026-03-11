# StockHub — Automated Stock Monitoring System

## What This Is
A fully standalone web application that monitors stocks for you and your clients,
fires Telegram + Gmail alerts when targets or stop losses are hit.
Same logic as your Google Sheets system — now with a proper UI and multi-user support.

---

## Local Setup (Test on your machine first)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set environment variables (create a .env file or export directly)
export TELEGRAM_TOKEN="your_bot_token"
export GMAIL_ADDRESS="your_gmail@gmail.com"
export GMAIL_PASSWORD="your_16char_app_password"

# 3. Run
python app.py
```

Open http://localhost:5000

---

## Default Admin Login
- Email: admin@stockhub.com
- Password: admin123
- **Change this immediately after first login**

---

## Deploy to Railway (Free, Always-On)

1. Go to railway.app → sign up with GitHub
2. Click "New Project" → "Deploy from GitHub repo"
3. Push this folder to a GitHub repo first:
   ```bash
   git init
   git add .
   git commit -m "Initial StockHub"
   git push origin main
   ```
4. Connect Railway to your GitHub repo
5. In Railway dashboard → Variables tab, add:
   - TELEGRAM_TOKEN = your bot token
   - GMAIL_ADDRESS = your gmail
   - GMAIL_PASSWORD = your app password
6. Railway auto-deploys. Copy the public URL and share with clients.

---

## Ticker Format
| Exchange | Format | Example |
|---|---|---|
| NASDAQ | NASDAQ:AAPL | Apple |
| NYSE | NYSE:BAC | Bank of America |
| NSE India | NSE:TCS | TCS |
| BSE India | BSE:500325 | Reliance |
| Nifty 50 | INDEXNSE:NIFTY_50 | Index |
| Sensex | INDEXBOM:SENSEX | Index |

---

## How Alert Logic Works
- Hard Refresh locks T1 (+2%), T2 (+5%), SL1 (-2%), SL2 (-5%) at entry price
- Engine checks every 5 minutes automatically
- Alerts fire to that user's Telegram Chat ID and email
- Admin can add stocks for any user
- Users can add their own stocks
