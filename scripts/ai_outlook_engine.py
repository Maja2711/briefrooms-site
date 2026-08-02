#!/usr/bin/env python3
"""Deterministic candidate ranking for AI Outlook Engine v1.

The language model proposes forecast candidates and dimension scores. This module
applies source-grounded adjustments, hard safety gates and deterministic selection.
The publication priority is: economy, geopolitics, health, science.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

ENGINE_VERSION = "ai-outlook-engine-v1"
AREA_ORDER = ("economy", "geopolitics", "health", "science")
AREA_PRIORITY = {area: index for index, area in enumerate(AREA_ORDER)}
AREA_LABELS = {
    "economy": {"pl": "Ekonomia", "en": "Economy"},
    "geopolitics": {"pl": "Geopolityka", "en": "Geopolitics"},
    "health": {"pl": "Zdrowie", "en": "Health"},
    "science": {"pl": "Nauka", "en": "Science"},
}

# A candidate must clear the threshold for its own area. Health and science have
# a slightly higher bar because overclaiming in these areas is more harmful.
AREA_THRESHOLDS = {
    "economy": 68.0,
    "geopolitics": 68.0,
    "health": 72.0,
    "science": 72.0,
}
FALLBACK_MIN_SCORE = 64.0

DIMENSION_WEIGHTS = {
    "evidence_quality": 0.22,
    "measurability": 0.20,
    "causal_strength": 0.18,
    "verifiability": 0.15,
    "novelty": 0.10,
    "source_quality": 0.15,
}

AREA_KEYWORDS = {
    "economy": {
        "gospodarka", "ekonomia", "inflacja", "pkb", "bezrobocie", "stopy procentowe",
        "bank", "banki", "kredyt", "obligacje", "waluta", "rynek", "rynki", "giełda",
        "spółka", "spółki", "przychody", "zysk", "marża", "nieruchomości", "mieszkania",
        "handel", "podatki", "budżet", "economy", "economic", "inflation", "gdp",
        "unemployment", "rates", "interest rate", "banking", "credit", "bond", "currency",
        "market", "markets", "company", "earnings", "revenue", "margin", "housing",
        "real estate", "trade", "tax", "budget",
    },
    "geopolitics": {
        "geopolityka", "wojna", "konflikt", "sankcje", "nato", "ue", "unia europejska",
        "rosja", "ukraina", "chiny", "usa", "bliski wschód", "iran", "izrael", "granica",
        "wybory", "dyplomacja", "bezpieczeństwo", "militarny", "geopolitics", "war",
        "conflict", "sanctions", "russia", "ukraine", "china", "united states", "middle east",
        "border", "election", "diplomacy", "security", "military",
    },
    "health": {
        "zdrowie", "medycyna", "pacjent", "choroba", "leczenie", "lek", "szpital",
        "epidemia", "szczepionka", "rak", "serce", "ciśnienie", "cholesterol", "kliniczny",
        "health", "medicine", "patient", "disease", "treatment", "drug", "hospital",
        "epidemic", "vaccine", "cancer", "heart", "blood pressure", "cholesterol", "clinical",
    },
    "science": {
        "nauka", "badanie", "badania", "naukowcy", "odkrycie", "kosmos", "fizyka",
        "chemia", "biologia", "technologia", "sztuczna inteligencja", "ai", "klimat",
        "science", "research", "scientists", "discovery", "space", "physics", "chemistry",
        "biology", "technology", "artificial intelligence", "climate",
    },
}

AUTHORITATIVE_HOST_HINTS = (
    ".gov", ".gov.pl", ".europa.eu", "who.int", "oecd.org", "imf.org", "worldbank.org",
    "nbp.pl", "stat.gov.pl", "ecb.europa.eu", "eurostat", "ema.europa.eu", "fda.gov",
    "cdc.gov", "nih.gov", "nature.com", "science.org", "thelancet.com", "nejm.org",
    "cochrane.org", "nasa.gov", "esa.int",
)
ESTABLISHED_NEWS_HOST_HINTS = (
    "reuters.com", "apnews.com", "bbc.", "ft.com", "economist.com", "bloomberg.com",
    "bankier.pl", "businessinsider.com", "pap.pl", "tvn24.pl", "rmf24.pl", "polsatnews.pl",
    "theguardian.com", "aljazeera.com", "dw.com", "france24.com", "euronews.com",
)


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def host_from_url(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def infer_area(item: dict[str, Any]) -> str | None:
    """Classify one BriefRooms source into the four v1 areas."""
    haystack = " ".join(
        _text(item.get(field))
        for field in ("category", "title", "summary", "details", "source")
    )
    scores: dict[str, int] = {}
    for area, keywords in AREA_KEYWORDS.items():
        score = 0
        for keyword in keywords:
            if keyword in haystack:
                score += 2 if " " in keyword else 1
        scores[area] = score
    best = max(AREA_ORDER, key=lambda area: (scores[area], -AREA_PRIORITY[area]))
    return best if scores[best] > 0 else None


def source_quality(item: dict[str, Any]) -> int:
    """Return a transparent 0-100 source quality prior."""
    host = host_from_url(str(item.get("url") or item.get("link") or ""))
    if any(hint in host for hint in AUTHORITATIVE_HOST_HINTS):
        return 94
    if any(hint in host for hint in ESTABLISHED_NEWS_HOST_HINTS):
        return 78
    if host:
        return 62
    return 45


def annotate_sources(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    annotated: list[dict[str, Any]] = []
    for index, original in enumerate(items, start=1):
        item = dict(original)
        item["id"] = index
        item["area"] = infer_area(item)
        item["source_quality"] = source_quality(item)
        item["host"] = host_from_url(str(item.get("url") or item.get("link") or ""))
        annotated.append(item)
    return annotated


def balanced_source_pool(items: list[dict[str, Any]], per_area: int = 7, total: int = 24) -> list[dict[str, Any]]:
    """Keep source coverage balanced while preserving the publication priority."""
    annotated = annotate_sources(items)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in annotated:
        area = item.get("area")
        if area in AREA_ORDER:
            buckets[area].append(item)

    selected: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for area in AREA_ORDER:
        ranked = sorted(
            buckets[area],
            key=lambda item: (
                int(item.get("source_quality") or 0),
                bool(item.get("published_at")),
            ),
            reverse=True,
        )
        for item in ranked[:per_area]:
            url = str(item.get("url") or item.get("link") or "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            selected.append(item)
            if len(selected) >= total:
                break
        if len(selected) >= total:
            break

    # Re-number after balancing so model source IDs are compact and deterministic.
    for index, item in enumerate(selected, start=1):
        item["id"] = index
    return selected


def _dimension(candidate: dict[str, Any], name: str) -> float:
    try:
        value = float(candidate.get(name))
    except (TypeError, ValueError):
        value = 0.0
    return max(0.0, min(100.0, value))


def _candidate_sources(candidate: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {int(item["id"]): item for item in items if "id" in item}
    resolved: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw in candidate.get("source_ids") or []:
        try:
            source_id = int(raw)
        except (TypeError, ValueError):
            continue
        if source_id in by_id and source_id not in seen:
            seen.add(source_id)
            resolved.append(by_id[source_id])
    return resolved


def _source_component(candidate: dict[str, Any], items: list[dict[str, Any]]) -> tuple[float, dict[str, Any]]:
    sources = _candidate_sources(candidate, items)
    if not sources:
        return 0.0, {"source_count": 0, "distinct_hosts": 0, "authoritative": False}
    qualities = [float(item.get("source_quality") or 0) for item in sources]
    hosts = {str(item.get("host") or "") for item in sources if item.get("host")}
    authoritative = any(quality >= 90 for quality in qualities)
    independent_bonus = min(8.0, max(0, len(hosts) - 1) * 4.0)
    component = min(100.0, sum(qualities) / len(qualities) + independent_bonus)
    return component, {
        "source_count": len(sources),
        "distinct_hosts": len(hosts),
        "authoritative": authoritative,
        "average_source_quality": round(sum(qualities) / len(qualities), 2),
        "independent_source_bonus": independent_bonus,
    }


def _novelty_penalty(candidate: dict[str, Any], recent_titles: list[str]) -> float:
    title_tokens = set(re.findall(r"[a-ząćęłńóśźż0-9]{4,}", _text(candidate.get("title"))))
    if not title_tokens:
        return 12.0
    max_overlap = 0.0
    for title in recent_titles:
        old_tokens = set(re.findall(r"[a-ząćęłńóśźż0-9]{4,}", _text(title)))
        if not old_tokens:
            continue
        overlap = len(title_tokens & old_tokens) / max(1, len(title_tokens | old_tokens))
        max_overlap = max(max_overlap, overlap)
    if max_overlap >= 0.55:
        return 18.0
    if max_overlap >= 0.35:
        return 9.0
    return 0.0


def score_candidate(
    candidate: dict[str, Any],
    items: list[dict[str, Any]],
    recent_titles: list[str],
) -> dict[str, Any]:
    """Score one model-proposed candidate with deterministic adjustments."""
    area = str(candidate.get("area") or "").strip().lower()
    if area not in AREA_ORDER:
        raise ValueError(f"Unsupported AI Outlook area: {area!r}")

    sources = _candidate_sources(candidate, items)
    if not 1 <= len(sources) <= 3:
        raise ValueError("Candidate must cite 1-3 known sources")
    if any(item.get("area") not in (area, None) for item in sources):
        raise ValueError("Candidate sources conflict with the candidate area")

    source_score, source_meta = _source_component(candidate, items)
    dimensions = {
        "evidence_quality": _dimension(candidate, "evidence_quality"),
        "measurability": _dimension(candidate, "measurability"),
        "causal_strength": _dimension(candidate, "causal_strength"),
        "verifiability": _dimension(candidate, "verifiability"),
        "novelty": _dimension(candidate, "novelty"),
        "source_quality": source_score,
    }
    weighted = sum(dimensions[name] * weight for name, weight in DIMENSION_WEIGHTS.items())

    speculation_risk = _dimension(candidate, "speculation_risk")
    speculation_penalty = max(0.0, speculation_risk - 45.0) * 0.22
    novelty_penalty = _novelty_penalty(candidate, recent_titles)

    # Harder safety gate for health/science: require either one authoritative source
    # or at least two independent sources. The candidate remains in diagnostics but
    # cannot pass publication selection without this evidence structure.
    safety_gate = True
    safety_reason = "passed"
    if area in ("health", "science"):
        if not source_meta["authoritative"] and source_meta["distinct_hosts"] < 2:
            safety_gate = False
            safety_reason = "health_science_requires_authoritative_or_two_independent_sources"

    resolution_criteria = _text(candidate.get("resolution_criteria"))
    if len(resolution_criteria) < 35:
        safety_gate = False
        safety_reason = "resolution_criteria_too_weak"

    engine_score = max(0.0, min(100.0, weighted - speculation_penalty - novelty_penalty))
    if not safety_gate:
        engine_score = min(engine_score, AREA_THRESHOLDS[area] - 0.1)

    enriched = dict(candidate)
    enriched["area"] = area
    enriched["engine_score"] = round(engine_score, 2)
    enriched["score_breakdown"] = {
        **{name: round(value, 2) for name, value in dimensions.items()},
        "speculation_risk": round(speculation_risk, 2),
        "speculation_penalty": round(speculation_penalty, 2),
        "novelty_penalty": round(novelty_penalty, 2),
        "threshold": AREA_THRESHOLDS[area],
        "safety_gate": safety_gate,
        "safety_reason": safety_reason,
        **source_meta,
    }
    return enriched


def select_candidate(
    candidates: list[dict[str, Any]],
    items: list[dict[str, Any]],
    recent_titles: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Select by area priority, but only after the area's quality threshold is met."""
    scored: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            scored.append(score_candidate(candidate, items, recent_titles))
        except (TypeError, ValueError):
            continue
    if not scored:
        raise ValueError("No valid AI Outlook candidates")

    for area in AREA_ORDER:
        eligible = [
            item for item in scored
            if item["area"] == area
            and item["engine_score"] >= AREA_THRESHOLDS[area]
            and item["score_breakdown"]["safety_gate"]
        ]
        if eligible:
            winner = max(eligible, key=lambda item: item["engine_score"])
            winner["selection_mode"] = "priority_area_threshold"
            return winner, sorted(scored, key=lambda item: item["engine_score"], reverse=True)

    fallback = max(scored, key=lambda item: item["engine_score"])
    if fallback["engine_score"] < FALLBACK_MIN_SCORE or not fallback["score_breakdown"]["safety_gate"]:
        raise ValueError("No AI Outlook candidate cleared the minimum publication quality")
    fallback["selection_mode"] = "global_quality_fallback"
    return fallback, sorted(scored, key=lambda item: item["engine_score"], reverse=True)


def probability_from_score(score: float) -> int:
    """Map the v1 quality score to a conservative, explicitly heuristic estimate."""
    value = round(45.0 + 0.35 * max(0.0, min(100.0, float(score))))
    return int(max(55, min(80, value)))


def finite_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
