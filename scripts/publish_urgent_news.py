#!/usr/bin/env python3
"""Read-only-to-trading 15-minute breaking-news collector for the separate public rail.

Does not write canonical PL/EN feeds, HTML, homepage selections or trading evidence.
Uses source timestamps, existing quality/provenance/event intelligence, and an
immutable first-display clock maintained across scheduled runs.
"""
from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from scripts import publish_live_news as base
from scripts.news_quality import evaluate_story
from scripts.news_source_architecture import source_profile
from scripts.news_source_expansion_v3 import deduplicate_dispatches
from scripts.news_event_intelligence_v4 import cluster_events, same_event

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/news/urgent.json"
STATE = ROOT / "data/news/urgent_state.json"
SCHEMA = "briefrooms-urgent-news-v1"
MAX_CARDS = 3
MAX_SOURCE_AGE = timedelta(minutes=75)
MAX_DISPLAY = timedelta(minutes=90)
MAX_FEED_AGE = timedelta(minutes=45)
FUTURE_TOLERANCE = timedelta(minutes=2)

# Small, named and explicitly allowed RSS pool. A 15-minute cycle must not
# perform the large, full-publication crawl or request licensed wire endpoints.
FEEDS = {
    "pl": [
        ("polityka", "TVN24", "https://tvn24.pl/najnowsze.xml"),
        ("polityka", "RMF24", "https://www.rmf24.pl/fakty/swiat/feed"),
        ("polityka", "Polsat News", "https://www.polsatnews.pl/rss/polska.xml"),
        ("ekonomia", "Bankier.pl", "https://www.bankier.pl/rss/wiadomosci.xml"),
        ("ekonomia", "Business Insider Polska", "https://businessinsider.com.pl/.feed"),
        ("zdrowie", "Serwis Zdrowie PAP", "https://zdrowie.pap.pl/rss.xml"),
    ],
    "en": [
        ("world-news", "BBC News", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("world-news", "The Guardian", "https://www.theguardian.com/world/rss"),
        ("middle-east", "Al Jazeera English", "https://www.aljazeera.com/xml/rss/all.xml"),
        ("business", "BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
        ("business", "ECB", "https://www.ecb.europa.eu/rss/press.html"),
        ("business", "Federal Reserve", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
    ],
}

# Explicit, outcome/event-oriented gates. "Breaking" by itself is not evidence
# that the underlying story is substantive or actually happened.
CRITICAL_EVENT = re.compile(
    r"\b(?:atak\w*|uderz\w*|eksplozj\w*|wybuch\w*|zamach\w*|inwazj\w*|"
    r"trzęsieni\w*\s+ziemi|powódź|katastrof\w*|ewakuacj\w*|"
    r"attack\w*|strik\w*|struck|explosion\w*|bombing\w*|invasion|"
    r"earthquake|flood\w*|disaster|evacuat\w*|ceasefire|rozejm\w*)\b", re.I)
CRITICAL_CONTEXT = re.compile(
    r"\b(?:rosj\w*|ukrain\w*|iran\w*|izrael\w*|nato|"
    r"państw\w*|miast\w*|szpital\w*|lotnisk\w*|energetyk\w*|"
    r"russi\w*|ukrain\w*|iran\w*|israel\w*|nato|"
    r"civilian\w*|hospital\w*|airport\w*|power\s+grid|capital|"
    r"government|minister\w*|people\s+killed|ofiary\w*)\b", re.I)
CENTRAL_BANK = re.compile(
    r"\b(?:nbp|rpp|ecb|fed|fomc|federal reserve|european central bank|"
    r"bank\s+centraln\w*|central\s+bank)\b", re.I)
RATE_ACTION = re.compile(
    r"\b(?:obniż\w*|podwyż\w*|zmieni\w*|utrzyma\w*|"
    r"cuts?|raises?|hikes?|lowers?|holds?|reduc\w*|"
    r"stopy\s+procentowe|interest\s+rates?|policy\s+rate\w*)\b", re.I)
SYSTEMIC_DISRUPTION = re.compile(
    r"\b(?:cyberatak\w*|awari\w*|bankructw\w*|niewypłacalno\w*|"
    r"cyberattack\w*|systemic\s+outage|bankrupt\w*|"
    r"critical\s+infrastructure|infrastruktur\w*\s+krytyczn\w*)\b", re.I)
SYSTEMIC_CONTEXT = re.compile(
    r"\b(?:bank\w*|giełd\w*|elektrowni\w*|energetyczn\w*|"
    r"szpital\w*|system\w*|payment\w*|stock\s+exchange|"
    r"power\s+grid|hospital\w*|network\w*|bank\w*|market\w*)\b", re.I)
SPECULATION = re.compile(
    r"\b(?:może|mogą|możliwe|planuje|zapowiada|rozważa|prognoz\w*|"
    r"could|might|may\s+(?:cut|raise|attack)|plans?\s+to|"
    r"expected\s+to|forecast\w*|analysis|opinion|live\s+blog)\b", re.I)

def utc(raw: Any) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if value.tzinfo is None:
            return None
        return value.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        return None

def material_event(story: dict[str, Any]) -> bool:
    title = str(story.get("title") or "")
    if SPECULATION.search(title):
        return False
    return bool(
        (CRITICAL_EVENT.search(title) and CRITICAL_CONTEXT.search(title))
        or (CENTRAL_BANK.search(title) and RATE_ACTION.search(title))
        or (SYSTEMIC_DISRUPTION.search(title) and SYSTEMIC_CONTEXT.search(title))
    )

def timely(story: dict[str, Any], now: datetime) -> bool:
    published = utc(story.get("published_at"))
    if not published:
        return False
    age = now - published
    return -FUTURE_TOLERANCE <= age <= MAX_SOURCE_AGE

def sufficiently_verified(story: dict[str, Any]) -> bool:
    if story.get("claim_consistency_status") == "disputed":
        return False
    roots = story.get("evidence_roots") or []
    if len(roots) != int(story.get("independent_evidence_paths") or 0):
        return False
    first_party = (
        source_profile(story.get("source")).tier == "primary"
        and story.get("provenance_role") == "original"
        and story.get("origin_source") == source_profile(story.get("source")).canonical_name
    )
    independent = (
        len(roots) >= 2
        and float(story.get("claim_adjusted_corroboration_score") or 0) >= 40
    )
    return bool(first_party or independent)

def clean_state(data: Any, now: datetime) -> dict[str, str]:
    seen = data.get("seen") if isinstance(data, dict) else {}
    if not isinstance(seen, dict):
        return {}
    result = {}
    for identity, raw in seen.items():
        date = utc(raw)
        if isinstance(identity, str) and date and timedelta(0) <= now - date <= timedelta(hours=24):
            result[identity] = date.isoformat(timespec="seconds")
    return result

def canonical_stories(lang: str) -> list[dict[str, Any]]:
    try:
        data = json.loads((ROOT / "data/news" / f"{lang}.json").read_text(encoding="utf-8"))
        rows = [*data.get("home", [])]
        for group in data.get("sections", {}).values():
            if isinstance(group, list):
                rows.extend(group)
        return [row for row in rows if isinstance(row, dict)]
    except (OSError, ValueError, TypeError):
        return []

def fresh_candidate(story: dict[str, Any], now: datetime) -> bool:
    link = base.safe_url(story.get("link"))
    parsed = urlsplit(link)
    return bool(
        link and parsed.hostname and parsed.scheme == "https"
        and timely(story, now)
        and material_event(story)
        and evaluate_story(story.get("title"), story.get("summary")).accepted
    )

def build_lang(
    lang: str,
    fetched: list[dict[str, Any]],
    previous_seen: dict[str, str],
    canonical: list[dict[str, Any]],
    now: datetime,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    candidates = [row for row in fetched if fresh_candidate(row, now)]
    # Existing lineage clustering prevents Reuters copies being counted as
    # independent confirmations; no new ungoverned scoring layer is introduced.
    dispatches, _ = deduplicate_dispatches(candidates)
    events, _ = cluster_events(dispatches)
    valid = [
        e for e in events
        if material_event(e) and sufficiently_verified(e)
        and timely(e, now)
        and not any(same_event(e, old) for old in canonical if old.get("title"))
    ]
    valid.sort(
        key=lambda row: (
            float(row.get("claim_adjusted_corroboration_score") or 0),
            base.story_time(row),
        ),
        reverse=True,
    )
    selected = []
    seen = dict(previous_seen)
    for row in valid:
        if len(selected) == MAX_CARDS:
            break
        event_id = str(row.get("canonical_event_id") or "")
        if not event_id:
            continue
        first_seen = utc(seen.get(event_id)) or now
        published = utc(row.get("published_at"))
        if not published:
            continue
        expires = min(first_seen + MAX_DISPLAY, published + MAX_DISPLAY)
        if now >= expires:
            continue
        image = str(row.get("image") or "")
        if not image.startswith("https://"):
            image = base.page_image(str(row.get("link") or ""))
        if not image.startswith("https://"):
            continue
        seen.setdefault(event_id, first_seen.isoformat(timespec="seconds"))
        selected.append({
            "canonical_event_id": event_id,
            "title": row["title"],
            "summary": row.get("summary") or "",
            "link": row["link"],
            "image": image,
            "source": row["source"],
            "published_at": published.isoformat(timespec="seconds"),
            "news_first_seen_at": first_seen.isoformat(timespec="seconds"),
            "news_expires_at": expires.isoformat(timespec="seconds"),
            "provenance_role": row.get("provenance_role"),
            "origin_source": row.get("origin_source"),
            "independent_evidence_paths": row.get("independent_evidence_paths"),
            "evidence_roots": row.get("evidence_roots"),
            "claim_consistency_status": row.get("claim_consistency_status"),
            "claim_adjusted_corroboration_score": row.get("claim_adjusted_corroboration_score"),
        })
    return selected, seen

def fetch_sources(lang: str, now: datetime) -> tuple[list[dict[str, Any]], list[str]]:
    jobs = {}
    rows = []
    errors = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for section, source, url in FEEDS[lang]:
            jobs[pool.submit(base.fetch_feed, source, url, section, now)] = source
        for job in as_completed(jobs):
            stories, error = job.result()
            rows.extend(stories)
            if error:
                errors.append(error)
    return rows, errors

def assemble(
    fetched: dict[str, list[dict[str, Any]]],
    errors: dict[str, list[str]],
    previous: dict[str, Any],
    now: datetime,
    canonical: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    old = previous.get("languages", {}) if isinstance(previous, dict) else {}
    result = {}
    new_state = {"schema_version": "urgent-seen-v1", "languages": {}}
    for lang in ("pl", "en"):
        prior = clean_state(old.get(lang, {}), now)
        rows, seen = build_lang(lang, fetched.get(lang, []), prior, canonical.get(lang, []), now)
        new_state["languages"][lang] = {"seen": seen}
        result[lang] = {
            "status": "degraded" if errors.get(lang) else "ok",
            "source_error_count": len(errors.get(lang, [])),
            "stories": rows,
        }
    payload = {
        "schema_version": SCHEMA,
        "checked_at": now.isoformat(timespec="seconds"),
        "max_checked_age_minutes": 45,
        "languages": result,
    }
    return payload, new_state

def validate(data: dict[str, Any], now: datetime, max_age: timedelta = MAX_FEED_AGE) -> None:
    if data.get("schema_version") != SCHEMA:
        raise ValueError("urgent schema mismatch")
    checked = utc(data.get("checked_at"))
    if not checked or now - checked > max_age or checked - now > FUTURE_TOLERANCE:
        raise ValueError("urgent collector heartbeat is stale or future-dated")
    for lang in ("pl", "en"):
        lane = data.get("languages", {}).get(lang)
        if not isinstance(lane, dict) or not isinstance(lane.get("stories"), list):
            raise ValueError(f"{lang} missing lane")
        rows = lane["stories"]
        if len(rows) > MAX_CARDS:
            raise ValueError(f"{lang} exceeded urgent hard cap")
        ids = set()
        for row in rows:
            event_id = row.get("canonical_event_id")
            published = utc(row.get("published_at"))
            first = utc(row.get("news_first_seen_at"))
            expires = utc(row.get("news_expires_at"))
            if not event_id or event_id in ids or not published or not first or not expires:
                raise ValueError(f"{lang} invalid event identity or clock")
            ids.add(event_id)
            if not (-FUTURE_TOLERANCE <= now - published <= MAX_SOURCE_AGE):
                raise ValueError(f"{lang} stale source")
            if first > now + FUTURE_TOLERANCE or expires > min(first, published) + MAX_DISPLAY or expires <= now:
                raise ValueError(f"{lang} invalid exposure")
            if not str(row.get("link") or "").startswith("https://") or not str(row.get("image") or "").startswith("https://"):
                raise ValueError(f"{lang} invalid source/image URL")
            if row.get("claim_consistency_status") == "disputed":
                raise ValueError(f"{lang} disputed evidence")

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if args.validate:
        validate(json.loads(OUT.read_text(encoding="utf-8")), now)
        return
    try:
        prior = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prior = {}
    fetched, errors = {}, {}
    for lang in ("pl", "en"):
        fetched[lang], errors[lang] = fetch_sources(lang, now)
    payload, state = assemble(
        fetched, errors, prior, now,
        {lang: canonical_stories(lang) for lang in ("pl", "en")},
    )
    validate(payload, now)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"checked_at": payload["checked_at"],
                      "counts": {lang: len(payload["languages"][lang]["stories"]) for lang in ("pl", "en")},
                      "source_error_counts": {lang: len(errors[lang]) for lang in ("pl", "en")}}))

if __name__ == "__main__":
    main()
