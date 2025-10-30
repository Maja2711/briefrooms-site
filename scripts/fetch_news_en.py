#!/usr/bin/env python3
import datetime
from pathlib import Path

import feedparser

# =========================
# 1. RSS SOURCES (EN)
# =========================
# Uwaga: tu są prawdziwe, „czyste” feedy.
FEEDS = {
    "Politics / World": [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://feeds.bbci.co.uk/news/politics/rss.xml",
        "https://apnews.com/apf-topnews?format=rss"
    ],
    "Business / Economy": [
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best",
        "https://apnews.com/apf-business?format=rss",
    ],
    "Sports": [
        "https://www.espn.com/espn/rss/news",
        "https://feeds.bbci.co.uk/sport/rss.xml"
    ],
}

# ile newsów na sekcję chcemy pokazać
LIMIT_PER_SECTION = 6

# gdzie zapisać – w Twoim repo:
OUTPUT_PATH = Path("en/news.html")


def fetch_first_entries(urls, limit):
    """Return up to `limit` (title, link) from given RSS urls."""
    items = []
    for url in urls:
        feed = feedparser.parse(url)
        if not getattr(feed, "entries", None):
            continue

        for e in feed.entries:
            title = getattr(e, "title", "").strip()
            link = getattr(e, "link", "").strip()
            if title and link:
                items.append((title, link))
            if len(items) >= limit:
                break

        if len(items) >= limit:
            break

    return items[:limit]


def build_html(sections):
    """Build final HTML page (same look as PL version)."""
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")

    parts = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '  <meta charset="utf-8" />',
        '  <meta name="viewport" content="width=device-width,initial-scale=1" />',
        "  <title>News — BriefRooms</title>",
        '  <meta name="description" content="Automatic daily digest: politics/world, business/economy, sports." />',
        '  <link rel="icon" href="/assets/favicon.svg" />',
        '  <link rel="stylesheet" href="/assets/site.css" />',
        "</head>",
        "<body>",
        '  <header style="text-align:center; padding:26px 16px 8px">',
        "    <h1>News</h1>",
        f'    <p class="sub">Last ~36 hours • {today}</p>',
        "  </header>",
        '  <main style="max-width:980px; margin:0 auto; padding:0 16px 48px">',
    ]

    for section_name, items in sections.items():
        parts.append('    <section class="card" style="margin:16px 0; border-radius:22px;">')
        parts.append(f"      <h2>{section_name}</h2>")
        parts.append("      <ul>")
        if items:
            for title, link in items:
                parts.append(
                    f'        <li><a href="{link}" target="_blank" rel="noopener">{title}</a></li>'
                )
        else:
            parts.append("        <li>No headlines right now.</li>")
        parts.append("      </ul>")
        parts.append("    </section>")

    parts.extend(
        [
            '    <p class="sub" style="margin-top:22px">',
            "      Automatic digest (RSS). Links go to original publishers. Page is overwritten daily.",
            "    </p>",
            "  </main>",
            '  <footer style="text-align:center; opacity:.6; padding:16px 12px 36px">© BriefRooms</footer>',
            "</body>",
            "</html>",
        ]
    )

    return "\n".join(parts)


def main():
    sections_data = {}
    for section, urls in FEEDS.items():
        sections_data[section] = fetch_first_entries(urls, LIMIT_PER_SECTION)

    html = build_html(sections_data)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
