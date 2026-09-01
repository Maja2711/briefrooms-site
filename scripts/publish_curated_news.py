#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from . import publish_live_news as base
    from . import publish_live_news_filtered as filtered
    from .news_source_architecture import (
        EMERGENCY_MAX_SOURCE_CARDS,
        SOURCE_POLICY_VERSION,
        TARGET_MAX_SOURCE_CARDS,
        diversity_status,
        extend_config,
        public_source_policy,
        source_authority_bonus,
        source_mix,
        source_profile,
        tier_mix,
    )
except ImportError:
    import publish_live_news as base
    import publish_live_news_filtered as filtered
    from news_source_architecture import (
        EMERGENCY_MAX_SOURCE_CARDS,
        SOURCE_POLICY_VERSION,
        TARGET_MAX_SOURCE_CARDS,
        diversity_status,
        extend_config,
        public_source_policy,
        source_authority_bonus,
        source_mix,
        source_profile,
        tier_mix,
    )

ROOT = Path(__file__).resolve().parents[1]

_original_editorial_value_score = filtered.editorial_value_score
_original_filtered_select_sections = filtered.select_sections
_original_filtered_build_language = filtered.build_language
_original_filtered_validate = filtered.validate

# Enrich the canonical source pool. Existing feeds remain intact and the base
# fetcher treats reserve-feed failures as non-fatal errors.
base.PL = extend_config(base.PL, "pl")
base.EN = extend_config(base.EN, "en")

# Preserve five only as a continuity guard. Three is the curation target whenever
# the candidate pool is sufficiently diverse.
filtered.MAX_SOURCE_SHARE = EMERGENCY_MAX_SOURCE_CARDS
filtered.EDITORIAL_SELECTION_POLICY_VERSION = SOURCE_POLICY_VERSION


def curated_editorial_value_score(story: dict[str, Any], section_id: str, now: Any) -> float:
    """Combine editorial value with provenance authority.

    Authority is deliberately additive. A source tier cannot make a story
    publishable by itself because filtering happens before this ranking stage.
    """
    base_score = _original_editorial_value_score(story, section_id, now)
    return base_score + source_authority_bonus(story.get("source"))


# filtered.select_sections resolves this global function at runtime.
filtered.editorial_value_score = curated_editorial_value_score


def _candidate_tier_mix(candidates: list[dict[str, Any]]) -> dict[str, int]:
    unique_sources: dict[str, dict[str, Any]] = {}
    for story in candidates:
        if not story.get("image"):
            continue
        source = str(story.get("source") or "unknown").strip() or "unknown"
        unique_sources.setdefault(source, {"source": source})
    return tier_mix(unique_sources.values())


def select_sections(
    config: list[tuple[str, str, list[tuple[str, str]]]],
    fetched: dict[str, list[dict[str, Any]]],
    previous: dict[str, Any],
    now: Any,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    sections, health = _original_filtered_select_sections(config, fetched, previous, now)

    for section_id, rows in sections.items():
        counts = source_mix(rows)
        maximum = max(counts.values(), default=0)
        status = diversity_status(rows)
        candidate_sources = {
            str(item.get("source") or "unknown").strip() or "unknown"
            for item in (fetched.get(section_id) or [])
            if item.get("image")
        }
        high_authority_sources = sorted(
            source
            for source in candidate_sources
            if source_profile(source).tier in {"primary", "wire", "premium"}
        )

        section_health = health.setdefault(section_id, {})
        section_health.update(
            {
                "source_policy_version": SOURCE_POLICY_VERSION,
                "source_tier_mix": tier_mix(rows),
                "candidate_source_tier_mix": _candidate_tier_mix(fetched.get(section_id) or []),
                "high_authority_candidate_sources": high_authority_sources,
                "target_source_cap": TARGET_MAX_SOURCE_CARDS,
                "emergency_source_cap": EMERGENCY_MAX_SOURCE_CARDS,
                "max_selected_source_cards": maximum,
                "source_diversity_status": status,
            }
        )

        if status == "violation":
            raise RuntimeError(
                f"{section_id} exceeds emergency publisher cap: {counts}"
            )

    return sections, health


def build_language(lang: str, config: Any, marker: str, now: Any) -> dict[str, Any]:
    payload = _original_filtered_build_language(lang, config, marker, now)
    selection = payload.setdefault("health", {}).setdefault("editorial_selection", {})
    selection.update(
        {
            "status": "active",
            "mode": "source_authority_then_public_impact_recency_with_publisher_diversity",
            "version": SOURCE_POLICY_VERSION,
            "target_max_cards_per_source": TARGET_MAX_SOURCE_CARDS,
            "emergency_max_cards_per_source": EMERGENCY_MAX_SOURCE_CARDS,
            "source_policy": public_source_policy(),
        }
    )
    return payload


def validate(max_age_minutes: int = 30) -> None:
    _original_filtered_validate(max_age_minutes)
    for lang in ("pl", "en"):
        path = ROOT / "data" / "news" / f"{lang}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        selection = (payload.get("health") or {}).get("editorial_selection") or {}
        if selection.get("version") != SOURCE_POLICY_VERSION:
            raise RuntimeError(f"{lang} curated source policy missing or outdated")
        if selection.get("target_max_cards_per_source") != TARGET_MAX_SOURCE_CARDS:
            raise RuntimeError(f"{lang} curated target source cap missing")

        sections = payload.get("sections") if isinstance(payload.get("sections"), dict) else {}
        section_health = (payload.get("health") or {}).get("sections") or {}
        for section_id, rows in sections.items():
            counts = source_mix(rows if isinstance(rows, list) else [])
            if counts and max(counts.values()) > EMERGENCY_MAX_SOURCE_CARDS:
                raise RuntimeError(
                    f"{lang}/{section_id} exceeds emergency source cap: {counts}"
                )
            health = section_health.get(section_id) if isinstance(section_health, dict) else None
            if isinstance(health, dict):
                status = health.get("source_diversity_status")
                if status == "violation":
                    raise RuntimeError(f"{lang}/{section_id} source diversity violation")


base.select_sections = select_sections
base.build_language = build_language
base.validate = validate


if __name__ == "__main__":
    base.main()
