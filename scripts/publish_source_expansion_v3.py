#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from . import publish_live_news as base
    from . import publish_live_news_filtered as filtered
    from . import publish_curated_news as curated
    from .news_event_intelligence_v4 import (
        CANONICAL_EVENT_VERSION,
        EVIDENCE_MODEL_VERSION,
        EVENT_INTELLIGENCE_VERSION,
        cluster_events,
        corroboration_bonus,
        public_event_policy,
    )
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
    from news_event_intelligence_v4 import (
        CANONICAL_EVENT_VERSION,
        EVIDENCE_MODEL_VERSION,
        EVENT_INTELLIGENCE_VERSION,
        cluster_events,
        corroboration_bonus,
        public_event_policy,
    )
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
                "canonical_event_candidate_count": int(event_diag.get("canonical_event_count") or 0),
                "event_duplicates_suppressed": int(event_diag.get("event_duplicates_suppressed") or 0),
                "multi_source_event_candidates": int(event_diag.get("multi_source_events") or 0),
                "corroborated_event_candidates": int(event_diag.get("corroborated_events") or 0),
                "selected_corroborated_events": sum(
                    1 for row in rows if row.get("corroboration_status") in {"corroborated", "strong"}
                ),
                "selected_single_lineage_events": sum(
                    1 for row in rows if row.get("corroboration_status") == "single_lineage"
                ),
                "mean_corroboration_score": round(mean_score, 1),
            }
        )
    return selected, health


def _wire_adapter_errors(errors: list[Any]) -> list[str]:
    result: list[str] = []
    for raw in errors:
        error = str(raw)
        if any(error.startswith(f"{source}:") for source in CONFIGURED_WIRE_SOURCES):
            result.append(error)
    return result


def build_language(lang: str, config: Any, marker: str, now: Any) -> dict[str, Any]:
    payload = _original_build_language(lang, config, marker, now)
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
    selection = health.setdefault("editorial_selection", {})
    selection["mode"] = (
        "canonical_event_then_independent_corroboration_origin_authority_public_impact_recency_and_publisher_diversity"
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
                score = float(story.get("corroboration_score") or 0.0)
                if not 0.0 <= score <= 100.0:
                    raise RuntimeError(f"{lang}/{section_id} invalid corroboration score {score}")
                roots = story.get("evidence_roots") if isinstance(story.get("evidence_roots"), list) else []
                if int(story.get("independent_evidence_paths") or 0) != len(roots):
                    raise RuntimeError(f"{lang}/{section_id} evidence path mismatch")


base.fetch_all = fetch_all
base.select_sections = select_sections
base.build_language = build_language
base.validate = validate


if __name__ == "__main__":
    base.main()
