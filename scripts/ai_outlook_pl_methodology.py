#!/usr/bin/env python3
"""Polish-only AI Outlook methodology focused on real-world outcomes.

The Polish edition is generated independently from the English edition. The model
may propose candidates, but deterministic rules decide which candidates are
eligible. Meta-forecasts about media attention, articles, communications or
future updates are never publishable.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

import requests

import update_ai_outlook as legacy
import update_ai_outlook_v3 as v3
from ai_outlook_engine import (
    AREA_LABELS,
    AREA_ORDER,
    BRIER_PUBLIC_MIN_N,
    ENGINE_VERSION,
    GOVERNANCE_SCHEMA_VERSION,
    PROVENANCE_SCHEMA_VERSION,
    RESOLUTION_SCHEMA_VERSION,
    SCORING_POLICY_VERSION,
    WEIGHTS_VERSION,
    candidate_sources,
    probability_from_score,
    select_candidate,
    thresholds_snapshot,
    weights_snapshot,
)
from ai_outlook_pl_quality import validate_pl_edition
from comment_quality import request_json_completion

METHODOLOGY_VERSION = "pl-outcome-forecast-v2"
MIN_VALID_CANDIDATES = 1
MAX_CANDIDATES = 10
ALLOWED_FORECAST_TYPES = {
    "official_decision",
    "official_indicator",
    "policy_implementation",
    "regulatory_milestone",
    "market_indicator",
    "clinical_endpoint",
    "scientific_result",
}
TYPE_PRIORITY = {
    "official_decision": 12.0,
    "official_indicator": 11.0,
    "policy_implementation": 10.0,
    "regulatory_milestone": 10.0,
    "market_indicator": 8.0,
    "clinical_endpoint": 8.0,
    "scientific_result": 7.0,
}
OFFICIAL_VERIFICATION_HOSTS = (
    "prezydent.pl",
    "gov.pl",
    "sejm.gov.pl",
    "senat.gov.pl",
    "dziennikustaw.gov.pl",
    "isap.sejm.gov.pl",
    "stat.gov.pl",
    "nbp.pl",
    "knf.gov.pl",
    "uokik.gov.pl",
    "rf.gov.pl",
    "zus.pl",
    "nfz.gov.pl",
    "pacjent.gov.pl",
    "pzh.gov.pl",
    "ema.europa.eu",
    "ec.europa.eu",
    "eurostat.ec.europa.eu",
    "ecb.europa.eu",
    "consilium.europa.eu",
    "eur-lex.europa.eu",
    "who.int",
    "clinicaltrials.gov",
    "nasa.gov",
    "esa.int",
)
META_FORECAST_RE = re.compile(
    r"\b("
    r"komunikat(?:y|ów|u|ach)?|aktualizacj\w*|artykuł\w*|publikacj\w*|"
    r"wzmiank\w*|doniesieni\w*|nagłówk\w*|zainteresowani\w*|"
    r"centrum uwagi|debat\w*|dyskusj\w*|reakcj\w* informacyjn\w*|"
    r"kolejne wyjaśnienia|dalszy ciąg tematu"
    r")\b",
    re.IGNORECASE,
)
VAGUE_OUTCOME_RE = re.compile(
    r"\b("
    r"może mieć znaczenie|pozostanie ważn\w*|utrzyma się presja|"
    r"będzie obserwowan\w*|pozostanie w centrum uwagi|"
    r"rynek będzie śledził|temat będzie wracał"
    r")\b",
    re.IGNORECASE,
)
TOKEN_RE = re.compile(r"[a-ząćęłńóśźż0-9]{4,}", re.IGNORECASE)
STOPWORDS = {
    "oraz", "przez", "który", "która", "które", "tego", "jest", "będzie",
    "zostanie", "zostaną", "wobec", "polsce", "polski", "polska", "roku",
    "dnia", "okresie", "wartość", "wskaźnik", "liczba", "wynik", "poziom",
    "sprawie", "dotyczący", "dotycząca", "dotyczące", "publiczny", "publiczne",
}


class PolishMethodologyError(ValueError):
    """Raised when no meaningful Polish forecast can be produced."""


def _compact(value: Any, limit: int) -> str:
    return legacy.compact(value, limit)


def _normal(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _tokens(value: Any) -> set[str]:
    return {
        token[:8]
        for token in TOKEN_RE.findall(_normal(value))
        if token not in STOPWORDS and not token.isdigit()
    }


def _finite(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _official_verification_url(value: Any) -> bool:
    try:
        host = (urlparse(str(value or "")).hostname or "").lower().removeprefix("www.")
    except Exception:
        return False
    return any(host == allowed or host.endswith("." + allowed) for allowed in OFFICIAL_VERIFICATION_HOSTS)


def _candidate_sources(candidate: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return candidate_sources(candidate, items)


def _source_payload(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.get("id"),
            "area": item.get("area"),
            "title": item.get("title"),
            "summary": item.get("summary"),
            "source": item.get("source"),
            "source_quality": item.get("source_quality"),
            "published_at": item.get("published_at"),
            "provenance_id": item.get("provenance_id"),
        }
        for item in items
    ]


def candidate_messages(
    items: list[dict[str, Any]],
    recent_titles: list[str],
    publication_date: str,
) -> list[dict[str, str]]:
    system = (
        "Jesteś analitykiem prognostycznym polskiej edycji BriefRooms AI Outlook. "
        "Tworzysz wyłącznie prognozy realnych rezultatów: decyzji urzędowej, odczytu "
        "oficjalnego wskaźnika, wejścia regulacji w życie, mierzalnego kamienia milowego, "
        "wyniku badania albo jednoznacznego zdarzenia rynkowego. Nie wolno prognozować "
        "liczby artykułów, komunikatów, publikacji, wzmianek, reakcji medialnych, dalszych "
        "aktualizacji ani tego, że temat pozostanie w centrum uwagi. Nie wymyślaj danych, "
        "wartości bazowych, źródeł ani adresów URL. Zwróć wyłącznie poprawny JSON."
    )
    user = {
        "publication_date_europe_warsaw": publication_date,
        "language": "pl",
        "task": (
            "Zaproponuj od 4 do 10 kandydatów. Każdy kandydat ma dotyczyć jednego "
            "spójnego zjawiska i kończyć się jednym obiektywnie rozstrzygalnym wynikiem. "
            "Jeżeli źródła nie podają liczbowej wartości bazowej dla wskaźnika ciągłego, "
            "nie twórz takiego kandydata. Dla oczekującej decyzji urzędowej użyj zdarzenia "
            "binarnego: baseline_value=0, threshold=1, unit='zdarzenie binarne'."
        ),
        "preferred_forecast_order": [
            "official_decision",
            "official_indicator",
            "policy_implementation",
            "regulatory_milestone",
            "market_indicator",
            "clinical_endpoint",
            "scientific_result",
        ],
        "hard_rules": [
            "forecast_type musi być jedną z dozwolonych wartości",
            "użyj 1-3 source_ids i nie mieszaj niezwiązanych tematów",
            "resolution.metric opisuje rezultat w świecie, nie aktywność mediów",
            "resolution.baseline_value i threshold muszą być liczbami",
            "resolution ma dokładnie jedną metrykę i jeden operator",
            "verification_url musi prowadzić do oficjalnej instytucji lub rejestru",
            "resolution_date musi przypadać po dacie publikacji i nie później niż za 18 miesięcy",
            "tytuł i forecast_statement muszą mówić wprost co ma się wydarzyć",
            "nie używaj porad inwestycyjnych ani dokładnych prognoz cen pojedynczych akcji",
            "nie twórz prognozy tylko dlatego, że temat jest głośny",
        ],
        "recent_titles_to_avoid": recent_titles,
        "sources": _source_payload(items),
        "required_json_shape": {
            "candidates": [
                {
                    "candidate_id": "pl_01",
                    "area": "economy",
                    "content_category": "regulatory",
                    "forecast_type": "official_decision",
                    "source_ids": [1],
                    "title": "...",
                    "forecast_statement": "...",
                    "why_now": "...",
                    "causal_chain": "...",
                    "horizon_pl": "3–6 miesięcy",
                    "horizon_en": "3–6 months",
                    "selection_reason": "...",
                    "resolution": {
                        "metric": "...",
                        "comparison_operator": ">=",
                        "threshold": 1,
                        "unit": "zdarzenie binarne",
                        "baseline_date": publication_date,
                        "baseline_value": 0,
                        "data_source_for_verification": "...",
                        "verification_url": "https://...",
                        "resolution_date": "YYYY-MM-DD",
                        "geography": "Polska",
                    },
                    "evidence_quality": 0,
                    "measurability": 0,
                    "causal_strength": 0,
                    "verifiability": 0,
                    "novelty": 0,
                    "speculation_risk": 0,
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
        raise PolishMethodologyError("Polish candidate response has no candidates array")
    candidates = [item for item in raw["candidates"] if isinstance(item, dict)]
    if not MIN_VALID_CANDIDATES <= len(candidates) <= MAX_CANDIDATES:
        raise PolishMethodologyError("Polish candidate response must contain 1-10 objects")
    return candidates


def candidate_rejection_reason(
    candidate: dict[str, Any],
    items: list[dict[str, Any]],
    publication_date: str,
) -> str | None:
    forecast_type = str(candidate.get("forecast_type") or "")
    if forecast_type not in ALLOWED_FORECAST_TYPES:
        return "unsupported_forecast_type"
    if candidate.get("area") not in AREA_ORDER:
        return "invalid_area"

    sources = _candidate_sources(candidate, items)
    if not 1 <= len(sources) <= 3:
        return "invalid_source_count"
    provenance = {str(item.get("provenance_id") or "") for item in sources}
    if "" in provenance or len(provenance) != len(sources):
        return "sources_not_independent"

    resolution = candidate.get("resolution")
    if not isinstance(resolution, dict):
        return "missing_resolution"
    metric = _compact(resolution.get("metric"), 300)
    statement = " ".join(
        _compact(candidate.get(field), 600)
        for field in ("title", "forecast_statement", "selection_reason")
    )
    if not metric or META_FORECAST_RE.search(metric) or META_FORECAST_RE.search(statement):
        return "meta_forecast"
    if VAGUE_OUTCOME_RE.search(statement):
        return "vague_outcome"
    if re.search(r"\b(?:lub|albo|ewentualnie)\b|[/;]", metric, re.IGNORECASE):
        return "ambiguous_metric"

    baseline = resolution.get("baseline_value")
    threshold = resolution.get("threshold")
    if not _finite(baseline) or not _finite(threshold):
        return "missing_numeric_baseline_or_threshold"
    operator = str(resolution.get("comparison_operator") or "")
    if operator not in {">", ">=", "<", "<=", "==", "increase_by", "decrease_by", "between"}:
        return "invalid_operator"
    unit = _compact(resolution.get("unit"), 80)
    if not unit or META_FORECAST_RE.search(unit):
        return "invalid_unit"
    if not _official_verification_url(resolution.get("verification_url")):
        return "verification_not_official"
    if not _compact(resolution.get("data_source_for_verification"), 220):
        return "missing_verification_source"

    try:
        baseline_date = date.fromisoformat(str(resolution.get("baseline_date") or ""))
        resolution_date = date.fromisoformat(str(resolution.get("resolution_date") or ""))
        published = date.fromisoformat(publication_date)
    except ValueError:
        return "invalid_dates"
    if baseline_date != published:
        return "baseline_date_mismatch"
    days = (resolution_date - published).days
    if days < 7 or days > 550:
        return "invalid_resolution_horizon"

    is_binary = "binar" in _normal(unit)
    if forecast_type in {"official_decision", "policy_implementation", "regulatory_milestone"}:
        if not is_binary or float(baseline) != 0.0 or float(threshold) != 1.0:
            return "official_event_not_binary"
    elif is_binary and float(threshold) != 1.0:
        return "invalid_binary_threshold"

    topic_tokens = _tokens(statement + " " + metric)
    if len(topic_tokens) < 2:
        return "topic_too_vague"
    for source in sources:
        source_tokens = _tokens(
            f"{source.get('title', '')} {source.get('summary', '')}"
        )
        if len(topic_tokens & source_tokens) < 1:
            return "source_topic_mismatch"

    if not _compact(candidate.get("why_now"), 500):
        return "missing_why_now"
    if not _compact(candidate.get("causal_chain"), 500):
        return "missing_causal_chain"
    return None


def filter_candidates(
    candidates: list[dict[str, Any]],
    items: list[dict[str, Any]],
    publication_date: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for candidate in candidates:
        reason = candidate_rejection_reason(candidate, items, publication_date)
        if reason:
            rejected.append(
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "title": _compact(candidate.get("title"), 120),
                    "reason": reason,
                }
            )
            continue
        adjusted = dict(candidate)
        forecast_type = str(adjusted["forecast_type"])
        bonus = TYPE_PRIORITY[forecast_type]
        adjusted["measurability"] = min(
            100.0, float(adjusted.get("measurability") or 0) + bonus
        )
        adjusted["verifiability"] = min(
            100.0, float(adjusted.get("verifiability") or 0) + bonus / 2
        )
        adjusted["methodology_version"] = METHODOLOGY_VERSION
        accepted.append(adjusted)
    return accepted, rejected


def _final_messages(
    winner: dict[str, Any],
    items: list[dict[str, Any]],
    probability: int,
    publication_date: str,
) -> list[dict[str, str]]:
    evidence = [
        {
            "id": item.get("id"),
            "title": item.get("title"),
            "summary": item.get("summary"),
            "source": item.get("source"),
            "url": item.get("url"),
        }
        for item in _candidate_sources(winner, items)
    ]
    system = (
        "Jesteś redaktorem polskiego BriefRooms AI Outlook. Kandydat, kierunek prognozy, "
        "metryka, próg, wartość bazowa, termin i źródła są zablokowane. Napisz prostą, "
        "konkretną prognozę po polsku. Nie dodawaj innych tematów. Nie prognozuj liczby "
        "komunikatów ani zainteresowania mediów. Zwróć wyłącznie poprawny JSON."
    )
    user = {
        "publication_date": publication_date,
        "locked_candidate": winner,
        "locked_probability": probability,
        "locked_sources": evidence,
        "requirements": [
            "tytuł maksymalnie 100 znaków i zawiera prognozowany rezultat",
            "thesis maksymalnie 420 znaków i mówi co, do kiedy oraz przy jakim warunku",
            "rationale maksymalnie 420 znaków i wyjaśnia punkt wyjścia oraz mechanizm",
            "confirmation i invalidation są wzajemnie rozłączne",
            "resolution_summary dokładnie odpowiada zablokowanej metryce",
            "nie przedstawiaj prognozy jako faktu",
            "unikaj języka technicznego, jeżeli można użyć prostszego",
        ],
        "required_json_shape": {
            "category": AREA_LABELS[winner["area"]]["pl"],
            "title": "...",
            "thesis": "...",
            "horizon": winner["horizon_pl"],
            "rationale": "...",
            "confirmation": "...",
            "invalidation": "...",
            "resolution_summary": "...",
        },
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def _clean_final(raw: dict[str, Any], winner: dict[str, Any]) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise PolishMethodologyError("Polish final response is not an object")
    limits = {
        "category": 90,
        "title": 100,
        "thesis": 420,
        "horizon": 30,
        "rationale": 420,
        "confirmation": 280,
        "invalidation": 280,
        "resolution_summary": 340,
    }
    clean: dict[str, str] = {}
    for field, limit in limits.items():
        value = _compact(raw.get(field), limit)
        if not value:
            raise PolishMethodologyError(f"missing Polish final field: {field}")
        clean[field] = value
    if clean["category"] != AREA_LABELS[winner["area"]]["pl"]:
        raise PolishMethodologyError("Polish final category changed")
    if clean["horizon"] != winner["horizon_pl"]:
        raise PolishMethodologyError("Polish final horizon changed")
    if META_FORECAST_RE.search(" ".join(clean.values())):
        raise PolishMethodologyError("Polish final copy reverted to a meta-forecast")
    return clean


def _source_rows(winner: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": _compact(item.get("source"), 100) or "Źródło",
            "url": _compact(item.get("url"), 500),
            "provenance_id": _compact(item.get("provenance_id"), 100),
            "source_language": "pl",
        }
        for item in _candidate_sources(winner, items)
    ]


def generate_pl_edition(
    moment: datetime,
    runtime: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    items = v3.source_items("pl")
    if len(items) < v3.MIN_SOURCES:
        raise PolishMethodologyError("not enough current Polish source items")
    publication_date = moment.astimezone(legacy.WARSAW).date().isoformat()
    recent = v3.recent_titles("pl")

    raw_candidates = request_json_completion(
        post=requests.post,
        runtime=runtime,
        messages=candidate_messages(items, recent, publication_date),
        max_tokens=3600,
        temperature=0.20,
        timeout=90,
    )
    parsed = parse_candidates(raw_candidates)
    accepted, methodology_rejections = filter_candidates(
        parsed, items, publication_date
    )
    if not accepted:
        raise PolishMethodologyError(
            "No Polish candidate passed the real-outcome methodology gate"
        )

    winner, ranked, engine_log = select_candidate(
        accepted, items, recent, publication_date
    )
    probability = min(75, probability_from_score(winner["engine_score"]))
    final_raw = request_json_completion(
        post=requests.post,
        runtime=runtime,
        messages=_final_messages(
            winner, items, probability, publication_date
        ),
        max_tokens=1500,
        temperature=0.10,
        timeout=90,
    )
    final = _clean_final(final_raw, winner)
    date_label = legacy.date_labels(moment)[0]
    governance = winner["governance"]
    resolution = dict(winner["resolution"])
    sources = _source_rows(winner, items)
    forecast_type = str(winner["forecast_type"])

    edition: dict[str, Any] = {
        **final,
        "date_label": date_label,
        "probability": probability,
        "sources": sources,
        "selection_reason": _compact(winner.get("selection_reason"), 320),
        "resolution_criteria": final["resolution_summary"],
        "disclaimer": governance["disclaimers"]["pl"],
        "forecast_id": f"{publication_date}-pl-{winner['candidate_id']}",
        "source_language": "pl",
        "source_policy": v3.SOURCE_POLICY["pl"],
        "forecast_type": forecast_type,
        "resolution": resolution,
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
            "selection_mode": "pl_real_outcome_gate_then_rank",
            "engine_score": winner["engine_score"],
            "score_breakdown": winner["score_breakdown"],
            "probability_method": "heuristic_v1_not_historically_calibrated",
            "statistical_layer_enabled": False,
            "model_layers": [
                "deterministic_real_outcome_gate",
                "gemini_candidate_generation",
                "deterministic_candidate_ranking",
                "gemini_locked_copy_edit",
            ],
            "public_brier_min_resolved": BRIER_PUBLIC_MIN_N,
            "candidate_count": len(ranked),
            "top_candidates": [
                {
                    "candidate_id": item.get("candidate_id"),
                    "area": item.get("area"),
                    "forecast_type": item.get("forecast_type"),
                    "title": _compact(item.get("title"), 100),
                    "engine_score": item.get("engine_score"),
                    "passed_safety_gate": item.get("score_breakdown", {}).get(
                        "safety_gate"
                    ),
                }
                for item in ranked[:5]
            ],
            "edition_language": "pl",
            "methodology_version": METHODOLOGY_VERSION,
        },
        "quality_gate": {
            "version": "pl-semantic-quality-v2",
            "status": "passed",
            "methodology_version": METHODOLOGY_VERSION,
            "rejected_candidate_count": len(methodology_rejections),
        },
    }
    v3.validate_edition("pl", edition)
    validate_pl_edition(edition)

    audit = {
        "schema_version": "ai-outlook-pl-methodology-audit-v2",
        "date": publication_date,
        "generated_at": moment.astimezone(legacy.WARSAW).isoformat(
            timespec="seconds"
        ),
        "edition_language": "pl",
        "methodology_version": METHODOLOGY_VERSION,
        "selected_candidate_id": winner["candidate_id"],
        "selected_forecast_type": forecast_type,
        "selected_resolution": resolution,
        "selected_sources": sources,
        "methodology_rejections": methodology_rejections,
        "engine_decision_log": engine_log,
        "ranked_candidates": ranked,
    }
    return edition, audit
