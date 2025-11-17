import feedparser, json, os

sources = [
    "http://feeds.bbci.co.uk/news/rss.xml",
    "https://www.theguardian.com/world/rss",
    "https://www.aljazeera.com/xml/rss/all.xml"
]

items = []
limit = 15

for url in sources:
    feed = feedparser.parse(url)
    for e in feed.entries[:limit]:
        title = e.title.strip()
        if title not in items:
            items.append(title)

items = items[:25]

os.makedirs("cache", exist_ok=True)

with open("cache/news_summaries_en.json", "w", encoding="utf-8") as f:
    json.dump(items, f, ensure_ascii=False, indent=2)
