#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

try:
    from . import publish_live_news as base
    from .dedupe_home_brief_stories import same_topic as homepage_same_topic
except ImportError:
    import publish_live_news as base
    from dedupe_home_brief_stories import same_topic as homepage_same_topic

ROOT = Path(__file__).resolve().parents[1]
NEWS_DIR = ROOT / "data" / "news"
STATE_PATH = NEWS_DIR / "homepage_exposure.json"
HOME_MAX_AGE = timedelta(days=3)
FUTURE_TOLERANCE = timedelta(minutes=10)
HOME_LIMIT = 12
HOME_RESERVE_LIMIT = 12
POLICY_VERSION = "max-72h-first-display-v1"
IMAGE_POLICY_VERSION = "https-image-required-v1"


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


def _homepage_image_url(story: dict[str, Any]) -> str:
    raw = str(story.get("image") or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        return ""
    if parsed.username or parsed.password:
        return ""
    return raw


def _candidate_sequence(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Use only publisher-approved homepage and reserve candidates."""
    seen: set[str] = set()

    def emit(story: Any) -> dict[str, Any] | None:
        if not isinstance(story, dict):
            return None
        identity = base.normalized_identity(story)
        if not identity or identity in seen:
            return None
        seen.add(identity)
        return story

    for field in ("home", "home_reserve"):
        for story in payload.get(field) or []:
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
    candidates = list(_candidate_sequence(payload))
    selected: list[dict[str, Any]] = []
    reserve: list[dict[str, Any]] = []
    expired_count = 0
    source_stale_count = 0
    image_rejected_count = 0
    topic_duplicate_rejected_count = 0
    reserve_topic_duplicate_rejected_count = 0

    def qualify(story: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        if not _homepage_image_url(story):
            return None, "image"
        if not _source_is_fresh(story, current):
            return None, "source_stale"

        identity = base.normalized_identity(story)
        if not identity:
            return None, "identity"
        exposure = state_lang.get(identity)
        first_seen = _first_seen(story, exposure if isinstance(exposure, dict) else None, current)
        if first_seen is None:
            return None, "timestamp"

        age = current - first_seen
        if age < -FUTURE_TOLERANCE or age > HOME_MAX_AGE:
            return None, "expired"

        if not isinstance(exposure, dict):
            state_lang[identity] = {
                "first_seen_at": _iso(first_seen),
                "source": str(story.get("source") or ""),
                "title": str(story.get("title") or ""),
            }

        copy = dict(story)
        copy["homepage_first_seen_at"] = _iso(first_seen)
        copy["homepage_expires_at"] = _iso(first_seen + HOME_MAX_AGE)
        return copy, "ok"

    for story in candidates:
        if len(selected) >= HOME_LIMIT:
            break
        copy, reason = qualify(story)
        if copy is None:
            if reason == "image":
                image_rejected_count += 1
            elif reason == "source_stale":
                source_stale_count += 1
            elif reason == "expired":
                expired_count += 1
            continue
        if any(homepage_same_topic(copy, previous) for previous in selected):
            topic_duplicate_rejected_count += 1
            continue
        selected.append(copy)

    selected_ids = {
        base.normalized_identity(story)
        for story in selected
        if base.normalized_identity(story)
    }
    approved_topics = list(selected)
    reserve_ids: set[str] = set()

    for story in candidates:
        if len(reserve) >= HOME_RESERVE_LIMIT:
            break
        identity = base.normalized_identity(story)
        if not identity or identity in selected_ids or identity in reserve_ids:
            continue
        copy, reason = qualify(story)
        if copy is None:
            continue
        duplicate = next(
            (previous for previous in approved_topics if homepage_same_topic(copy, previous)),
            None,
        )
        if duplicate is not None:
            reserve_topic_duplicate_rejected_count += 1
            continue
        reserve.append(copy)
        reserve_ids.add(identity)
        approved_topics.append(copy)

    selected.sort(key=lambda story: int(story.get("homepage_priority_rank") or 999))
    reserve.sort(key=lambda story: int(story.get("homepage_priority_rank") or 999))
    payload["home"] = selected
    payload["home_reserve"] = reserve
    payload["homepage_policy"] = {
        "version": POLICY_VERSION,
        "max_display_hours": 72,
        "clock": "first_display_on_briefrooms",
        "also_requires_source_age_hours_lte": 72,
        "target_story_count": HOME_LIMIT,
        "minimum_story_count": HOME_LIMIT,
        "reserve_story_limit": HOME_RESERVE_LIMIT,
        "runtime_backfill_policy": "approved_home_reserve_only",
        "requires_https_image": True,
        "image_policy_version": IMAGE_POLICY_VERSION,
    }
    payload.setdefault("health", {})["homepage_freshness"] = {
        "status": "ok" if len(selected) == HOME_LIMIT else "underfilled",
        "version": POLICY_VERSION,
        "image_policy_version": IMAGE_POLICY_VERSION,
        "published_count": len(selected),
        "target_story_count": HOME_LIMIT,
        "minimum_story_count": HOME_LIMIT,
        "reserve_count": len(reserve),
        "reserve_story_limit": HOME_RESERVE_LIMIT,
        "runtime_backfill_policy": "approved_home_reserve_only",
        "expired_exposure_rejected": expired_count,
        "source_stale_rejected": source_stale_count,
        "image_rejected": image_rejected_count,
        "topic_duplicate_rejected": topic_duplicate_rejected_count,
        "reserve_topic_duplicate_rejected": reserve_topic_duplicate_rejected_count,
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
            raise RuntimeError(f"{lang} homepage twelve-story contract missing")
        if policy.get("requires_https_image") is not True or policy.get("image_policy_version") != IMAGE_POLICY_VERSION:
            raise RuntimeError(f"{lang} homepage HTTPS-image policy missing")

        if policy.get("runtime_backfill_policy") != "approved_home_reserve_only":
            raise RuntimeError(f"{lang} homepage runtime reserve policy missing")
        if int(policy.get("reserve_story_limit") or 0) != HOME_RESERVE_LIMIT:
            raise RuntimeError(f"{lang} homepage reserve limit missing")

        home = payload.get("home") if isinstance(payload.get("home"), list) else []
        reserve = payload.get("home_reserve") if isinstance(payload.get("home_reserve"), list) else []
        if len(home) != HOME_LIMIT:
            raise RuntimeError(f"{lang} homepage has {len(home)} stories; exactly {HOME_LIMIT} are required")
        if len(reserve) > HOME_RESERVE_LIMIT:
            raise RuntimeError(f"{lang} homepage reserve exceeds {HOME_RESERVE_LIMIT} stories")

        identities: set[str] = set()
        approved: list[dict[str, Any]] = []
        for scope, rows in (("homepage", home), ("homepage reserve", reserve)):
            for story in rows:
                identity = base.normalized_identity(story)
                if not identity or identity in identities:
                    raise RuntimeError(f"{lang} {scope} contains a duplicate or invalid story")
                duplicate = next(
                    (previous for previous in approved if homepage_same_topic(story, previous)),
                    None,
                )
                if duplicate is not None:
                    raise RuntimeError(
                        f"{lang} {scope} contains topic duplicate: "
                        f"{duplicate.get('title')} <> {story.get('title')}"
                    )
                identities.add(identity)
                approved.append(story)
                if not _homepage_image_url(story):
                    raise RuntimeError(f"{lang} {scope} contains story without HTTPS image: {story.get('title')}")
                if not _source_is_fresh(story, now):
                    raise RuntimeError(f"{lang} {scope} contains source-stale story: {story.get('title')}")
                first_seen = _parse_time(story.get("homepage_first_seen_at"))
                if first_seen is None or now - first_seen > HOME_MAX_AGE:
                    raise RuntimeError(f"{lang} {scope} contains overexposed story: {story.get('title')}")


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
