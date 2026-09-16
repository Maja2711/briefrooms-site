#!/usr/bin/env python3
"""Engine-specific weighting for production Investment Event Intelligence.

The shared Event Intelligence collector/classifier remains the single source of event
truth. This module changes only how a classified event is translated into decision
impact for each investment engine.

Principles:
- Weekly Trading is event-dominant and keeps the legacy 1.00x sensitivity.
- Daily Trading is event-sensitive but slightly less reactive than Weekly.
- Stock Trading is fundamentals-dominant for generic macro/geopolitical news.
- Direct sector/company/fundamental events can escalate Stock Trading sensitivity.
- Directional position decisions are symmetric: positive impact can hurt shorts just
  as negative impact can hurt longs.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

import investment_event_intelligence as event

PROFILE_VERSION = "engine-weighting-v1"
WEEKLY = "WEEKLY"
DAILY = "DAILY"
STOCK_TRADING = "STOCK_TRADING"

ENGINE_PROFILES: dict[str, dict[str, Any]] = {
    WEEKLY: {
        "role": "EVENT_DOMINANT",
        "base_weight": 1.00,
        "entry_block": -0.55,
        "reduce_risk": -0.35,
        "close": -0.72,
        "minimum_entry_confidence": 0.64,
        "minimum_close_confidence": 0.72,
    },
    DAILY: {
        "role": "EVENT_SENSITIVE",
        "base_weight": 0.75,
        "entry_block": -0.48,
        "reduce_risk": -0.30,
        "close": -0.64,
        "minimum_entry_confidence": 0.66,
        "minimum_close_confidence": 0.76,
    },
    STOCK_TRADING: {
        "role": "FUNDAMENTALS_DOMINANT",
        "base_weight": 0.30,
        "entry_block": -0.58,
        "reduce_risk": -0.40,
        "close": -0.78,
        "minimum_entry_confidence": 0.70,
        "minimum_close_confidence": 0.80,
    },
}

STOCK_SCOPE_WEIGHTS = {
    "macro_geopolitical": 0.30,
    "sector_direct": 0.60,
    "company_direct": 0.85,
    "company_fundamental": 1.00,
}

FUNDAMENTAL_TERMS = (
    "earnings", "guidance", "revenue", "profit warning", "profit forecast",
    "bankruptcy", "default", "recall", "license revoked", "licence revoked",
    "regulator", "investigation", "antitrust", "acquisition", "merger",
    "export ban", "export control", "sanctions on", "sanctioned", "delisted",
    "production halt", "plant closure", "contract cancelled", "contract canceled",
)

LEGAL_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "company", "co", "plc", "sa",
    "spa", "ag", "holdings", "holding", "group", "ltd", "limited", "nv", "se",
}


def profile(name: str) -> dict[str, Any]:
    key = str(name or WEEKLY).upper()
    if key not in ENGINE_PROFILES:
        raise ValueError(f"Unknown Event Intelligence engine profile: {name}")
    return dict(ENGINE_PROFILES[key])


def thresholds(name: str) -> dict[str, float]:
    cfg = profile(name)
    return {
        "entry_block": float(cfg["entry_block"]),
        "reduce_risk": float(cfg["reduce_risk"]),
        "close": float(cfg["close"]),
        "minimum_entry_confidence": float(cfg["minimum_entry_confidence"]),
        "minimum_close_confidence": float(cfg["minimum_close_confidence"]),
    }


def public_profiles() -> dict[str, Any]:
    return {
        "version": PROFILE_VERSION,
        "profiles": {name: dict(values) for name, values in ENGINE_PROFILES.items()},
        "stock_scope_weights": dict(STOCK_SCOPE_WEIGHTS),
    }


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()


def _phrase(text: str, phrase: str) -> bool:
    phrase = _norm(phrase)
    if not phrase:
        return False
    escaped = re.escape(phrase).replace(r"\ ", r"\s+")
    return re.search(rf"(?<![\w]){escaped}(?![\w])", text) is not None


def _target_name(target: Mapping[str, Any]) -> str:
    direct = str(target.get("name") or "").strip()
    if direct:
        return direct
    payload = target.get("candidate_payload")
    if isinstance(payload, Mapping):
        selection = payload.get("selection")
        if isinstance(selection, Mapping):
            return str(selection.get("name") or "").strip()
    return ""


def _company_phrases(target: Mapping[str, Any]) -> list[str]:
    phrases: list[str] = []
    symbol = str(target.get("symbol") or "").strip()
    if len(symbol) >= 4:
        phrases.append(symbol)
    name = _target_name(target)
    if name:
        tokens = [
            token for token in re.findall(r"[a-z0-9]+", _norm(name))
            if token not in LEGAL_SUFFIXES and len(token) >= 3
        ]
        if len(tokens) >= 2:
            phrases.append(" ".join(tokens[:3]))
            phrases.append(" ".join(tokens[:2]))
        elif tokens and len(tokens[0]) >= 6:
            phrases.append(tokens[0])
    return list(dict.fromkeys(phrase for phrase in phrases if phrase))


def _sector_direct(target: Mapping[str, Any], row: Mapping[str, Any]) -> bool:
    sector = _norm(target.get("sector"))
    tags = {str(tag) for tag in row.get("scenario_tags") or []}
    if not sector:
        return False
    if "energy_supply" in tags and any(token in sector for token in ("energy", "oil", "gas", "energia", "paliwa", "rafiner")):
        return True
    if tags & {"middle_east", "russia_ukraine"} and any(token in sector for token in ("defense", "defence", "aerospace", "zbrojeni", "obron", "airline", "aviation", "lotnic", "travel", "tourism")):
        return True
    if "china_taiwan" in tags and any(token in sector for token in ("semiconductor", "technology", "tech", "software", "technolog")):
        return True
    if "trade_sanctions" in tags and any(token in sector for token in ("semiconductor", "technology", "industrial", "energy", "bank", "financial")):
        return True
    return False


def classify_scope(target: Mapping[str, Any], row: Mapping[str, Any], engine_profile: str) -> str:
    if str(engine_profile).upper() != STOCK_TRADING:
        return "macro_geopolitical"
    text = _norm(row.get("title"))
    company_direct = any(_phrase(text, phrase) for phrase in _company_phrases(target))
    if company_direct and any(term in text for term in FUNDAMENTAL_TERMS):
        return "company_fundamental"
    if company_direct:
        return "company_direct"
    if _sector_direct(target, row):
        return "sector_direct"
    return "macro_geopolitical"


def event_weight(engine_profile: str, scope: str) -> float:
    key = str(engine_profile or WEEKLY).upper()
    cfg = profile(key)
    if key == STOCK_TRADING:
        return float(STOCK_SCOPE_WEIGHTS.get(scope, cfg["base_weight"]))
    return float(cfg["base_weight"])


def _adverse_impact(impact: float, direction: str) -> float:
    side = str(direction or "").upper()
    if side in {"SHORT", "SELL"}:
        return -float(impact)
    return float(impact)


def directional_decision(score: Mapping[str, Any], direction: str) -> str:
    """Translate signed asset impact into risk for a held LONG/SHORT direction."""
    limits = score.get("thresholds_applied") or thresholds(str(score.get("engine_profile") or WEEKLY))
    confidence = float(score.get("confidence") or 0.0)
    adverse = _adverse_impact(float(score.get("normalized_impact") or 0.0), direction)
    if adverse <= float(limits["close"]) and confidence >= float(limits["minimum_close_confidence"]):
        return "CLOSE"
    if adverse <= float(limits["reduce_risk"]):
        return "REDUCE_RISK"
    if adverse >= 0.35:
        return "SUPPORTIVE"
    return "HOLD"


def entry_blocked_for_direction(score: Mapping[str, Any], direction: str) -> bool:
    limits = score.get("thresholds_applied") or thresholds(str(score.get("engine_profile") or WEEKLY))
    confidence = float(score.get("confidence") or 0.0)
    adverse = _adverse_impact(float(score.get("normalized_impact") or 0.0), direction)
    return adverse <= float(limits["entry_block"]) and confidence >= float(limits["minimum_entry_confidence"])


def score_target(
    target: Mapping[str, Any],
    events: Iterable[Mapping[str, Any]],
    *,
    engine_profile: str = WEEKLY,
) -> dict[str, Any]:
    key = str(engine_profile or WEEKLY).upper()
    cfg = profile(key)
    limits = thresholds(key)
    coefficient = event.target_coefficient(target)
    contributions: list[dict[str, Any]] = []

    for row in events:
        strength = float(row.get("event_strength") or 0.0)
        pressure = float(row.get("pressure") or 0.0)
        raw_signed = pressure * coefficient * strength
        scope = classify_scope(target, row, key)
        weight = event_weight(key, scope)
        signed = raw_signed * weight
        if abs(signed) < 0.035:
            continue
        contributions.append({
            "event_id": row.get("event_id"),
            "title": row.get("title"),
            "published_at": row.get("published_at"),
            "source": row.get("source"),
            "source_ref": row.get("source_ref"),
            "confidence": row.get("confidence"),
            "action_type": row.get("action_type"),
            "event_type": row.get("event_type"),
            "scenario_tags": row.get("scenario_tags"),
            "event_scope": scope,
            "engine_weight": round(weight, 4),
            "raw_signed_impact": round(raw_signed, 4),
            "signed_impact": round(signed, 4),
        })

    contributions.sort(key=lambda row: abs(float(row.get("signed_impact") or 0.0)), reverse=True)
    top = contributions[:3]
    aggregate = 0.0
    raw_aggregate = 0.0
    multipliers = (1.0, 0.45, 0.25)
    for idx, row in enumerate(top):
        aggregate += float(row["signed_impact"]) * multipliers[idx]
        raw_aggregate += float(row["raw_signed_impact"]) * multipliers[idx]
    aggregate = event.clamp(aggregate)
    raw_aggregate = event.clamp(raw_aggregate)
    confidence = max((float(row.get("confidence") or 0.0) for row in top), default=0.0)

    action = "HOLD"
    if aggregate <= limits["close"] and confidence >= limits["minimum_close_confidence"]:
        action = "CLOSE"
    elif aggregate <= limits["reduce_risk"]:
        action = "REDUCE_RISK"
    elif aggregate >= 0.35:
        action = "SUPPORTIVE"

    dominant = top[0] if top else {}
    return {
        "target_id": target.get("target_id"),
        "symbol": target.get("symbol"),
        "market": target.get("market"),
        "sector": target.get("sector"),
        "engine_profile": key,
        "engine_role": cfg["role"],
        "profile_base_weight": float(cfg["base_weight"]),
        "exposure_coefficient": coefficient,
        "raw_normalized_impact": round(raw_aggregate, 4),
        "normalized_impact": round(aggregate, 4),
        "score_delta": round(aggregate * 20.0, 2),
        "confidence": round(confidence, 4),
        "dominant_event_scope": dominant.get("event_scope"),
        "dominant_engine_weight": dominant.get("engine_weight", float(cfg["base_weight"])),
        "thresholds_applied": limits,
        "decision_overlay": action,
        "entry_blocked": aggregate <= limits["entry_block"] and confidence >= limits["minimum_entry_confidence"],
        "top_events": top,
    }
