#!/usr/bin/env python3
"""Generate AI Outlook with candidate ranking, provenance and governance controls."""

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
    BRIER_PUBLIC_MIN_N,
    ENGINE_VERSION,
    GOVERNANCE_SCHEMA_VERSION,
    PROVENANCE_SCHEMA_VERSION,
    RESOLUTION_SCHEMA_VERSION,
    SCORING_POLICY_VERSION,
    WEIGHTS_VERSION,
    balanced_source_pool,
    probability_from_score,
    select_candidate,
    thresholds_snapshot,
    weights_snapshot,
)
from comment_quality import get_ai_runtime, request_json_completion  # noqa: E402
from ai_outlook_analysis_contract import (  # noqa: E402
    ANALYSIS_CONTRACT_VERSION,
    ANALYSIS_TEXT_FIELDS,
    validate_public_analysis,
)

MAX_RAW_SOURCES = 80
MAX_CANDIDATES = 10
AUDIT_DIR = ROOT / "data" / "internal" / "ai_outlook_audit"
REQUIRED_FINAL_FIELDS = (
    "category", "title", "thesis", "horizon", "rationale",
    "confirmation", "invalidation", "resolution_summary",
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
                raw.append({
                    "language": language,
                    "category": legacy.compact(item.get("category"), 100),
                    "title": title,
                    "summary": legacy.compact(item.get("summary") or item.get("details"), 700),
                    "source": legacy.compact(item.get("source"), 100) or "Source",
                    "url": url,
                    "published_at": legacy.compact(item.get("published_at"), 60),
                    "provenance_id": legacy.compact(item.get("provenance_id"), 100),
                    "origin_organization": legacy.compact(item.get("origin_organization"), 120),
                    "origin_document_url": legacy.compact(item.get("origin_document_url"), 500),
                    "origin_published_at": legacy.compact(item.get("origin_published_at"), 60),
                })
                if len(raw) >= MAX_RAW_SOURCES:
                    return balanced_source_pool(raw)
    return balanced_source_pool(raw)


def candidate_messages(items: list[dict[str, Any]], recent_titles: list[str], publication_date: str) -> list[dict[str, str]]:
    compact_items = [{
        "id": item["id"],
        "area": item.get("area"),
        "category": item.get("category"),
        "title": item.get("title"),
        "summary": item.get("summary"),
        "source": item.get("source"),
        "source_quality_prior": item.get("source_quality"),
        "published_at": item.get("published_at"),
        "provenance": item.get("provenance"),
    } for item in items]
    system = (
        "You are the candidate analyst for BriefRooms AI Outlook Engine v1.1. "
        "Propose testable forecasts from supplied evidence only. Do not select the winner. "
        "Do not invent facts, sources, provenance, people, events or source IDs. Return strict JSON only."
    )
    user = {
        "publication_date_europe_warsaw": publication_date,
        "allowed_areas_in_priority_order": list(AREA_ORDER),
        "allowed_content_categories": [
            "macro", "market_investment", "regulatory", "technology",
            "geopolitics", "public_health", "clinical_health", "science_research",
        ],
        "task": (
            "Create 4 to 10 distinct forecast candidates. Cover every area that has useful evidence. "
            "Every candidate must forecast a future development, cite 1-3 supplied source IDs and "
            "define one machine-readable resolution rubric before publication."
        ),
        "scoring_instructions": {
            "evidence_quality": "0-100: direct support for the premise",
            "measurability": "0-100: objective testability",
            "causal_strength": "0-100: clarity of causal chain",
            "verifiability": "0-100: public data availability at resolution",
            "novelty": "0-100: distinctness from recent outlooks",
            "speculation_risk": "0-100: unsupported leap risk; lower is better",
        },
        "hard_rules": [
            "area must be exactly economy, geopolitics, health or science",
            "content_category must match the area",
            "do not count different articles as independent when provenance_id is the same",
            "resolution.threshold must be numeric",
            "resolution.resolution_date must be YYYY-MM-DD and after publication date",
            "resolution.data_source_for_verification must name a public verification source",
            "do not use exact security price targets or direct buy/sell advice",
            "health and science must remain conservative",
        ],
        "recent_polish_titles_to_avoid": recent_titles,
        "sources": compact_items,
        "required_json_shape": {
            "candidates": [{
                "candidate_id": "cand_01",
                "area": "economy",
                "content_category": "macro",
                "source_ids": [1, 2],
                "title": "...",
                "thesis": "...",
                "horizon_pl": "6–12 miesięcy",
                "horizon_en": "6–12 months",
                "selection_reason": "...",
                "resolution": {
                    "metric": "...",
                    "comparison_operator": ">=",
                    "threshold": 10,
                    "unit": "percent",
                    "baseline_date": publication_date,
                    "baseline_value": None,
                    "data_source_for_verification": "...",
                    "verification_url": "https://... or empty",
                    "resolution_date": "2027-08-02",
                    "geography": "...",
                },
                "evidence_quality": 75,
                "measurability": 80,
                "causal_strength": 74,
                "verifiability": 82,
                "novelty": 78,
                "speculation_risk": 32,
            }]
        },
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def parse_candidates(raw: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(raw, dict) or not isinstance(raw.get("candidates"), list):
        raise ValueError("candidate response must contain candidates")
    candidates = [item for item in raw["candidates"] if isinstance(item, dict)]
    if not 1 <= len(candidates) <= MAX_CANDIDATES:
        raise ValueError("candidate response must contain 1-10 objects")
    return candidates


def final_messages(winner: dict[str, Any], items: list[dict[str, Any]], probability: int, publication_date: str) -> list[dict[str, str]]:
    by_id = {int(item["id"]): item for item in items}
    evidence = []
    for raw_id in winner.get("source_ids") or []:
        item = by_id[int(raw_id)]
        evidence.append({
            "id": item["id"], "title": item.get("title"), "summary": item.get("summary"),
            "source": item.get("source"), "provenance": item.get("provenance"),
        })
    system = (
        "You are the final editor for BriefRooms AI Outlook. The selected candidate, probability, "
        "structured resolution rubric, governance category and sources are locked. Write concise bilingual "
        "publication copy only. Do not alter the forecast direction, metric, threshold or deadline."
    )
    user = {
        "publication_date_europe_warsaw": publication_date,
        "locked_candidate": winner,
        "locked_probability": probability,
        "locked_sources": evidence,
        "requirements": [
            "Polish and English must express the same forecast",
            "title max 100 characters; thesis max 520; rationale max 480",
            "confirmation and invalidation max 260 characters each",
            "resolution_summary must accurately summarize the locked resolution JSON",
            "probability_event must name exactly what the locked probability measures, including the deadline and threshold or binary outcome",
            "analysis_summary must add an inference or mechanism beyond the source recap and explicitly separate known evidence from inference",
            "impact must explain the downstream consequence of both success and failure of the forecast",
            "watch_items must name 2-4 concrete future observations that would materially change the assessment",
            "direction must never call an outcome positive or negative without naming the stakeholder perspective",
            "for a procedural decision forecast, use direction.status=estimated only when supplied evidence supports a directional split; otherwise use insufficient_evidence",
            "when the locked probability already measures a substantive direction, use direction.status=embedded_in_event",
            "for non-directional indicators use direction.status=not_applicable",
            "direction.scenarios must contain 2-3 mutually exclusive integer probabilities summing to 100 only when status=estimated",
            "do not present the forecast as an established fact",
        ],
        "required_json_shape": {
            "pl": {
                "category": AREA_LABELS[winner["area"]]["pl"], "title": "...", "thesis": "...",
                "horizon": winner["horizon_pl"], "rationale": "...", "confirmation": "...",
                "invalidation": "...", "resolution_summary": "...",
                "probability_event": "Dokładne zdarzenie objęte procentem, wraz z terminem...",
                "analysis_summary": "Wniosek analityczny wykraczający poza streszczenie...",
                "impact": "Konsekwencje realizacji i braku realizacji prognozy...",
                "watch_items": "2-4 konkretne sygnały, które zmienią ocenę...",
                "direction": {
                    "status": "estimated | embedded_in_event | insufficient_evidence | not_applicable",
                    "perspective": "dla kogo oceniany jest kierunek",
                    "explanation": "co dokładnie można i czego nie można wnioskować",
                    "scenarios": [{"label": "...", "probability": 60, "meaning": "..."}],
                },
            },
            "en": {
                "category": AREA_LABELS[winner["area"]]["en"], "title": "...", "thesis": "...",
                "horizon": winner["horizon_en"], "rationale": "...", "confirmation": "...",
                "invalidation": "...", "resolution_summary": "...",
                "probability_event": "The exact event measured by the probability, including its deadline...",
                "analysis_summary": "An analytical inference beyond the source recap...",
                "impact": "Consequences if the forecast occurs and if it does not...",
                "watch_items": "2-4 concrete observations that would change the assessment...",
                "direction": {
                    "status": "estimated | embedded_in_event | insufficient_evidence | not_applicable",
                    "perspective": "the stakeholder whose direction is assessed",
                    "explanation": "what can and cannot be inferred",
                    "scenarios": [{"label": "...", "probability": 60, "meaning": "..."}],
                },
            },
        },
    }
    return [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]


def _evidence_records(winner: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {int(item["id"]): item for item in items}
    records = []
    for raw_id in winner.get("source_ids") or []:
        item = by_id[int(raw_id)]
        records.append({
            "source_id": item["id"],
            "name": item["source"],
            "url": item["url"],
            "provenance_id": item["provenance_id"],
            "provenance": item["provenance"],
        })
    return records


def build_payload(raw: dict[str, Any], winner: dict[str, Any], ranked: list[dict[str, Any]], decision_log: list[dict[str, Any]], items: list[dict[str, Any]], moment: datetime, probability: int) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(raw, dict):
        raise ValueError("final response is not an object")
    evidence = _evidence_records(winner, items)
    pl_date, en_date = legacy.date_labels(moment)
    governance = winner["governance"]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "date": moment.astimezone(legacy.WARSAW).date().isoformat(),
        "generated_at": moment.astimezone(legacy.WARSAW).isoformat(timespec="seconds"),
        "probability": probability,
        "source_policy": "provenance-deduplicated-local-sources",
        "resolution": winner["resolution"],
        "governance": governance,
        "engine": {
            "version": ENGINE_VERSION,
            "weights_version": WEIGHTS_VERSION,
            "weights_snapshot": weights_snapshot(),
            "scoring_policy_version": SCORING_POLICY_VERSION,
            "provenance_schema_version": PROVENANCE_SCHEMA_VERSION,
            "resolution_schema_version": RESOLUTION_SCHEMA_VERSION,
            "governance_schema_version": GOVERNANCE_SCHEMA_VERSION,
            "area_priority": list(AREA_ORDER),
            "area_thresholds": thresholds_snapshot(),
            "selected_area": winner["area"],
            "selection_mode": winner.get("selection_mode"),
            "engine_score": winner["engine_score"],
            "score_breakdown": winner["score_breakdown"],
            "probability_method": "heuristic_v1_not_historically_calibrated",
            "statistical_layer_enabled": False,
            "model_layers": ["deterministic_rules", "ai_assessment"],
            "public_brier_min_resolved": BRIER_PUBLIC_MIN_N,
            "candidate_count": len(ranked),
            "top_candidates": [{
                "candidate_id": item["candidate_id"], "area": item["area"],
                "title": legacy.compact(item.get("title"), 100), "engine_score": item["engine_score"],
                "passed_safety_gate": item["score_breakdown"]["safety_gate"],
            } for item in ranked[:5]],
        },
    }
    limits = {"category": 90, "title": 100, "thesis": 520, "horizon": 30, "rationale": 480, "confirmation": 260, "invalidation": 260, "resolution_summary": 320, "probability_event": 420, "analysis_summary": 620, "impact": 620, "watch_items": 520}
    for language, date_label in (("pl", pl_date), ("en", en_date)):
        section = raw.get(language)
        if not isinstance(section, dict):
            raise ValueError(f"missing final {language} section")
        clean = {}
        for field in REQUIRED_FINAL_FIELDS:
            value = legacy.compact(section.get(field), limits[field])
            if not value:
                raise ValueError(f"missing final {language}.{field}")
            clean[field] = value
        for field in ANALYSIS_TEXT_FIELDS:
            value = legacy.compact(section.get(field), limits[field])
            if not value:
                raise ValueError(f"missing final {language}.{field}")
            clean[field] = value
        direction = section.get("direction")
        if not isinstance(direction, dict):
            raise ValueError(f"missing final {language}.direction")
        clean["direction"] = direction
        expected_horizon = winner["horizon_pl" if language == "pl" else "horizon_en"]
        if clean["horizon"] != expected_horizon or clean["horizon"] not in legacy.ALLOWED_HORIZONS[language]:
            raise ValueError(f"final {language} horizon changed")
        clean.update({
            "date_label": date_label,
            "probability": probability,
            "sources": [{"name": row["name"], "url": row["url"], "provenance_id": row["provenance_id"]} for row in evidence],
            "selection_reason": legacy.compact(winner.get("selection_reason"), 300),
            "resolution_criteria": clean["resolution_summary"],
            "disclaimer": governance["disclaimers"][language],
            "analysis_contract_version": ANALYSIS_CONTRACT_VERSION,
            "resolution": winner["resolution"],
        })
        validate_public_analysis(clean, language)
        clean.pop("resolution", None)
        payload[language] = clean
    audit = {
        "schema_version": "ai-outlook-audit-v1",
        "date": payload["date"],
        "generated_at": payload["generated_at"],
        "engine": payload["engine"],
        "selected_candidate_id": winner["candidate_id"],
        "selected_resolution": winner["resolution"],
        "selected_governance": governance,
        "evidence": evidence,
        "decision_log": decision_log,
        "candidates": ranked,
    }
    validate_payload(payload)
    return payload, audit


def validate_payload(payload: dict[str, Any]) -> None:
    legacy.validate_file(payload)
    engine = payload.get("engine")
    if not isinstance(engine, dict) or engine.get("version") != ENGINE_VERSION:
        raise ValueError("missing AI Outlook Engine v1.1 metadata")
    if engine.get("weights_version") != WEIGHTS_VERSION or engine.get("weights_snapshot") != weights_snapshot():
        raise ValueError("weights snapshot/version mismatch")
    resolution = payload.get("resolution")
    if not isinstance(resolution, dict) or resolution.get("schema_version") != RESOLUTION_SCHEMA_VERSION:
        raise ValueError("missing structured resolution rubric")
    governance = payload.get("governance")
    if not isinstance(governance, dict) or governance.get("schema_version") != GOVERNANCE_SCHEMA_VERSION:
        raise ValueError("missing governance record")
    if governance.get("disclaimer_required"):
        disclaimers = governance.get("disclaimers")
        if not isinstance(disclaimers, dict) or not disclaimers.get("pl") or not disclaimers.get("en"):
            raise ValueError("required disclaimer missing from data")
        for language in ("pl", "en"):
            if payload.get(language, {}).get("disclaimer") != disclaimers[language]:
                raise ValueError(f"{language} disclaimer mismatch")
    provenance_ids = [source.get("provenance_id") for source in payload.get("pl", {}).get("sources", [])]
    if not provenance_ids or any(not value for value in provenance_ids):
        raise ValueError("source provenance missing")


def generate(moment: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    items = source_items()
    if len(items) < 4:
        raise RuntimeError("not enough classified source items")
    runtime = get_ai_runtime()
    if not runtime.available:
        raise RuntimeError("AI provider is unavailable")
    recent = legacy.recent_titles()
    publication_date = moment.astimezone(legacy.WARSAW).date().isoformat()
    candidate_raw = request_json_completion(
        post=requests.post, runtime=runtime,
        messages=candidate_messages(items, recent, publication_date),
        max_tokens=3200, temperature=0.30, timeout=90,
    )
    winner, ranked, decision_log = select_candidate(parse_candidates(candidate_raw), items, recent, publication_date)
    probability = probability_from_score(winner["engine_score"])
    final_raw = request_json_completion(
        post=requests.post, runtime=runtime,
        messages=final_messages(winner, items, probability, publication_date),
        max_tokens=2600, temperature=0.18, timeout=90,
    )
    return build_payload(final_raw, winner, ranked, decision_log, items, moment, probability)


def publish(payload: dict[str, Any], audit: dict[str, Any]) -> None:
    validate_payload(payload)
    legacy.publish(payload)
    legacy.write_json(AUDIT_DIR / f"{payload['date']}.json", audit)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.validate_only:
        payload = legacy.load_json(legacy.OUT, {})
        legacy.validate_file(payload)
        if isinstance(payload.get("engine"), dict) and payload["engine"].get("version") == ENGINE_VERSION:
            validate_payload(payload)
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
        payload, audit = generate(moment)
    except Exception as exc:
        if legacy.OUT.exists():
            legacy.validate_file(current)
            print(f"AI Outlook Engine v1.1 skipped; keeping previous edition: {exc}", file=sys.stderr)
            return 0
        raise
    publish(payload, audit)
    print(f"Published AI Outlook {payload['date']} with {ENGINE_VERSION}; area={payload['engine']['selected_area']} score={payload['engine']['engine_score']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
