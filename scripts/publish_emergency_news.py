#!/usr/bin/env python3
from __future__ import annotations

import argparse, html, json, re, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import feedparser, requests

ROOT = Path(__file__).resolve().parents[1]
UA = "BriefRooms emergency news/1.0"
TARGET = 6

PL = [
    ("polityka", "Polityka / Kraj", [("TVN24", "https://tvn24.pl/najnowsze.xml"), ("Polsat News", "https://www.polsatnews.pl/rss/polska.xml"), ("RMF24", "https://www.rmf24.pl/fakty/polityka/feed")]),
    ("ekonomia", "Ekonomia / Biznes", [("Bankier.pl", "https://www.bankier.pl/rss/wiadomosci.xml"), ("Business Insider Polska", "https://businessinsider.com.pl/.feed"), ("RMF24", "https://www.rmf24.pl/ekonomia/feed")]),
    ("zdrowie", "Zdrowie", [("Nauka w Polsce", "https://naukawpolsce.pl/zdrowie/rss.xml"), ("RMF24", "https://www.rmf24.pl/zdrowie/feed")]),
    ("nauka", "Nauka / Technologie", [("Nauka w Polsce", "https://naukawpolsce.pl/naukowy/rss.xml"), ("RMF24", "https://www.rmf24.pl/nauka/feed"), ("Polsat News", "https://www.polsatnews.pl/rss/technologie.xml")]),
    ("sport", "Sport", [("Polsat Sport", "https://www.polsatsport.pl/rss/wszystkie.xml"), ("RMF24 Sport", "https://www.rmf24.pl/sport/feed"), ("TVP Sport", "https://sport.tvp.pl/rss")]),
]

EN = [
    ("world-news", "World News", [("BBC News", "https://feeds.bbci.co.uk/news/world/rss.xml"), ("The Guardian", "https://www.theguardian.com/world/rss")]),
    ("asia-pacific", "Asia-Pacific", [("BBC News", "https://feeds.bbci.co.uk/news/world/asia/rss.xml")]),
    ("europe", "Europe", [("BBC News", "https://feeds.bbci.co.uk/news/world/europe/rss.xml")]),
    ("middle-east", "Middle East", [("BBC News", "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml")]),
    ("business", "Business", [("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"), ("The Guardian", "https://www.theguardian.com/uk/business/rss")]),
    ("science", "Science", [("BBC Science", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"), ("The Guardian", "https://www.theguardian.com/science/rss")]),
    ("health", "Health", [("BBC Health", "https://feeds.bbci.co.uk/news/health/rss.xml"), ("The Guardian", "https://www.theguardian.com/society/health/rss")]),
    ("sport", "Sport", [("BBC Sport", "https://feeds.bbci.co.uk/sport/rss.xml?edition=int"), ("The Guardian", "https://www.theguardian.com/sport/rss")]),
]

IMG = re.compile(r'<img[^>]+src=["\']([^"\']+)', re.I)
OG1 = re.compile(r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)', re.I)
OG2 = re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\']', re.I)


def clean(value):
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(value or "")))).strip()


def url(value):
    try:
        p = urlsplit(str(value or "").strip())
    except ValueError:
        return ""
    if p.scheme not in {"http", "https"} or not p.netloc:
        return ""
    return urlunsplit((p.scheme, p.netloc, p.path or "/", p.query, ""))


def entry_image(entry):
    for attr in ("media_content", "media_thumbnail"):
        for item in getattr(entry, attr, []) or []:
            candidate = url(item.get("url") if isinstance(item, dict) else "")
            if candidate:
                return candidate
    for item in getattr(entry, "enclosures", []) or []:
        if isinstance(item, dict) and str(item.get("type", "")).startswith("image"):
            candidate = url(item.get("href") or item.get("url"))
            if candidate:
                return candidate
    match = IMG.search(str(getattr(entry, "summary", "") or ""))
    return url(match.group(1)) if match else ""


def page_image(link):
    try:
        r = requests.get(link, headers={"User-Agent": UA}, timeout=7)
        r.raise_for_status()
        body = r.text[:500000]
        for pattern in (OG1, OG2):
            match = pattern.search(body)
            if match:
                return url(html.unescape(match.group(1)))
    except Exception:
        pass
    return ""


def section_stories(feeds, seen):
    out = []
    for source, feed_url in feeds:
        try:
            r = requests.get(feed_url, headers={"User-Agent": UA}, timeout=12)
            r.raise_for_status()
            entries = feedparser.parse(r.content).entries[:16]
        except Exception as exc:
            print(f"WARN feed {source}: {exc}")
            continue
        for entry in entries:
            title = clean(getattr(entry, "title", ""))
            link = url(getattr(entry, "link", ""))
            if not title or not link or link in seen:
                continue
            image = entry_image(entry) or page_image(link)
            if not image:
                continue
            seen.add(link)
            out.append({"title": title, "link": link, "image": image, "source": source})
            if len(out) >= TARGET:
                return out
    return out


def render(lang, config, marker, now):
    pl = lang == "pl"
    seen = set(); blocks = []; tabs = []; counts = {}
    for sid, label, feeds in config:
        stories = section_stories(feeds, seen)
        counts[sid] = len(stories)
        cards = "".join(
            f'<article><a href="{html.escape(s["link"], quote=True)}" target="_blank" rel="noopener noreferrer">'
            f'<img src="{html.escape(s["image"], quote=True)}" alt="" loading="lazy">'
            f'<div><h3>{html.escape(s["title"])}</h3><p>{html.escape(s["source"])}</p></div></a></article>'
            for s in stories
        )
        tabs.append(f'<a href="#{sid}">{html.escape(label)}</a>')
        blocks.append(f'<section id="{sid}"><h2>{html.escape(label)}</h2><div class="grid">{cards}</div></section>')
    total = sum(counts.values())
    if total < (8 if pl else 12):
        raise RuntimeError(f"too few photo stories for {lang}: {total}")
    title = "Aktualności" if pl else "News"
    updated_label = "Ostatnia aktualizacja" if pl else "Last updated"
    stamp = now.astimezone().strftime("%d.%m.%Y, %H:%M" if pl else "%Y-%m-%d %H:%M")
    home = "/pl/" if pl else "/en/"
    other = "/en/news.html" if pl else "/pl/aktualnosci.html"
    other_label = "EN" if pl else "PL"
    doc = f'''<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="briefrooms-news-updated-at" content="{now.isoformat(timespec='seconds')}"><meta name="briefrooms-emergency-publication" content="{marker}"><title>{title} — BriefRooms</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at top,#132640,#07101f 45%);color:#f5f7fb;font-family:Inter,system-ui,sans-serif}}a{{color:inherit}}header,main,footer{{max-width:1240px;margin:auto;padding-left:20px;padding-right:20px}}header{{padding-top:20px;display:flex;align-items:center}}.brand{{font-weight:950;font-size:24px;text-decoration:none}}.brand b{{display:inline-grid;place-items:center;width:46px;height:40px;border-radius:13px;background:linear-gradient(135deg,#087f9a,#27d4cf,#d6fbff);color:#07101f}}header nav{{margin-left:auto;display:flex;gap:10px}}header nav a,.tabs a{{text-decoration:none;padding:9px 13px;border:1px solid #ffffff24;border-radius:999px}}h1{{font-size:clamp(38px,6vw,64px);margin:35px 0 5px}}.updated{{color:#a8b4c7}}.tabs{{position:sticky;top:0;z-index:5;display:flex;gap:8px;overflow:auto;padding:10px;background:#07101fee;border-radius:18px}}.tabs a{{white-space:nowrap;background:#ffffff0d}}section{{padding-top:28px;scroll-margin-top:80px}}h2{{font-size:28px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(245px,1fr));gap:16px}}article{{background:#101d31;border:1px solid #ffffff18;border-radius:18px;overflow:hidden}}article a{{text-decoration:none}}article img{{display:block;width:100%;height:165px;object-fit:cover;background:#132238}}article div{{padding:15px}}article h3{{font-size:18px;line-height:1.3;margin:0 0 12px}}article p{{color:#efc77b;font-weight:800;margin:0}}footer{{padding-top:40px;padding-bottom:40px;color:#a8b4c7}}
</style></head><body><header><a class="brand" href="{home}"><b>BRs</b></a><nav><a href="{home}">Home</a><a href="{other}">{other_label}</a></nav></header><main><h1>{title}</h1><p class="updated">{updated_label}: {stamp}</p><nav class="tabs">{''.join(tabs)}</nav>{''.join(blocks)}</main><footer>BriefRooms · source-only edition</footer></body></html>'''
    return doc, counts


def publish(root, marker):
    now = datetime.now(timezone.utc); report = {"marker": marker, "generated_at": now.isoformat(), "languages": {}}
    for lang, config, path in (("pl", PL, root / "pl" / "aktualnosci.html"), ("en", EN, root / "en" / "news.html")):
        doc, counts = render(lang, config, marker, now)
        path.write_text(doc, encoding="utf-8", newline="\n")
        report["languages"][lang] = counts
    status = root / "data" / "emergency_news_status.json"
    status.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


def verify(base, marker):
    expected = f'content="{marker}"'.encode()
    for attempt in range(30):
        missing = []
        for path in ("pl/aktualnosci.html", "en/news.html"):
            try:
                req = urllib.request.Request(f"{base.rstrip('/')}/{path}?e={marker}&n={attempt}", headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=15) as r:
                    if expected not in r.read(): missing.append(path)
            except Exception: missing.append(path)
        if not missing: return
        time.sleep(10)
    raise RuntimeError("production marker missing: " + ", ".join(missing))


def main():
    p = argparse.ArgumentParser(); p.add_argument("--marker", required=True); p.add_argument("--verify", action="store_true"); p.add_argument("--base-url", default="https://briefrooms.com"); a = p.parse_args()
    verify(a.base_url, a.marker) if a.verify else publish(ROOT, a.marker)

if __name__ == "__main__": main()
