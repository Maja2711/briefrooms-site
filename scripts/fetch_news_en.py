#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import html
from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser
from dateutil import tz
import requests

# =========================
# TIMEZONE (UTC dla wersji EN jest bezpieczniejsze)
# =========================
TZ = timezone.utc

# =========================
# CONFIG
# =========================
MAX_PER_SECTION = 5
MAX_PER_HOST = 5
HOTBAR_LIMIT = 15 # Trochę więcej newsów w pasku

AI_ENABLED = bool(os.getenv("OPENAI_API_KEY"))
AI_MODEL = os.getenv("NEWS_AI_MODEL", "gpt-4o-mini")

# Ścieżki plików
AI_CACHE_PATH = ".cache/ai_cache_en.json"
HOTBAR_JSON_PATH = ".cache/news_summaries_en.json" # To czyta hotbar.js
HTML_OUTPUT_PATH = "en/news.html" # Generowana strona zbiorcza

# Feeds i inne konfiguracje pozostają bez zmian...
FEEDS = {
    "world": [
        "http://feeds.bbci.co.uk/news/world/rss.xml",
        "https://feeds.reuters.com/reuters/worldNews",
        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
        "https://feeds.washingtonpost.com/rss/world",
        "https://www.aljazeera.com/xml/rss/all.xml",
    ],
    "business": [
        "http://feeds.bbci.co.uk/news/business/rss.xml",
        "https://feeds.reuters.com/reuters/businessNews",
        "https://www.cnbc.com/id/100003/device/rss/rss.html",
    ],
    "science": [
        "http://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
        "https://rss.nytimes.com/services/xml/rss/nyt/Science.xml",
        "https://www.nasa.gov/rss/dyn/breaking_news.rss",
        "https://www.sciencedaily.com/rss/top/science.xml",
    ],
    "health": [
        "http://feeds.bbci.co.uk/news/health/rss.xml",
        "https://rss.nytimes.com/services/xml/rss/nyt/Health.xml",
        "https://www.who.int/feeds/entity/mediacentre/news/en/rss.xml",
        "https://www.nih.gov/news-events/feed.xml",
    ]
}

BOOST = {
    "world": [
        (re.compile(r"US|USA|UK|China|Ukraine|Russia|G7|NATO|UN|White House|Downing St", re.I), 25),
        (re.compile(r"breaking|live", re.I), 30),
    ],
    "business": [
        (re.compile(r"market|stocks|inflation|fed|rate|ECB|crypto|bitcoin|AI|tech|Nvidia|Apple|Tesla", re.I), 25),
    ],
    "science": [
        (re.compile(r"space|nasa|moon|mars|climate|discovery|study|research|cancer|brain", re.I), 25),
    ],
}

BAN_PATTERNS = [
    re.compile(r"sport|football|cricket|rugby|quiz|puzzle|crossword|review|horoscope|opinion", re.I),
]

# =========================
# TOKENIZATION / HELPER
# =========================
STOP_EN = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by", "from",
    "up", "down", "is", "are", "was", "were", "be", "been", "being", "it", "its", "he", "his", "she", "her",
    "daily", "briefing", "live", "updates"
}
TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def tokens_en(text: str):
    toks = [t.lower() for t in TOKEN_RE.findall(text or "")]
    toks = [t for t in toks if t not in STOP_EN and len(t) > 2 and not t.isdigit()]
    return set(toks)

def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0

SIMILARITY_THRESHOLD = 0.70

# =========================
# HELPERY
# =========================
def norm_title(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"&\w+;|&#\d+;", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def host_of(url: str) -> str:
    try:
        return urlparse(url).netloc
    except Exception:
        return ""

def today_str() -> str:
    now = datetime.now(TZ)
    return now.strftime("%Y-%m-%d")

def esc(s: str) -> str:
    return html.escape(s or "", quote=True)

def ensure_period(txt: str) -> str:
    txt = (txt or "").strip()
    if not txt:
        return txt
    if txt[-1] not in ".!?…":
        txt += "."
    return txt

def ensure_full_sentence(text: str, max_chars: int = 320) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    if len(t) > max_chars:
        t = t[:max_chars+1]
    end = max(t.rfind("."), t.rfind("!"), t.rfind("?"))
    if end != -1:
        t = t[:end+1]
    return t.strip()

def score_item(item, section_key: str) -> float:
    score = 0.0
    published_parsed = item.get("published_parsed") or item.get("updated_parsed")
    if published_parsed:
        dt = datetime(*published_parsed[:6], tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
        score += max(0.0, 36.0 - age_h)
    t = item.get("title", "") or ""
    for rx, pts in BOOST.get(section_key, []):
        if rx.search(t):
            score += pts
    h = host_of(item.get("link", "") or "")
    # Note: Source priority logic for EN is omitted here for simplicity and focus on fixing the link issue.
    if len(t) > 140:
        score -= 5
    return score

# =========================
# CACHE KOMENTARZY AI
# =========================
# ... (Funkcje load_cache, save_cache, ai_summarize_en są pominięte w tym fragmencie 
# dla zwięzłości, zakładając, że działają poprawnie, lub używany jest fallback) ...

# Fallback dla ai_summarize_en
def ai_summarize_en(title: str, snippet: str, url: str) -> dict:
    # Uproszczona wersja fallback (bez faktycznego AI)
    return {
        "summary": ensure_full_sentence((snippet or title or "")[:320], 320),
        "uncertain": ""
    }

# =========================
# WARSTWA WERYFIKACJI (EN)
# ... (Pominięte dla zwięzłości) ...
def verify_note_en(title: str, snippet: str) -> str:
    # Zwraca puste ostrzeżenie w trybie fallback
    return ""


# =========================
# POBIERANIE + DEDUPE
# =========================
def fetch_section(section_key: str):
    items = []
    FALLBACK_URL = "/en/news.html" # <-- Nowy link zastępczy

    for feed_url in FEEDS[section_key]:
        try:
            parsed = feedparser.parse(feed_url)
            for e in parsed.entries:
                title = e.get("title", "") or ""
                link  = e.get("link", "") or FALLBACK_URL # <-- Jeśli link jest pusty, użyj fallback
                if not title or not link:
                    continue
                if any(rx.search(title) for rx in BAN_PATTERNS):
                    continue
                snippet = e.get("summary", "") or e.get("description", "") or ""
                items.append({
                    "title": title.strip(),
                    "link":  link.strip(),
                    "summary_raw": re.sub("<[^<]+?>", "", snippet).strip(),
                    "published_parsed": e.get("published_parsed") or e.get("updated_parsed"),
                })
        except Exception as ex:
            print(f"[WARN] RSS error: {feed_url} -> {ex}", file=sys.stderr)

    # Scoring, tokenizacja, deduplikacja i limitowanie (pozostawione bez zmian)
    for it in items:
        it["_score"] = score_item(it, section_key)
        it["_tok"] = tokens_en(it["title"])

    items.sort(key=lambda x: x["_score"], reverse=True)

    kept = []
    for it in items:
        if not any(jaccard(it["_tok"], got["_tok"]) >= SIMILARITY_THRESHOLD for got in kept):
            kept.append(it)

    per_host = {}
    pool = []
    for it in kept:
        h = host_of(it["link"])
        per_host[h] = per_host.get(h, 0)
        if per_host[h] >= MAX_PER_HOST:
            continue
        per_host[h] += 1
        pool.append(it)

    picked = pool[:MAX_PER_SECTION] # Uproszczone limitowanie

    # AI + weryfikacja
    for it in picked:
        s = ai_summarize_en(it["title"], it.get("summary_raw", ""), it["link"])
        verify = verify_note_en(it["title"], it.get("summary_raw",""))
        final_warn = verify or s.get("uncertain","")
        it["ai_summary"] = ensure_period(s["summary"])
        it["ai_uncertain"] = ensure_period(final_warn) if final_warn else ""
        it["ai_model"] = s.get("model","")

    return picked

# =========================
# HOTBAR JSON (dla paska)
# =========================
def build_hotbar_json(sections: dict) -> dict:
    all_items = []
    for sec_key, items in sections.items():
        for it in items:
            all_items.append(it)

    all_items.sort(key=lambda it: it.get("_score", 0.0), reverse=True)

    out = {}
    taken = 0
    for it in all_items:
        if taken >= HOTBAR_LIMIT:
            break
        title = (it.get("title") or "").strip()
        link = (it.get("link") or "").strip()
        if not title or not link:
            continue

        pp = it.get("published_parsed")
        if pp:
            dt = datetime(*pp[:6], tzinfo=timezone.utc).astimezone(TZ)
            dstr = dt.strftime("%Y-%m-%d")
        else:
            dstr = today_str()

        key = f"v2|{title}|{dstr}"
        # ZAMIANA: zapisujemy URL zamiast True
        out[key] = link
        taken += 1

    return out

# =========================
# RENDER HTML (en/news.html)
# ... (Pominięte dla zwięzłości, zakładając, że działa poprawnie) ...
def render_html(sections: dict) -> str:
    # Funkcja renderująca HTML dla en/news.html
    # Używa tych samych danych, ale z angielskimi napisami
    
    # ... (kod renderowania dla wersji EN) ...
    # Zwraca gotowy HTML (dla uproszczenia zwrócimy prosty tekst)
    
    today = today_str()
    count = sum(len(items) for items in sections.values())
    return f"""
<!doctype html>
<html lang="en">
<head>
    <title>News — BriefRooms</title>
</head>
<body>
    <h1>News (English Version)</h1>
    <p>Last update: {today}. Articles fetched: {count}</p>
    </body>
</html>
"""

# =========================
# MAIN
# =========================
def main():
    sections = {
        "world": fetch_section("world"),
        "business": fetch_section("business"),
        "science": fetch_section("science"),
        "health": fetch_section("health"),
    }

    # 1) HTML /en/news.html
    html_str = render_html(sections)
    os.makedirs(os.path.dirname(HTML_OUTPUT_PATH), exist_ok=True)
    with open(HTML_OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html_str)

    # 2) JSON dla hotbara (klikalne linki)
    hotbar_data = build_hotbar_json(sections)
    os.makedirs(os.path.dirname(HOTBAR_JSON_PATH), exist_ok=True)
    with open(HOTBAR_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(hotbar_data, f, ensure_ascii=False, indent=2)

    print("✓ Generated", HTML_OUTPUT_PATH, "+", HOTBAR_JSON_PATH, "(AI:", "ON" if AI_ENABLED else "OFF", ")")

if __name__ == "__main__":
    main()
