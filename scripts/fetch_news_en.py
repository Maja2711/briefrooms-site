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
HOTBAR_LIMIT = 15  # Trochę więcej newsów w pasku

AI_ENABLED = bool(os.getenv("OPENAI_API_KEY"))
AI_MODEL = os.getenv("NEWS_AI_MODEL", "gpt-4o-mini")

# Ścieżki plików
AI_CACHE_PATH = ".cache/ai_cache_en.json"
HOTBAR_JSON_PATH = ".cache/news_summaries_en.json"  # To czyta hotbar.js
HTML_OUTPUT_PATH = "en/news.html"                  # Generowana strona zbiorcza

# ŹRÓDŁA (USA + UK + Science + Health)
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

# Słowa kluczowe do podbijania ważności (USA/UK/Global)
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


SIMILARITY_THRESHOLD = 0.65


def host_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def today_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def ensure_full_sentence(text: str, max_chars: int = 320) -> str:
    t = (text or "").strip()
    if len(t) > max_chars:
        t = t[:max_chars + 1]
    end = max(t.rfind("."), t.rfind("!"), t.rfind("?"))
    if end != -1:
        t = t[:end + 1]
    return t.strip()


def score_item(item, section_key: str) -> float:
    score = 0.0
    # Świeżość
    pp = item.get("published_parsed")
    if pp:
        dt = datetime(*pp[:6], tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
        score += max(0.0, 36.0 - age)

    t = item.get("title", "") or ""
    for rx, pts in BOOST.get(section_key, []):
        if rx.search(t):
            score += pts

    h = host_of(item.get("link", "") or "")
    if "reuters" in h:
        score += 10
    if "bbc" in h:
        score += 8

    return score


# =========================
# AI LOGIC
# =========================
CACHE = {}
if os.path.exists(AI_CACHE_PATH):
    try:
        with open(AI_CACHE_PATH, "r", encoding="utf-8") as f:
            CACHE = json.load(f)
    except Exception:
        CACHE = {}


def save_cache():
    try:
        os.makedirs(os.path.dirname(AI_CACHE_PATH), exist_ok=True)
        with open(AI_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(CACHE, f, indent=2)
    except Exception:
        pass


def ai_summarize_en(title: str, snippet: str, url: str) -> dict:
    cache_key = f"{(title or '').strip()}|{today_str()}"
    if cache_key in CACHE:
        return CACHE[cache_key]

    if not AI_ENABLED:
        out = {
            "summary": ensure_full_sentence((snippet or title or "")[:300], 300),
            "uncertain": ""
        }
        CACHE[cache_key] = out
        return out

    prompt = f"""Summarize this news item in English (max 15 words, 1 sentence).
Title: {title}
Snippet: {snippet}
Rules:
- Objective, journalistic tone.
- No clickbait.
- End with a period.
- Plain text only."""

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}",
                "Content-Type": "application/json"
            },
            json={
                "model": AI_MODEL,
                "messages": [
                    {"role": "system", "content": "You are a concise news editor."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.2,
                "max_tokens": 100,
            },
            timeout=20,
        )
        txt = resp.json()["choices"][0]["message"]["content"].strip()
        out = {"summary": txt, "uncertain": ""}
        CACHE[cache_key] = out
        save_cache()
        return out
    except Exception as e:
        print(f"AI Error: {e}")
        return {"summary": title, "uncertain": ""}


# =========================
# FETCH & BUILD
# =========================
def fetch_section(sec_key: str):
    items = []
    for url in FEEDS.get(sec_key, []):
        try:
            d = feedparser.parse(url)
            for e in d.entries[:20]:
                t = e.get("title", "") or ""
                l = e.get("link", "") or ""
                if not t or not l:
                    continue
                if any(rx.search(t) for rx in BAN_PATTERNS):
                    continue

                s = e.get("summary", "") or e.get("description", "") or ""
                items.append(
                    {
                        "title": t,
                        "link": l,
                        "summary_raw": re.sub("<[^<]+?>", "", s),
                        "published_parsed": e.get("published_parsed"),
                    }
                )
        except Exception:
            pass

    # Score & Dedupe
    for i in items:
        i["_score"] = score_item(i, sec_key)
        i["_tok"] = tokens_en(i["title"])

    items.sort(key=lambda x: x["_score"], reverse=True)

    kept = []
    for i in items:
        if not any(jaccard(i["_tok"], k["_tok"]) > SIMILARITY_THRESHOLD for k in kept):
            kept.append(i)

    # Limit per host
    per_host = {}
    final = []
    for i in kept:
        h = host_of(i["link"])
        if per_host.get(h, 0) >= MAX_PER_HOST:
            continue
        per_host[h] = per_host.get(h, 0) + 1
        final.append(i)

    return final[:MAX_PER_SECTION]


def build_hotbar_json(sections: dict) -> dict:
    """
    Buduje słownik:
      { "v2|Summary or title|2025-11-19": "<URL>" }
    Wartość to URL artykułu – używa go hotbar.js do linkowania.
    """
    hotbar = {}
    d_str = today_str()

    # Zbieramy wszystko do jednego worka
    pool = []
    for _, v in sections.items():
        pool.extend(v)

    # Najpierw po _score (ważność)
    pool.sort(key=lambda x: x.get("_score", 0.0), reverse=True)

    for item in pool[:HOTBAR_LIMIT]:
        txt = (item.get("ai_summary") or item.get("title") or "").replace("\n", " ").strip()
        url = (item.get("link") or "").strip()
        if not txt or not url:
            continue

        key = f"v2|{txt}|{d_str}"
        hotbar[key] = url

    return hotbar


def render_html(sections: dict) -> str:
    # Prosta funkcja generująca HTML (gdybyś potrzebował pliku en/news.html)
    # Używa stylów z assets/site.css

    def make_section(title, items):
        if not items:
            return ""
        lis = ""
        for it in items:
            lis += f"""
            <li>
                <a href="{esc(it['link'])}" target="_blank" rel="noopener">
                    <strong>{esc(it['title'])}</strong>
                </a>
                <p style="font-size:0.9em; opacity:0.8; margin:4px 0;">
                    {esc(it.get('ai_summary', ''))}
                </p>
            </li>"""
        return f'<section class="card"><h2>{esc(title)}</h2><ul class="news" style="list-style:none; padding:0;">{lis}</ul></section>'

    html_content = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>BriefRooms News (EN)</title>
  <link rel="stylesheet" href="/assets/site.css">
</head>
<body class="rooms-light">
<header>
  <h1>Latest Updates</h1>
  <p class="sub">Brief summaries from US, UK & World.</p>
</header>
<main>
  {make_section("World & Politics", sections.get('world', []))}
  {make_section("Business", sections.get('business', []))}
  {make_section("Science", sections.get('science', []))}
  {make_section("Health", sections.get('health', []))}
</main>
</body>
</html>"""
    return html_content


def main():
    print(f"--- START EN FETCH: {datetime.now()} ---")
    sections = {}

    # 1. Pobieranie sekcji (zgodnych z menu na stronie)
    for cat in ["world", "business", "science", "health"]:
        print(f"... fetching {cat}")
        sections[cat] = fetch_section(cat)

        # AI processing
        for item in sections[cat]:
            ai = ai_summarize_en(item["title"], item["summary_raw"], item["link"])
            item["ai_summary"] = ai["summary"]

    # 2. Zapis Hotbar JSON
    hotbar_data = build_hotbar_json(sections)
    try:
        os.makedirs(os.path.dirname(HOTBAR_JSON_PATH), exist_ok=True)
        with open(HOTBAR_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(hotbar_data, f, ensure_ascii=False, indent=2)
        print(f"Saved Hotbar JSON: {len(hotbar_data)} items")
    except Exception as e:
        print(f"Err saving hotbar: {e}")

    # 3. Zapis HTML (opcjonalnie, jako strona zbiorcza)
    try:
        html_source = render_html(sections)
        os.makedirs(os.path.dirname(HTML_OUTPUT_PATH), exist_ok=True)
        with open(HTML_OUTPUT_PATH, "w", encoding="utf-8") as f:
            f.write(html_source)
        print(f"Saved HTML: {HTML_OUTPUT_PATH}")
    except Exception as e:
        print(f"Err saving html: {e}")


if __name__ == "__main__":
    main()
