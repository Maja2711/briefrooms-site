#!/usr/bin/env python3
# Build /en/home_brief.json with stricter editorial quality.
from __future__ import annotations

import html
import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin, urlparse

import feedparser
import requests

OUT_PATH = "en/home_brief.json"
TIMEOUT = 9
HEADERS = {"User-Agent": "BriefRoomsBot/2.0 (+https://briefrooms.com)"}
MAX_ITEMS = 12

FEEDS = {
    "World / news": [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://www.theguardian.com/world/rss",
        "https://news.google.com/rss/search?q=" + quote_plus("Reuters world geopolitics NATO oil sanctions") + "&hl=en&gl=US&ceid=US:en",
    ],
    "Business / markets": [
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
        "https://news.google.com/rss/search?q=" + quote_plus("Reuters markets Fed inflation rates stocks oil crypto") + "&hl=en&gl=US&ceid=US:en",
    ],
    "Technology": [
        "https://feeds.bbci.co.uk/news/technology/rss.xml",
        "https://www.theverge.com/rss/index.xml",
        "https://news.google.com/rss/search?q=" + quote_plus("Reuters AI chips cybersecurity regulation") + "&hl=en&gl=US&ceid=US:en",
    ],
    "Science": [
        "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
        "https://www.nasa.gov/news-release/feed/",
        "https://www.esa.int/rssfeed/Our_Activities/Space_Science",
    ],
    "Health": [
        "https://feeds.bbci.co.uk/news/health/rss.xml",
        "https://www.who.int/feeds/entity/mediacentre/news/en/rss.xml",
        "https://www.nih.gov/news-events/news-releases/rss.xml",
    ],
}

SOURCE_NAMES = [
    ("reuters.com", "Reuters"), ("bbc.", "BBC"), ("theguardian.com", "The Guardian"), ("nytimes.com", "The New York Times"),
    ("cnbc.com", "CNBC"), ("marketwatch.com", "MarketWatch"), ("wsj.com", "WSJ"), ("dj.com", "WSJ"),
    ("nasa.gov", "NASA"), ("esa.int", "ESA"), ("who.int", "WHO"), ("nih.gov", "NIH"), ("cdc.gov", "CDC"),
    ("theverge.com", "The Verge"), ("wired.com", "WIRED"),
]
SOURCE_WEIGHT = {"Reuters": 42, "BBC": 26, "WSJ": 25, "CNBC": 22, "The Guardian": 20, "WHO": 18, "NASA": 16, "ESA": 16, "The Verge": 15}
BAN = re.compile(r"horoscope|quiz|gallery|sponsored|lottery|gossip|live blog|coupon code|promo code|deals for|watch live|interview preview|will be a guest|tv programme", re.I)
NOISE = re.compile(r"cookie|cookies|advertisement|subscribe|newsletter|privacy|sign in|log in|read more|related|photo credit|all rights reserved", re.I)
LOW_VALUE = re.compile(r"will be a guest|watch live|programme|preview interview|read full transcript", re.I)
IMPORTANT = re.compile(r"war|nato|ukraine|russia|china|iran|middle east|oil|gas|sanctions|tariffs|inflation|rates|fed|ecb|stocks|bond|dollar|recession|cyber|attack|health|ai|chips|crypto|bitcoin|election", re.I)
IMG_META = re.compile(r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)["\']', re.I)
IMG_META_ALT = re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\']', re.I)


def clean_text(value, max_len=190):
    value = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", value or "", flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"^\s*(?:Reuters|AP|Bloomberg|BBC)\s*[-–—:]\s*", "", value, flags=re.I)
    value = re.sub(r"\s+", " ", value).strip(" -–—·•/\t\n\r")
    if len(value) <= max_len:
        return value
    cut = value[: max_len + 1]
    end = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
    if end > 80:
        return cut[: end + 1].strip()
    return value[:max_len].rsplit(" ", 1)[0].strip() + "…"


def ensure_period(text):
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text + "." if text and text[-1] not in ".!?…" else text


def source_name(url, raw=""):
    host = urlparse(url).netloc.replace("www.", "")
    blob = f"{host} {raw}".lower()
    for needle, name in SOURCE_NAMES:
        if needle in blob:
            return name
    if "news.google." in host:
        return "Google News"
    return host or "Source"


def sentence_list(text):
    text = clean_text(text, 2400).replace("…", ".")
    parts = re.findall(r"[^.!?]+[.!?]+|[^.!?]+$", text)
    out = []
    for p in parts:
        p = ensure_period(clean_text(p, 260))
        if len(p) >= 45 and not NOISE.search(p) and not LOW_VALUE.search(p):
            out.append(p)
    return out


def editorial_summary(article_text, rss_summary, title):
    sents = sentence_list(article_text or rss_summary or title)
    if sents:
        return clean_text(" ".join(sents[:2]), 240)
    return ensure_period(clean_text(rss_summary or title, 220))


def details_summary(article_text, fallback):
    sents = sentence_list(article_text or fallback)
    if sents:
        return clean_text(" ".join(sents[:4]), 620)
    return ensure_period(clean_text(fallback, 480))


def html_to_article_text(raw):
    raw = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<noscript[\s\S]*?</noscript>", " ", raw[:280000], flags=re.I)
    paras = re.findall(r"<p[^>]*>([\s\S]*?)</p>", raw, flags=re.I)
    cleaned = []
    for p in paras[:34]:
        t = clean_text(p, 850)
        if len(t) >= 55 and not NOISE.search(t) and not LOW_VALUE.search(t):
            cleaned.append(t)
        if len(cleaned) >= 9:
            break
    return " ".join(cleaned)


def article_excerpt(url):
    if not url.startswith("http"):
        return ""
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if not r.ok:
            return ""
        return html_to_article_text(r.text)
    except Exception:
        return ""


def image_from_article(url):
    if not url.startswith("http"):
        return ""
    try:
        text = requests.get(url, headers=HEADERS, timeout=TIMEOUT).text[:180000]
    except Exception:
        return ""
    m = IMG_META.search(text) or IMG_META_ALT.search(text)
    return urljoin(url, html.unescape(m.group(1).strip())) if m else ""


def fallback_image(category):
    color = "38d6c9"
    if "world" in category.lower(): color = "ffd15e"
    if "health" in category.lower(): color = "86ffb7"
    if "science" in category.lower(): color = "7fc8ff"
    label = html.escape(category[:18] or "Brief")
    svg = f"<svg xmlns='http://www.w3.org/2000/svg' width='1200' height='720'><defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop stop-color='%23061526'/><stop offset='1' stop-color='%23{color}' stop-opacity='.55'/></linearGradient></defs><rect width='1200' height='720' fill='url(%23g)'/><circle cx='220' cy='160' r='150' fill='%23{color}' opacity='.22'/><path d='M120 520 C320 320 520 440 720 250 S990 180 1120 300' stroke='%23{color}' stroke-width='20' fill='none' opacity='.65'/><text x='82' y='620' font-family='Arial' font-size='62' font-weight='800' fill='white'>{label}</text></svg>"
    return "data:image/svg+xml;charset=utf-8," + quote_plus(svg)


def image_from_entry(entry, link, category):
    for key in ("media_content", "media_thumbnail"):
        for item in entry.get(key) or []:
            url = item.get("url", "") if isinstance(item, dict) else ""
            if url:
                return url
    for enc in entry.get("enclosures") or []:
        url = enc.get("href") or enc.get("url") or ""
        if url:
            return url
    for field in (entry.get("summary", ""), entry.get("description", "")):
        match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', field or "", re.I)
        if match:
            return urljoin(link, match.group(1))
    return image_from_article(link) or fallback_image(category)


def timestamp(entry):
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            return datetime(*parsed[:6], tzinfo=timezone.utc).timestamp()
        except Exception:
            pass
    return time.time()


def quality_score(item):
    text = f"{item.get('title','')} {item.get('summary','')} {item.get('details','')}"
    score = SOURCE_WEIGHT.get(item.get("source", ""), 10)
    if IMPORTANT.search(text): score += 35
    if item.get("image") and not str(item.get("image", "")).startswith("data:image"): score += 8
    if item.get("category") in {"World / news", "Business / markets", "Technology", "Health"}: score += 8
    if BAN.search(text) or LOW_VALUE.search(text): score -= 100
    if len(clean_text(item.get("summary", ""), 500)) < 60: score -= 15
    return score


def make_item(entry, category):
    title = clean_text(entry.get("title", ""), 96).rstrip(".")
    link = entry.get("link", "")
    rss_raw = entry.get("summary", "") or entry.get("description", "") or title
    rss_summary = clean_text(rss_raw, 260)
    if not title or not link or BAN.search(title + " " + rss_summary) or LOW_VALUE.search(title + " " + rss_summary):
        return None
    article_text = article_excerpt(link)
    source = source_name(link, f"{title} {rss_summary}")
    summary = editorial_summary(article_text, rss_summary, title)
    details = details_summary(article_text, summary)
    if BAN.search(f"{title} {summary} {details}") or LOW_VALUE.search(f"{title} {summary} {details}"):
        return None
    item = {"category": category, "title": title, "summary": summary, "details": details, "source": source, "link": link, "image": image_from_entry(entry, link, category), "time": "today", "ts": timestamp(entry)}
    item["quality_score"] = quality_score(item)
    if item["quality_score"] < 15:
        return None
    return item


def collect_items():
    seen, items = set(), []
    for category, urls in FEEDS.items():
        for url in urls:
            try:
                feed = feedparser.parse(url)
            except Exception:
                continue
            for entry in feed.entries[:18]:
                item = make_item(entry, category)
                if not item:
                    continue
                key = re.sub(r"\W+", "", item["title"].lower())[:90]
                if key in seen:
                    continue
                seen.add(key)
                items.append(item)
    items.sort(key=lambda x: (x.get("quality_score", 0), x.get("ts", 0)), reverse=True)
    return items


def strip_internal(items):
    result = []
    for item in items:
        copy = dict(item)
        copy.pop("ts", None)
        copy.pop("quality_score", None)
        result.append(copy)
    return result


def build_payload(items):
    latest = []
    seen = set()
    for item in items:
        if item["link"] in seen:
            continue
        latest.append(item)
        seen.add(item["link"])
        if len(latest) >= MAX_ITEMS:
            break
    return {"language": "en", "updated_at": datetime.now().astimezone().isoformat(timespec="minutes"), "quality_mode": "important-news-v3", "count": len(latest), "latest": strip_internal(latest), "radar": []}


def main():
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    payload = build_payload(collect_items())
    with open(OUT_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    print(f"Generated {OUT_PATH}: {len(payload['latest'])} important briefs, radar removed")


if __name__ == "__main__":
    main()
