#!/usr/bin/env python3
"""Homepage news policy for BriefRooms PL/EN.

Rules:
- promote politics/geopolitics, economy/business and health on the homepage;
- no homepage news item may be older than 72 hours from its source published_at;
- missing/invalid timestamps are never promoted;
- static fallback must not retain expired cards when live JS is unavailable.

This module does not change the section feeds themselves. It only governs homepage
selection and the static homepage fallback.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
NEWS_DIR = ROOT / "data" / "news"
CACHE_DIR = ROOT / ".cache"
MAX_HOME_AGE = timedelta(days=3)
FUTURE_TOLERANCE = timedelta(minutes=10)
HOME_LIMIT = 10
PRIORITY_ROUNDS = 2
HOME_BRIEFS_START = "<!-- HOME_BRIEFS_START -->"
HOME_BRIEFS_END = "<!-- HOME_BRIEFS_END -->"

LIVE_PRIORITY = {
    "pl": ("polityka", "ekonomia", "zdrowie"),
    "en": ("world-news", "business", "health"),
}

CATEGORY_ALIASES = {
    "pl": {
        "politics": ("polityka", "geopolityka", "polityka kraj"),
        "economy": ("ekonomia", "biznes", "finanse", "rynki"),
        "health": ("zdrowie", "medycyna"),
    },
    "en": {
        "politics": ("politics", "geopolitics", "world news", "world", "europe", "middle east"),
        "economy": ("economy", "business", "finance", "markets"),
        "health": ("health", "medicine"),
    },
}

STRICT_STATUS = "passed_strict_v7"
STRICT_VERSION = 7
STRICT_BASIS = "article_text_ai_reviewed"
STRICT_GENERATION = "ai_review_approved"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_published_at(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def is_home_fresh(item: dict[str, Any], now: datetime) -> bool:
    published = parse_published_at(item.get("published_at") or item.get("source_published_at"))
    if published is None:
        return False
    current = now.astimezone(timezone.utc)
    age = current - published
    return -FUTURE_TOLERANCE <= age <= MAX_HOME_AGE


def _identity(item: dict[str, Any]) -> str:
    link = str(item.get("link") or item.get("source_url") or "").strip()
    if link:
        try:
            parsed = urlsplit(link)
            if parsed.hostname:
                return f"{parsed.hostname.lower()}{parsed.path.rstrip('/').lower()}"
        except ValueError:
            pass
    return re.sub(r"\W+", " ", str(item.get("title") or "").lower()).strip()


def _add_story(
    output: list[dict[str, Any]],
    seen: set[str],
    story: dict[str, Any],
    *,
    category: str | None = None,
) -> None:
    identity = _identity(story)
    if not identity or identity in seen:
        return
    copy = dict(story)
    if category:
        copy["category"] = category
    output.append(copy)
    seen.add(identity)


def select_live_home(payload: dict[str, Any], lang: str, now: datetime) -> list[dict[str, Any]]:
    sections = payload.get("sections") if isinstance(payload.get("sections"), dict) else {}
    labels = payload.get("labels") if isinstance(payload.get("labels"), dict) else {}
    priority_ids = LIVE_PRIORITY[lang]
    output: list[dict[str, Any]] = []
    seen: set[str] = set()

    fresh_sections: dict[str, list[dict[str, Any]]] = {}
    for section_id, stories in sections.items():
        if not isinstance(stories, list):
            continue
        fresh_sections[section_id] = [
            story for story in stories
            if isinstance(story, dict) and is_home_fresh(story, now)
        ]

    # Reserve the leading homepage positions for two rounds of the three strategic
    # topics. This normally yields Politics/Economy/Health twice before other news.
    for index in range(PRIORITY_ROUNDS):
        for section_id in priority_ids:
            stories = fresh_sections.get(section_id) or []
            if index < len(stories):
                _add_story(output, seen, stories[index], category=str(labels.get(section_id) or section_id))
                if len(output) >= HOME_LIMIT:
                    return output

    # Preserve the publisher's remaining editorial ordering, but never re-admit an
    # expired or undated item.
    for story in payload.get("home") or []:
        if not isinstance(story, dict) or not is_home_fresh(story, now):
            continue
        _add_story(output, seen, story)
        if len(output) >= HOME_LIMIT:
            return output

    # Last fallback: newest eligible section stories that were not present in home.
    for section_id, stories in fresh_sections.items():
        for story in stories:
            _add_story(output, seen, story, category=str(labels.get(section_id) or section_id))
            if len(output) >= HOME_LIMIT:
                return output
    return output


def apply_live(now: datetime | None = None) -> None:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    for lang in ("pl", "en"):
        path = NEWS_DIR / f"{lang}.json"
        payload = _load(path)
        payload["home"] = select_live_home(payload, lang, current)
        payload["homepage_policy"] = {
            "version": "homepage-priority-72h-v1",
            "max_age_hours": 72,
            "priority_sections": list(LIVE_PRIORITY[lang]),
            "priority_rounds": PRIORITY_ROUNDS,
            "missing_timestamp_policy": "exclude",
        }
        _write(path, payload)


def snapshot_last_good() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for lang in ("pl", "en"):
        source = ROOT / lang / "home_brief.json"
        target = CACHE_DIR / f"home_brief_{lang}_last_good.json"
        if not source.is_file():
            raise FileNotFoundError(source)
        shutil.copyfile(source, target)


def _normalized_category(value: object) -> str:
    text = str(value or "").casefold()
    text = text.translate(str.maketrans({"ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n", "ó": "o", "ś": "s", "ź": "z", "ż": "z"}))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def category_bucket(item: dict[str, Any], lang: str) -> str | None:
    category = _normalized_category(item.get("category"))
    for bucket, aliases in CATEGORY_ALIASES[lang].items():
        if any(alias in category for alias in aliases):
            return bucket
    return None


def _prioritize_home_brief(items: list[dict[str, Any]], lang: str, now: datetime) -> list[dict[str, Any]]:
    fresh = [item for item in items if isinstance(item, dict) and is_home_fresh(item, now)]
    order = {"politics": 0, "economy": 1, "health": 2}
    return sorted(
        enumerate(fresh),
        key=lambda pair: (
            order.get(category_bucket(pair[1], lang), 9),
            pair[0],
        ),
    ) and [item for _, item in sorted(
        enumerate(fresh),
        key=lambda pair: (order.get(category_bucket(pair[1], lang), 9), pair[0]),
    )]


def apply_home_brief(now: datetime | None = None) -> None:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    for lang in ("pl", "en"):
        path = ROOT / lang / "home_brief.json"
        payload = _load(path)
        for section in ("latest", "radar"):
            items = payload.get(section) if isinstance(payload.get(section), list) else []
            payload[section] = _prioritize_home_brief(items, lang, current)
        payload["count"] = len(payload.get("latest") or []) + len(payload.get("radar") or [])
        payload["homepage_policy"] = {
            "version": "homepage-priority-72h-v1",
            "max_age_hours": 72,
            "priority_topics": ["politics", "economy", "health"],
            "missing_timestamp_policy": "exclude",
        }
        _write(path, payload)


def _strict_home_item(item: dict[str, Any], now: datetime) -> bool:
    return bool(
        is_home_fresh(item, now)
        and item.get("comment_quality_status") == STRICT_STATUS
        and item.get("comment_quality_version") == STRICT_VERSION
        and item.get("summary_basis") == STRICT_BASIS
        and item.get("comment_generation_status") == STRICT_GENERATION
        and str(item.get("permalink") or "").startswith("/")
    )


def enforce_static(now: datetime | None = None) -> None:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    card_re = re.compile(r'<a class="brief-card" href="([^"]+)">.*?</a>', re.DOTALL)
    block_re = re.compile(
        rf"{re.escape(HOME_BRIEFS_START)}(.*?){re.escape(HOME_BRIEFS_END)}",
        re.DOTALL,
    )

    for lang in ("pl", "en"):
        home = _load(ROOT / lang / "home_brief.json")
        eligible = {
            str(item.get("permalink"))
            for section in ("latest", "radar")
            for item in (home.get(section) or [])
            if isinstance(item, dict) and _strict_home_item(item, current)
        }
        page = ROOT / lang / "index.html"
        source = page.read_text(encoding="utf-8")
        match = block_re.search(source)
        if not match:
            raise RuntimeError(f"homepage brief markers missing: {page}")
        kept = []
        for card in card_re.finditer(match.group(1)):
            if card.group(1) in eligible:
                kept.append(card.group(0))
        replacement = f"{HOME_BRIEFS_START}\n" + "\n".join(kept) + f"\n{HOME_BRIEFS_END}"
        source = source[:match.start()] + replacement + source[match.end():]
        page.write_text(source, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-last-good", action="store_true")
    parser.add_argument("--apply-live", action="store_true")
    parser.add_argument("--apply-home-brief", action="store_true")
    parser.add_argument("--enforce-static", action="store_true")
    args = parser.parse_args()
    if not any((args.snapshot_last_good, args.apply_live, args.apply_home_brief, args.enforce_static)):
        parser.error("choose at least one action")
    if args.snapshot_last_good:
        snapshot_last_good()
    if args.apply_live:
        apply_live()
    if args.apply_home_brief:
        apply_home_brief()
    if args.enforce_static:
        enforce_static()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
