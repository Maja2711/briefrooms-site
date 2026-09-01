#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import feedparser
import requests

ROOT = Path(__file__).resolve().parents[1]
UA = "BriefRooms source-only news/2.0"
TARGET = 9
HOME_TARGET = 10

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
WEATHER = re.compile(r"\b(pogoda|burza|burze|opady|deszcz|grad|upał|mróz|weather|storm|rain|forecast)\b", re.I)

SHARED_TABS_STYLE = """
<style id="briefrooms-shared-section-tabs">
body[data-page="news"] .section-tabs{position:sticky!important;top:10px!important;z-index:20!important;display:flex!important;gap:8px!important;justify-content:flex-start!important;align-items:center!important;flex-wrap:wrap!important;margin:18px auto 30px!important;padding:8px!important;max-width:1180px!important;border:1px solid rgba(172,224,240,.20)!important;border-radius:16px!important;background:linear-gradient(145deg,rgba(14,38,57,.94),rgba(5,18,31,.94))!important;box-shadow:0 16px 38px rgba(0,0,0,.28),inset 0 1px 0 rgba(255,255,255,.08)!important;backdrop-filter:blur(16px)!important}
body[data-page="news"] .section-tabs a{display:inline-flex!important;align-items:center!important;justify-content:center!important;min-width:0!important;padding:9px 14px!important;border:1px solid rgba(190,226,240,.18)!important;border-radius:11px!important;background:linear-gradient(145deg,rgba(255,255,255,.10),rgba(255,255,255,.035))!important;color:#eaf6ff!important;font-size:13px!important;font-weight:850!important;text-decoration:none!important;box-shadow:inset 0 1px 0 rgba(255,255,255,.10)!important;transition:transform .18s ease,border-color .18s ease,background .18s ease!important}
body[data-page="news"] .section-tabs a:hover,body[data-page="news"] .section-tabs a:focus-visible{transform:translateY(-1px)!important;border-color:rgba(56,214,201,.45)!important;background:linear-gradient(145deg,rgba(56,214,201,.17),rgba(255,255,255,.05))!important;color:#fff!important;outline:none!important}
body[data-page="news"] .section-tabs .brand-link{background:linear-gradient(135deg,rgba(56,214,201,.22),rgba(127,200,255,.09))!important;border-color:rgba(56,214,201,.30)!important;padding:7px 11px!important}
body[data-page="news"] .section-tabs .brand-mark{display:inline-flex!important;align-items:center!important;justify-content:center!important;width:34px!important;height:28px!important;border-radius:9px!important;color:#07121e!important;background:linear-gradient(135deg,#087f9a,#23d5cc 42%,#d6fbff)!important;font-weight:950!important;letter-spacing:-.08em!important}
@media(max-width:640px){body[data-page="news"] .section-tabs{flex-wrap:nowrap!important;overflow-x:auto!important;justify-content:flex-start!important;border-radius:14px!important;scrollbar-width:none!important}body[data-page="news"] .section-tabs::-webkit-scrollbar{display:none!important}body[data-page="news"] .section-tabs a{flex:0 0 auto!important;white-space:nowrap!important}}
</style>
""".strip()


def clean(value: object, limit: int = 600) -> str:
    text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(value or "")))).strip()
    if len(text) <= limit:
        return text
    clipped = text[: limit + 1].rsplit(" ", 1)[0].strip()
    return clipped + "…"


def safe_url(value: object) -> str:
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path or "/", parsed.query, ""))


def entry_image(entry: object) -> str:
    for attr in ("media_content", "media_thumbnail"):
        for item in getattr(entry, attr, []) or []:
            candidate = safe_url(item.get("url") if isinstance(item, dict) else "")
            if candidate:
                return candidate
    for item in getattr(entry, "enclosures", []) or []:
        if isinstance(item, dict) and str(item.get("type", "")).startswith("image"):
            candidate = safe_url(item.get("href") or item.get("url"))
            if candidate:
                return candidate
    match = IMG.search(str(getattr(entry, "summary", "") or ""))
    return safe_url(match.group(1)) if match else ""


def page_image(link: str) -> str:
    try:
        response = requests.get(link, headers={"User-Agent": UA}, timeout=7)
        response.raise_for_status()
        body = response.text[:500000]
        for pattern in (OG1, OG2):
            match = pattern.search(body)
            if match:
                return safe_url(html.unescape(match.group(1)))
    except Exception:
        pass
    return ""


def fetch_candidates(source: str, feed_url: str, section_id: str) -> list[dict[str, str]]:
    try:
        response = requests.get(feed_url, headers={"User-Agent": UA}, timeout=12)
        response.raise_for_status()
        entries = feedparser.parse(response.content).entries[:20]
    except Exception as exc:
        print(f"WARN feed {source}: {exc}")
        return []
    candidates: list[dict[str, str]] = []
    for entry in entries:
        title = clean(getattr(entry, "title", ""), 220)
        link = safe_url(getattr(entry, "link", ""))
        summary = clean(
            getattr(entry, "summary", "")
            or getattr(entry, "description", "")
            or getattr(entry, "subtitle", ""),
            430,
        )
        if not title or not link:
            continue
        if section_id in {"polityka", "ekonomia"} and WEATHER.search(title):
            continue
        image = entry_image(entry) or page_image(link)
        if not image:
            continue
        candidates.append(
            {
                "title": title,
                "link": link,
                "image": image,
                "source": source,
                "summary": summary or title,
            }
        )
    return candidates


def section_stories(section_id: str, feeds: list[tuple[str, str]], seen: set[str]) -> list[dict[str, str]]:
    pools = [fetch_candidates(source, feed_url, section_id) for source, feed_url in feeds]
    output: list[dict[str, str]] = []
    index = 0
    while len(output) < TARGET and any(index < len(pool) for pool in pools):
        for pool in pools:
            if index >= len(pool):
                continue
            story = pool[index]
            if story["link"] in seen:
                continue
            seen.add(story["link"])
            output.append(story)
            if len(output) >= TARGET:
                break
        index += 1
    return output


def collect(config: list[tuple[str, str, list[tuple[str, str]]]]) -> tuple[dict[str, list[dict[str, str]]], dict[str, str]]:
    seen: set[str] = set()
    sections: dict[str, list[dict[str, str]]] = {}
    labels: dict[str, str] = {}
    for section_id, label, feeds in config:
        labels[section_id] = label
        sections[section_id] = section_stories(section_id, feeds, seen)
    return sections, labels


def round_robin_home(sections: dict[str, list[dict[str, str]]], labels: dict[str, str]) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    longest = max((len(items) for items in sections.values()), default=0)
    for index in range(longest):
        for section_id, items in sections.items():
            if index >= len(items):
                continue
            story = dict(items[index])
            story["category"] = labels[section_id]
            selected.append(story)
            if len(selected) >= HOME_TARGET:
                return selected
    return selected


def news_card(story: dict[str, str], lang: str) -> str:
    source_prefix = "Źródło" if lang == "pl" else "Source"
    return (
        "<li>"
        f'<a class="news-main-link" href="{html.escape(story["link"], quote=True)}" target="_blank" rel="noopener noreferrer external">'
        '<span class="news-thumb has-image">'
        f'<img src="{html.escape(story["image"], quote=True)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">'
        "</span>"
        '<span class="news-title-wrap">'
        f'<span class="news-text">{html.escape(story["title"])}</span>'
        f'<span class="source-line">{source_prefix}: {html.escape(story["source"])}</span>'
        "</span></a></li>"
    )


def add_marker(source: str, marker: str, now: datetime) -> str:
    source = re.sub(r'\s*<meta\s+name=["\']briefrooms-emergency-publication["\'][^>]*>', "", source, flags=re.I)
    source = re.sub(r'\s*<meta\s+name=["\']briefrooms-news-updated-at["\'][^>]*>', "", source, flags=re.I)
    source = re.sub(r'\s*<style\s+id=["\']briefrooms-shared-section-tabs["\'][\s\S]*?</style>', "", source, flags=re.I)
    tags = (
        f'<meta name="briefrooms-news-updated-at" content="{now.isoformat(timespec="seconds")}">\n'
        f'<meta name="briefrooms-emergency-publication" content="{html.escape(marker, quote=True)}">\n'
    )
    if "</head>" not in source:
        raise RuntimeError("HTML page has no closing head")
    return source.replace("</head>", tags + SHARED_TABS_STYLE + "\n</head>", 1)


def update_news_page(path: Path, lang: str, sections: dict[str, list[dict[str, str]]], marker: str, now: datetime) -> None:
    source = path.read_text(encoding="utf-8")
    for section_id, stories in sections.items():
        if not stories:
            continue
        pattern = re.compile(
            rf'(<section\b[^>]*\bid=["\']{re.escape(section_id)}["\'][^>]*>[\s\S]*?<ul\s+class=["\']news["\']>)[\s\S]*?(</ul>)',
            re.I,
        )
        replacement = rf"\1{''.join(news_card(story, lang) for story in stories)}\2"
        source, count = pattern.subn(replacement, source, count=1)
        if count != 1:
            raise RuntimeError(f"could not locate {lang} section {section_id} in preserved layout")
    source = add_marker(source, marker, now)
    label = "Ostatnia aktualizacja" if lang == "pl" else "Last updated"
    stamp = now.strftime("%d.%m.%Y, %H:%M" if lang == "pl" else "%Y-%m-%d %H:%M")
    source = re.sub(
        rf'(<time\b[^>]*datetime=["\'])[^"\']+(["\'][^>]*>)[\s\S]*?(</time>)',
        rf'\1{now.isoformat(timespec="seconds")}\2{label}: {stamp}\3',
        source,
        count=1,
        flags=re.I,
    )
    source = re.sub(rf'({re.escape(label)}:\s*)[^<\r\n]+', rf'\g<1>{stamp}', source, count=1, flags=re.I)
    path.write_text(source, encoding="utf-8", newline="\n")


def homepage_card(story: dict[str, str], lang: str) -> str:
    fallback = "".join(word[:1] for word in story.get("category", "BR").split()[:2]).upper() or "BR"
    read_label = "Czytaj źródło →" if lang == "pl" else "Read source →"
    preview_label = "Podgląd źródła" if lang == "pl" else "Source preview"
    return (
        f'<a class="brief-card" href="{html.escape(story["link"], quote=True)}" target="_blank" rel="noopener noreferrer external">'
        '<div class="thumb has-image">'
        f'<div class="fallback-art" aria-hidden="true">{html.escape(fallback)}</div>'
        f'<img src="{html.escape(story["image"], quote=True)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" data-br-external-media="source-linked" data-br-source-url="{html.escape(story["link"], quote=True)}">'
        f'<span class="media-source-badge">{preview_label}: {html.escape(story["source"])}</span>'
        "</div>"
        '<div class="brief-body">'
        f'<h3 class="brief-title">{html.escape(story["title"])}</h3>'
        f'<p class="brief-desc">{html.escape(story["summary"])}</p>'
        '<span class="brief-source">'
        f'<b>{html.escape(story["source"])}</b><span class="brief-link">{read_label}</span>'
        "</span></div></a>"
    )


def update_homepage(path: Path, lang: str, stories: list[dict[str, str]], marker: str, now: datetime) -> None:
    if not stories:
        raise RuntimeError(f"no homepage stories for {lang}")
    source = path.read_text(encoding="utf-8")
    cards = "\n".join(homepage_card(story, lang) for story in stories)
    source, count = re.subn(
        r'(<!-- HOME_BRIEFS_START -->)[\s\S]*?(<!-- HOME_BRIEFS_END -->)',
        rf'\1\n{cards}\n\2',
        source,
        count=1,
    )
    if count != 1:
        raise RuntimeError(f"homepage markers missing for {lang}")
    iso_now = now.isoformat(timespec="seconds")
    source = re.sub(r'data-home-updated-at=["\'][^"\']*["\']', f'data-home-updated-at="{iso_now}"', source, count=1)
    label = "Aktualizacja" if lang == "pl" else "Update"
    date_text = now.strftime("%d.%m.%Y" if lang == "pl" else "%d/%m/%Y")
    source = re.sub(
        r'(<span\s+class=["\']pill["\']\s+id=["\']updated-at["\']>)[\s\S]*?(</span>)',
        rf'\1{label}: {date_text}\2',
        source,
        count=1,
        flags=re.I,
    )
    source = re.sub(r'\s*<meta\s+name=["\']briefrooms-emergency-publication["\'][^>]*>', "", source, flags=re.I)
    source = source.replace(
        "</head>",
        f'<meta name="briefrooms-emergency-publication" content="{html.escape(marker, quote=True)}">\n</head>',
        1,
    )
    path.write_text(source, encoding="utf-8", newline="\n")


def write_home_feed(path: Path, lang: str, stories: list[dict[str, str]], now: datetime) -> None:
    latest = []
    for story in stories:
        latest.append(
            {
                "category": story["category"],
                "title": story["title"],
                "summary": story["summary"],
                "details": story["summary"],
                "source": story["source"],
                "link": story["link"],
                "image": story["image"],
                "time": "teraz" if lang == "pl" else "now",
                "published_at": now.isoformat(timespec="seconds"),
                "full_brief": story["summary"],
                "summary_basis": "source_only",
                "comment_generation_status": "source_only",
                "image_policy": "source-linked-external",
            }
        )
    payload = {
        "language": lang,
        "updated_at": now.isoformat(timespec="seconds"),
        "quality_mode": "source-only-layout-preserving",
        "count": len(latest),
        "latest": latest,
        "radar": [],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def publish(root: Path, marker: str) -> None:
    now = datetime.now(timezone.utc)
    report: dict[str, object] = {
        "marker": marker,
        "generated_at": now.isoformat(),
        "mode": "source_only_layout_preserving",
        "languages": {},
    }
    for lang, config, news_path, home_path, feed_path in (
        ("pl", PL, root / "pl" / "aktualnosci.html", root / "pl" / "index.html", root / "pl" / "home_brief.json"),
        ("en", EN, root / "en" / "news.html", root / "en" / "index.html", root / "en" / "home_brief.json"),
    ):
        sections, labels = collect(config)
        total = sum(len(items) for items in sections.values())
        minimum = 8 if lang == "pl" else 12
        if total < minimum:
            raise RuntimeError(f"too few photo stories for {lang}: {total}")
        home = round_robin_home(sections, labels)
        update_news_page(news_path, lang, sections, marker, now)
        update_homepage(home_path, lang, home, marker, now)
        write_home_feed(feed_path, lang, home, now)
        report["languages"][lang] = {
            "sections": {section_id: len(items) for section_id, items in sections.items()},
            "homepage": len(home),
        }
    status = root / "data" / "emergency_news_status.json"
    status.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


def verify(base: str, marker: str) -> None:
    expected = f'content="{marker}"'.encode()
    paths = ("pl/aktualnosci.html", "en/news.html", "pl/index.html", "en/index.html")
    missing: list[str] = list(paths)
    for attempt in range(30):
        missing = []
        for path in paths:
            try:
                request = urllib.request.Request(
                    f"{base.rstrip('/')}/{path}?e={marker}&n={attempt}",
                    headers={"User-Agent": UA},
                )
                with urllib.request.urlopen(request, timeout=15) as response:
                    if expected not in response.read():
                        missing.append(path)
            except Exception:
                missing.append(path)
        if not missing:
            return
        time.sleep(10)
    raise RuntimeError("production marker missing: " + ", ".join(missing))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--marker", required=True)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--base-url", default="https://briefrooms.com")
    args = parser.parse_args()
    verify(args.base_url, args.marker) if args.verify else publish(ROOT, args.marker)


if __name__ == "__main__":
    main()
