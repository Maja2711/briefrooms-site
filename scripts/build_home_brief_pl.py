#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build /pl/home_brief.json for the BriefRooms homepage.

The homepage uses this file to render the latest cards and the Live Radar.
Images are taken from RSS media fields first, then from the article page's
Open Graph / Twitter image meta tags. If no image is available, the frontend
uses a safe visual fallback.
"""

import html
import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import feedparser
import requests

OUT_PATH = "pl/home_brief.json"
TIMEOUT = 8
USER_AGENT = "BriefRoomsBot/1.0 (+https://briefrooms.com)"

FEEDS = {
    "Polityka / kraj": [
        "https://tvn24.pl/najnowsze.xml",
        "https://tvn24.pl/polska.xml",
        "https://www.polsatnews.pl/rss/wszystkie.xml",
        "https://www.pap.pl/rss.xml",
    ],
    "Ekonomia": [
        "https://www.bankier.pl/rss/wiadomosci.xml",
        "https://www.bankier.pl/rss/gospodarka.xml",
        "https://www.pap.pl/rss.xml",
    ],
    "Sport": [
        "https://www.polsatsport.pl/rss/wszystkie.xml",
        "https://sport.tvp.pl/rss",
        "https://przegladsportowy.onet.pl/.feed",
        "https://sportowefakty.wp.pl/rss.xml",
    ],
    "Nauka": [
        "https://www.esa.int/rssfeed/Our_Activities/Space_Science",
        "https://www.nasa.gov/news-release/feed/",
        "https://www.sciencedaily.com/rss/top/science.xml",
    ],
    "Zdrowie": [
        "https://www.who.int/feeds/entity/mediacentre/news/en/rss.xml",
        "https://www.cdc.gov/media/rss.htm",
        "https://www.nhs.uk/news/feed/",
    ],
}

SOURCE_NAMES = [
    (r"tvn24\.pl", "TVN24"),
    (r"polsatnews\.pl", "Polsat News"),
    (r"pap\.pl", "PAP"),
    (r"bankier\.pl", "Bankier.pl"),
    (r"polsatsport\.pl", "Polsat Sport"),
    (r"sport\.tvp\.pl", "TVP Sport"),
    (r"przegladsportowy\.onet\.pl|sport\.onet\.pl", "Przegląd Sportowy"),
    (r"sportowefakty\.wp\.pl", "SportoweFakty WP"),
    (r"nasa\.gov", "NASA"),
    (r"esa\.int", "ESA"),
    (r"sciencedaily\.com", "ScienceDaily"),
    (r"who\.int", "WHO"),
    (r"cdc\.gov", "CDC"),
    (r"nhs\.uk", "NHS"),
]

BAN = re.compile(r"horoskop|quiz|galeria|plotk|sponsorowany|lotto|eurojackpot|na żywo|relacja live", re.I)
IMG_META_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)
IMG_META_RE_ALT = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\']',
    re.I,
)


def clean_text(value: str, max_len: int = 190) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= max_len:
        return value
    cut = value[: max_len + 1]
    end = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
    if end > 80:
        return cut[: end + 1].strip()
    return cut[:max_len].rsplit(" ", 1)[0].strip() + "…"


def source_name(url: str) -> str:
    host = urlparse(url).netloc.replace("www.", "")
    for pattern, name in SOURCE_NAMES:
        if re.search(pattern, host, re.I):
            return name
    return host or "Źródło"


def entry_timestamp(entry) -> float:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            return datetime(*parsed[:6], tzinfo=timezone.utc).timestamp()
        except Exception:
            pass
    return time.time()


def entry_image(entry, article_url: str) -> str:
    # RSS media extensions: feedparser normalizes many variants here.
    for key in ("media_content", "media_thumbnail"):
        media = entry.get(key) or []
        if isinstance(media, list):
            for item in media:
                url = item.get("url") if isinstance(item, dict) else ""
                if is_image_url(url):
                    return url

    # Enclosures.
    for enc in entry.get("enclosures") or []:
        url = enc.get("href") or enc.get("url") or ""
        mime = enc.get("type") or ""
        if is_image_url(url) or mime.startswith("image/"):
            return url

    # Some feeds keep the image inside summary/content HTML.
    for html_field in (entry.get("summary", ""), entry.get("description", "")):
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html_field or "", re.I)
        if m and is_image_url(m.group(1)):
            return urljoin(article_url, m.group(1))

    # Last resort: article page Open Graph image.
    return article_og_image(article_url)


def is_image_url(url: str) -> bool:
    if not url:
        return False
    low = url.lower().split("?", 1)[0]
    return low.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")) or "image" in low


def article_og_image(url: str) -> str:
    if not url.startswith("http"):
        return ""
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        if not r.ok:
            return ""
        head = r.text[:180000]
        m = IMG_META_RE.search(head) or IMG_META_RE_ALT.search(head)
        if not m:
            return ""
        return urljoin(url, html.unescape(m.group(1).strip()))
    except Exception:
        return ""


def make_item(entry, category: str) -> dict | None:
    title = clean_text(entry.get("title", ""), 92)
    link = entry.get("link", "")
    summary_raw = entry.get("summary", "") or entry.get("description", "") or title
    summary = clean_text(summary_raw, 150)
    if not title or not link or BAN.search(f"{title} {summary}"):
        return None
    return {
        "category": category,
        "title": title,
        "summary": summary or "Krótki kontekst i link do źródła.",
        "source": source_name(link),
        "link": link,
        "image": entry_image(entry, link),
        "time": "dzisiaj",
        "ts": entry_timestamp(entry),
    }


def collect_items() -> list[dict]:
    seen = set()
    items = []
    for category, feeds in FEEDS.items():
        for feed_url in feeds:
            try:
                parsed = feedparser.parse(feed_url)
            except Exception:
                continue
            for entry in parsed.entries[:12]:
                item = make_item(entry, category)
                if not item:
                    continue
                norm = re.sub(r"\W+", "", item["title"].lower())[:80]
                if norm in seen:
                    continue
                seen.add(norm)
                items.append(item)
    items.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return items


def build_payload(items: list[dict]) -> dict:
    latest = []
    used_categories = set()
    for item in items:
        if item["category"] not in used_categories:
            latest.append(item)
            used_categories.add(item["category"])
        if len(latest) >= 4:
            break
    if len(latest) < 4:
        for item in items:
            if item not in latest:
                latest.append(item)
            if len(latest) >= 4:
                break

    radar = [item for item in items if item not in latest][:4]
    return {
        "updated_at": datetime.now().astimezone().isoformat(timespec="minutes"),
        "count": min(len(items), 24),
        "latest": strip_ts(latest),
        "radar": strip_ts(radar),
    }


def strip_ts(items: list[dict]) -> list[dict]:
    out = []
    for item in items:
        x = dict(item)
        x.pop("ts", None)
        out.append(x)
    return out


def main():
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    payload = build_payload(collect_items())
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"✓ Wygenerowano {OUT_PATH}: {len(payload['latest'])} briefów + {len(payload['radar'])} radar")


if __name__ == "__main__":
    main()
