#!/usr/bin/env python3
"""Generate AI Outlook with a two-stage candidate and ranking engine."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import update_ai_outlook as legacy  # noqa: E402
from ai_outlook_engine import (  # noqa: E402
    AREA_LABELS,
    AREA_ORDER,
    AREA_THRESHOLDS,
    ENGINE_VERSION,
    balanced_source_pool,
    probability_from_score,
    select_candidate,
)
from comment_quality import get_ai_runtime, request_json_completion  # noqa: E402

MAX_RAW_SOURCES = 80
MAX_CANDIDATES = 10
REQUIRED_FINAL_FIELDS = (
    "category",
    "title",
    "thesis",
    "horizon",
    "rationale",
    "confirmation",
    "invalidation",
    "resolution_criteria",
)


def source_items() -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    seen: set[str] = set()
    for language in ("pl", "en"):
        payload = legacy.load_json(ROOT / language / "home_brief.json", {})
        if not isinstance(payload, dict):
            continue
        for section in ("latest", "radar"):
            entries = payload.get(section, [])
            if not isinstance(entries, list):
                continue
            for item in entries:
                if not isinstance(item, dict):
                    continue
                url = legacy.compact(item.get("link"), 500)
                title = legacy.compact(item.get("title"), 240)
                if not url.startswith("https://") or not title or url in seen:
                    continue
                seen.add(url)
                raw.append(
                    {
                        "language": language,
                        "category": legacy.compact(item.get("category"), 100),
                        "title": title,
                        "summary": legacy.compact(item.get("summary") or item.get("details"), 700),
                        "source": legacy.compact(item.get("source"), 100) or "Source",
                        "url": url,
                        "published_at": legacy.compact(item.get("published_at"), 60),
                    }
                )
                if len(raw) >= MAX_RAW_SOURCES:
                    return balanced_source_pool(raw)
    return balanced_source_pool(raw)


def candidate_messages(items: list[dict[str, Any]], recent_titles: list[str], date: str) -> list[dict[str, str]]:
    compact_items = [
        {
            "id": item["id"],
            "area": item.get("area"),
            "category": item.get("category"),
            "title": item.get("title"),
            "summary": item.get("summary"),
            "source": item.get("source"),
            "source_quality_prior": item.get("source_quality"),
            "published_at": item.get("published_at"),
        }
        for item in items
    ]
    system = (
        "You are the candidate analyst for BriefRooms AI Outlook Engine v1. "
        "Propose testable forecasts from supplied sources only. Do not select the winner. "
        "The deterministic engine will rank your candidates. Do not invent facts, numbers, "
        "sources, people, events or source IDs. Return strict JSON only."
    )
    user = {
        "publication_date_europe_warsaw": date,
        "allowed_areas_in_priority_order": list(AREA_ORDER),
        "task": (
            "Create 4 to 10 distinct forecast candidates. Cover every area that has useful supplied "
            "evidence. A candidate must forecast a future development rather than summarize a story. "
            "Prefer clear causal mechanisms and outcomes that can later be resolved using public data."
        ),
        "scoring_instructions": {
            "evidence_quality": "0-100: how directly the cited material supports the premise",
            "measurability": "0-100: whether the outcome and deadline are objectively testable",
            "causal_strength": "0-100: clarity and plausibility of the causal chain",
            "verifiability": "0-100: likelihood that public data will exist to resolve it",
            "novelty": "0-100: distinctness from recent outlooks",
            "speculation_risk": "0-100: unsupported leap risk; lower is better",
        },
        "hard_rules": [
            "area must be exactly economy, geopolitics, health or science",
            "cite 1 to 3 supplied source_ids",
            "do not use an exact security price target or direct buy/sell advice",
            "title max 100 characters",
            "thesis max 420 characters",
            "resolution_criteria must include an observable outcome and time boundary",
            "horizon_pl must be one of: 3–6 miesięcy, 6–12 miesięcy, 1–3 lata, 3–10 lat",
            "horizon_en must be one of: 3–6 months, 6–12 months, 1–3 years, 3–10 years",
            "health and science candidates should be conservative and distinguish evidence from prediction",
        ],
        "recent_polish_titles_to_avoid": recent_titles,
        "sources": compact_items,
        "required_json_shape": {
            "candidates": [
                {
                    "area": "economy",
                    "source_ids": [1, 2],
                    "title": "...",
                    "thesis": "...",
                    "horizon_pl": "6–12 miesięcy",
                    "horizon_en": "6–12 months",
                    "resolution_criteria": "...",
                    "selection_reason": "...",
                    "evidence_quality": 75,
                    "measurability": 80,
                    "causal_strength": 74,
                    "verifiability": 82,
                    "novelty": 78,
                    "speculation_risk": 32,
                }
            ]
        },
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def parse_candidates(raw: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(raw, dict) or not isinstance(raw.get("candidates"), list):
        raise ValueError("Candidate response must contain a candidates list")
    candidates = [item for item in raw["candidates"] if isinstance(item, dict)]
    if not 1 <= len(candidates) <= MAX_CANDIDATES:
        raise ValueError("Candidate response must contain 1-10 objects")
    return candidates


def final_messages(
    winner: dict[str, Any],
    items: list[dict[str, Any]],
    probability: int,
    date: str,
) -> list[dict[str, str]]:
    by_id = {int(item["id"]): item for item in items}
    evidence = []
    for raw_id in winner.get("source_ids") or []:
        source_id = int(raw_id)
        item = by_id[source_id]
        evidence.append(
            {
                "id": source_id,
                "area": item.get("area"),
                "title": item.get("title"),
                "summary": item.get("summary"),
                "source": item.get("source"),
            }
        )

    system = (
        "You are the final editor for BriefRooms AI Outlook. The candidate, probability, area, horizon "
        "and sources are locked. Write a concise bilingual publication from the supplied evidence only. "
        "Do not change the forecast direction or introduce new factual claims. Return strict JSON only."
    )
    user = {
        "publication_date_europe_warsaw": date,
        "locked_candidate": winner,
        "locked_probability": probability,
        "locked_sources": evidence,
        "requirements": [
            "Polish and English must express the same forecast",
            "title max 100 characters",
            "thesis max 520 characters",
            "rationale max 480 characters",
            "confirmation and invalidation max 260 characters each",
            "resolution_criteria max 320 characters and must be objectively testable",
            "do not present the forecast as established fact",
        ],
        "required_json_shape": {
            "pl": {
                "category": AREA_LABELS[winner["area"]]["pl"],
                "title": "...",
                "thesis": "...",
                "horizon": winner["horizon_pl"],
                "rationale": "...",
                "confirmation": "...",
                "invalidation": "...",
                "resolution_criteria": "...",
            },
            "en": {
                "category": AREA_LABELS[winner["area"]]["en"],
                "title": "...",
                "thesis": "...",
                "horizon": winner["horizon_en"],
                "rationale": "...",
                "confirmation": "...",
                "invalidation": "...",
                "resolution_criteria": "...",
            },
        },
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def build_payload(
    raw: dict[str, Any],
    winner: dict[str, Any],
    ranked: list[dict[str, Any]],
    items: list[dict[str, Any]],
    moment: datetime,
    probability: int,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Final AI Outlook response is not an object")

    by_id = {int(item["id"]): item for item in items}
    sources = []
    for raw_id in winner.get("source_ids") or []:
        source_id = int(raw_id)
        item = by_id[source_id]
        sources.append({"name": item["source"], "url": item["url"]})

    pl_date, en_date = legacy.date_labels(moment)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "date": moment.astimezone(legacy.WARSAW).date().isoformat(),
        "generated_at": moment.astimezone(legacy.WARSAW).isoformat(timespec="seconds"),
        "probability": probability,
        "source_policy": "local-source-grounded",
        "engine": {
            "version": ENGINE_VERSION,
            "area_priority": list(AREA_ORDER),
            "area_thresholds": AREA_THRESHOLDS,
            "selected_area": winner["area"],
            "selection_mode": winner.get("selection_mode"),
            "engine_score": winner["engine_score"],
            "score_breakdown": winner["score_breakdown"],
            "probability_method": "heuristic_v1_not_historically_calibrated",
            "candidate_count": len(ranked),
            "top_candidates": [
                {
                    "area": item["area"],
                    "title": legacy.compact(item.get("title"), 100),
                    "engine_score": item["engine_score"],
                    "passed_safety_gate": item["score_breakdown"]["safety_gate"],
                }
                for item in ranked[:5]
            ],
        },
    }

    limits = {
        "category": 90,
        "title": 100,
        "thesis": 520,
        "horizon": 30,
        "rationale": 480,
        "confirmation": 260,
        "invalidation": 260,
        "resolution_criteria": 320,
    }
    for language, date_label in (("pl", pl_date), ("en", en_date)):
        section = raw.get(language)
        if not isinstance(section, dict):
            raise ValueError(f"Missing final {language} section")
        clean: dict[str, Any] = {}
        for field in REQUIRED_FINAL_FIELDS:
            value = legacy.compact(section.get(field), limits[field])
            if not value:
                raise ValueError(f"Missing final {language}.{field}")
            clean[field] = value
        if clean["horizon"] not in legacy.ALLOWED_HORIZONS[language]:
            raise ValueError(f"Unsupported final {language} horizon")
        expected_horizon = winner["horizon_pl" if language == "pl" else "horizon_en"]
        if clean["horizon"] != expected_horizon:
            raise ValueError(f"Final {language} horizon changed from the selected candidate")
        clean["date_label"] = date_label
        clean["probability"] = probability
        clean["sources"] = sources
        clean["selection_reason"] = legacy.compact(winner.get("selection_reason"), 300)
        payload[language] = clean

    legacy.validate_file(payload)
    return payload


def generate(moment: datetime) -> dict[str, Any]:
    items = source_items()
    if len(items) < 4:
        raise RuntimeError("Not enough classified source items for AI Outlook Engine v1")
    runtime = get_ai_runtime()
    if not runtime.available:
        raise RuntimeError("AI provider is unavailable")

    recent = legacy.recent_titles()
    local_date = moment.astimezone(legacy.WARSAW).date().isoformat()
    candidate_raw = request_json_completion(
        post=requests.post,
        runtime=runtime,
        messages=candidate_messages(items, recent, local_date),
        max_tokens=2600,
        temperature=0.35,
        timeout=75,
    )
    winner, ranked = select_candidate(parse_candidates(candidate_raw), items, recent)
    probability = probability_from_score(winner["engine_score"])

    final_raw = request_json_completion(
        post=requests.post,
        runtime=runtime,
        messages=final_messages(winner, items, probability, local_date),
        max_tokens=1700,
        temperature=0.2,
        timeout=75,
    )
    return build_payload(final_raw, winner, ranked, items, moment, probability)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.validate_only:
        payload = legacy.load_json(legacy.OUT, {})
        legacy.validate_file(payload)
        engine = payload.get("engine") if isinstance(payload, dict) else None
        if engine and engine.get("version") != ENGINE_VERSION:
            raise ValueError("Unexpected AI Outlook engine version")
        print(f"AI Outlook valid: {legacy.OUT}")
        return 0

    moment = datetime.now(legacy.WARSAW)
    current = legacy.load_json(legacy.OUT, {})
    today = moment.date().isoformat()
    if not args.force and current.get("date") == today:
        legacy.validate_file(current)
        print(f"AI Outlook already published for {today}")
        return 0

    try:
        payload = generate(moment)
    except Exception as exc:
        if legacy.OUT.exists():
            legacy.validate_file(current)
            print(f"AI Outlook Engine v1 skipped; keeping previous edition: {exc}", file=sys.stderr)
            return 0
        raise

    legacy.publish(payload)
    print(
        f"Published AI Outlook for {payload['date']} with {ENGINE_VERSION}; "
        f"area={payload['engine']['selected_area']} score={payload['engine']['engine_score']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
