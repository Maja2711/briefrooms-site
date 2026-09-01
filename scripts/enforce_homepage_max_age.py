#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from . import publish_live_news as base
except ImportError:
    import publish_live_news as base

ROOT = Path(__file__).resolve().parents[1]
NEWS_DIR = ROOT / "data" / "news"
STATE_PATH = NEWS_DIR / "homepage_exposure.json"
HOME_MAX_AGE = timedelta(days=3)
FUTURE_TOLERANCE = timedelta(minutes=10)
HOME_LIMIT = 10
POLICY_VERSION = "max-72h-first-display-v1"


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _source_is_fresh(story: dict[str, Any], now: datetime) -> bool:
    published = _parse_time(story.get("published_at"))
    if published is None:
        return False
    age = now - published
    return -FUTURE_TOLERANCE <= age <= HOME_MAX_AGE


def _candidate_sequence(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Keep the publisher's homepage order, then use section rows as replacements."""
    seen: set[str] = set()

    def emit(story: Any) -> dict[str, Any] | None:
        if not isinstance(story, dict):
            return None
        identity = base.normalized_identity(story)
        if not identity or identity in seen:
            return None
        seen.add(identity)
        return story

    for story in payload.get("home") or []:
        accepted = emit(story)
        if accepted is not None:
            yield accepted

    sections = payload.get("sections") if isinstance(payload.get("sections"), dict) else {}
    labels = payload.get("labels") if isinstance(payload.get("labels"), dict) else {}
    max_rows = max((len(rows) for rows in sections.values() if isinstance(rows, list)), default=0)
    for index in range(max_rows):
        for section_id, rows in sections.items():
            if not isinstance(rows, list) or index >= len(rows):
                continue
            story = dict(rows[index]) if isinstance(rows[index], dict) else None
            if story is None:
                continue
            story.setdefault("category", labels.get(section_id, section_id))
            accepted = emit(story)
            if accepted is not None:
                yield accepted


def _first_seen(
    story: dict[str, Any],
    exposure: dict[str, Any] | None,
    now: datetime,
) -> datetime | None:
    if isinstance(exposure, dict):
        stored = _parse_time(exposure.get("first_seen_at"))
        if stored is not None:
            return stored

    # Migration/new-story default: publication time is conservative. It can only
    # shorten homepage exposure; it can never extend it beyond the 72-hour cap.
    published = _parse_time(story.get("published_at"))
    if published is None:
        return None
    return min(published, now)


def enforce_payload(
    payload: dict[str, Any],
    state_lang: dict[str, Any],
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current = now.astimezone(timezone.utc)
    selected: list[dict[str, Any]] = []
    expired_count = 0
    source_stale_count = 0

    for story in _candidate_sequence(payload):
        if len(selected) >= HOME_LIMIT:
            break
        if not _source_is_fresh(story, current):
            source_stale_count += 1
            continue

        identity = base.normalized_identity(story)
        if not identity:
            continue
        exposure = state_lang.get(identity)
        first_seen = _first_seen(story, exposure if isinstance(exposure, dict) else None, current)
        if first_seen is None:
            continue

        age = current - first_seen
        if age < -FUTURE_TOLERANCE or age > HOME_MAX_AGE:
            expired_count += 1
            continue

        if not isinstance(exposure, dict):
            state_lang[identity] = {
                "first_seen_at": _iso(first_seen),
                "source": str(story.get("source") or ""),
                "title": str(story.get("title") or ""),
            }

        copy = dict(story)
        copy["homepage_first_seen_at"] = _iso(first_seen)
        copy["homepage_expires_at"] = _iso(first_seen + HOME_MAX_AGE)
        selected.append(copy)

    payload["home"] = selected
    payload["homepage_policy"] = {
        "version": POLICY_VERSION,
        "max_display_hours": 72,
        "clock": "first_display_on_briefrooms",
        "also_requires_source_age_hours_lte": 72,
        "target_story_count": HOME_LIMIT,
        "minimum_story_count": HOME_LIMIT,
    }
    payload.setdefault("health", {})["homepage_freshness"] = {
        "status": "ok" if len(selected) == HOME_LIMIT else "underfilled",
        "version": POLICY_VERSION,
        "published_count": len(selected),
        "target_story_count": HOME_LIMIT,
        "minimum_story_count": HOME_LIMIT,
        "expired_exposure_rejected": expired_count,
        "source_stale_rejected": source_stale_count,
    }
    return payload, state_lang


def _load_state() -> dict[str, Any]:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    if value.get("schema_version") != "homepage-exposure-v1":
        value = {"schema_version": "homepage-exposure-v1", "languages": {}}
    languages = value.get("languages")
    if not isinstance(languages, dict):
        value["languages"] = {}
    return value


def enforce_files() -> None:
    now = datetime.now(timezone.utc)
    state = _load_state()
    languages = state.setdefault("languages", {})

    for lang in ("pl", "en"):
        path = NEWS_DIR / f"{lang}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("language") != lang or payload.get("schema_version") != "news-live-v2":
            raise RuntimeError(f"invalid {lang} live news payload")
        state_lang = languages.get(lang)
        if not isinstance(state_lang, dict):
            state_lang = {}
            languages[lang] = state_lang
        payload, _ = enforce_payload(payload, state_lang, now)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_files() -> None:
    now = datetime.now(timezone.utc)
    for lang in ("pl", "en"):
        payload = json.loads((NEWS_DIR / f"{lang}.json").read_text(encoding="utf-8"))
        policy = payload.get("homepage_policy") or {}
        if policy.get("version") != POLICY_VERSION:
            raise RuntimeError(f"{lang} homepage 72-hour exposure policy missing")
        if policy.get("target_story_count") != HOME_LIMIT or policy.get("minimum_story_count") != HOME_LIMIT:
            raise RuntimeError(f"{lang} homepage ten-story contract missing")
        home = payload.get("home") if isinstance(payload.get("home"), list) else []
        if len(home) != HOME_LIMIT:
            raise RuntimeError(f"{lang} homepage has {len(home)} stories; exactly {HOME_LIMIT} are required")
        identities: set[str] = set()
        for story in home:
            identity = base.normalized_identity(story)
            if not identity or identity in identities:
                raise RuntimeError(f"{lang} homepage contains a duplicate or invalid story")
            identities.add(identity)
            if not _source_is_fresh(story, now):
                raise RuntimeError(f"{lang} homepage contains source-stale story: {story.get('title')}")
            first_seen = _parse_time(story.get("homepage_first_seen_at"))
            if first_seen is None or now - first_seen > HOME_MAX_AGE:
                raise RuntimeError(f"{lang} homepage contains overexposed story: {story.get('title')}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    if args.validate:
        validate_files()
    else:
        enforce_files()


if __name__ == "__main__":
    main()
