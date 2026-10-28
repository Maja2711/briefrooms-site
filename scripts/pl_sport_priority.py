#!/usr/bin/env python3
"""PL-only event-first discovery for monitored Polish athletes.

This is a supplemental acquisition lane, never a publisher or freshness bypass.
Stories must be on a real publisher page with a verified date and image. A
source failure returns a diagnostic, not an exception stopping PL/EN publication.
"""
from __future__ import annotations

import html
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

try:
    from . import publish_live_news as base
    from .news_quality import evaluate_story
except ImportError:
    import publish_live_news as base
    from news_quality import evaluate_story

ROOT = Path(__file__).resolve().parents[1]
WARSAW = ZoneInfo("Europe/Warsaw")
EVENTS_PATH = ROOT / "data/news/pl_sport_priority_events.json"
WATCHLIST_PATH = ROOT / "data/news/polish_sport_watchlist.json"
# These are publisher listings, not general web search or invented schedules.
SPORT_LISTINGS = (
    ("Polskie Radio 24", "https://polskieradio24.pl/", "polskieradio24.pl"),
    ("Interia Sport", "https://sport.interia.pl/tenis", "sport.interia.pl"),
    ("SportoweFakty WP", "https://sportowefakty.wp.pl/tenis", "sportowefakty.wp.pl"),
)
SPORT_FIXTURE_RE = re.compile(
    r"\b(?:mecz\w*|zagra\w*|gra\s+dziś|gra\s+dzisiaj|"
    r"zmierz\w*|startuj\w*|wystąpi\w*|"
    r"ćwierćfinał\w*|półfinał\w*|finał\w*|"
    r"na\s+żywo|relacj\w*|transmisj\w*)\b", re.I
)
ANCHOR_RE = re.compile(r"<a\b([^>]*?)>(.*?)</a\s*>", re.I | re.S)
HREF_RE = re.compile(r"\bhref\s*=\s*(['\"])(.*?)\1", re.I | re.S)
MAX_PER_SOURCE = 4


def _athlete_patterns() -> list[re.Pattern[str]]:
    watchlist = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
    patterns = []
    for player in watchlist.get("athletes", []):
        aliases = [re.escape(str(alias).strip()) for alias in player.get("aliases", []) if alias]
        if aliases:
            patterns.append(re.compile(r"(?<!\w)(?:" + "|".join(aliases) + r")(?!\w)", re.I))
    return patterns


def is_tracked_story(title: str, patterns: list[re.Pattern[str]]) -> bool:
    return bool(SPORT_FIXTURE_RE.search(title) and any(p.search(title) for p in patterns))


def _link_on_host(raw: str, landing: str, host: str) -> str:
    url = base.safe_url(urljoin(landing, html.unescape(raw)))
    if not url:
        return ""
    parsed = urlsplit(url)
    if parsed.hostname not in {host, "www." + host}:
        return ""
    if parsed.path in {"/", "/tenis", "/sport"}:
        return ""
    return url


def discover_links(body: str, landing: str, host: str, patterns: list[re.Pattern[str]]) -> list[tuple[str, str]]:
    results = []
    seen = set()
    for match in ANCHOR_RE.finditer(body[:1800000]):
        href = HREF_RE.search(match.group(1))
        if not href:
            continue
        title = base.clean(html.unescape(re.sub(r"<[^>]+>", " ", match.group(2))), 220)
        if not is_tracked_story(title, patterns):
            continue
        url = _link_on_host(href.group(2), landing, host)
        if not url or url in seen:
            continue
        seen.add(url)
        results.append((title, url))
        if len(results) >= MAX_PER_SOURCE * 3:
            break
    return results


def verified_story(title: str, link: str, source: str, now: datetime, *, published_at: str = "") -> dict | None:
    if not is_tracked_story(title, _athlete_patterns()):
        return None
    if not evaluate_story(title, title).accepted:
        return None
    image, canonical_published = base.page_metadata(link, now)
    published = canonical_published
    if published_at:
        try:
            recorded = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            if recorded.tzinfo is None:
                return None
            # Source timestamp provided by the editor; do not overwrite it with
            # the dynamically fetched page date, which can be updated later.
            published = recorded.astimezone(timezone.utc)
            if canonical_published is not None and abs((canonical_published - published).total_seconds()) > 48 * 3600:
                return None
        except (TypeError, ValueError):
            return None
    if not image or published is None:
        return None
    current = now.astimezone(timezone.utc)
    if published > current + base.FUTURE_TOLERANCE or current - published > timedelta(hours=24):
        return None
    if published.astimezone(WARSAW).date() != current.astimezone(WARSAW).date():
        return None
    return {
        "title": title,
        "summary": title,
        "link": link,
        "image": image,
        "source": source,
        "published_at": published.isoformat(timespec="seconds"),
        "published_at_basis": "verified_publisher_article",
        "image_basis": "publisher_article_metadata",
        "acquisition_basis": "pl_top_athlete_fixture_watch_v1",
        "sport_priority_fixture": True,
    }


def _curated_events(now: datetime) -> list[dict]:
    if not EVENTS_PATH.exists():
        return []
    events = json.loads(EVENTS_PATH.read_text(encoding="utf-8"))
    today = now.astimezone(WARSAW).date().isoformat()
    return [
        event for event in events.get("events", [])
        if event.get("event_date_warsaw") == today
    ]


def collect(now: datetime, *, listings=SPORT_LISTINGS, include_curated=True) -> tuple[list[dict], list[str]]:
    patterns = _athlete_patterns()
    tasks: list[tuple[str, str, str]] = []
    warnings: list[str] = []
    for source, landing, host in listings:
        try:
            body = base.request(landing, timeout=8).text
            for title, link in discover_links(body, landing, host, patterns):
                tasks.append((source, title, link))
        except Exception as exc:
            warnings.append(f"{source}: listing unavailable ({type(exc).__name__})")
    candidates: list[tuple[str, str, str, str]] = []
    if include_curated:
        for event in _curated_events(now):
            link = base.safe_url(event.get("link"))
            host = str(event.get("publisher_host") or "")
            if not link or urlsplit(link).hostname != host:
                warnings.append("Sport curated event: invalid publisher URL")
                continue
            candidates.append((str(event.get("source") or ""), str(event.get("title") or ""),
                               link, str(event.get("published_at") or "")))
    candidates += [(source, title, link, "") for source, title, link in tasks]
    result: list[dict] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(verified_story, title, link, source, now, published_at=published): (source, link)
            for source, title, link, published in candidates[:38]
        }
        for future in as_completed(futures):
            try:
                story = future.result()
            except Exception as exc:
                warnings.append(f"Sport article metadata: {type(exc).__name__}")
                continue
            if story:
                result.append(story)
    # Deterministic cap and identity, one event can be discussed by more than one
    # publisher but the downstream CanonicalEvent dedupe owns topic selection.
    result.sort(key=lambda s: (s["published_at"], s["link"]), reverse=True)
    by_source: dict[str, int] = {}
    unique: list[dict] = []
    seen = set()
    for story in result:
        identity = base.normalized_identity(story)
        if identity in seen or by_source.get(story["source"], 0) >= MAX_PER_SOURCE:
            continue
        seen.add(identity)
        unique.append(story)
        by_source[story["source"]] = by_source.get(story["source"], 0) + 1
    return unique, warnings
