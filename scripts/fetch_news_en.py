#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate /en/news.html from a few public RSS feeds.
Layout = the same as on PL: 3 boksy (Politics/World, Business/Economy, Sports).
"""

import datetime as dt
from pathlib import Path

import feedparser


# --- 1. źródła RSS (możesz zmieniać)
POLITICS_FEEDS = [
    "https://feeds.bbci.co.uk/news/world/rss.xml",          # BBC World
    "https://www.reutersagency.com/feed/?best-sectors=politics&post_type=best",  # Reuters politics
    "https://apnews.com/rss"                                # AP top
]

BUSINESS_FEEDS = [
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best",
    "https://www.ft.com/?format=rss"  # FT – bywa ciężki, ale niech będzie jako zapas
]

SPORTS_FEEDS = [
    "https://www.espn.com/espn/rss/news",
    "https://feeds.bbci.co.uk/sport/rss.xml?edition=uk",
    "https://apnews.com/hub/sports/rss"
]


def pick_entries(urls, limit=6):
    """Z kilku feedów zbierz pierwsze unikalne linki."""
    items = []
    seen = set()
    for url in urls:
        try:
            feed = feedparser.parse(url)
        except Exception:
            continue
        for e in feed.entries:
            title = e.get("title") or ""
            link = e.get("link") or ""
            if not title or not link:
                continue
            if link in seen:
                continue
            seen.add(link)
            items.append({"title": title.strip(), "link": link.strip()})
            if len(items) >= limit:
                return items
    return items


def build_html(politics, business, sports):
    today = dt.datetime.utcnow().strftime("%Y-%m-%d")
    # taki sam szkielet jak masz teraz w repo
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>News — BriefRooms</title>
  <link rel="icon" href="/assets/favicon.svg" />
  <link rel="stylesheet" href="/assets/site.css" />
</head>
<body>
  <header class="top">
    <h1>News</h1>
    <p class="sub">Last ~36 hours • {today}</p>
  </header>

  <main class="news-wrap">
    <section class="news-card">
      <h2>Politics / World</h2>
      <ul>
"""
    if politics:
        for item in politics:
            html += f'        <li><a href="{item["link"]}" target="_blank" rel="noreferrer">{item["title"]}</a></li>\n'
    else:
        html += "        <li>No fresh items.</li>\n"

    html += """      </ul>
    </section>

    <section class="news-card">
      <h2>Business / Economy</h2>
      <ul>
"""
    if business:
        for item in business:
            html += f'        <li><a href="{item["link"]}" target="_blank" rel="noreferrer">{item["title"]}</a></li>\n'
    else:
        html += "        <li>No fresh items.</li>\n"

    html += """      </ul>
    </section>

    <section class="news-card">
      <h2>Sports</h2>
      <ul>
"""
    if sports:
        for item in sports:
            html += f'        <li><a href="{item["link"]}" target="_blank" rel="noreferrer">{item["title"]}</a></li>\n'
    else:
        html += "        <li>No fresh items.</li>\n"

    html += """      </ul>
    </section>

    <p class="footnote">Automatic digest (RSS). Links go to original publishers. Page is overwritten daily.</p>
  </main>

  <footer>© BriefRooms</footer>
</body>
</html>
"""
    return html


def main():
    politics = pick_entries(POLITICS_FEEDS, limit=6)
    business = pick_entries(BUSINESS_FEEDS, limit=6)
    sports = pick_entries(SPORTS_FEEDS, limit=6)

    html = build_html(politics, business, sports)

    out_path = Path("en") / "news.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()

