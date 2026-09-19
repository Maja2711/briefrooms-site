#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
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
POST_FRESHNESS_SELECTION_VERSION = "post-freshness-editorial-v2"
PRIMARY_BULLETIN_POLICY_VERSION = "en-primary-bulletin-same-day-v1"
PRIMARY_BULLETIN_SOURCES = {
    "federal reserve",
    "federal reserve board",
    "ecb",
    "european central bank",
    "bank of england",
    "bank of japan",
}
PRIMARY_BULLETIN_TITLE = re.compile(
    r"\b(fomc statement|monetary policy statement|monetary policy decision|interest rate decision|"
    r"rate decision|meeting minutes|minutes of .*meeting|consumer expectations survey results|"
    r"survey results)\b",
    re.I,
)
HOMEPAGE_PRIORITY_ORDER = (
    "polityka",
    "geopolityka",
    "ekonomia",
    "ai_technologia",
    "nauka",
    "zdrowie",
    "sport",
)
HOMEPAGE_PRIORITY_INDEX = {lane: index for index, lane in enumerate(HOMEPAGE_PRIORITY_ORDER)}
TARGET_SOURCE_CAP = 2
EMERGENCY_SOURCE_CAP = 3
TARGET_SECTION_CAP = 5
EMERGENCY_SECTION_CAP = 6
TARGET_LANE_CAP = 3
EMERGENCY_LANE_CAP = 4
SPORT_HARD_CAP = 3


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


def _is_primary_bulletin(story: dict[str, Any]) -> bool:
    source = str(story.get("source") or "").strip().casefold()
    title = str(story.get("title") or "").strip()
    return source in PRIMARY_BULLETIN_SOURCES and bool(PRIMARY_BULLETIN_TITLE.search(title))


def _primary_bulletin_is_current(story: dict[str, Any], now: datetime, lang: str | None) -> bool:
    """EN homepage: raw central-bank bulletins are day-of-release content only."""
    if lang != "en" or not _is_primary_bulletin(story):
        return True
    published = _parse_time(story.get("published_at"))
    if published is None:
        return False
    current = now.astimezone(timezone.utc)
    return published.date() == current.date()


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


def _homepage_lane(story: dict[str, Any]) -> str:
    lane = str(story.get("homepage_lane") or "").strip()
    if lane in HOMEPAGE_PRIORITY_INDEX:
        return lane

    category = str(story.get("category") or "").casefold()
    if "geopol" in category or category in {"world news", "europe", "middle east", "asia-pacific"}:
        return "geopolityka"
    if "polit" in category or "polity" in category:
        return "polityka"
    if any(token in category for token in ("ekonom", "biznes", "business", "econom")):
        return "ekonomia"
    if "ai" in category or "sztuczn" in category:
        return "ai_technologia"
    if any(token in category for token in ("zdrow", "health", "medyc")):
        return "zdrowie"
    if "sport" in category:
        return "sport"
    return "nauka"


def _priority_rank(story: dict[str, Any]) -> int:
    return HOMEPAGE_PRIORITY_INDEX.get(_homepage_lane(story), len(HOMEPAGE_PRIORITY_ORDER))


def _mix(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for story in rows:
        if field == "homepage_lane":
            value = _homepage_lane(story)
        else:
            value = str(story.get(field) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _select_editorial_candidates(
    candidates: list[dict[str, Any]],
    limit: int,
    blocked: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Re-apply homepage priority after freshness/image/exposure filtering."""
    blocked_rows = list(blocked or [])
    blocked_ids = {
        base.normalized_identity(story)
        for story in blocked_rows
        if base.normalized_identity(story)
    }
    blocked_sport = sum(1 for story in blocked_rows if _homepage_lane(story) == "sport")

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    source_counts: dict[str, int] = {}
    section_counts: dict[str, int] = {}
    lane_counts: dict[str, int] = {}
    topic_rejected = 0
    cap_rejected = 0

    def try_add(
        story: dict[str, Any],
        source_cap: int,
        section_cap: int,
        lane_cap: int,
    ) -> bool:
        nonlocal topic_rejected, cap_rejected
        identity = base.normalized_identity(story)
        if not identity or identity in blocked_ids or identity in selected_ids:
            return False

        source = str(story.get("source") or "unknown")
        section = str(story.get("category") or "unknown")
        lane = _homepage_lane(story)
        effective_lane_cap = max(0, SPORT_HARD_CAP - blocked_sport) if lane == "sport" else lane_cap
        if (
            source_counts.get(source, 0) >= source_cap
            or section_counts.get(section, 0) >= section_cap
            or lane_counts.get(lane, 0) >= effective_lane_cap
        ):
            cap_rejected += 1
            return False

        if any(homepage_same_topic(story, previous) for previous in blocked_rows + selected):
            topic_rejected += 1
            return False

        copy = dict(story)
        copy["homepage_lane"] = lane
        copy["homepage_priority_rank"] = _priority_rank(copy) + 1
        selected.append(copy)
        selected_ids.add(identity)
        source_counts[source] = source_counts.get(source, 0) + 1
        section_counts[section] = section_counts.get(section, 0) + 1
        lane_counts[lane] = lane_counts.get(lane, 0) + 1
        return True

    # Restore the editorial breadth after freshness filtering.
    for lane in HOMEPAGE_PRIORITY_ORDER:
        if len(selected) >= limit:
            break
        for story in candidates:
            if _homepage_lane(story) != lane:
                continue
            if try_add(story, TARGET_SOURCE_CAP, TARGET_SECTION_CAP, TARGET_LANE_CAP):
                break

    for source_cap, section_cap, lane_cap in (
        (TARGET_SOURCE_CAP, TARGET_SECTION_CAP, TARGET_LANE_CAP),
        (EMERGENCY_SOURCE_CAP, EMERGENCY_SECTION_CAP, EMERGENCY_LANE_CAP),
        (4, 7, 5),
        (limit, limit, limit),
    ):
        for story in candidates:
            if len(selected) >= limit:
                break
            try_add(story, source_cap, section_cap, lane_cap)
        if len(selected) >= limit:
            break

    selected.sort(key=lambda story: (_priority_rank(story), int(story.get("homepage_priority_rank") or 999)))
    return selected, {
        "topic_duplicates_suppressed": topic_rejected,
        "diversity_cap_rejections": cap_rejected,
    }


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
    lang: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current = now.astimezone(timezone.utc)
    candidates = list(_candidate_sequence(payload))
    eligible: list[dict[str, Any]] = []
    expired_count = 0
    source_stale_count = 0
    image_rejected_count = 0
    primary_bulletin_stale_count = 0

    def qualify(story: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        if not _homepage_image_url(story):
            return None, "image"
        if not _source_is_fresh(story, current):
            return None, "source_stale"
        if not _primary_bulletin_is_current(story, current, lang):
            return None, "primary_bulletin_stale"

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
        copy["homepage_lane"] = _homepage_lane(copy)
        copy["homepage_priority_rank"] = _priority_rank(copy) + 1
        copy["homepage_first_seen_at"] = _iso(first_seen)
        copy["homepage_expires_at"] = _iso(first_seen + HOME_MAX_AGE)
        return copy, "ok"

    for story in candidates:
        copy, reason = qualify(story)
        if copy is not None:
            eligible.append(copy)
            continue
        if reason == "image":
            image_rejected_count += 1
        elif reason == "source_stale":
            source_stale_count += 1
        elif reason == "primary_bulletin_stale":
            primary_bulletin_stale_count += 1
        elif reason == "expired":
            expired_count += 1

    selected, selection_diag = _select_editorial_candidates(eligible, HOME_LIMIT)
    reserve, reserve_diag = _select_editorial_candidates(
        eligible,
        HOME_RESERVE_LIMIT,
        blocked=selected,
    )

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
        "post_freshness_selection_version": POST_FRESHNESS_SELECTION_VERSION,
        "primary_bulletin_policy_version": PRIMARY_BULLETIN_POLICY_VERSION,
        "priority_order": list(HOMEPAGE_PRIORITY_ORDER),
        "sport_hard_cap": SPORT_HARD_CAP,
        "requires_https_image": True,
        "image_policy_version": IMAGE_POLICY_VERSION,
    }
    payload.setdefault("health", {})["homepage_freshness"] = {
        "status": "ok" if len(selected) == HOME_LIMIT else "underfilled",
        "version": POLICY_VERSION,
        "image_policy_version": IMAGE_POLICY_VERSION,
        "post_freshness_selection_version": POST_FRESHNESS_SELECTION_VERSION,
        "primary_bulletin_policy_version": PRIMARY_BULLETIN_POLICY_VERSION,
        "published_count": len(selected),
        "target_story_count": HOME_LIMIT,
        "minimum_story_count": HOME_LIMIT,
        "reserve_count": len(reserve),
        "reserve_story_limit": HOME_RESERVE_LIMIT,
        "runtime_backfill_policy": "approved_home_reserve_only",
        "priority_order": list(HOMEPAGE_PRIORITY_ORDER),
        "sport_hard_cap": SPORT_HARD_CAP,
        "lane_mix": _mix(selected, "homepage_lane"),
        "source_mix": _mix(selected, "source"),
        "section_mix": _mix(selected, "category"),
        "reserve_lane_mix": _mix(reserve, "homepage_lane"),
        "expired_exposure_rejected": expired_count,
        "source_stale_rejected": source_stale_count,
        "primary_bulletin_same_day_rejected": primary_bulletin_stale_count,
        "image_rejected": image_rejected_count,
        "topic_duplicate_rejected": selection_diag["topic_duplicates_suppressed"],
        "diversity_cap_rejected": selection_diag["diversity_cap_rejections"],
        "reserve_topic_duplicate_rejected": reserve_diag["topic_duplicates_suppressed"],
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
        payload, _ = enforce_payload(payload, state_lang, now, lang=lang)
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
        if policy.get("post_freshness_selection_version") != POST_FRESHNESS_SELECTION_VERSION:
            raise RuntimeError(f"{lang} post-freshness editorial selection missing")
        if policy.get("primary_bulletin_policy_version") != PRIMARY_BULLETIN_POLICY_VERSION:
            raise RuntimeError(f"{lang} primary bulletin freshness policy missing")
        if policy.get("priority_order") != list(HOMEPAGE_PRIORITY_ORDER):
            raise RuntimeError(f"{lang} homepage priority order missing")
        if policy.get("sport_hard_cap") != SPORT_HARD_CAP:
            raise RuntimeError(f"{lang} homepage sport hard cap missing")

        home = payload.get("home") if isinstance(payload.get("home"), list) else []
        reserve = payload.get("home_reserve") if isinstance(payload.get("home_reserve"), list) else []
        if len(home) != HOME_LIMIT:
            raise RuntimeError(f"{lang} homepage has {len(home)} stories; exactly {HOME_LIMIT} are required")
        if len(reserve) > HOME_RESERVE_LIMIT:
            raise RuntimeError(f"{lang} homepage reserve exceeds {HOME_RESERVE_LIMIT} stories")

        home_lanes = [_homepage_lane(story) for story in home]
        if home_lanes != sorted(home_lanes, key=lambda lane: HOMEPAGE_PRIORITY_INDEX[lane]):
            raise RuntimeError(f"{lang} homepage priority lanes are out of order after freshness filtering")
        if home_lanes.count("sport") > SPORT_HARD_CAP:
            raise RuntimeError(f"{lang} homepage exceeds sport hard cap after freshness filtering")

        identities: set[str] = set()
        approved: list[dict[str, Any]] = []
        for scope, rows in (("homepage", home), ("homepage reserve", reserve)):
            for story in rows:
                if not _primary_bulletin_is_current(story, now, lang):
                    raise RuntimeError(
                        f"{lang} {scope} contains stale same-day primary bulletin: {story.get('title')}"
                    )
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
