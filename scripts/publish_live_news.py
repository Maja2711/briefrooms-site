#!/usr/bin/env python3
from __future__ import annotations

import argparse
import calendar
import hashlib
import html
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import feedparser
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "news"
UA = "BriefRooms live news/1.0"
TARGET = 6
MIN_SECTION = 4
MAX_WORKERS = 10
REQUEST_TIMEOUT = 12

PL = [
    ("polityka", "Polityka / Kraj", [("TVN24", "https://tvn24.pl/najnowsze.xml"), ("Polsat News", "https://www.polsatnews.pl/rss/polska.xml"), ("RMF24", "https://www.rmf24.pl/fakty/polityka/feed")]),
    ("ekonomia", "Ekonomia / Biznes", [("Bankier.pl", "https://www.bankier.pl/rss/wiadomosci.xml"), ("Business Insider Polska", "https://businessinsider.com.pl/.feed"), ("RMF24", "https://www.rmf24.pl/ekonomia/feed")]),
    ("zdrowie", "Zdrowie", [("Nauka w Polsce", "https://naukawpolsce.pl/zdrowie/rss.xml"), ("RMF24", "https://www.rmf24.pl/zdrowie/feed"), ("Polsat News", "https://www.polsatnews.pl/rss/zdrowie.xml")]),
    ("nauka", "Nauka / Technologie", [("Nauka w Polsce", "https://naukawpolsce.pl/naukowy/rss.xml"), ("RMF24", "https://www.rmf24.pl/nauka/feed"), ("Polsat News", "https://www.polsatnews.pl/rss/technologie.xml")]),
    ("sport", "Sport", [("Polsat Sport", "https://www.polsatsport.pl/rss/wszystkie.xml"), ("RMF24 Sport", "https://www.rmf24.pl/sport/feed"), ("TVP Sport", "https://sport.tvp.pl/rss")]),
]

EN = [
    ("world-news", "World News", [("BBC News", "https://feeds.bbci.co.uk/news/world/rss.xml"), ("The Guardian", "https://www.theguardian.com/world/rss")]),
    ("asia-pacific", "Asia-Pacific", [("BBC News", "https://feeds.bbci.co.uk/news/world/asia/rss.xml"), ("The Guardian", "https://www.theguardian.com/world/asia-pacific/rss")]),
    ("europe", "Europe", [("BBC News", "https://feeds.bbci.co.uk/news/world/europe/rss.xml"), ("The Guardian", "https://www.theguardian.com/world/europe-news/rss")]),
    ("middle-east", "Middle East", [("BBC News", "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml"), ("The Guardian", "https://www.theguardian.com/world/middleeast/rss")]),
    ("business", "Business", [("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"), ("The Guardian", "https://www.theguardian.com/uk/business/rss")]),
    ("science", "Science", [("BBC Science", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"), ("The Guardian", "https://www.theguardian.com/science/rss")]),
    ("health", "Health", [("BBC Health", "https://feeds.bbci.co.uk/news/health/rss.xml"), ("The Guardian", "https://www.theguardian.com/society/health/rss")]),
    ("sport", "Sport", [("BBC Sport", "https://feeds.bbci.co.uk/sport/rss.xml?edition=int"), ("The Guardian", "https://www.theguardian.com/sport/rss")]),
]

IMG = re.compile(r'<img[^>]+src=["\']([^"\']+)', re.I)
OG = [
    re.compile(r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\']', re.I),
]
WEATHER = re.compile(r"\b(pogoda|burza|burze|opady|deszcz|grad|upał|mróz|weather|storm|rain|forecast)\b", re.I)


def clean(value: Any, limit: int = 600) -> str:
    text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(value or "")))).strip()
    if len(text) <= limit:
        return text
    return text[: limit + 1].rsplit(" ", 1)[0].strip() + "…"


def safe_url(value: Any) -> str:
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))


def normalized_identity(story: dict[str, Any]) -> str:
    link = safe_url(story.get("link"))
    if link:
        parsed = urlsplit(link)
        return f"{parsed.netloc}{parsed.path}".lower().rstrip("/")
    return re.sub(r"\W+", " ", str(story.get("title") or "").lower()).strip()


def parse_entry_time(entry: Any) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        value = getattr(entry, attr, None)
        if value:
            try:
                return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
            except Exception:
                pass
    for attr in ("published", "updated", "created"):
        raw = str(getattr(entry, attr, "") or "").strip()
        if not raw:
            continue
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                pass
    return None


def entry_image(entry: Any) -> str:
    for attr in ("media_content", "media_thumbnail"):
        for item in getattr(entry, attr, []) or []:
            if isinstance(item, dict):
                candidate = safe_url(item.get("url"))
                if candidate:
                    return candidate
    for item in getattr(entry, "enclosures", []) or []:
        if isinstance(item, dict) and str(item.get("type", "")).startswith("image"):
            candidate = safe_url(item.get("href") or item.get("url"))
            if candidate:
                return candidate
    match = IMG.search(str(getattr(entry, "summary", "") or ""))
    return safe_url(match.group(1)) if match else ""


def request(url: str, timeout: int = REQUEST_TIMEOUT) -> requests.Response:
    headers = {"User-Agent": UA, "Cache-Control": "no-cache", "Pragma": "no-cache", "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.8, */*;q=0.5"}
    last: Exception | None = None
    for attempt in range(3):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response
        except Exception as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"request failed after 3 attempts: {url}: {last}")


def page_image(link: str) -> str:
    try:
        body = request(link, timeout=8).text[:500000]
        for pattern in OG:
            match = pattern.search(body)
            if match:
                candidate = safe_url(html.unescape(match.group(1)))
                if candidate:
                    return candidate
    except Exception:
        pass
    return ""


def fetch_feed(source: str, feed_url: str, section_id: str) -> tuple[list[dict[str, Any]], str | None]:
    try:
        response = request(feed_url)
        parsed = feedparser.parse(response.content)
        entries = list(parsed.entries[:30])
    except Exception as exc:
        return [], f"{source}: {exc}"

    stories: list[dict[str, Any]] = []
    for entry in entries:
        title = clean(getattr(entry, "title", ""), 220)
        link = safe_url(getattr(entry, "link", ""))
        if not title or not link:
            continue
        if section_id in {"polityka", "ekonomia"} and WEATHER.search(title):
            continue
        summary = clean(getattr(entry, "summary", "") or getattr(entry, "description", "") or title, 430)
        image = entry_image(entry)
        published = parse_entry_time(entry)
        stories.append({
            "title": title,
            "link": link,
            "image": image,
            "source": source,
            "summary": summary or title,
            "published_at": published.isoformat(timespec="seconds") if published else None,
            "published_at_basis": "source" if published else "unavailable",
        })
    return stories, None


def fetch_all(config: list[tuple[str, str, list[tuple[str, str]]]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str], list[str]]:
    labels = {section_id: label for section_id, label, _ in config}
    jobs: dict[Any, str] = {}
    grouped: dict[str, list[dict[str, Any]]] = {section_id: [] for section_id, _, _ in config}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for section_id, _, feeds in config:
            for source, url in feeds:
                jobs[pool.submit(fetch_feed, source, url, section_id)] = section_id
        for future in as_completed(jobs):
            section_id = jobs[future]
            stories, error = future.result()
            grouped[section_id].extend(stories)
            if error:
                errors.append(error)

    image_jobs: dict[Any, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for stories in grouped.values():
            for story in stories[:24]:
                if not story.get("image"):
                    image_jobs[pool.submit(page_image, story["link"])] = story
        for future in as_completed(image_jobs):
            story = image_jobs[future]
            image = future.result()
            if image:
                story["image"] = image

    return grouped, labels, errors


def story_time(story: dict[str, Any]) -> float:
    raw = story.get("published_at")
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def load_previous(lang: str) -> dict[str, Any]:
    path = OUT / f"{lang}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def select_sections(config: list[tuple[str, str, list[tuple[str, str]]]], fetched: dict[str, list[dict[str, Any]]], previous: dict[str, Any], now: datetime) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    selected: dict[str, list[dict[str, Any]]] = {}
    health: dict[str, Any] = {}
    previous_sections = previous.get("sections") if isinstance(previous.get("sections"), dict) else {}
    global_seen: set[str] = set()

    for section_id, _, _ in config:
        candidates = list(fetched.get(section_id) or [])
        candidates.sort(key=story_time, reverse=True)
        fresh: list[dict[str, Any]] = []
        local_seen: set[str] = set()
        for story in candidates:
            identity = normalized_identity(story)
            if not identity or identity in local_seen or identity in global_seen or not story.get("image"):
                continue
            local_seen.add(identity)
            global_seen.add(identity)
            fresh.append(story)
            if len(fresh) >= TARGET:
                break

        carried = 0
        if len(fresh) < TARGET:
            for old in previous_sections.get(section_id, []) if isinstance(previous_sections.get(section_id), list) else []:
                identity = normalized_identity(old)
                if not identity or identity in local_seen or identity in global_seen or not old.get("image"):
                    continue
                try:
                    published = datetime.fromisoformat(str(old.get("published_at") or "").replace("Z", "+00:00"))
                    if published.tzinfo is None:
                        published = published.replace(tzinfo=timezone.utc)
                    if now - published.astimezone(timezone.utc) > timedelta(days=14):
                        continue
                except Exception:
                    continue
                local_seen.add(identity)
                global_seen.add(identity)
                old = dict(old)
                old["carried_forward"] = True
                fresh.append(old)
                carried += 1
                if len(fresh) >= TARGET:
                    break

        if len(fresh) < MIN_SECTION:
            raise RuntimeError(f"section {section_id} has only {len(fresh)} publishable stories; minimum is {MIN_SECTION}")
        selected[section_id] = fresh[:TARGET]
        source_times = [story_time(item) for item in fresh if story_time(item) > 0]
        health[section_id] = {
            "count": len(selected[section_id]),
            "fresh_count": len(selected[section_id]) - carried,
            "carried_count": carried,
            "newest_source_at": datetime.fromtimestamp(max(source_times), tz=timezone.utc).isoformat(timespec="seconds") if source_times else None,
        }
    return selected, health


def round_robin(sections: dict[str, list[dict[str, Any]]], labels: dict[str, str], limit: int = 10) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    longest = max((len(items) for items in sections.values()), default=0)
    for index in range(longest):
        for section_id, items in sections.items():
            if index >= len(items):
                continue
            story = dict(items[index])
            story["category"] = labels[section_id]
            output.append(story)
            if len(output) >= limit:
                return output
    return output


def content_hash(sections: dict[str, list[dict[str, Any]]]) -> str:
    canonical = {key: [normalized_identity(item) for item in value] for key, value in sections.items()}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def compatibility_home(lang: str, home: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    latest = []
    for item in home:
        latest.append({
            "category": item.get("category"),
            "title": item.get("title"),
            "summary": item.get("summary"),
            "details": item.get("summary"),
            "source": item.get("source"),
            "link": item.get("link"),
            "image": item.get("image"),
            "time": "teraz" if lang == "pl" else "now",
            "published_at": item.get("published_at"),
            "source_published_at": item.get("published_at"),
            "full_brief": item.get("summary"),
            "summary_basis": "source_only",
            "comment_generation_status": "source_only",
            "image_policy": "source-linked-external",
        })
    return {"language": lang, "updated_at": now.isoformat(timespec="seconds"), "quality_mode": "source-only-live-v1", "count": len(latest), "latest": latest, "radar": []}


def build_language(lang: str, config: list[tuple[str, str, list[tuple[str, str]]]], marker: str, now: datetime) -> dict[str, Any]:
    fetched, labels, errors = fetch_all(config)
    previous = load_previous(lang)
    sections, health = select_sections(config, fetched, previous, now)
    home = round_robin(sections, labels)
    newest = [story_time(item) for items in sections.values() for item in items if story_time(item) > 0]
    payload = {
        "schema_version": "news-live-v1",
        "language": lang,
        "marker": marker,
        "generated_at": now.isoformat(timespec="seconds"),
        "content_hash": content_hash(sections),
        "labels": labels,
        "sections": sections,
        "home": home,
        "health": {
            "status": "ok" if not errors and all(item["carried_count"] == 0 for item in health.values()) else "degraded",
            "source_errors": errors,
            "sections": health,
            "freshest_source_at": datetime.fromtimestamp(max(newest), tz=timezone.utc).isoformat(timespec="seconds") if newest else None,
        },
    }
    return payload


def publish(marker: str) -> None:
    now = datetime.now(timezone.utc)
    OUT.mkdir(parents=True, exist_ok=True)
    payloads = {"pl": build_language("pl", PL, marker, now), "en": build_language("en", EN, marker, now)}
    for lang, payload in payloads.items():
        (OUT / f"{lang}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        home_path = ROOT / lang / "home_brief.json"
        home_path.write_text(json.dumps(compatibility_home(lang, payload["home"], now), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    status = {
        "schema_version": "news-live-status-v1",
        "marker": marker,
        "generated_at": now.isoformat(timespec="seconds"),
        "languages": {lang: payload["health"] for lang, payload in payloads.items()},
        "content_hashes": {lang: payload["content_hash"] for lang, payload in payloads.items()},
    }
    (OUT / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False))


def validate(max_age_minutes: int = 30) -> None:
    now = datetime.now(timezone.utc)
    for lang, config in (("pl", PL), ("en", EN)):
        path = OUT / f"{lang}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        generated = datetime.fromisoformat(data["generated_at"].replace("Z", "+00:00"))
        if now - generated > timedelta(minutes=max_age_minutes):
            raise RuntimeError(f"{lang} feed is stale: {data['generated_at']}")
        expected = {section_id for section_id, _, _ in config}
        if set(data.get("sections", {})) != expected:
            raise RuntimeError(f"{lang} section set mismatch")
        for section_id, stories in data["sections"].items():
            if len(stories) < MIN_SECTION:
                raise RuntimeError(f"{lang}/{section_id} has only {len(stories)} stories")
            if any(not item.get("title") or not item.get("link") or not item.get("image") for item in stories):
                raise RuntimeError(f"{lang}/{section_id} contains an incomplete story")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--marker", default=f"manual-{int(time.time())}")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--max-age-minutes", type=int, default=30)
    args = parser.parse_args()
    if args.validate:
        validate(args.max_age_minutes)
    else:
        publish(args.marker)


if __name__ == "__main__":
    main()
