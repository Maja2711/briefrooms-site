from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import portfolio_10k_brace_engine as engine
import portfolio_10k_brace_learning as learning


def full_scores(value=70.0):
    return {pillar: value for pillar in engine.PILLARS}


def test_decision_memory_is_immutable_and_not_truncated():
    memory = learning.new_memory("2026-01-01T00:00:00+00:00")
    for index in range(500):
        assert learning.append_decision(memory, {
            "decision_id": f"d-{index}",
            "week_id": f"w-{index}",
            "review_date": "2026-01-01",
            "id": "asset",
            "decision": "HOLD",
        }) is True
    assert learning.append_decision(memory, {"decision_id": "d-1"}) is False
    assert len(memory["decisions"]) == 500
    assert len(learning.compatibility_history(memory)) == 416


def test_multi_horizon_outcomes_are_append_only_and_not_duplicated():
    memory = learning.new_memory("2026-01-01T00:00:00+00:00")
    memory["decisions"].append({
        "decision_id": "2026-W01:asset:2.0.0",
        "review_date": "2026-01-01",
        "id": "asset",
        "broker_symbol": "ASSET",
        "asset_type": "Stock",
        "market_regime": "risk_on",
        "decision": "HOLD",
        "reference_price": 100.0,
        "reference_benchmark_price": 100.0,
        "evidence": [{
            "evidence_id": "growth:0",
            "code": "growth",
            "pillar": "business_quality",
            "direction": 1,
        }],
        "counterfactuals": [{
            "evidence_id": "growth:0",
            "importance": 1.0,
            "decision_changed": True,
            "marginal_score": 3.0,
        }],
    })
    first = learning.evaluate_due_outcomes(memory, {"asset": 110.0}, 102.0, date(2026, 1, 8))
    assert [item["horizon_days"] for item in first] == [7]
    assert first[0]["evidence_attribution"][0]["signal_correct"] is True
    assert learning.evaluate_due_outcomes(memory, {"asset": 111.0}, 103.0, date(2026, 1, 9)) == []
    later = learning.evaluate_due_outcomes(memory, {"asset": 120.0}, 105.0, date(2026, 1, 31))
    assert [item["horizon_days"] for item in later] == [30]
    assert len(memory["outcome_events"]) == 2


def outcome(code, correct, index):
    return {
        "outcome_event_id": f"event-{code}-{index}",
        "asset_type": "Stock",
        "market_regime": "risk_on",
        "evidence_attribution": [{
            "code": code,
            "signal_correct": correct,
            "neutral_outcome": False,
            "credit": 1.0,
        }],
    }


def test_bayesian_reliability_stays_neutral_until_minimum_sample():
    memory = learning.new_memory()
    memory["outcome_events"] = [outcome("signal", True, index) for index in range(7)]
    state = learning.rebuild_reliability(memory)["global"]["signal"]
    assert state["effective_samples"] == 7.0
    assert state["active"] is False
    assert state["multiplier"] == 1.0


def test_reliable_and_unreliable_signals_move_in_opposite_bounded_directions():
    memory = learning.new_memory()
    memory["outcome_events"] = (
        [outcome("good", True, index) for index in range(30)]
        + [outcome("bad", False, index) for index in range(30)]
    )
    reliability = learning.rebuild_reliability(memory)["global"]
    assert 1.0 < reliability["good"]["multiplier"] <= learning.MAX_MULTIPLIER
    assert learning.MIN_MULTIPLIER <= reliability["bad"]["multiplier"] < 1.0


def test_counterfactual_marks_pivotal_evidence_and_assigns_more_importance():
    scores = full_scores(70.0)
    pivotal = engine.Evidence(
        "pivotal", "business_quality", 1, 1.0, 1.0,
        "2026-07-17", 90, "test", "pivotal", "pivotal",
    )
    tiny = engine.Evidence(
        "tiny", "events_information", 1, 0.05, 0.2,
        "2026-07-17", 90, "test", "tiny", "tiny",
    )
    result, decision, attribution = learning.counterfactual_attribution(
        scores,
        {pillar: 1.0 for pillar in engine.PILLARS},
        [pivotal, tiny],
        "2026-07-17",
        {"pivotal": 1.0, "tiny": 1.0},
        {
            "current_weight": 0.10,
            "target_weight": 0.15,
            "days_to_earnings": 20,
            "material_risk_count": 0,
            "previous_score": 75.0,
            "previous_decision": "HOLD_BUILD_EVIDENCE",
            "asset_type": "Stock",
        },
    )
    by_code = {item["code"]: item for item in attribution}
    assert result["score"] >= 72
    assert decision.code == "ADD_REVIEW"
    assert by_code["pivotal"]["decision_changed"] is True
    assert by_code["pivotal"]["importance"] > by_code["tiny"]["importance"]


def test_contextual_multiplier_preferred_when_mature():
    memory = learning.new_memory()
    memory["outcome_events"] = [
        {
            "outcome_event_id": f"event-{index}",
            "asset_type": "Stock",
            "market_regime": "risk_off",
            "evidence_attribution": [{
                "code": "valuation",
                "signal_correct": True,
                "neutral_outcome": False,
                "credit": 1.0,
            }],
        }
        for index in range(30)
    ]
    learning.rebuild_reliability(memory)
    multiplier, meta = learning.multiplier_for(memory, "valuation", "Stock", "risk_off")
    assert multiplier > 1.0
    assert meta["source"] == "contextual"
