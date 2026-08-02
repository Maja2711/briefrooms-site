#!/usr/bin/env python3
"""Deterministic candidate ranking for AI Outlook Engine v1.1.

The language model proposes forecast candidates and dimension scores. This module
adds source provenance, immutable scoring policy, structured resolution rules,
regulated-content governance and an auditable rejection log.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from datetime import date
from types import MappingProxyType
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

ENGINE_VERSION = "ai-outlook-engine-v1.1"
WEIGHTS_VERSION = "weights-2026-08-v1"
SCORING_POLICY_VERSION = "scoring-policy-v1.1"
PROVENANCE_SCHEMA_VERSION = "provenance-v1"
RESOLUTION_SCHEMA_VERSION = "resolution-v1"
GOVERNANCE_SCHEMA_VERSION = "governance-v1"
BRIER_PUBLIC_MIN_N = 30

AREA_ORDER = ("economy", "geopolitics", "health", "science")
AREA_PRIORITY = {area: index for index, area in enumerate(AREA_ORDER)}
AREA_LABELS = {
    "economy": {"pl": "Ekonomia", "en": "Economy"},
    "geopolitics": {"pl": "Geopolityka", "en": "Geopolitics"},
    "health": {"pl": "Zdrowie", "en": "Health"},
    "science": {"pl": "Nauka", "en": "Science"},
}
AREA_THRESHOLDS = MappingProxyType({
    "economy": 68.0,
    "geopolitics": 68.0,
    "health": 72.0,
    "science": 72.0,
})
FALLBACK_MIN_SCORE = 64.0
DIMENSION_WEIGHTS = MappingProxyType({
    "evidence_quality": 0.22,
    "measurability": 0.20,
    "causal_strength": 0.18,
    "verifiability": 0.15,
    "novelty": 0.10,
    "source_quality": 0.15,
})

CONTENT_CATEGORY_AREA = MappingProxyType({
    "macro": "economy",
    "market_investment": "economy",
    "regulatory": "economy",
    "technology": "science",
    "geopolitics": "geopolitics",
    "public_health": "health",
    "clinical_health": "health",
    "science_research": "science",
})
DISCLAIMER_CATALOG = MappingProxyType({
    "general_forecast_v1": {
        "pl": "Prognoza AI oparta na wskazanych źródłach. Nie jest ustalonym faktem.",
        "en": "An AI forecast based on the listed sources. It is not an established fact.",
    },
    "investment_forecast_v1": {
        "pl": "Prognoza AI oparta na wskazanych źródłach. Nie stanowi rekomendacji ani porady inwestycyjnej.",
        "en": "An AI forecast based on the listed sources. It is not investment advice or a recommendation.",
    },
    "medical_forecast_v1": {
        "pl": "Prognoza AI ma charakter informacyjny i nie zastępuje porady, diagnozy ani leczenia medycznego.",
        "en": "This AI forecast is informational and does not replace medical advice, diagnosis or treatment.",
    },
})

AREA_KEYWORDS = {
    "economy": {"gospodarka", "ekonomia", "inflacja", "pkb", "bezrobocie", "stopy", "bank", "kredyt", "obligacje", "waluta", "rynek", "giełda", "spółka", "przychody", "zysk", "marża", "nieruchomości", "mieszkania", "handel", "podatki", "budżet", "economy", "inflation", "gdp", "unemployment", "rates", "banking", "credit", "bond", "currency", "market", "company", "earnings", "revenue", "housing", "trade", "tax", "budget"},
    "geopolitics": {"geopolityka", "wojna", "konflikt", "sankcje", "nato", "rosja", "ukraina", "chiny", "usa", "iran", "izrael", "granica", "wybory", "dyplomacja", "bezpieczeństwo", "geopolitics", "war", "conflict", "sanctions", "russia", "ukraine", "china", "border", "election", "diplomacy", "security", "military"},
    "health": {"zdrowie", "medycyna", "pacjent", "choroba", "leczenie", "lek", "szpital", "epidemia", "szczepionka", "rak", "serce", "kliniczny", "health", "medicine", "patient", "disease", "treatment", "drug", "hospital", "epidemic", "vaccine", "cancer", "clinical"},
    "science": {"nauka", "badanie", "naukowcy", "odkrycie", "kosmos", "fizyka", "chemia", "biologia", "technologia", "sztuczna inteligencja", "klimat", "science", "research", "scientists", "discovery", "space", "physics", "chemistry", "biology", "technology", "artificial intelligence", "climate"},
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
WIRE_ORIGINS = ("reuters", "associated press", "ap", "pap", "afp", "bloomberg")
TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}
RESOLUTION_OPERATORS = {">", ">=", "<", "<=", "==", "increase_by", "decrease_by", "between"}


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _text(value)).strip("-")


def _sha(value: str, length: int = 20) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def host_from_url(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def canonical_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS]
        path = re.sub(r"/{2,}", "/", parsed.path or "/").rstrip("/") or "/"
        return urlunparse((parsed.scheme.lower(), (parsed.netloc or "").lower(), path, "", urlencode(query), ""))
    except Exception:
        return str(url or "").strip()


def infer_area(item: dict[str, Any]) -> str | None:
    haystack = " ".join(_text(item.get(field)) for field in ("category", "title", "summary", "details", "source"))
    scores = {area: sum(2 if " " in keyword else 1 for keyword in keywords if keyword in haystack) for area, keywords in AREA_KEYWORDS.items()}
    best = max(AREA_ORDER, key=lambda area: (scores[area], -AREA_PRIORITY[area]))
    return best if scores[best] > 0 else None


def source_quality(item: dict[str, Any]) -> int:
    host = host_from_url(str(item.get("url") or item.get("link") or ""))
    if any(hint in host for hint in AUTHORITATIVE_HOST_HINTS):
        return 94
    if any(hint in host for hint in ESTABLISHED_NEWS_HOST_HINTS):
        return 78
    return 62 if host else 45


def _date_part(value: Any) -> str:
    match = re.search(r"\d{4}-\d{2}-\d{2}", str(value or ""))
    return match.group(0) if match else "unknown-date"


def _origin_organization(item: dict[str, Any]) -> str:
    explicit = item.get("origin_organization") or item.get("primary_source") or item.get("agency")
    if explicit:
        return str(explicit).strip()
    haystack = " ".join(_text(item.get(field)) for field in ("source", "title", "summary"))
    for origin in WIRE_ORIGINS:
        if re.search(rf"\b{re.escape(origin)}\b", haystack):
            return origin.upper() if origin in {"ap", "pap", "afp"} else origin.title()
    host = host_from_url(str(item.get("url") or ""))
    return host or "unknown"


def _story_fingerprint(item: dict[str, Any]) -> str:
    words = re.findall(r"[a-ząćęłńóśźż0-9]{4,}", _text(item.get("title")))
    stop = {"oraz", "który", "która", "przez", "about", "after", "with", "from", "that", "this", "will"}
    compact = sorted({word for word in words if word not in stop})[:12]
    return _sha("|".join(compact) or canonical_url(str(item.get("url") or "")), 16)


def provenance_record(item: dict[str, Any]) -> dict[str, Any]:
    explicit_id = str(item.get("provenance_id") or "").strip()
    origin_url = canonical_url(str(item.get("origin_document_url") or item.get("primary_source_url") or ""))
    source_url = canonical_url(str(item.get("url") or item.get("link") or ""))
    organization = _origin_organization(item)
    published = _date_part(item.get("origin_published_at") or item.get("published_at"))
    if explicit_id:
        provenance_id = explicit_id
        basis = "explicit"
        confidence = "high"
    elif origin_url:
        provenance_id = "prov_" + _sha(origin_url)
        basis = "origin_document_url"
        confidence = "high"
    elif organization.lower() not in {"unknown", host_from_url(source_url)}:
        provenance_id = "prov_" + _sha(f"{_slug(organization)}|{published}|{_story_fingerprint(item)}")
        basis = "origin_organization_date_story"
        confidence = "medium"
    else:
        provenance_id = "prov_" + _sha(source_url or f"{published}|{_story_fingerprint(item)}")
        basis = "canonical_source_url"
        confidence = "low"
    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "provenance_id": provenance_id,
        "origin_organization": organization,
        "origin_document_url": origin_url or None,
        "origin_published_at": published if published != "unknown-date" else None,
        "source_url": source_url,
        "basis": basis,
        "confidence": confidence,
    }


def annotate_sources(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    annotated = []
    for index, original in enumerate(items, start=1):
        item = dict(original)
        item.update({"id": index, "area": infer_area(item), "source_quality": source_quality(item)})
        item["host"] = host_from_url(str(item.get("url") or item.get("link") or ""))
        item["provenance"] = provenance_record(item)
        item["provenance_id"] = item["provenance"]["provenance_id"]
        annotated.append(item)
    return annotated


def balanced_source_pool(items: list[dict[str, Any]], per_area: int = 7, total: int = 24) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in annotate_sources(items):
        if item.get("area") in AREA_ORDER:
            buckets[item["area"]].append(item)
    selected: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for area in AREA_ORDER:
        ranked = sorted(buckets[area], key=lambda x: (int(x.get("source_quality") or 0), bool(x.get("published_at"))), reverse=True)
        for item in ranked[:per_area]:
            url = canonical_url(str(item.get("url") or item.get("link") or ""))
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            selected.append(item)
            if len(selected) >= total:
                break
        if len(selected) >= total:
            break
    for index, item in enumerate(selected, start=1):
        item["id"] = index
    return selected


def weights_snapshot() -> dict[str, float]:
    return {name: float(weight) for name, weight in DIMENSION_WEIGHTS.items()}


def thresholds_snapshot() -> dict[str, float]:
    return {name: float(value) for name, value in AREA_THRESHOLDS.items()}


def _dimension(candidate: dict[str, Any], name: str) -> float:
    try:
        value = float(candidate.get(name))
    except (TypeError, ValueError):
        value = 0.0
    return max(0.0, min(100.0, value))


def candidate_sources(candidate: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {int(item["id"]): item for item in items if "id" in item}
    resolved, seen = [], set()
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
    sources = candidate_sources(candidate, items)
    if not sources:
        return 0.0, {"source_count": 0, "independent_provenance_count": 0, "authoritative": False}
    qualities = [float(item.get("source_quality") or 0) for item in sources]
    provenance_ids = {str(item.get("provenance_id") or "") for item in sources if item.get("provenance_id")}
    hosts = {str(item.get("host") or "") for item in sources if item.get("host")}
    authoritative = any(quality >= 90 for quality in qualities)
    independent_bonus = min(8.0, max(0, len(provenance_ids) - 1) * 4.0)
    component = min(100.0, sum(qualities) / len(qualities) + independent_bonus)
    return component, {
        "source_count": len(sources),
        "distinct_hosts": len(hosts),
        "independent_provenance_count": len(provenance_ids),
        "provenance_ids": sorted(provenance_ids),
        "authoritative": authoritative,
        "average_source_quality": round(sum(qualities) / len(qualities), 2),
        "independent_source_bonus": independent_bonus,
    }


def normalize_resolution(candidate: dict[str, Any], publication_date: str | None = None) -> dict[str, Any]:
    raw = candidate.get("resolution")
    if not isinstance(raw, dict):
        raise ValueError("resolution must be a JSON object")
    required = ("metric", "comparison_operator", "threshold", "unit", "data_source_for_verification", "resolution_date")
    missing = [field for field in required if raw.get(field) in (None, "")]
    if missing:
        raise ValueError("resolution missing fields: " + ",".join(missing))
    operator = str(raw["comparison_operator"]).strip()
    if operator not in RESOLUTION_OPERATORS:
        raise ValueError("unsupported resolution comparison_operator")
    threshold = raw["threshold"]
    if operator == "between":
        if not isinstance(threshold, list) or len(threshold) != 2 or not all(_finite_number(v) for v in threshold):
            raise ValueError("between threshold must contain two numbers")
        threshold = [float(threshold[0]), float(threshold[1])]
    elif not _finite_number(threshold):
        raise ValueError("resolution threshold must be numeric")
    else:
        threshold = float(threshold)
    resolution_date = str(raw["resolution_date"]).strip()
    try:
        resolved_date = date.fromisoformat(resolution_date)
    except ValueError as exc:
        raise ValueError("resolution_date must be YYYY-MM-DD") from exc
    if publication_date and resolved_date <= date.fromisoformat(publication_date):
        raise ValueError("resolution_date must be after publication date")
    verification_url = str(raw.get("verification_url") or "").strip()
    if verification_url and not verification_url.startswith("https://"):
        raise ValueError("verification_url must use https")
    return {
        "schema_version": RESOLUTION_SCHEMA_VERSION,
        "metric": str(raw["metric"]).strip()[:160],
        "comparison_operator": operator,
        "threshold": threshold,
        "unit": str(raw["unit"]).strip()[:60],
        "baseline_date": str(raw.get("baseline_date") or "").strip() or None,
        "baseline_value": float(raw["baseline_value"]) if _finite_number(raw.get("baseline_value")) else None,
        "data_source_for_verification": str(raw["data_source_for_verification"]).strip()[:200],
        "verification_url": verification_url or None,
        "resolution_date": resolution_date,
        "geography": str(raw.get("geography") or "").strip()[:120] or None,
        "status": "open",
    }


def governance_for(candidate: dict[str, Any]) -> dict[str, Any]:
    category = str(candidate.get("content_category") or "").strip().lower()
    if category not in CONTENT_CATEGORY_AREA:
        raise ValueError("unsupported content_category")
    area = str(candidate.get("area") or "").strip().lower()
    if CONTENT_CATEGORY_AREA[category] != area:
        raise ValueError("content_category conflicts with area")
    if category == "market_investment":
        risk_class, disclaimer_id = "regulated_financial_content", "investment_forecast_v1"
    elif category in {"public_health", "clinical_health"}:
        risk_class, disclaimer_id = "medical_information", "medical_forecast_v1"
    else:
        risk_class, disclaimer_id = "general_forecast", "general_forecast_v1"
    return {
        "schema_version": GOVERNANCE_SCHEMA_VERSION,
        "content_category": category,
        "risk_class": risk_class,
        "disclaimer_required": True,
        "disclaimer_id": disclaimer_id,
        "disclaimers": dict(DISCLAIMER_CATALOG[disclaimer_id]),
    }


def _novelty_penalty(candidate: dict[str, Any], recent_titles: list[str]) -> float:
    tokens = set(re.findall(r"[a-ząćęłńóśźż0-9]{4,}", _text(candidate.get("title"))))
    if not tokens:
        return 12.0
    maximum = 0.0
    for old in recent_titles:
        old_tokens = set(re.findall(r"[a-ząćęłńóśźż0-9]{4,}", _text(old)))
        if old_tokens:
            maximum = max(maximum, len(tokens & old_tokens) / max(1, len(tokens | old_tokens)))
    return 18.0 if maximum >= 0.55 else 9.0 if maximum >= 0.35 else 0.0


def _finite_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def score_candidate(candidate: dict[str, Any], items: list[dict[str, Any]], recent_titles: list[str], publication_date: str | None = None) -> dict[str, Any]:
    area = str(candidate.get("area") or "").strip().lower()
    if area not in AREA_ORDER:
        raise ValueError(f"unsupported area: {area!r}")
    sources = candidate_sources(candidate, items)
    if not 1 <= len(sources) <= 3:
        raise ValueError("candidate must cite 1-3 known sources")
    if any(item.get("area") not in (area, None) for item in sources):
        raise ValueError("candidate sources conflict with area")
    resolution = normalize_resolution(candidate, publication_date)
    governance = governance_for(candidate)
    source_score, source_meta = _source_component(candidate, items)
    dimensions = {
        "evidence_quality": _dimension(candidate, "evidence_quality"),
        "measurability": _dimension(candidate, "measurability"),
        "causal_strength": _dimension(candidate, "causal_strength"),
        "verifiability": _dimension(candidate, "verifiability"),
        "novelty": _dimension(candidate, "novelty"),
        "source_quality": source_score,
    }
    weighted = sum(dimensions[name] * DIMENSION_WEIGHTS[name] for name in DIMENSION_WEIGHTS)
    speculation_risk = _dimension(candidate, "speculation_risk")
    speculation_penalty = max(0.0, speculation_risk - 45.0) * 0.22
    novelty_penalty = _novelty_penalty(candidate, recent_titles)
    reasons: list[dict[str, Any]] = []
    safety_gate = True
    if area in {"health", "science"} and not source_meta["authoritative"] and source_meta["independent_provenance_count"] < 2:
        safety_gate = False
        reasons.append({"code": "INSUFFICIENT_INDEPENDENT_PROVENANCE", "required": 2, "actual": source_meta["independent_provenance_count"]})
    score = max(0.0, min(100.0, weighted - speculation_penalty - novelty_penalty))
    if not safety_gate:
        score = min(score, AREA_THRESHOLDS[area] - 0.1)
    enriched = dict(candidate)
    enriched.update({
        "area": area,
        "resolution": resolution,
        "governance": governance,
        "engine_score": round(score, 2),
        "score_breakdown": {
            **{name: round(value, 2) for name, value in dimensions.items()},
            "speculation_risk": round(speculation_risk, 2),
            "speculation_penalty": round(speculation_penalty, 2),
            "novelty_penalty": round(novelty_penalty, 2),
            "threshold": AREA_THRESHOLDS[area],
            "safety_gate": safety_gate,
            "safety_reasons": reasons,
            **source_meta,
        },
    })
    return enriched


def _rejection_reasons(item: dict[str, Any], winner: dict[str, Any] | None) -> list[dict[str, Any]]:
    reasons = list(item.get("score_breakdown", {}).get("safety_reasons") or [])
    threshold = AREA_THRESHOLDS[item["area"]]
    if item["engine_score"] < threshold:
        reasons.append({"code": "BELOW_AREA_THRESHOLD", "actual": item["engine_score"], "required": threshold})
    if winner and item is not winner:
        if AREA_PRIORITY[item["area"]] > AREA_PRIORITY[winner["area"]]:
            reasons.append({"code": "LOWER_PRIORITY_AREA", "selected_area": winner["area"]})
        elif item["area"] == winner["area"] and item["engine_score"] < winner["engine_score"]:
            reasons.append({"code": "LOWER_SCORE_IN_AREA", "selected_score": winner["engine_score"]})
        elif not reasons:
            reasons.append({"code": "NOT_SELECTED"})
    return reasons


def select_candidate(candidates: list[dict[str, Any]], items: list[dict[str, Any]], recent_titles: list[str], publication_date: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    scored, invalid_logs = [], []
    for index, candidate in enumerate(candidates, start=1):
        candidate_id = str(candidate.get("candidate_id") or f"candidate_{index:02d}")
        candidate = {**candidate, "candidate_id": candidate_id}
        try:
            scored.append(score_candidate(candidate, items, recent_titles, publication_date))
        except (TypeError, ValueError) as exc:
            invalid_logs.append({
                "candidate_id": candidate_id,
                "area": str(candidate.get("area") or "unknown"),
                "title": str(candidate.get("title") or "")[:100],
                "decision": "rejected",
                "engine_score": None,
                "rejection_reasons": [{"code": "INVALID_CANDIDATE", "detail": str(exc)}],
            })
    if not scored:
        raise ValueError("no valid AI Outlook candidates")
    winner = None
    for area in AREA_ORDER:
        eligible = [x for x in scored if x["area"] == area and x["engine_score"] >= AREA_THRESHOLDS[area] and x["score_breakdown"]["safety_gate"]]
        if eligible:
            winner = max(eligible, key=lambda x: x["engine_score"])
            winner["selection_mode"] = "priority_area_threshold"
            break
    if winner is None:
        fallback = max(scored, key=lambda x: x["engine_score"])
        if fallback["engine_score"] < FALLBACK_MIN_SCORE or not fallback["score_breakdown"]["safety_gate"]:
            raise ValueError("no AI Outlook candidate cleared publication quality")
        winner = fallback
        winner["selection_mode"] = "global_quality_fallback"
    ranked = sorted(scored, key=lambda x: x["engine_score"], reverse=True)
    decision_log = invalid_logs + [
        {
            "candidate_id": item["candidate_id"],
            "area": item["area"],
            "title": str(item.get("title") or "")[:100],
            "decision": "selected" if item is winner else "rejected",
            "engine_score": item["engine_score"],
            "threshold": AREA_THRESHOLDS[item["area"]],
            "rejection_reasons": [] if item is winner else _rejection_reasons(item, winner),
            "score_breakdown": item["score_breakdown"],
        }
        for item in ranked
    ]
    return winner, ranked, decision_log


def probability_from_score(score: float) -> int:
    value = round(45.0 + 0.35 * max(0.0, min(100.0, float(score))))
    return int(max(55, min(80, value)))
