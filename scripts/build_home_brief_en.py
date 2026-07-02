#!/usr/bin/env python3
# Build /en/home_brief.json from English-language RSS feeds only.
import html, json, os, re, time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
import feedparser, requests

OUT_PATH = "en/home_brief.json"
TIMEOUT = 8
HEADERS = {"User-Agent": "BriefRoomsBot/1.0 (+https://briefrooms.com)"}

FEEDS = {
    "World / news": ["https://feeds.bbci.co.uk/news/world/rss.xml", "https://www.theguardian.com/world/rss", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"],
    "Business / markets": ["https://feeds.bbci.co.uk/news/business/rss.xml", "https://www.cnbc.com/id/100003114/device/rss/rss.html", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"],
    "Science": ["https://feeds.bbci.co.uk/news/science_and_environment/rss.xml", "https://www.nasa.gov/news-release/feed/", "https://www.esa.int/rssfeed/Our_Activities/Space_Science", "https://www.sciencedaily.com/rss/top/science.xml"],
    "Health": ["https://feeds.bbci.co.uk/news/health/rss.xml", "https://www.who.int/feeds/entity/mediacentre/news/en/rss.xml", "https://www.nih.gov/news-events/news-releases/rss.xml", "https://www.cdc.gov/media/rss.htm"],
    "Technology": ["https://feeds.bbci.co.uk/news/technology/rss.xml", "https://www.theverge.com/rss/index.xml", "https://www.wired.com/feed/rss"],
}

SOURCE_NAMES = [
    ("bbc.", "BBC"), ("theguardian.com", "The Guardian"), ("nytimes.com", "The New York Times"), ("cnbc.com", "CNBC"),
    ("marketwatch.com", "MarketWatch"), ("wsj.com", "WSJ"), ("dj.com", "WSJ"), ("nasa.gov", "NASA"), ("esa.int", "ESA"),
    ("sciencedaily.com", "ScienceDaily"), ("who.int", "WHO"), ("nih.gov", "NIH"), ("cdc.gov", "CDC"), ("theverge.com", "The Verge"), ("wired.com", "WIRED"),
]

BAN = re.compile("horoscope|quiz|gallery|sponsored|lottery|gossip|live blog|coupon code|promo code|deals for", re.I)
NOISE = re.compile("cookie|cookies|advertisement|subscribe|newsletter|privacy|sign in|log in|read more|related", re.I)


def clean_text(value, max_len=190):
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= max_len:
        return value
    cut = value[: max_len + 1]
    end = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
    if end > 80:
        return cut[: end + 1].strip()
    return value[:max_len].rsplit(" ", 1)[0].strip() + "…"


def source_name(url):
    host = urlparse(url).netloc.replace("www.", "")
    for needle, name in SOURCE_NAMES:
        if needle in host:
            return name
    return host or "Source"


def ensure_period(text):
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text + "." if text and text[-1] not in ".!?…" else text


def sentence_list(text):
    text = clean_text(text, 2400).replace("…", ".")
    parts = re.findall(r"[^.!?]+[.!?]+|[^.!?]+$", text)
    out = []
    for p in parts:
        p = ensure_period(p)
        if len(p) >= 35 and not NOISE.search(p):
            out.append(p)
    return out


def summary_3_4(text, max_len=620):
    sents = sentence_list(text)[:4]
    if not sents:
        return ensure_period(clean_text(text, max_len))
    return clean_text(" ".join(sents), max_len)


def html_to_article_text(raw):
    raw = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<noscript[\s\S]*?</noscript>", " ", raw, flags=re.I)
    paras = re.findall(r"<p[^>]*>([\s\S]*?)</p>", raw, flags=re.I)
    cleaned = []
    for p in paras[:24]:
        t = clean_text(p, 900)
        if len(t) >= 45 and not NOISE.search(t):
            cleaned.append(t)
        if len(cleaned) >= 7:
            break
    return " ".join(cleaned)


def article_excerpt(url):
    if not url.startswith("http"):
        return ""
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if not r.ok:
            return ""
        return html_to_article_text(r.text[:260000])
    except Exception:
        return ""


def image_from_entry(entry, link):
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
    return image_from_article(link)


def image_from_article(url):
    if not url.startswith("http"):
        return ""
    try:
        text = requests.get(url, headers=HEADERS, timeout=TIMEOUT).text[:160000]
    except Exception:
        return ""
    patterns = [r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)["\']', r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\']']
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return urljoin(url, html.unescape(match.group(1).strip()))
    return ""


def timestamp(entry):
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            return datetime(*parsed[:6], tzinfo=timezone.utc).timestamp()
        except Exception:
            pass
    return time.time()


def make_item(entry, category):
    title = clean_text(entry.get("title", ""), 92)
    link = entry.get("link", "")
    summary_raw = entry.get("summary", "") or entry.get("description", "") or title
    summary = clean_text(summary_raw, 150)
    if not title or not link or BAN.search(title + " " + summary):
        return None
    details = summary_3_4(article_excerpt(link) or summary_raw, 620)
    return {"category": category, "title": title, "summary": summary, "details": details or summary, "source": source_name(link), "link": link, "image": image_from_entry(entry, link), "time": "today", "ts": timestamp(entry)}


def collect_items():
    seen, items = set(), []
    for category, urls in FEEDS.items():
        for url in urls:
            try:
                feed = feedparser.parse(url)
            except Exception:
                continue
            for entry in feed.entries[:12]:
                item = make_item(entry, category)
                if not item:
                    continue
                key = re.sub(r"\W+", "", item["title"].lower())[:80]
                if key in seen:
                    continue
                seen.add(key)
                items.append(item)
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items


def strip_ts(items):
    result = []
    for item in items:
        copy = dict(item)
        copy.pop("ts", None)
        result.append(copy)
    return result


def build_payload(items):
    latest, used = [], set()
    for item in items:
        if item["category"] not in used:
            latest.append(item)
            used.add(item["category"])
        if len(latest) == 4:
            break
    for item in items:
        if len(latest) == 4:
            break
        if item not in latest:
            latest.append(item)
    radar = [item for item in items if item not in latest][:4]
    return {"language": "en", "updated_at": datetime.now().astimezone().isoformat(timespec="minutes"), "count": min(len(items), 24), "latest": strip_ts(latest), "radar": strip_ts(radar)}


def main():
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    payload = build_payload(collect_items())
    with open(OUT_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    print(f"Generated {OUT_PATH}: {len(payload['latest'])} briefs + {len(payload['radar'])} radar")


if __name__ == "__main__":
    main()
