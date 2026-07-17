from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import portfolio_10k_brace_engine as engine


def full_scores(value=70.0):
    return {pillar: value for pillar in engine.PILLARS}


def test_negative_evidence_is_asymmetric_and_decays():
    positive = engine.Evidence(
        "p", "events_information", 1, 1.0, 1.0, "2026-01-01", 30,
        "source", "plus", "plus",
    )
    negative = engine.Evidence(
        "n", "events_information", -1, 1.0, 1.0, "2026-01-01", 30,
        "source", "minus", "minus",
    )
    assert abs(negative.decayed_weight("2026-01-01")) > positive.decayed_weight("2026-01-01")
    assert abs(negative.decayed_weight("2026-03-01")) < abs(negative.decayed_weight("2026-01-01"))


def test_missing_data_reduces_confidence_without_crashing():
    result = engine.aggregate_score(
        {"confirmation": 70, "risk": 60},
        {"confirmation": 1.0, "risk": 1.0},
        as_of="2026-07-17",
    )
    assert 0 <= result["score"] <= 100
    assert result["confidence"] < 70
    assert result["data_completeness"] < 0.5


def test_contradiction_penalty_is_applied():
    scores = full_scores(65)
    scores["business_quality"] = 80
    scores["confirmation"] = 25
    result = engine.aggregate_score(scores, as_of="2026-07-17")
    assert "fundamentals_vs_market" in result["contradictions"]
    assert result["contradiction_penalty"] > 0


def test_add_requires_persistence_and_no_earnings_blackout():
    scores = full_scores(78)
    first = engine.decide(engine.DecisionInput(
        score=78,
        confidence=90,
        pillar_scores=scores,
        contradictions=[],
        current_weight=0.10,
        target_weight=0.15,
        days_to_earnings=20,
        previous_score=None,
    ))
    assert first.code == "HOLD_BUILD_EVIDENCE"

    persisted = engine.decide(engine.DecisionInput(
        score=78,
        confidence=90,
        pillar_scores=scores,
        contradictions=[],
        current_weight=0.10,
        target_weight=0.15,
        days_to_earnings=20,
        previous_score=75,
    ))
    assert persisted.code == "ADD_REVIEW"

    blackout = engine.decide(engine.DecisionInput(
        score=78,
        confidence=90,
        pillar_scores=scores,
        contradictions=[],
        current_weight=0.10,
        target_weight=0.15,
        days_to_earnings=3,
        previous_score=75,
    ))
    assert blackout.code == "WAIT_FOR_EVENT"


def test_critical_risk_overrides_good_average_score():
    scores = full_scores(80)
    scores["risk"] = 20
    decision = engine.decide(engine.DecisionInput(
        score=72,
        confidence=85,
        pillar_scores=scores,
        contradictions=[],
        current_weight=0.10,
        target_weight=0.10,
        days_to_earnings=30,
    ))
    assert decision.code == "THESIS_REVIEW"
    assert decision.urgency == "urgent"


def test_thesis_clock_never_uses_future_progress():
    clock = engine.thesis_clock("2026-01-01", date(2026, 7, 1), 4)
    assert 1.9 < clock["quarters_elapsed"] < 2.1
    assert 0 < clock["progress"] < 1
