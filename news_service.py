"""
news_service.py
Fetches financial news for the global news globe.
Uses free RSS feeds — no API key required.
Falls back gracefully if network is unavailable.
"""
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import json, re

RSS_FEEDS = {
    "global":    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US",
    "us":        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SPY&region=US&lang=en-US",
    "india":     "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^BSESN&region=IN&lang=en-IN",
    "europe":    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^STOXX50E&region=DE&lang=en-DE",
    "asia":      "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^N225&region=JP&lang=en-US",
    "crypto":    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=BTC-USD&region=US&lang=en-US",
}

# Region metadata for globe positioning
REGION_META = {
    "global":  {"lat": 20,   "lng": 0,    "label": "Global Markets",   "color": "#00d4ff"},
    "us":      {"lat": 39,   "lng": -98,  "label": "United States",    "color": "#00ff88"},
    "india":   {"lat": 20,   "lng": 78,   "label": "India / South Asia","color": "#ffcc00"},
    "europe":  {"lat": 51,   "lng": 10,   "label": "Europe",           "color": "#b060ff"},
    "asia":    {"lat": 35,   "lng": 139,  "label": "East Asia",        "color": "#ff6b35"},
    "crypto":  {"lat": -10,  "lng": -60,  "label": "Crypto",           "color": "#ff3b5c"},
}

_cache = {}
_cache_ts = {}
CACHE_TTL = 600  # 10 min


def _fetch_rss(url, timeout=8):
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "StockHub/1.0"})
        if r.status_code != 200:
            return []
        root  = ET.fromstring(r.content)
        items = []
        for item in root.findall(".//item")[:8]:
            title = item.findtext("title", "")
            link  = item.findtext("link",  "")
            desc  = item.findtext("description", "")
            pub   = item.findtext("pubDate", "")
            # Strip HTML tags from description
            desc  = re.sub(r"<[^>]+>", "", desc)[:200]
            items.append({"title": title, "link": link, "description": desc, "published": pub})
        return items
    except Exception:
        return []


def get_news(region="global"):
    now = datetime.now()
    if region in _cache and (now - _cache_ts.get(region, datetime.min)).seconds < CACHE_TTL:
        return _cache[region]

    url   = RSS_FEEDS.get(region, RSS_FEEDS["global"])
    items = _fetch_rss(url)

    if not items:
        items = [{"title": "News unavailable — check network", "link": "", "description": "", "published": ""}]

    result = {"region": region, "meta": REGION_META.get(region, {}), "articles": items}
    _cache[region]    = result
    _cache_ts[region] = now
    return result


def get_all_regions():
    """Returns metadata for all regions — used to render globe pins."""
    return [{"region": k, **v} for k, v in REGION_META.items()]


def get_globe_data():
    """Single call returns region pins + latest headline per region."""
    regions = []
    for region, meta in REGION_META.items():
        articles = _fetch_rss(RSS_FEEDS[region]) if region not in _cache else _cache.get(region, {}).get("articles", [])
        headline = articles[0]["title"] if articles else "No data"
        regions.append({**meta, "region": region, "headline": headline})
    return regions
