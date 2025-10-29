#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Generuje /pl/aktualnosci.html z krótką listą najważniejszych newsów z ostatnich ~36 h.
Źródła: TVN24, RMF24, Bankier (gospodarka), Polsat Sport – RSS.
"""

import time
from datetime import datetime, timedelta, timezone
import feedparser
import html
import re
from pathlib import Path

# --- USTAWIENIA ---
MAX_PER_SOURCE = 6
HORIZON_HOURS = 36
OUT_FILE = Path("pl/aktualnosci.html")

SOURCES = {
    "Polityka / Kraj": [
        "https://tvn24.pl/najnowsze.xml",
        "https://www.rmf24.pl/kanaly/rss.html",
    ],
    "Ekonomia / Biznes": [
        "https://www.bankier.pl/rss/wiadomosci.xml",
        "https://www.bankier.pl/rss/gospodarka.xml",
    ],
    "Sport": [
        "https://www.polsatsport.pl/rss/wszystkie.xml",
    ],
}

# --- POMOCNICZE ---
NOW = datetime.now(timezone.utc)
CUTOFF = NOW - timedelta(hours=HORIZON_HOURS)

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def within_horizon(entry) -> bool:
    # feedparser może zwrócić published_parsed albo updated_parsed
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if not t:
        return True  # brak daty – dopuszczamy
    dt = datetime(*t[:6], tzinfo=timezone.utc)
    return dt >= CUTOFF

def collect():
    all_by_section = {}
    for section, urls in SOURCES.items():
        items = []
        seen_titles = set()
        for url in urls:
            feed = feedparser.parse(url)
            for e in feed.entries[: MAX_PER_SOURCE * 2]:
                if not within_horizon(e):
                    continue
                title = norm(e.get("title", ""))
                if not title or title.lower() in seen_titles:
                    continue
                seen_titles.add(title.lower())
                link = e.get("link", "")
                items.append({"title": title, "link": link})
                if len(items) >= MAX_PER_SOURCE:
                    break
        all_by_section[section] = items
    return all_by_section

def render_html(sections):
    today = NOW.astimezone().strftime("%Y-%m-%d")
    head = f"""<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Aktualności — BriefRooms</title>
  <meta name="description" content="Automatycznie odświeżane aktualności z ostatnich godzin: polityka, ekonomia, sport." />
  <link rel="icon" href="/assets/favicon.svg" />
  <link rel="stylesheet" href="/assets/site.css" />
  <style>
    header{{ text-align:center; padding:24px 12px 6px }}
    .sub{{ color:#b9c5d8 }}
    main{{ max-width:980px; margin:0 auto; padding:0 16px 48px }}
    .card{{ background:linear-gradient(180deg,rgba(255,255,255,.06),rgba(255,255,255,.02));
           border:1px solid rgba(255,255,255,.08); border-radius:16px; padding:18px 20px; margin:14px 0;
           box-shadow:inset 0 1px 0 rgba(255,255,255,.04), 0 10px 30px rgba(0,0,0,.25) }}
    h2{{ margin:8px 0 6px; color:#d7e6ff }}
    h3{{ margin:12px 0 6px; color:#aecdff }}
    ul.news{{ margin:6px 0 0 18px }}
    .note{{ color:#9fb3c8; font-size:.92rem }}
  </style>
</head>
<body>
<header>
  <h1>Aktualności</h1>
  <p class="sub">Ostatnie ~36 godzin • {html.escape(today)}</p>
</header>
<main>
"""
    parts = [head]
    for section, items in sections.items():
        if not items:
            continue
        parts.append(f'<section class="card"><h2>{html.escape(section)}</h2><ul class="news">')
        for it in items:
            parts.append(
                f'<li><a href="{html.escape(it["link"])}" target="_blank" rel="noopener">{html.escape(it["title"])}</a></li>'
            )
        parts.append("</ul></section>")
    parts.append(
        """<p class="note">Automatyczny skrót (RSS). Linki prowadzą do wydawców. Strona nadpisywana codziennie.</p>
</main>
<footer style="text-align:center; opacity:.55; padding:18px">© BriefRooms</footer>
</body></html>"""
    )
    return "\n".join(parts)

def main():
    sections = collect()
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(render_html(sections), encoding="utf-8")

if __name__ == "__main__":
    main()

