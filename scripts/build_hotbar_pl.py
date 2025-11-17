import feedparser, json, os

sources = [
    "https://www.rmf24.pl/fakty/feed",
    "https://wiadomosci.wp.pl/rss.xml",
    "https://www.interia.pl/feed"
]

items = []
limit = 15  # max newsów

for url in sources:
    feed = feedparser.parse(url)
    for e in feed.entries[:limit]:
        title = e.title.strip()
        if title not in items:
            items.append(title)

items = items[:25]  # finalne ucięcie

os.makedirs("cache", exist_ok=True)

with open("cache/news_summaries_pl.json", "w", encoding="utf-8") as f:
    json.dump(items, f, ensure_ascii=False, indent=2)
