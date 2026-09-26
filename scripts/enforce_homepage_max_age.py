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
NEWS_MAX_AGE = timedelta(hours=24)
# Compatibility alias for older tests/importers. The policy is now global, not homepage-only.
HOME_MAX_AGE = NEWS_MAX_AGE
FUTURE_TOLERANCE = timedelta(minutes=10)
HOME_LIMIT = 12
HOME_RESERVE_LIMIT = 12
POLICY_VERSION = "max-24h-public-news-display-v1"
EXPOSURE_SCHEMA_VERSION = "public-news-exposure-v2"
LEGACY_EXPOSURE_SCHEMA_VERSION = "homepage-exposure-v1"
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
PL_SECTION_MINIMUMS = {"polityka": 9, "ekonomia": 9, "zdrowie": 6, "nauka": 6, "sport": 9}
PL_AI_CRYPTO_RE = re.compile(
    r"\b(?:AI|sztuczn\w*\s+inteligencj\w*|artificial\s+intelligence|OpenAI|ChatGPT|"
    r"bitcoin|BTC|ethereum|ETH|kryptowalut\w*|crypto|blockchain|stablecoin\w*)\b",
    re.IGNORECASE,
)


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
    return -FUTURE_TOLERANCE <= age <= NEWS_MAX_AGE


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
    """Apply the hard 24h public-display contract to every news surface.

    The same identity clock is shared by section pages, homepage and homepage reserve.
    Freshness wins over card-count targets: an expired story is removed rather than
    retained to keep a section visually full.
    """
    current = now.astimezone(timezone.utc)
    raw_home_candidates = list(_candidate_sequence(payload))
    qualification_cache: dict[str, tuple[dict[str, Any] | None, str]] = {}
    counted_rejections: set[str] = set()
    rejection_counts = {
        "expired": 0,
        "source_stale": 0,
        "image": 0,
        "primary_bulletin_stale": 0,
    }

    def qualify(story: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        identity = base.normalized_identity(story)
        if identity and identity in qualification_cache:
            cached, reason = qualification_cache[identity]
            if not isinstance(cached, dict):
                return None, reason
            merged = dict(story)
            merged["news_first_seen_at"] = cached.get("news_first_seen_at")
            merged["news_expires_at"] = cached.get("news_expires_at")
            return merged, reason

        if not _homepage_image_url(story):
            result = (None, "image")
        elif not _source_is_fresh(story, current):
            result = (None, "source_stale")
        elif not _primary_bulletin_is_current(story, current, lang):
            result = (None, "primary_bulletin_stale")
        elif not identity:
            result = (None, "identity")
        else:
            exposure = state_lang.get(identity)
            first_seen = _first_seen(
                story,
                exposure if isinstance(exposure, dict) else None,
                current,
            )
            if first_seen is None:
                result = (None, "timestamp")
            else:
                age = current - first_seen
                if age < -FUTURE_TOLERANCE or age > NEWS_MAX_AGE:
                    result = (None, "expired")
                else:
                    if not isinstance(exposure, dict):
                        state_lang[identity] = {
                            "first_seen_at": _iso(first_seen),
                            "source": str(story.get("source") or ""),
                            "title": str(story.get("title") or ""),
                        }
                    copy = dict(story)
                    copy["news_first_seen_at"] = _iso(first_seen)
                    copy["news_expires_at"] = _iso(first_seen + NEWS_MAX_AGE)
                    result = (copy, "ok")

        if identity:
            cached_story = dict(result[0]) if isinstance(result[0], dict) else None
            qualification_cache[identity] = (cached_story, result[1])
        return result

    def record_rejection(story: dict[str, Any], reason: str) -> None:
        if reason not in rejection_counts:
            return
        identity = base.normalized_identity(story) or f"anonymous:{id(story)}"
        if identity in counted_rejections:
            return
        counted_rejections.add(identity)
        rejection_counts[reason] += 1

    # Section pages are public surfaces too. Filter them before static rendering.
    raw_sections = payload.get("sections") if isinstance(payload.get("sections"), dict) else {}
    filtered_sections: dict[str, list[dict[str, Any]]] = {}
    for section_id, rows in raw_sections.items():
        fresh_rows: list[dict[str, Any]] = []
        if isinstance(rows, list):
            for story in rows:
                if not isinstance(story, dict):
                    continue
                copy, reason = qualify(story)
                if copy is not None:
                    # Final post-freshness event/topic gate. Canonical event IDs are
                    # available here, so this catches duplicate coverage even when
                    # publishers use different URLs/headlines.
                    duplicate = False
                    for previous_story in fresh_rows:
                        if not homepage_same_topic(copy, previous_story):
                            continue
                        current_numbers = set(re.findall(r"\b\d+\b", str(copy.get("title") or "")))
                        previous_numbers = set(re.findall(r"\b\d+\b", str(previous_story.get("title") or "")))
                        if current_numbers and previous_numbers and current_numbers.isdisjoint(previous_numbers):
                            continue
                        duplicate = True
                        break
                    if not duplicate:
                        fresh_rows.append(copy)
                else:
                    record_rejection(story, reason)
        filtered_sections[str(section_id)] = fresh_rows

    payload["sections"] = filtered_sections

    eligible: list[dict[str, Any]] = []
    for story in raw_home_candidates:
        copy, reason = qualify(story)
        if copy is not None:
            eligible.append(copy)
        else:
            record_rejection(story, reason)

    selected, selection_diag = _select_editorial_candidates(eligible, HOME_LIMIT)
    reserve, reserve_diag = _select_editorial_candidates(
        eligible,
        HOME_RESERVE_LIMIT,
        blocked=selected,
    )

    # Keep legacy homepage metadata for the existing browser consumers while the
    # canonical clock is now news_first_seen_at/news_expires_at across all surfaces.
    for story in selected + reserve:
        first_seen = str(story.get("news_first_seen_at") or "")
        expires = str(story.get("news_expires_at") or "")
        if first_seen:
            story["homepage_first_seen_at"] = first_seen
        if expires:
            story["homepage_expires_at"] = expires

    payload["home"] = selected
    payload["home_reserve"] = reserve
    payload["homepage_policy"] = {
        "version": POLICY_VERSION,
        "scope": "all_public_news_surfaces",
        "max_display_hours": 24,
        "clock": "first_known_briefrooms_display_with_source_age_ceiling",
        "also_requires_source_age_hours_lte": 24,
        "target_story_count": HOME_LIMIT,
        "minimum_story_count": 0,
        "underfill_allowed_when_needed_for_freshness": True,
        "reserve_story_limit": HOME_RESERVE_LIMIT,
        "runtime_backfill_policy": "approved_home_reserve_only",
        "post_freshness_selection_version": POST_FRESHNESS_SELECTION_VERSION,
        "primary_bulletin_policy_version": PRIMARY_BULLETIN_POLICY_VERSION,
        "priority_order": list(HOMEPAGE_PRIORITY_ORDER),
        "sport_hard_cap": SPORT_HARD_CAP,
        "requires_https_image": True,
        "image_policy_version": IMAGE_POLICY_VERSION,
    }
    payload.setdefault("health", {})["public_news_freshness"] = {
        "status": "ok",
        "version": POLICY_VERSION,
        "scope": "all_public_news_surfaces",
        "max_display_hours": 24,
        "section_published_counts": {
            section_id: len(rows)
            for section_id, rows in filtered_sections.items()
        },
        "expired_exposure_rejected": rejection_counts["expired"],
        "source_stale_rejected": rejection_counts["source_stale"],
        "primary_bulletin_same_day_rejected": rejection_counts["primary_bulletin_stale"],
        "image_rejected": rejection_counts["image"],
    }
    payload["health"]["homepage_freshness"] = {
        "status": "ok" if len(selected) == HOME_LIMIT else "underfilled",
        "version": POLICY_VERSION,
        "scope": "all_public_news_surfaces",
        "max_display_hours": 24,
        "image_policy_version": IMAGE_POLICY_VERSION,
        "post_freshness_selection_version": POST_FRESHNESS_SELECTION_VERSION,
        "primary_bulletin_policy_version": PRIMARY_BULLETIN_POLICY_VERSION,
        "published_count": len(selected),
        "target_story_count": HOME_LIMIT,
        "minimum_story_count": 0,
        "underfill_allowed_when_needed_for_freshness": True,
        "reserve_count": len(reserve),
        "reserve_story_limit": HOME_RESERVE_LIMIT,
        "runtime_backfill_policy": "approved_home_reserve_only",
        "priority_order": list(HOMEPAGE_PRIORITY_ORDER),
        "sport_hard_cap": SPORT_HARD_CAP,
        "lane_mix": _mix(selected, "homepage_lane"),
        "source_mix": _mix(selected, "source"),
        "section_mix": _mix(selected, "category"),
        "reserve_lane_mix": _mix(reserve, "homepage_lane"),
        "expired_exposure_rejected": rejection_counts["expired"],
        "source_stale_rejected": rejection_counts["source_stale"],
        "primary_bulletin_same_day_rejected": rejection_counts["primary_bulletin_stale"],
        "image_rejected": rejection_counts["image"],
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

    schema = value.get("schema_version")
    if schema == LEGACY_EXPOSURE_SCHEMA_VERSION:
        # Preserve existing homepage first-seen clocks so the 24h policy cannot
        # reset old stories simply because the scope is being widened to sections.
        value["schema_version"] = EXPOSURE_SCHEMA_VERSION
        value["migrated_from"] = LEGACY_EXPOSURE_SCHEMA_VERSION
    elif schema != EXPOSURE_SCHEMA_VERSION:
        value = {"schema_version": EXPOSURE_SCHEMA_VERSION, "languages": {}}

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
        if lang == "pl":
            sections = payload.get("sections") if isinstance(payload.get("sections"), dict) else {}
            for section_id, minimum in PL_SECTION_MINIMUMS.items():
                rows = sections.get(section_id, []) if isinstance(sections.get(section_id), list) else []
                if len(rows) < minimum:
                    raise RuntimeError(
                        f"PL section {section_id} below required fresh unique minimum after 24h gate: "
                        f"{len(rows)}/{minimum}"
                    )
            economy_rows = sections.get("ekonomia", [])
            if not any(
                PL_AI_CRYPTO_RE.search(
                    " ".join(str(story.get(key) or "") for key in ("title", "summary"))
                )
                for story in economy_rows
            ):
                raise RuntimeError("PL ekonomia missing required fresh AI/crypto story after 24h gate")
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_files() -> None:
    now = datetime.now(timezone.utc)
    for lang in ("pl", "en"):
        payload = json.loads((NEWS_DIR / f"{lang}.json").read_text(encoding="utf-8"))
        policy = payload.get("homepage_policy") or {}
        if policy.get("version") != POLICY_VERSION:
            raise RuntimeError(f"{lang} public-news 24-hour exposure policy missing")
        if policy.get("scope") != "all_public_news_surfaces":
            raise RuntimeError(f"{lang} public-news freshness scope is incomplete")
        if int(policy.get("max_display_hours") or 0) != 24:
            raise RuntimeError(f"{lang} public-news display cap is not 24 hours")
        if int(policy.get("also_requires_source_age_hours_lte") or 0) != 24:
            raise RuntimeError(f"{lang} public-news source-age cap is not 24 hours")
        if policy.get("target_story_count") != HOME_LIMIT:
            raise RuntimeError(f"{lang} homepage twelve-story target missing")
        if policy.get("underfill_allowed_when_needed_for_freshness") is not True:
            raise RuntimeError(f"{lang} freshness-over-card-count fallback missing")
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
        if len(home) > HOME_LIMIT:
            raise RuntimeError(f"{lang} homepage exceeds {HOME_LIMIT} stories")
        if len(reserve) > HOME_RESERVE_LIMIT:
            raise RuntimeError(f"{lang} homepage reserve exceeds {HOME_RESERVE_LIMIT} stories")

        home_lanes = [_homepage_lane(story) for story in home]
        if home_lanes != sorted(home_lanes, key=lambda lane: HOMEPAGE_PRIORITY_INDEX[lane]):
            raise RuntimeError(f"{lang} homepage priority lanes are out of order after freshness filtering")
        if home_lanes.count("sport") > SPORT_HARD_CAP:
            raise RuntimeError(f"{lang} homepage exceeds sport hard cap after freshness filtering")

        identities: set[str] = set()
        approved: list[dict[str, Any]] = []

        def validate_story(scope: str, story: dict[str, Any], *, dedupe: bool) -> None:
            if not _primary_bulletin_is_current(story, now, lang):
                raise RuntimeError(
                    f"{lang} {scope} contains stale same-day primary bulletin: {story.get('title')}"
                )
            identity = base.normalized_identity(story)
            if not identity:
                raise RuntimeError(f"{lang} {scope} contains an invalid story")
            if dedupe:
                if identity in identities:
                    raise RuntimeError(f"{lang} {scope} contains a duplicate story")
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
            first_seen = _parse_time(story.get("news_first_seen_at"))
            expires = _parse_time(story.get("news_expires_at"))
            if first_seen is None or expires is None:
                raise RuntimeError(f"{lang} {scope} is missing 24h exposure metadata: {story.get('title')}")
            if expires != first_seen + NEWS_MAX_AGE:
                raise RuntimeError(f"{lang} {scope} has invalid 24h expiry: {story.get('title')}")
            if now - first_seen > NEWS_MAX_AGE:
                raise RuntimeError(f"{lang} {scope} contains overexposed story: {story.get('title')}")

        for section_id, rows in (payload.get("sections") or {}).items():
            if not isinstance(rows, list):
                raise RuntimeError(f"{lang}/{section_id} section payload is invalid")
            if len(rows) > base.TARGET:
                raise RuntimeError(f"{lang}/{section_id} exceeds section target {base.TARGET}")
            for story in rows:
                validate_story(f"section {section_id}", story, dedupe=False)

        for scope, rows in (("homepage", home), ("homepage reserve", reserve)):
            for story in rows:
                validate_story(scope, story, dedupe=True)


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
