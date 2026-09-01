#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from . import publish_live_news as base
    from . import publish_live_news_filtered as filtered
    from . import publish_source_expansion_v3 as v3
    from .news_event_intelligence_v4 import CANONICAL_EVENT_VERSION, EVIDENCE_MODEL_VERSION, EVENT_INTELLIGENCE_VERSION, cluster_events, corroboration_bonus, public_event_policy
except ImportError:
    import publish_live_news as base
    import publish_live_news_filtered as filtered
    import publish_source_expansion_v3 as v3
    from news_event_intelligence_v4 import CANONICAL_EVENT_VERSION, EVIDENCE_MODEL_VERSION, EVENT_INTELLIGENCE_VERSION, cluster_events, corroboration_bonus, public_event_policy

ROOT = Path(__file__).resolve().parents[1]
_original_select_sections = base.select_sections
_original_build_language = base.build_language
_original_validate = base.validate
_original_editorial_value_score = filtered.editorial_value_score


def event_editorial_score(story: dict[str, Any], section_id: str, now: Any) -> float:
    return _original_editorial_value_score(story, section_id, now) + corroboration_bonus(story)


filtered.editorial_value_score = event_editorial_score


def _cluster_previous(previous: dict[str, Any]) -> dict[str, Any]:
    copy = dict(previous)
    sections = copy.get("sections") if isinstance(copy.get("sections"), dict) else {}
    clustered: dict[str, Any] = {}
    for section_id, rows in sections.items():
        if isinstance(rows, list):
            clustered[section_id], _ = cluster_events(rows)
        else:
            clustered[section_id] = rows
    copy["sections"] = clustered
    return copy


def select_sections(config: list[tuple[str, str, list[tuple[str, str]]]], fetched: dict[str, list[dict[str, Any]]], previous: dict[str, Any], now: Any) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    canonical: dict[str, list[dict[str, Any]]] = {}
    diagnostics: dict[str, dict[str, Any]] = {}
    for section_id, _, _ in config:
        events, diag = cluster_events(fetched.get(section_id) or [])
        canonical[section_id] = events
        diagnostics[section_id] = diag

    selected, health = _original_select_sections(config, canonical, _cluster_previous(previous), now)
    for section_id, rows in selected.items():
        diag = diagnostics.get(section_id, {})
        section_health = health.setdefault(section_id, {})
        mean_score = sum(float(row.get("corroboration_score") or 0.0) for row in rows) / len(rows) if rows else 0.0
        section_health.update({
            "event_intelligence_version": EVENT_INTELLIGENCE_VERSION,
            "canonical_event_version": CANONICAL_EVENT_VERSION,
            "evidence_model_version": EVIDENCE_MODEL_VERSION,
            "raw_event_story_count": int(diag.get("raw_story_count") or 0),
            "canonical_event_candidate_count": int(diag.get("canonical_event_count") or 0),
            "event_duplicates_suppressed": int(diag.get("event_duplicates_suppressed") or 0),
            "multi_source_event_candidates": int(diag.get("multi_source_events") or 0),
            "corroborated_event_candidates": int(diag.get("corroborated_events") or 0),
            "selected_corroborated_events": sum(1 for row in rows if row.get("corroboration_status") in {"corroborated", "strong"}),
            "selected_single_lineage_events": sum(1 for row in rows if row.get("corroboration_status") == "single_lineage"),
            "mean_corroboration_score": round(mean_score, 1),
        })
    return selected, health


def build_language(lang: str, config: Any, marker: str, now: Any) -> dict[str, Any]:
    payload = _original_build_language(lang, config, marker, now)
    health = payload.setdefault("health", {})
    health["event_intelligence"] = {"status": "active", **public_event_policy()}
    health.setdefault("editorial_selection", {})["mode"] = "canonical_event_then_independent_corroboration_origin_authority_public_impact_recency_and_diversity"
    return payload


def validate(max_age_minutes: int = 30) -> None:
    _original_validate(max_age_minutes)
    for lang in ("pl", "en"):
        payload = json.loads((ROOT / "data" / "news" / f"{lang}.json").read_text(encoding="utf-8"))
        policy = (payload.get("health") or {}).get("event_intelligence") or {}
        if policy.get("version") != EVENT_INTELLIGENCE_VERSION or policy.get("evidence_model_version") != EVIDENCE_MODEL_VERSION:
            raise RuntimeError(f"{lang} Event Intelligence v4 missing or outdated")
        for section_id, stories in (payload.get("sections") or {}).items():
            event_ids: set[str] = set()
            for story in stories:
                event_id = str(story.get("canonical_event_id") or "")
                if not event_id or event_id in event_ids:
                    raise RuntimeError(f"{lang}/{section_id} invalid or duplicate canonical event")
                event_ids.add(event_id)
                if story.get("event_intelligence_version") != EVENT_INTELLIGENCE_VERSION or story.get("evidence_model_version") != EVIDENCE_MODEL_VERSION:
                    raise RuntimeError(f"{lang}/{section_id} missing event/evidence metadata")
                score = float(story.get("corroboration_score") or 0.0)
                if not 0.0 <= score <= 100.0:
                    raise RuntimeError(f"{lang}/{section_id} invalid corroboration score")
                roots = story.get("evidence_roots") if isinstance(story.get("evidence_roots"), list) else []
                if int(story.get("independent_evidence_paths") or 0) != len(roots):
                    raise RuntimeError(f"{lang}/{section_id} evidence path mismatch")


base.select_sections = select_sections
base.build_language = build_language
base.validate = validate

if __name__ == "__main__":
    base.main()
