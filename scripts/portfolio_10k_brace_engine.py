#!/usr/bin/env python3
"""Transparent BRACE decision engine for the BriefRooms 10K portfolio.

BRACE = Business quality, Results & revisions, Attractiveness, Confirmation,
Risk, Context and Events. The module contains only deterministic scoring and
decision logic so it can be unit-tested without network access.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

PILLAR_WEIGHTS: Dict[str, float] = {
    "business_quality": 0.20,
    "results_revisions": 0.20,
    "attractiveness": 0.15,
    "confirmation": 0.15,
    "risk": 0.15,
    "context": 0.10,
    "events_information": 0.05,
}
PILLARS: Tuple[str, ...] = tuple(PILLAR_WEIGHTS)


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _as_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()


@dataclass(frozen=True)
class Evidence:
    code: str
    pillar: str
    direction: int
    strength: float
    quality: float
    observed_at: str
    half_life_days: int
    source: str
    description_pl: str
    description_en: str

    def decayed_weight(self, as_of: date | datetime | str) -> float:
        """Signed evidence weight with exponential half-life decay.

        Negative evidence is deliberately asymmetric: at equal strength and
        quality it counts 1.35x as much as positive evidence.
        """
        age = max(0, (_as_date(as_of) - _as_date(self.observed_at)).days)
        half_life = max(1, int(self.half_life_days))
        decay = 0.5 ** (age / half_life)
        asymmetry = 1.35 if self.direction < 0 else 1.0
        return (
            float(self.direction)
            * clamp(self.strength, 0, 1)
            * clamp(self.quality, 0, 1)
            * decay
            * asymmetry
        )

    def to_dict(self, as_of: date | datetime | str) -> Dict[str, Any]:
        payload = asdict(self)
        payload["decayed_weight"] = round(self.decayed_weight(as_of), 6)
        return payload


@dataclass(frozen=True)
class DecisionInput:
    score: float
    confidence: float
    pillar_scores: Mapping[str, float]
    contradictions: Sequence[str]
    current_weight: Optional[float]
    target_weight: float
    days_to_earnings: Optional[int]
    material_risk_count: int = 0
    previous_score: Optional[float] = None
    previous_decision: Optional[str] = None
    asset_type: str = "Stock"


@dataclass(frozen=True)
class Decision:
    code: str
    urgency: str
    reason_codes: Tuple[str, ...]
    max_position_weight: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "urgency": self.urgency,
            "reason_codes": list(self.reason_codes),
            "max_position_weight": self.max_position_weight,
        }


def evidence_adjustment(
    evidence: Iterable[Evidence], as_of: date | datetime | str
) -> Dict[str, float]:
    """Return bounded score adjustments by pillar.

    A full-strength, high-quality fresh item moves a pillar by roughly 12
    points. Multiple headlines cannot move a pillar by more than 20 points.
    """
    totals = {pillar: 0.0 for pillar in PILLARS}
    for item in evidence:
        if item.pillar in totals:
            totals[item.pillar] += 12.0 * item.decayed_weight(as_of)
    return {pillar: clamp(value, -20.0, 20.0) for pillar, value in totals.items()}


def contradiction_flags(pillar_scores: Mapping[str, float]) -> List[str]:
    p = {name: finite(pillar_scores.get(name)) for name in PILLARS}
    flags: List[str] = []
    if (p["business_quality"] or 50) >= 65 and (p["confirmation"] or 50) <= 40:
        flags.append("fundamentals_vs_market")
    if (p["results_revisions"] or 50) >= 65 and (p["confirmation"] or 50) <= 38:
        flags.append("results_vs_price_reaction")
    if (p["attractiveness"] or 50) <= 32 and (p["confirmation"] or 50) >= 72:
        flags.append("price_outrunning_valuation")
    if (p["business_quality"] or 50) <= 38 and (p["attractiveness"] or 50) >= 68:
        flags.append("cheap_for_a_reason")
    if (p["risk"] or 50) <= 32 and (p["confirmation"] or 50) >= 70:
        flags.append("momentum_masking_risk")
    return flags


def aggregate_score(
    pillar_scores: Mapping[str, float],
    completeness: Mapping[str, float] | None = None,
    evidence: Iterable[Evidence] = (),
    as_of: date | datetime | str | None = None,
) -> Dict[str, Any]:
    """Calculate score, confidence and transparent weighted contributions."""
    as_of = as_of or date.today()
    completeness = completeness or {}
    adjustments = evidence_adjustment(evidence, as_of)
    normalized: Dict[str, float] = {}
    contributions: Dict[str, float] = {}
    observed_weight = 0.0
    completeness_weight = 0.0

    for pillar, weight in PILLAR_WEIGHTS.items():
        raw = finite(pillar_scores.get(pillar))
        if pillar in completeness:
            coverage = clamp(finite(completeness.get(pillar)) or 0.0, 0.0, 1.0)
        else:
            coverage = 1.0 if raw is not None else 0.0
        score = 50.0 if raw is None else raw
        score = clamp(score + adjustments[pillar])
        normalized[pillar] = round(score, 3)
        contributions[pillar] = round(score * weight, 4)
        observed_weight += weight if raw is not None else 0.0
        completeness_weight += weight * coverage

    contradictions = contradiction_flags(normalized)
    contradiction_penalty = min(14.0, 4.0 * len(contradictions))
    score = sum(contributions.values()) - contradiction_penalty

    confidence = 100.0 * completeness_weight
    confidence -= 7.5 * len(contradictions)
    if observed_weight < 0.70:
        confidence -= 10.0

    return {
        "score": round(clamp(score), 2),
        "confidence": round(clamp(confidence), 2),
        "pillar_scores": normalized,
        "weighted_contributions": contributions,
        "contradictions": contradictions,
        "contradiction_penalty": round(contradiction_penalty, 2),
        "data_completeness": round(clamp(completeness_weight, 0.0, 1.0), 4),
        "evidence_adjustments": {k: round(v, 3) for k, v in adjustments.items()},
    }


def decide(inputs: DecisionInput) -> Decision:
    p = {name: finite(inputs.pillar_scores.get(name)) or 50.0 for name in PILLARS}
    score = clamp(inputs.score)
    confidence = clamp(inputs.confidence)
    target = max(0.0, float(inputs.target_weight))
    current = finite(inputs.current_weight)
    max_weight = max(target + 0.05, target * 1.5)
    reasons: List[str] = []

    critical_business = p["business_quality"] <= 30 and p["results_revisions"] <= 35
    critical_risk = p["risk"] <= 25 or inputs.material_risk_count >= 2

    if critical_business and (critical_risk or p["confirmation"] <= 30):
        reasons.extend(["thesis_failure", "business_and_results_breakdown"])
        if critical_risk:
            reasons.append("critical_risk")
        return Decision("EXIT_REVIEW", "urgent", tuple(reasons), 0.0)

    if critical_risk or score <= 35:
        reasons.append("critical_risk" if critical_risk else "very_low_conviction")
        return Decision("THESIS_REVIEW", "urgent", tuple(reasons), min(target, 0.05))

    if current is not None and current > max_weight:
        reasons.extend(["excess_concentration", "risk_budget_breach"])
        return Decision("TRIM_REVIEW", "normal", tuple(reasons), max_weight)

    if score < 45:
        persistence = inputs.previous_score is not None and inputs.previous_score < 48
        reasons.append("low_conviction")
        if persistence:
            reasons.append("persistent_deterioration")
            return Decision(
                "TRIM_REVIEW", "normal", tuple(reasons), max(target * 0.5, 0.02)
            )
        return Decision("WAIT_INVESTIGATE", "normal", tuple(reasons), max_weight)

    if inputs.contradictions:
        reasons.extend(["contradictory_evidence", *inputs.contradictions])
        return Decision("WAIT_INVESTIGATE", "normal", tuple(reasons), max_weight)

    strong_pillars = sum(1 for value in p.values() if value >= 65)
    near_earnings = (
        inputs.days_to_earnings is not None and 0 <= inputs.days_to_earnings <= 5
    )
    persistence = inputs.previous_score is not None and inputs.previous_score >= 68
    room_to_add = current is None or current <= target + 0.02

    if (
        score >= 72
        and confidence >= 55
        and strong_pillars >= 3
        and p["risk"] >= 45
        and room_to_add
    ):
        if near_earnings:
            reasons.extend(["strong_setup", "earnings_event_risk"])
            return Decision("WAIT_FOR_EVENT", "normal", tuple(reasons), max_weight)
        if not persistence:
            reasons.extend(["strong_setup", "awaiting_signal_persistence"])
            return Decision(
                "HOLD_BUILD_EVIDENCE", "normal", tuple(reasons), max_weight
            )
        reasons.extend(
            ["high_conviction", "multi_pillar_confirmation", "signal_persistence"]
        )
        return Decision("ADD_REVIEW", "normal", tuple(reasons), max_weight)

    if score >= 62:
        reasons.append("positive_but_not_add_threshold")
        return Decision("HOLD", "low", tuple(reasons), max_weight)

    reasons.append("mixed_evidence")
    return Decision("HOLD", "low", tuple(reasons), max_weight)


def thesis_clock(
    launch_date: str | None,
    as_of: date | datetime | str,
    target_quarters: int,
    milestones: Sequence[Mapping[str, Any]] = (),
) -> Dict[str, Any]:
    target_quarters = max(1, int(target_quarters))
    if not launch_date:
        return {
            "quarters_elapsed": 0.0,
            "target_quarters": target_quarters,
            "progress": 0.0,
            "status": "not_started",
            "milestones_met": 0,
            "milestones_total": len(milestones),
        }
    elapsed_days = max(0, (_as_date(as_of) - _as_date(launch_date)).days)
    quarters_elapsed = elapsed_days / 91.3125
    progress = min(1.0, quarters_elapsed / target_quarters)
    total = len(milestones)
    met = sum(1 for item in milestones if item.get("status") == "met")
    missed = sum(1 for item in milestones if item.get("status") == "missed")
    if missed:
        status = "behind"
    elif progress >= 1.0 and total and met < total:
        status = "due_for_review"
    elif progress >= 1.0:
        status = "mature"
    else:
        status = "on_track"
    return {
        "quarters_elapsed": round(quarters_elapsed, 2),
        "target_quarters": target_quarters,
        "progress": round(progress, 4),
        "status": status,
        "milestones_met": met,
        "milestones_total": total,
    }


def score_outcome(decision_code: str, excess_return: float) -> Dict[str, Any]:
    """Score outcome separately from decision quality."""
    action = str(decision_code or "").upper()
    if action in {"ADD_REVIEW", "HOLD", "HOLD_BUILD_EVIDENCE"}:
        correct = excess_return > 0
    elif action in {"TRIM_REVIEW", "EXIT_REVIEW"}:
        correct = excess_return < 0
    else:
        correct = abs(excess_return) < 0.03
    return {
        "outcome_correct": bool(correct),
        "excess_return": round(float(excess_return), 6),
        "label": "positive_outcome" if correct else "negative_outcome",
    }
