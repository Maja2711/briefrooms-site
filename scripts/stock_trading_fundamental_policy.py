#!/usr/bin/env python3
"""Frozen, bounded US Stock Trading fundamental quality gate.

Research and production must use identical bytes of this policy. It can only
reject a candidate from an already-eligible ordered frontier; it never adds,
boosts, or relaxes a trading/risk/quote gate. No network or LLM usage.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone, date
from typing import Any, Mapping, Sequence

POLICY_ID = "US_QUALITY_GATE_55_V1"
MIN_FUNDAMENTAL_SCORE = 55.0
MAX_ANNUAL_REPORT_AGE_DAYS = 475
SCORE_FACTORS = (
    ("revenue_growth_yoy", 25, -0.15, 0.20),
    ("eps_growth_yoy", 25, -0.30, 0.30),
    ("operating_margin", 20, -0.05, 0.25),
    ("fcf_margin", 20, -0.10, 0.20),
    ("return_on_equity", 10, -0.05, 0.25),
)


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def utc(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("point-in-time receipt requires timezone")
    return parsed.astimezone(timezone.utc)


def recompute_score(metrics: Mapping[str, Any]) -> tuple[float | None, int]:
    points = 0.0
    weight_sum = 0
    for key, weight, low, high in SCORE_FACTORS:
        raw = finite(metrics.get(key))
        if raw is None:
            continue
        normalized = max(0.0, min(100.0, 100.0 * (raw - low) / (high - low)))
        points += weight * normalized
        weight_sum += weight
    return (round(points / weight_sum, 4) if weight_sum >= 65 else None), weight_sum


def verify_fundamental(candidate: Mapping[str, Any], *, decision_at: str) -> tuple[bool, str]:
    """Check an actual signed-by-content SEC fact object, not only the score."""
    data = candidate.get("fundamental_analysis")
    if not isinstance(data, Mapping):
        return False, "fundamental_analysis_missing"
    if data.get("status") != "COMPLETE" or data.get("production_decision_influence") is not False:
        return False, "fundamental_incomplete"
    score = finite(data.get("fundamental_score"))
    metrics = data.get("metrics")
    if score is None or not 0 <= score <= 100 or not isinstance(metrics, Mapping):
        return False, "fundamental_score_invalid"
    recalculated, weight = recompute_score(metrics)
    if recalculated is None or abs(score - recalculated) > 0.0002 or weight < 65:
        return False, "fundamental_score_not_reproducible"
    try:
        decision = utc(decision_at)
        fiscal = date.fromisoformat(str(data["fiscal_end"]))
        if fiscal > decision.date() or (decision.date() - fiscal).days > MAX_ANNUAL_REPORT_AGE_DAYS:
            return False, "fundamental_fiscal_period_invalid"
        sources = data.get("source_facts")
        if not isinstance(sources, Mapping) or "revenue_fy" not in sources:
            return False, "fundamental_source_missing"
        for key, fact in sources.items():
            if not isinstance(fact, Mapping) or not str(fact.get("accession") or "").strip():
                return False, "fundamental_accession_missing"
            filed = date.fromisoformat(str(fact["filed"]))
            end = date.fromisoformat(str(fact["end"]))
            if filed >= decision.date() or end > decision.date():
                return False, "fundamental_file_after_decision"
            if fact.get("form") not in {"10-K", "10-K/A", "10-Q", "10-Q/A"}:
                return False, "fundamental_unapproved_form"
    except (KeyError, TypeError, ValueError):
        return False, "fundamental_temporal_provenance_invalid"
    return True, "fundamental_verified"


def original_candidates(opportunity: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Ranked candidates that already passed every pre-existing opportunity gate.

    We do not inspect portfolio slot count: this is an open-slot counterfactual.
    Production still checks the real slot cap and fresh execution price.
    """
    cash = opportunity.get("cash") or {}
    threshold = (finite(cash.get("utility")) or 50.0) + (finite(cash.get("minimum_new_position_edge_points")) or 5.0)
    rows = [
        dict(row) for row in opportunity.get("evaluated_candidates") or []
        if isinstance(row, Mapping)
        and row.get("eligible") is True
        and row.get("already_held") is not True
        and finite(row.get("utility")) is not None
        and float(row["utility"]) >= threshold
    ]
    return sorted(rows, key=lambda row: (-float(row["utility"]), int(row.get("deep_rank") or 999999)))


def select_one(opportunity: Mapping[str, Any], *, apply_fundamentals: bool) -> dict[str, Any] | None:
    ranked = original_candidates(opportunity)
    if not apply_fundamentals:
        return ranked[0] if ranked else None
    decision_at = str(opportunity.get("generated_at") or "")
    for row in ranked:
        verified, _ = verify_fundamental(row, decision_at=decision_at)
        if verified and float(row["fundamental_analysis"]["fundamental_score"]) >= MIN_FUNDAMENTAL_SCORE:
            return row
    return None


def filter_live(
    candidates: Sequence[Mapping[str, Any]], *, decision_at: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep only proven quality; *never* re-rank or invent opportunities."""
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in candidates:
        valid, reason = verify_fundamental(row, decision_at=decision_at)
        score = finite((row.get("fundamental_analysis") or {}).get("fundamental_score"))
        if valid and score is not None and score >= MIN_FUNDAMENTAL_SCORE:
            accepted.append(dict(row))
        else:
            rejected.append({
                "symbol": row.get("symbol"), "reason": reason if not valid else "below_fundamental_quality_floor",
                "fundamental_score": score,
            })
    return accepted, rejected
