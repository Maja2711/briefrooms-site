import datetime
import feedparser
from pathlib import Path

# --- 1. USTAWIENIA ŹRÓDEŁ (ANG) ---
POLITICS_FEEDS = [
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://rss.cnn.com/rss/edition_world.rss",
    "https://www.reuters.com/world/rss"   # world mix
]

BUSINESS_FEEDS = [
    "https://www.reuters.com/business/rss",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html"
]

SPORTS_FEEDS = [
    "https://www.espn.com/espn/rss/news",
    "https://www.cbssports.com/rss/headlines/"
]

# gdzie zapisać wynik – dokładnie taki jak w repo
TARGET = Path("en/news.html")

MAX_ITEMS_PER_SECTION = 8  # tyle pokażemy


def pick_items(feeds, limit):
    items = []
    for url in feeds:
        parsed = feedparser.parse(url)
        for e in parsed.entries[:limit]:
            title = e.get("title", "").strip()
            link = e.get("link", "").strip()
            if title and link:
                items.append((title, link))
    # obetnij nadmiar
    return items[:limit]


def build_html(date_str, politics, business, sports):
    # to jest prawie to samo co en/news.html, tylko z wypełnionymi <li>
    def render_list(items):
        return "\n".join(
            f'        <li><a href="{link}" target="_blank" rel="noopener">{title}</a></li>'
            for title, link in items
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>News — BriefRooms</title>
  <meta name="description" content="Automatic digest of the last hours: politics/world, business/economy, sports. Page is overwritten daily." />
  <link rel="icon" href="/assets/favicon.svg" />
  <link rel="stylesheet" href="/assets/site.css" />
  <link rel="alternate" hreflang="en" href="https://briefrooms.com/en/news.html">
  <link rel="alternate" hreflang="pl" href="https://briefrooms.com/pl/aktualnosci.html">
  <link rel="alternate" hreflang="x-default" href="https://briefrooms.com/">
  <style>
    header{{ text-align:center; padding:32px 16px 8px }}
    h1{{ margin:0 0 6px }}
    .sub{{ color:#b9c5d8; margin:0 0 18px }}
    main{{ max-width:1050px; margin:0 auto; padding:0 16px 48px }}
    .card{{
      background:linear-gradient(180deg,rgba(255,255,255,.06),rgba(255,255,255,.02));
      border:1px solid rgba(255,255,255,.07);
      border-radius:20px;
      padding:18px 20px 16px;
      margin:18px 0;
      box-shadow:inset 0 1px 0 rgba(255,255,255,.03), 0 18px 40px rgba(0,0,0,.28);
    }}
    h2{{ margin:0 0 10px }}
    ul{{ margin:0; padding-left:22px }}
    li{{ margin:6px 0 }}
    a{{ color:#9ed0ff }}
    a:hover{{ color:#bfe3ff }}
    footer{{ text-align:center; opacity:.7; padding:24px 16px 34px }}
  </style>
</head>
<body>
  <header>
    <h1>News</h1>
    <p class="sub">Last ~36 hours • {date_str}</p>
  </header>

  <main>
    <section class="card">
      <h2>Politics / World</h2>
      <ul>
{render_list(politics)}
      </ul>
    </section>

    <section class="card">
      <h2>Business / Economy</h2>
      <ul>
{render_list(business)}
      </ul>
    </section>

    <section class="card">
      <h2>Sports</h2>
      <ul>
{render_list(sports)}
      </ul>
    </section>

    <p class="sub">Automatic digest (RSS). Links go to original publishers. Page is overwritten daily.</p>
  </main>

  <footer>© BriefRooms</footer>
</body>
</html>
"""


def main():
    today = datetime.date.today().isoformat()

    politics = pick_items(POLITICS_FEEDS, MAX_ITEMS_PER_SECTION)
    business = pick_items(BUSINESS_FEEDS, MAX_ITEMS_PER_SECTION)
    sports = pick_items(SPORTS_FEEDS, MAX_ITEMS_PER_SECTION)

    html = build_html(today, politics, business, sports)
    TARGET.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
