#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from . import publish_live_news as base
    from . import publish_live_news_filtered as filtered
    from . import publish_curated_news as curated
    from .news_claim_intelligence import (
        CLAIM_INTELLIGENCE_VERSION,
        CONTRADICTION_DETECTION_VERSION,
        public_claim_policy,
    )
    from .news_event_intelligence_v4 import (
        CANONICAL_EVENT_VERSION,
        EVIDENCE_MODEL_VERSION,
        EVENT_INTELLIGENCE_VERSION,
        cluster_events,
        corroboration_bonus,
        public_event_policy,
    )
    from .dedupe_home_brief_stories import same_topic as homepage_same_topic
    from .news_source_expansion_v3 import (
        DISPATCH_DEDUPE_VERSION,
        ORIGIN_DETECTION_VERSION,
        SOURCE_EXPANSION_VERSION,
        annotate_story_provenance,
        configured_wire_feeds,
        deduplicate_dispatches,
        extend_config_with_wire_adapters,
        origin_mix,
        originality_bonus,
        public_expansion_policy,
    )
except ImportError:
    import publish_live_news as base
    import publish_live_news_filtered as filtered
    import publish_curated_news as curated
    from news_claim_intelligence import (
        CLAIM_INTELLIGENCE_VERSION,
        CONTRADICTION_DETECTION_VERSION,
        public_claim_policy,
    )
    from news_event_intelligence_v4 import (
        CANONICAL_EVENT_VERSION,
        EVIDENCE_MODEL_VERSION,
        EVENT_INTELLIGENCE_VERSION,
        cluster_events,
        corroboration_bonus,
        public_event_policy,
    )
    from dedupe_home_brief_stories import same_topic as homepage_same_topic
    from news_source_expansion_v3 import (
        DISPATCH_DEDUPE_VERSION,
        ORIGIN_DETECTION_VERSION,
        SOURCE_EXPANSION_VERSION,
        annotate_story_provenance,
        configured_wire_feeds,
        deduplicate_dispatches,
        extend_config_with_wire_adapters,
        origin_mix,
        originality_bonus,
        public_expansion_policy,
    )

ROOT = Path(__file__).resolve().parents[1]

HOMEPAGE_EDITORIAL_SELECTION_VERSION = "homepage-editorial-v5"
HOMEPAGE_LIMIT = 12
HOMEPAGE_TARGET_SOURCE_CAP = 2
HOMEPAGE_EMERGENCY_SOURCE_CAP = 3
HOMEPAGE_TARGET_SECTION_CAP = 5
HOMEPAGE_EMERGENCY_SECTION_CAP = 6
HOMEPAGE_TARGET_LANE_CAP = 3
HOMEPAGE_EMERGENCY_LANE_CAP = 4
HOMEPAGE_SPORT_HARD_CAP = 3
HOMEPAGE_RESERVE_LIMIT = 12
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
_LAST_HOMEPAGE_DIAGNOSTICS: dict[str, Any] = {}
_LAST_HOMEPAGE_RESERVE: list[dict[str, Any]] = []

_original_fetch_all = base.fetch_all
_original_select_sections = base.select_sections
_original_build_language = base.build_language
_original_validate = base.validate
_original_editorial_value_score = filtered.editorial_value_score

WIRE_FEEDS, WIRE_ADAPTER_DIAGNOSTICS = configured_wire_feeds()
base.PL = extend_config_with_wire_adapters(base.PL, "pl", WIRE_FEEDS)
base.EN = extend_config_with_wire_adapters(base.EN, "en", WIRE_FEEDS)
CONFIGURED_WIRE_SOURCES = {
    source
    for source, status in WIRE_ADAPTER_DIAGNOSTICS.items()
    if status.get("configured")
}


def source_expansion_editorial_score(story: dict[str, Any], section_id: str, now: Any) -> float:
    return (
        _original_editorial_value_score(story, section_id, now)
        + originality_bonus(story)
        + corroboration_bonus(story)
    )


filtered.editorial_value_score = source_expansion_editorial_score


def _annotate_grouped(grouped: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    for section_id, stories in list(grouped.items()):
        grouped[section_id] = [annotate_story_provenance(story) for story in stories]
    return grouped


def fetch_all(config: Any, now: Any):
    grouped, labels, errors = _original_fetch_all(config, now)
    return _annotate_grouped(grouped), labels, errors


def _prepare_previous(previous: dict[str, Any]) -> dict[str, Any]:
    copy = dict(previous)
    sections = copy.get("sections") if isinstance(copy.get("sections"), dict) else {}
    new_sections: dict[str, Any] = {}
    for section_id, stories in sections.items():
        if isinstance(stories, list):
            deduped, _ = deduplicate_dispatches(stories)
            canonical, _ = cluster_events(deduped)
            new_sections[section_id] = canonical
        else:
            new_sections[section_id] = stories
    copy["sections"] = new_sections
    return copy


def select_sections(
    config: list[tuple[str, str, list[tuple[str, str]]]],
    fetched: dict[str, list[dict[str, Any]]],
    previous: dict[str, Any],
    now: Any,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    canonical: dict[str, list[dict[str, Any]]] = {}
    dispatch_diagnostics: dict[str, dict[str, Any]] = {}
    event_diagnostics: dict[str, dict[str, Any]] = {}

    for section_id, _, _ in config:
        deduped, dispatch_diag = deduplicate_dispatches(fetched.get(section_id) or [])
        events, event_diag = cluster_events(deduped)
        canonical[section_id] = events
        dispatch_diagnostics[section_id] = dispatch_diag
        event_diagnostics[section_id] = event_diag

    selected, health = _original_select_sections(
        config,
        canonical,
        _prepare_previous(previous),
        now,
    )

    for section_id, rows in selected.items():
        section_health = health.setdefault(section_id, {})
        dispatch_diag = dispatch_diagnostics.get(section_id, {})
        event_diag = event_diagnostics.get(section_id, {})
        mean_score = (
            sum(float(row.get("corroboration_score") or 0.0) for row in rows) / len(rows)
            if rows else 0.0
        )
        mean_adjusted_score = (
            sum(float(row.get("claim_adjusted_corroboration_score") or 0.0) for row in rows) / len(rows)
            if rows else 0.0
        )
        section_health.update(
            {
                "source_expansion_version": SOURCE_EXPANSION_VERSION,
                "origin_detection_version": ORIGIN_DETECTION_VERSION,
                "dispatch_dedupe_version": DISPATCH_DEDUPE_VERSION,
                "origin_mix": origin_mix(rows),
                "raw_candidate_count": int(dispatch_diag.get("raw_count") or 0),
                "deduplicated_candidate_count": int(dispatch_diag.get("deduplicated_count") or 0),
                "republications_suppressed": int(dispatch_diag.get("suppressed_count") or 0),
                "wire_dispatch_clusters": int(dispatch_diag.get("wire_dispatch_clusters") or 0),
                "direct_original_wins": int(dispatch_diag.get("direct_original_wins") or 0),
                "event_intelligence_version": EVENT_INTELLIGENCE_VERSION,
                "canonical_event_version": CANONICAL_EVENT_VERSION,
                "evidence_model_version": EVIDENCE_MODEL_VERSION,
                "claim_intelligence_version": CLAIM_INTELLIGENCE_VERSION,
                "contradiction_detection_version": CONTRADICTION_DETECTION_VERSION,
                "canonical_event_candidate_count": int(event_diag.get("canonical_event_count") or 0),
                "event_duplicates_suppressed": int(event_diag.get("event_duplicates_suppressed") or 0),
                "multi_source_event_candidates": int(event_diag.get("multi_source_events") or 0),
                "corroborated_event_candidates": int(event_diag.get("corroborated_events") or 0),
                "disputed_event_candidates": int(event_diag.get("disputed_events") or 0),
                "consistent_claim_event_candidates": int(event_diag.get("consistent_claim_events") or 0),
                "evolving_claim_event_candidates": int(event_diag.get("evolving_claim_events") or 0),
                "selected_corroborated_events": sum(
                    1 for row in rows if row.get("corroboration_status") in {"corroborated", "strong"}
                ),
                "selected_single_lineage_events": sum(
                    1 for row in rows if row.get("corroboration_status") == "single_lineage"
                ),
                "selected_disputed_events": sum(
                    1 for row in rows if row.get("claim_consistency_status") == "disputed"
                ),
                "selected_consistent_claim_events": sum(
                    1 for row in rows if row.get("claim_consistency_status") == "consistent"
                ),
                "selected_evolving_claim_events": sum(
                    1 for row in rows if row.get("claim_consistency_status") == "evolving_update"
                ),
                "mean_corroboration_score": round(mean_score, 1),
                "mean_claim_adjusted_corroboration_score": round(mean_adjusted_score, 1),
            }
        )
    return selected, health


_AI_PATTERN = re.compile(
    r"\b(ai|sztuczn\w* inteligenc\w*|artificial intelligence|chatgpt|openai|anthropic|claude|gemini|copilot|"
    r"llm|large language model\w*|model\w* język\w*|uczeni\w* maszyn\w*|machine learning|deepmind|"
    r"neural\w*|sieci neur\w*|generative ai|genai|mistral|perplexity|xai|grok|hugging face|"
    r"cerebras|groq|nvidia\w* ai|cuda\w*)\b",
    re.I,
)
_AI_ECONOMIC_PATTERN = re.compile(
    r"\b(wycena|wartość spółk|wartosc spol|przychod\w*|zysk\w*|strat\w*|wynik\w* finans\w*|"
    r"akcj\w*|giełd\w*|gield\w*|inwestycj\w*|finansowan\w*|rund\w* finans\w*|pozyska\w* kapitał|"
    r"pozyska\w* kapital|przeję\w*|przejec\w*|fuzj\w*|ipo|obligac\w*|kapitalizac\w*)\b",
    re.I,
)
_GEO_ENTITY_PATTERN = re.compile(
    r"\b(ukrain\w*|rosj\w*|usa|stan\w* zjednoczon\w*|chin\w*|nato|unia europejsk\w*|ue|iran\w*|"
    r"izrael\w*|gaz\w*|palestyn\w*|bliski\w* wsch\w*|białoru\w*|bialoru\w*|niemc\w*|francj\w*|"
    r"wielk\w* brytani\w*|turcj\w*|tajwan\w*|kore\w*|trump\w*|putin\w*|zelensk\w*)\b",
    re.I,
)
_GEO_ACTION_PATTERN = re.compile(
    r"\b(wojn\w*|atak\w*|inwaz\w*|sankcj\w*|sojusz\w*|dyplomac\w*|rozejm\w*|pokoj\w*|pokój\w*|"
    r"bezpiecze\w*|wojsk\w*|arm\w*|rakiet\w*|dron\w*|nuklearn\w*|granica\w*|eskalac\w*|"
    r"konflikt\w*|obron\w*|szczyt\w*|traktat\w*|porozumien\w*|ultimatum\w*)\b",
    re.I,
)
_HEALTH_PATTERN = re.compile(
    r"\b(zdrow\w*|medyc\w*|chorob\w*|serc\w*|kardiolog\w*|lek\w*|terapi\w*|pacjent\w*|"
    r"nowotwor\w*|rak\w*|cukrzyc\w*|szczep\w*|stres\w*|depres\w*|sen\w*|dieta\w*|otył\w*|"
    r"otyl\w*|infekcj\w*|wirus\w*|bakter\w*|klinicz\w*|hospital\w*|health\w*|medical\w*|"
    r"disease\w*|heart\w*|cancer\w*|diabetes\w*|vaccine\w*|therapy\w*|patient\w*|stress\w*)\b",
    re.I,
)


def homepage_lane(story: dict[str, Any], section_id: str) -> str:
    """Classify homepage meaning, not merely the publisher's source section."""
    text = " ".join(
        str(story.get(key) or "")
        for key in ("title", "summary", "ai_summary")
    )
    section = str(section_id or "").casefold()

    # Hard domain boundaries first.
    if section == "sport":
        return "sport"
    if section in {"zdrowie", "health"}:
        return "zdrowie"
    if section in {"world-news", "asia-pacific", "europe", "middle-east"}:
        return "geopolityka"

    # Strong cross-border/security semantics override a broad source desk.
    # This catches e.g. a geopolitical agreement published in a business feed.
    if (
        section in {"polityka", "ekonomia", "business", "nauka", "science"}
        and _GEO_ACTION_PATTERN.search(text)
        and _GEO_ENTITY_PATTERN.search(text)
    ):
        return "geopolityka"

    if section == "polityka":
        return "polityka"
    if section in {"ekonomia", "business"}:
        # Product/model/research AI stories should not be trapped in a broad
        # business desk. Financing, valuation, earnings and M&A remain economy.
        if _AI_PATTERN.search(text) and not _AI_ECONOMIC_PATTERN.search(text):
            return "ai_technologia"
        return "ekonomia"
    if section in {"nauka", "science"}:
        if _AI_PATTERN.search(text):
            return "ai_technologia"
        if _HEALTH_PATTERN.search(text):
            return "zdrowie"
        return "nauka"
    return "nauka"


def _homepage_priority_rank(story: dict[str, Any]) -> int:
    return HOMEPAGE_PRIORITY_INDEX.get(
        str(story.get("_homepage_lane") or story.get("homepage_lane") or "nauka"),
        len(HOMEPAGE_PRIORITY_ORDER),
    )


def _homepage_summary_bonus(story: dict[str, Any]) -> float:
    title = " ".join(str(story.get("title") or "").casefold().split())
    summary = " ".join(str(story.get("summary") or story.get("ai_summary") or "").casefold().split())
    if not summary or summary == title:
        return -18.0
    if len(summary) >= 160:
        return 7.0
    if len(summary) >= 80:
        return 4.0
    return 1.0


def _homepage_score(
    story: dict[str, Any],
    section_id: str,
    now: datetime,
    sport_support: dict[str, int] | None = None,
) -> float:
    score = source_expansion_editorial_score(story, section_id, now) + _homepage_summary_bonus(story)
    if section_id == "sport":
        try:
            hot = filtered.sport_hot_score(story, now, sport_support or {})
            if (
                hot >= filtered.HOME_HOT_SPORT_THRESHOLD
                and filtered._is_live_sport(story)
                and not filtered._is_future_sport(story)
            ):
                score += 250.0
        except Exception:
            pass
    return score


def homepage_ranked_select(
    sections: dict[str, list[dict[str, Any]]],
    labels: dict[str, str],
    limit: int = HOMEPAGE_LIMIT,
    now: datetime | None = None,
    blocked_stories: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Choose homepage stories by editorial priority, quality and diversity."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    ranked: list[tuple[int, float, float, str, dict[str, Any]]] = []
    try:
        sport_support = filtered._sport_entity_support(sections.get("sport") or [])
    except Exception:
        sport_support = {}
    for section_id, rows in sections.items():
        for raw in rows:
            if not isinstance(raw, dict) or not raw.get("image"):
                continue
            story = dict(raw)
            story["category"] = labels.get(section_id, section_id)
            story["_homepage_section_id"] = section_id
            story["_homepage_lane"] = homepage_lane(story, section_id)
            story["_homepage_score"] = _homepage_score(story, section_id, current, sport_support)
            story["_homepage_story_time"] = float(base.story_time(story) or 0.0)
            ranked.append((
                _homepage_priority_rank(story),
                float(story["_homepage_score"]),
                float(story["_homepage_story_time"]),
                base.normalized_identity(story),
                story,
            ))
    ranked.sort(key=lambda row: (row[0], -row[1], -row[2], row[3]))

    blocked = list(blocked_stories or [])
    blocked_sport_count = sum(
        1 for story in blocked
        if str(story.get("homepage_lane") or story.get("_homepage_lane") or "") == "sport"
    )
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
        if not identity or identity in selected_ids:
            return False
        source = str(story.get("source") or "unknown")
        section_id = str(story.get("_homepage_section_id") or "")
        lane = str(story.get("_homepage_lane") or "nauka")
        effective_lane_cap = (
            max(0, HOMEPAGE_SPORT_HARD_CAP - blocked_sport_count)
            if lane == "sport"
            else lane_cap
        )
        if (
            source_counts.get(source, 0) >= source_cap
            or section_counts.get(section_id, 0) >= section_cap
            or lane_counts.get(lane, 0) >= effective_lane_cap
        ):
            cap_rejected += 1
            return False
        if any(homepage_same_topic(story, previous) for previous in blocked + selected):
            topic_rejected += 1
            return False
        selected.append(story)
        selected_ids.add(identity)
        source_counts[source] = source_counts.get(source, 0) + 1
        section_counts[section_id] = section_counts.get(section_id, 0) + 1
        lane_counts[lane] = lane_counts.get(lane, 0) + 1
        return True

    # First secure broad editorial coverage: the best available story from each
    # priority lane, in the exact order requested by the homepage policy.
    for lane in HOMEPAGE_PRIORITY_ORDER:
        if len(selected) >= limit:
            break
        for _, _, _, _, story in ranked:
            if story.get("_homepage_lane") != lane:
                continue
            if try_add(
                story,
                HOMEPAGE_TARGET_SOURCE_CAP,
                HOMEPAGE_TARGET_SECTION_CAP,
                HOMEPAGE_TARGET_LANE_CAP,
            ):
                break

    passes = (
        (HOMEPAGE_TARGET_SOURCE_CAP, HOMEPAGE_TARGET_SECTION_CAP, HOMEPAGE_TARGET_LANE_CAP),
        (HOMEPAGE_EMERGENCY_SOURCE_CAP, HOMEPAGE_EMERGENCY_SECTION_CAP, HOMEPAGE_EMERGENCY_LANE_CAP),
        (4, 7, 5),
        (limit, limit, limit),
    )
    for source_cap, section_cap, lane_cap in passes:
        for _, _, _, _, story in ranked:
            if len(selected) >= limit:
                break
            try_add(story, source_cap, section_cap, lane_cap)
        if len(selected) >= limit:
            break

    selected.sort(
        key=lambda story: (
            _homepage_priority_rank(story),
            -float(story.get("_homepage_score") or 0.0),
            -float(story.get("_homepage_story_time") or 0.0),
            base.normalized_identity(story),
        )
    )

    public = []
    for story in selected[:limit]:
        copy = dict(story)
        copy["homepage_lane"] = str(copy.pop("_homepage_lane", "nauka"))
        copy["homepage_priority_rank"] = _homepage_priority_rank(story) + 1
        copy.pop("_homepage_section_id", None)
        copy.pop("_homepage_score", None)
        copy.pop("_homepage_story_time", None)
        public.append(copy)

    diagnostics = {
        "status": "ok" if len(public) == limit else "underfilled",
        "version": HOMEPAGE_EDITORIAL_SELECTION_VERSION,
        "mode": "priority_lane_coverage_then_editorial_score_topic_dedupe_and_diversity",
        "priority_order": list(HOMEPAGE_PRIORITY_ORDER),
        "sport_hard_cap": HOMEPAGE_SPORT_HARD_CAP,
        "target_story_count": limit,
        "published_count": len(public),
        "target_max_cards_per_source": HOMEPAGE_TARGET_SOURCE_CAP,
        "emergency_max_cards_per_source": HOMEPAGE_EMERGENCY_SOURCE_CAP,
        "target_max_cards_per_section": HOMEPAGE_TARGET_SECTION_CAP,
        "emergency_max_cards_per_section": HOMEPAGE_EMERGENCY_SECTION_CAP,
        "target_max_cards_per_lane": HOMEPAGE_TARGET_LANE_CAP,
        "emergency_max_cards_per_lane": HOMEPAGE_EMERGENCY_LANE_CAP,
        "topic_duplicates_suppressed": topic_rejected,
        "diversity_cap_rejections": cap_rejected,
        "source_mix": source_counts,
        "section_mix": section_counts,
        "lane_mix": lane_counts,
    }
    return public, diagnostics


def _mix(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def homepage_round_robin(
    sections: dict[str, list[dict[str, Any]]],
    labels: dict[str, str],
    limit: int = HOMEPAGE_LIMIT,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    global _LAST_HOMEPAGE_DIAGNOSTICS, _LAST_HOMEPAGE_RESERVE
    selected, diagnostics = homepage_ranked_select(
        sections,
        labels,
        limit,
        now=now,
    )
    reserve, reserve_diagnostics = homepage_ranked_select(
        sections,
        labels,
        HOMEPAGE_RESERVE_LIMIT,
        now=now,
        blocked_stories=selected,
    )
    _LAST_HOMEPAGE_RESERVE = [dict(story) for story in reserve]

    diagnostics.update(
        {
            "target_story_count": limit,
            "published_count": len(selected),
            "reserve_limit": HOMEPAGE_RESERVE_LIMIT,
            "reserve_count": len(reserve),
            "source_mix": _mix(selected, "source"),
            "section_mix": _mix(selected, "category"),
            "reserve_source_mix": _mix(reserve, "source"),
            "reserve_section_mix": _mix(reserve, "category"),
            "reserve_lane_mix": _mix(reserve, "homepage_lane"),
            "reserve_topic_duplicates_suppressed": reserve_diagnostics.get("topic_duplicates_suppressed", 0),
            "runtime_backfill_policy": "approved_home_reserve_only",
        }
    )
    diagnostics["status"] = "ok" if len(selected) == limit else "underfilled"
    _LAST_HOMEPAGE_DIAGNOSTICS = diagnostics
    return selected


def _wire_adapter_errors(errors: list[Any]) -> list[str]:
    result: list[str] = []
    for raw in errors:
        error = str(raw)
        if any(error.startswith(f"{source}:") for source in CONFIGURED_WIRE_SOURCES):
            result.append(error)
    return result


def build_language(lang: str, config: Any, marker: str, now: Any) -> dict[str, Any]:
    payload = _original_build_language(lang, config, marker, now)
    payload["home_reserve"] = [dict(story) for story in _LAST_HOMEPAGE_RESERVE]
    health = payload.setdefault("health", {})
    all_errors = list(health.get("source_errors") or [])
    wire_errors = _wire_adapter_errors(all_errors)

    existing_required = list(health.get("required_source_errors") or [])
    non_wire_required = [error for error in existing_required if error not in wire_errors]
    existing_optional = list(health.get("optional_source_errors") or [])
    for error in wire_errors:
        if error not in existing_optional:
            existing_optional.append(error)
    health["required_source_errors"] = non_wire_required
    health["optional_source_errors"] = existing_optional
    health["wire_adapter_errors"] = wire_errors

    sections_health = health.get("sections") if isinstance(health.get("sections"), dict) else {}
    no_carry = all(
        int(item.get("carried_count") or 0) == 0
        for item in sections_health.values()
        if isinstance(item, dict)
    )
    if not non_wire_required and no_carry:
        health["status"] = "ok"

    expansion_status = "degraded" if wire_errors else "active"
    health["source_expansion"] = {
        "status": expansion_status,
        **public_expansion_policy(WIRE_ADAPTER_DIAGNOSTICS),
        "configured_wire_sources": sorted(CONFIGURED_WIRE_SOURCES),
        "wire_adapter_error_count": len(wire_errors),
    }
    health["event_intelligence"] = {
        "status": "active",
        **public_event_policy(),
    }
    health["claim_intelligence"] = {
        "status": "active",
        **public_claim_policy(),
    }
    health["homepage_editorial_selection"] = dict(_LAST_HOMEPAGE_DIAGNOSTICS)
    selection = health.setdefault("editorial_selection", {})
    selection["mode"] = (
        "canonical_event_then_claim_consistency_adjusted_corroboration_origin_authority_public_impact_recency_and_publisher_diversity"
    )
    return payload


def validate(max_age_minutes: int = 30) -> None:
    _original_validate(max_age_minutes)
    for lang in ("pl", "en"):
        path = ROOT / "data" / "news" / f"{lang}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        expansion = (payload.get("health") or {}).get("source_expansion") or {}
        if expansion.get("version") != SOURCE_EXPANSION_VERSION:
            raise RuntimeError(f"{lang} source expansion v3 missing or outdated")
        if expansion.get("origin_detection_version") != ORIGIN_DETECTION_VERSION:
            raise RuntimeError(f"{lang} origin detection policy missing or outdated")
        if expansion.get("dedupe_version") != DISPATCH_DEDUPE_VERSION:
            raise RuntimeError(f"{lang} dispatch dedupe policy missing or outdated")

        event_policy = (payload.get("health") or {}).get("event_intelligence") or {}
        if event_policy.get("version") != EVENT_INTELLIGENCE_VERSION:
            raise RuntimeError(f"{lang} Event Intelligence v4 missing or outdated")
        if event_policy.get("evidence_model_version") != EVIDENCE_MODEL_VERSION:
            raise RuntimeError(f"{lang} evidence/corroboration model missing or outdated")

        claim_policy = (payload.get("health") or {}).get("claim_intelligence") or {}
        if claim_policy.get("version") != CLAIM_INTELLIGENCE_VERSION:
            raise RuntimeError(f"{lang} Claim Intelligence missing or outdated")
        if claim_policy.get("contradiction_detection_version") != CONTRADICTION_DETECTION_VERSION:
            raise RuntimeError(f"{lang} contradiction detection missing or outdated")

        homepage_selection = (payload.get("health") or {}).get("homepage_editorial_selection") or {}
        if homepage_selection.get("version") != HOMEPAGE_EDITORIAL_SELECTION_VERSION:
            raise RuntimeError(f"{lang} homepage editorial selection missing or outdated")
        if homepage_selection.get("runtime_backfill_policy") != "approved_home_reserve_only":
            raise RuntimeError(f"{lang} homepage runtime reserve policy missing")
        home = payload.get("home") if isinstance(payload.get("home"), list) else []
        reserve = payload.get("home_reserve") if isinstance(payload.get("home_reserve"), list) else []
        if len(home) != HOMEPAGE_LIMIT:
            raise RuntimeError(f"{lang} homepage must contain exactly {HOMEPAGE_LIMIT} stories")
        if len(reserve) > HOMEPAGE_RESERVE_LIMIT:
            raise RuntimeError(f"{lang} homepage reserve exceeds {HOMEPAGE_RESERVE_LIMIT} stories")
        lane_order = {lane: index for index, lane in enumerate(HOMEPAGE_PRIORITY_ORDER)}
        home_lanes = [str(story.get("homepage_lane") or "") for story in home]
        if any(lane not in lane_order for lane in home_lanes):
            raise RuntimeError(f"{lang} homepage contains invalid editorial lane")
        if home_lanes != sorted(home_lanes, key=lambda lane: lane_order[lane]):
            raise RuntimeError(f"{lang} homepage priority lanes are out of order")
        if home_lanes.count("sport") > HOMEPAGE_SPORT_HARD_CAP:
            raise RuntimeError(f"{lang} homepage exceeds sport hard cap")
        approved = list(home)
        for story in reserve:
            duplicate = next(
                (previous for previous in approved if homepage_same_topic(story, previous)),
                None,
            )
            if duplicate is not None:
                raise RuntimeError(
                    f"{lang} homepage reserve topic duplicate: "
                    f"{duplicate.get('title')} <> {story.get('title')}"
                )
            approved.append(story)
        for index, story in enumerate(home):
            for previous in home[:index]:
                if homepage_same_topic(story, previous):
                    raise RuntimeError(
                        f"{lang} homepage topic duplicate: {previous.get('title')} <> {story.get('title')}"
                    )

        sections = payload.get("sections") if isinstance(payload.get("sections"), dict) else {}
        for section_id, stories in sections.items():
            if not isinstance(stories, list):
                continue
            event_ids: set[str] = set()
            for story in stories:
                if story.get("publisher_source") != story.get("source"):
                    raise RuntimeError(
                        f"{lang}/{section_id} publisher provenance mismatch: {story.get('title')}"
                    )
                if "origin_source" not in story or "provenance_role" not in story:
                    raise RuntimeError(
                        f"{lang}/{section_id} story missing provenance: {story.get('title')}"
                    )
                if story.get("dispatch_dedupe_version") != DISPATCH_DEDUPE_VERSION:
                    raise RuntimeError(
                        f"{lang}/{section_id} story missing dispatch dedupe metadata: {story.get('title')}"
                    )
                event_id = str(story.get("canonical_event_id") or "")
                if not event_id or event_id in event_ids:
                    raise RuntimeError(f"{lang}/{section_id} invalid or duplicate canonical event")
                event_ids.add(event_id)
                if story.get("event_intelligence_version") != EVENT_INTELLIGENCE_VERSION:
                    raise RuntimeError(f"{lang}/{section_id} story missing Event Intelligence v4 metadata")
                if story.get("evidence_model_version") != EVIDENCE_MODEL_VERSION:
                    raise RuntimeError(f"{lang}/{section_id} story missing evidence metadata")
                if story.get("claim_intelligence_version") != CLAIM_INTELLIGENCE_VERSION:
                    raise RuntimeError(f"{lang}/{section_id} story missing Claim Intelligence metadata")
                if story.get("contradiction_detection_version") != CONTRADICTION_DETECTION_VERSION:
                    raise RuntimeError(f"{lang}/{section_id} story missing contradiction metadata")
                score = float(story.get("corroboration_score") or 0.0)
                adjusted_score = float(story.get("claim_adjusted_corroboration_score") or 0.0)
                contradiction_score = float(story.get("contradiction_score") or 0.0)
                if not 0.0 <= score <= 100.0:
                    raise RuntimeError(f"{lang}/{section_id} invalid corroboration score {score}")
                if not 0.0 <= adjusted_score <= 100.0:
                    raise RuntimeError(f"{lang}/{section_id} invalid adjusted corroboration score {adjusted_score}")
                if not 0.0 <= contradiction_score <= 100.0:
                    raise RuntimeError(f"{lang}/{section_id} invalid contradiction score {contradiction_score}")
                consistency = str(story.get("claim_consistency_status") or "")
                if consistency not in {"insufficient_evidence", "consistent", "evolving_update", "disputed"}:
                    raise RuntimeError(f"{lang}/{section_id} invalid claim consistency status {consistency}")
                if consistency == "disputed" and int(story.get("disputed_claim_count") or 0) < 1:
                    raise RuntimeError(f"{lang}/{section_id} disputed event lacks disputed claim")
                groups = story.get("claim_groups") if isinstance(story.get("claim_groups"), list) else None
                if groups is None:
                    raise RuntimeError(f"{lang}/{section_id} claim groups missing")
                roots = story.get("evidence_roots") if isinstance(story.get("evidence_roots"), list) else []
                if int(story.get("independent_evidence_paths") or 0) != len(roots):
                    raise RuntimeError(f"{lang}/{section_id} evidence path mismatch")


base.fetch_all = fetch_all
base.select_sections = select_sections
base.round_robin = homepage_round_robin
base.build_language = build_language
base.validate = validate


if __name__ == "__main__":
    base.main()
