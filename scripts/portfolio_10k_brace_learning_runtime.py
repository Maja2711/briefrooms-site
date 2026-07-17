#!/usr/bin/env python3
"""Apply BRACE 2.0 adaptive learning to the freshly built BRACE snapshot."""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import portfolio_10k_brace_engine as engine
import portfolio_10k_brace_learning as learning
import portfolio_10k_brace_market as market_features
import portfolio_10k_weekly as base

SNAPSHOT_PATH = ROOT / "data" / "investments" / "portfolio_10k_brace.json"
MEMORY_PATH = ROOT / "data" / "investments" / "portfolio_10k_brace_memory.json"
PORTFOLIO_PATH = ROOT / "data" / "investments" / "portfolio_10k.json"
MODEL_VERSION = "2.0.0"


def read(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clamp(value: float, low=0.0, high=100.0) -> float:
    return max(low, min(high, float(value)))


def finite(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def score_from_pillars(pillars):
    contradictions = engine.contradiction_flags(pillars)
    penalty = min(14.0, 4.0 * len(contradictions))
    score = sum(finite(pillars.get(key), 50.0) * weight for key, weight in engine.PILLAR_WEIGHTS.items()) - penalty
    return round(clamp(score), 2), contradictions, round(penalty, 2)


def prior_for(memory, position_id, current_week):
    matches = [item for item in memory.get("decisions", []) if item.get("id") == position_id and item.get("week_id") != current_week]
    return matches[-1] if matches else {}


def decision_for(position, result, prior, as_of):
    return engine.decide(engine.DecisionInput(
        score=result["score"],
        confidence=result["confidence"],
        pillar_scores=result["pillar_scores"],
        contradictions=result["contradictions"],
        current_weight=engine.finite(position.get("current_weight")),
        target_weight=float(position.get("target_weight") or 0.0),
        days_to_earnings=market_features.days_to(position.get("next_earnings_date"), as_of),
        material_risk_count=sum(1 for item in position.get("evidence_ledger", []) if int(item.get("direction") or 0) < 0 and item.get("pillar") == "events_information"),
        previous_score=engine.finite(prior.get("score")),
        previous_decision=prior.get("decision"),
        asset_type=str(position.get("asset_type") or "Stock"),
    ))


def adapt_position(position, memory, regime, week_id, as_of, benchmark_price):
    asset_type = str(position.get("asset_type") or "Stock")
    pillars = {key: finite(value, 50.0) for key, value in (position.get("pillar_scores") or {}).items()}
    evidence = position.get("evidence_ledger", []) or []
    learning_rows = {}

    for item in evidence:
        code, pillar = str(item.get("code") or ""), str(item.get("pillar") or "")
        if not code or pillar not in engine.PILLAR_WEIGHTS:
            continue
        multiplier, meta = learning.multiplier_for(memory, code, asset_type, regime)
        base_weight = finite(item.get("decayed_weight"))
        adaptive_delta = 12.0 * base_weight * (multiplier - 1.0)
        pillars[pillar] = clamp(pillars.get(pillar, 50.0) + adaptive_delta)
        item["adaptive_multiplier"] = round(multiplier, 6)
        item["adaptive_score_delta"] = round(adaptive_delta, 6)
        item["adaptive_learning"] = meta
        learning_rows[code] = meta

    score, contradictions, penalty = score_from_pillars(pillars)
    old_contradictions = len(position.get("contradictions") or [])
    confidence = clamp(finite(position.get("confidence")) - 7.5 * (len(contradictions) - old_contradictions))
    result = {
        "score": score,
        "confidence": round(confidence, 2),
        "pillar_scores": {key: round(value, 3) for key, value in pillars.items()},
        "contradictions": contradictions,
        "contradiction_penalty": penalty,
    }
    prior = prior_for(memory, str(position.get("id")), week_id)
    decision = decision_for(position, result, prior, as_of)

    counterfactuals = []
    for index, item in enumerate(evidence):
        code, pillar = str(item.get("code") or ""), str(item.get("pillar") or "")
        if not code or pillar not in pillars:
            continue
        full_effect = 12.0 * finite(item.get("decayed_weight")) * finite(item.get("adaptive_multiplier"), 1.0)
        alternative_pillars = dict(pillars)
        alternative_pillars[pillar] = clamp(alternative_pillars[pillar] - full_effect)
        alt_score, alt_contradictions, alt_penalty = score_from_pillars(alternative_pillars)
        alt_result = {
            "score": alt_score,
            "confidence": clamp(confidence - 7.5 * (len(alt_contradictions) - len(contradictions))),
            "pillar_scores": alternative_pillars,
            "contradictions": alt_contradictions,
            "contradiction_penalty": alt_penalty,
        }
        alt_decision = decision_for(position, alt_result, prior, as_of)
        marginal = score - alt_score
        pivotal = alt_decision.code != decision.code
        counterfactuals.append({
            "evidence_id": f"{code}:{index}",
            "code": code,
            "pillar": pillar,
            "direction": int(item.get("direction") or 0),
            "marginal_score": round(marginal, 6),
            "decision_without": alt_decision.code,
            "decision_changed": pivotal,
            "importance": round(min(1.0, 0.15 + abs(marginal) / 8.0 + (0.45 if pivotal else 0.0)), 6),
            "method": "published_evidence_local_ablation",
        })
        item["evidence_id"] = f"{code}:{index}"

    previous_code = (position.get("decision") or {}).get("code")
    position.update(result)
    position["decision"] = decision.to_dict()
    position["decision_change"] = bool(previous_code and previous_code != decision.code)
    position["adaptive_learning"] = {
        "signals_observed": len(learning_rows),
        "multipliers_applied": sum(1 for item in learning_rows.values() if item.get("active")),
        "counterfactuals_computed": len(counterfactuals),
        "learned_from_prior_outcomes": True,
    }

    record = {
        "decision_id": f"{week_id}:{position.get('id')}:{MODEL_VERSION}",
        "week_id": week_id,
        "review_date": as_of.isoformat(),
        "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model_version": MODEL_VERSION,
        "id": position.get("id"),
        "broker_symbol": position.get("broker_symbol"),
        "label": position.get("label"),
        "asset_type": asset_type,
        "market_regime": regime,
        "decision": decision.code,
        "urgency": decision.urgency,
        "reason_codes": list(decision.reason_codes),
        "score": score,
        "confidence": round(confidence, 2),
        "pillar_scores": position.get("pillar_scores"),
        "contradictions": contradictions,
        "reference_price": position.get("market_price"),
        "reference_benchmark_price": round(float(benchmark_price), 6),
        "evidence": [dict(item) for item in evidence],
        "counterfactuals": counterfactuals,
        "immutable": True,
    }
    learning.append_decision(memory, record)


def run(snapshot_path: Path, memory_path: Path, portfolio_path: Path):
    snapshot, portfolio = read(snapshot_path), read(portfolio_path)
    memory = learning.load_memory(memory_path, snapshot)
    timestamp = datetime.now(timezone.utc)
    as_of = timestamp.date()
    week_id = f"{as_of.isocalendar().year}-W{as_of.isocalendar().week:02d}"
    memory["decisions"] = [item for item in memory.get("decisions", []) if not (item.get("legacy_record") and item.get("week_id") == week_id)]

    benchmark_cfg = portfolio.get("benchmark") or {}
    benchmark = base.fetch_market(str(benchmark_cfg.get("market_symbol") or "FWIA.DE"), str(benchmark_cfg.get("currency") or "EUR"), False)
    current_prices = {str(item.get("id")): finite(item.get("market_price")) for item in snapshot.get("positions", [])}
    new_outcomes = learning.evaluate_due_outcomes(memory, current_prices, benchmark.price, as_of)
    learning.rebuild_reliability(memory)

    regime = str((snapshot.get("market_context") or {}).get("regime") or "unknown")
    for position in snapshot.get("positions", []):
        adapt_position(position, memory, regime, week_id, as_of, benchmark.price)

    weights = {str(item.get("id")): finite(item.get("target_weight")) for item in snapshot.get("positions", [])}
    snapshot["portfolio"] = {
        "score": round(sum(finite(item.get("score")) * weights.get(str(item.get("id")), 0.0) for item in snapshot.get("positions", [])), 2),
        "confidence": round(sum(finite(item.get("confidence")) * weights.get(str(item.get("id")), 0.0) for item in snapshot.get("positions", [])), 2),
        "decision_counts": {},
        "positions_reviewed": len(snapshot.get("positions", [])),
    }
    for item in snapshot.get("positions", []):
        code = (item.get("decision") or {}).get("code")
        snapshot["portfolio"]["decision_counts"][code] = snapshot["portfolio"]["decision_counts"].get(code, 0) + 1

    state = learning.learning_summary(memory)
    state["new_outcomes_this_review"] = len(new_outcomes)
    snapshot.update({
        "schema_version": "2.0.0",
        "model_version": MODEL_VERSION,
        "status": "live_shadow_learning",
        "generated_at": timestamp.isoformat(timespec="seconds"),
        "objective": memory.get("objective"),
        "decision_history": learning.compatibility_history(memory),
        "learning": state,
    })
    snapshot.setdefault("policy", {}).update({
        "adaptive_evidence_learning": True,
        "counterfactual_attribution": True,
        "full_memory_file": "/data/investments/portfolio_10k_brace_memory.json",
        "learned_multipliers_bounded": [learning.MIN_MULTIPLIER, learning.MAX_MULTIPLIER],
        "human_approval_required": True,
    })
    learning_note_pl = "Uczenie zmienia wyłącznie ograniczone współczynniki wiarygodności dowodów; nie zmienia automatycznie wag filarów ani progów decyzji."
    learning_note_en = "Learning only changes bounded evidence-reliability multipliers; it does not automatically alter pillar weights or decision thresholds."
    if learning_note_pl not in snapshot.setdefault("limitations_pl", []):
        snapshot["limitations_pl"].append(learning_note_pl)
    if learning_note_en not in snapshot.setdefault("limitations_en", []):
        snapshot["limitations_en"].append(learning_note_en)
    write(snapshot_path, snapshot)
    learning.write_memory(memory_path, memory)
    print(f"BRACE learning applied: decisions={state['decisions_stored']}, outcomes={state['outcome_events_stored']}, active={state['active_multipliers']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT_PATH)
    parser.add_argument("--memory", type=Path, default=MEMORY_PATH)
    parser.add_argument("--portfolio", type=Path, default=PORTFOLIO_PATH)
    args = parser.parse_args()
    run(args.snapshot, args.memory, args.portfolio)


if __name__ == "__main__":
    main()
