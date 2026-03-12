"""
ai_assistant_service.py
Generates contextual market insights using the Anthropic API.
Falls back to rule-based commentary if API unavailable.
"""
import os, requests, json

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

def get_insight(ticker, company_name, analysis: dict) -> str:
    """
    Given engine analysis dict, return a 1-2 sentence
    friendly insight for the AI avatar bubble.
    """
    if ANTHROPIC_API_KEY:
        return _anthropic_insight(ticker, company_name, analysis)
    return _rule_based_insight(ticker, company_name, analysis)


def _anthropic_insight(ticker, company_name, analysis):
    trend     = analysis.get("trend_state", "unknown")
    rsi       = analysis.get("rsi")
    adx       = analysis.get("adx")
    vol_ratio = analysis.get("volume_ratio")
    exit_score= analysis.get("exit_score", 0)

    prompt = (
        f"You are a calm, friendly market analyst assistant inside a portfolio tracker app. "
        f"Give a 1-2 sentence insight about {company_name} ({ticker}) based on this data: "
        f"Trend={trend}, RSI={rsi}, ADX={adx}, Volume ratio vs avg={vol_ratio}x, "
        f"Exit risk score={exit_score}/7. "
        f"Be concise, use plain English, no jargon. No disclaimers. Just the insight."
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 120,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=10
        )
        data = r.json()
        return data["content"][0]["text"].strip()
    except Exception:
        return _rule_based_insight(ticker, company_name, analysis)


def _rule_based_insight(ticker, company_name, analysis):
    trend      = analysis.get("trend_state", "unknown")
    rsi        = analysis.get("rsi")
    exit_score = analysis.get("exit_score", 0)
    adx        = analysis.get("adx")

    parts = []

    if trend == "uptrend":
        parts.append(f"{ticker} is in an uptrend with MAs aligned bullishly.")
    elif trend == "downtrend":
        parts.append(f"{ticker} is in a downtrend — MAs confirm bearish structure.")
    elif trend == "consolidation":
        parts.append(f"{ticker} is consolidating — price is coiling between MAs.")

    if rsi is not None:
        if rsi < 30:
            parts.append(f"RSI at {rsi} signals extreme oversold territory — high reversal risk.")
        elif rsi < 40:
            parts.append(f"RSI at {rsi} is weak — selling pressure continues.")
        elif rsi > 70:
            parts.append(f"RSI at {rsi} is overbought — watch for pullback.")

    if exit_score >= 5:
        parts.append("Multiple exit signals firing — high risk position.")
    elif exit_score >= 3:
        parts.append("3+ risk signals active — consider reducing exposure.")

    if adx and adx > 30:
        parts.append(f"ADX at {adx} confirms a strong directional move.")

    if not parts:
        return f"No strong signals detected for {ticker} right now. Continue monitoring."

    return " ".join(parts[:2])
