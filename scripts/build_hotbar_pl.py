#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
from datetime import datetime
from dateutil import tz
import feedparser

# Strefa czasowa dla daty, gdy feed nie poda sensownej
TZ = tz.gettz("Europe/Warsaw")

# Źródła RSS dla paska (PL + świat + sport)
SOURCES = [
    "https://tvn24.pl/najnowsze.xml",
    "https://tvn24.pl/polska.xml",
    "https://tvn24.pl/swiat.xml",
    "https://www.polsatnews.pl/rss/wszystkie.xml",
    "https://www.pap.pl/rss.xml",
    "https://feeds.reuters.com/reuters/worldNews",
    "https://feeds.reuters.com/reuters/businessNews",
    "https://www.polsatsport.pl/rss/wszystkie.xml",
    "https://feeds.bbci.co.uk/sport/rss.xml?edition=int",
]

PER_FEED_LIMIT = 15   # max ile wpisów z jednego feedu
TOTAL_LIMIT    = 40   # globalny limit ilu kluczy do JSON
OUT_PATH       = ".cache/news_summaries_pl.json"


def main():
    out = {}

    for url in SOURCES:
        try:
            feed = feedparser.parse(url)
        except Exception as ex:
            print(f"[WARN] RSS error in hotbar feed: {url} -> {ex}")
            continue

        entries = getattr(feed, "entries", []) or []
        for entry in entries[:PER_FEED_LIMIT]:
            title = (entry.get("title") or "").strip()
            if not title:
                continue

            # Data w formacie RRRR-MM-DD
            date = ""
            published = entry.get("published") or entry.get("updated") or ""
            if published:
                # większość feedów ma ISO lub "2025-11-17 ..." – ucinamy do 10 znaków
                date = published[:10]

            if not date or len(date) != 10:
                now = datetime.now(TZ)
                date = now.strftime("%Y-%m-%d")

            key = f"{title}|{date}"
            out[key] = True

            if len(out) >= TOTAL_LIMIT:
                break

        if len(out) >= TOTAL_LIMIT:
            break

    # Zapis do .cache/news_summaries_pl.json
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"✓ Hotbar PL: zapisano {len(out)} wpisów do {OUT_PATH}")


if __name__ == "__main__":
    main()
