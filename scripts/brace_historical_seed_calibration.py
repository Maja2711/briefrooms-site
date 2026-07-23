#!/usr/bin/env python3
"""Calibrate BRACE historical seed credits to validated multiplier strength."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import portfolio_10k_brace_learning as learning

MEMORY_PATH = ROOT / "data" / "investments" / "portfolio_10k_brace_memory.json"
TRAINING_PATH = ROOT / "data" / "investments" / "portfolio_10k_brace_historical_learning.json"
CREDIT_CAP = 24.0
PRIOR_ALPHA = 4.0
PRIOR_BETA = 4.0
MULTIPLIER_SLOPE = 0.8


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def credits_for_multiplier(multiplier: float, total_credit: float = CREDIT_CAP) -> tuple[float, float]:
    """Return success/failure credits reproducing the requested mature multiplier.

    BRACE learning uses multiplier = 1 + (posterior_mean - 0.5) * 0.8 at
    full maturity. Historical credit is capped at 24, which is exactly the
    full-maturity threshold, so the inverse mapping is deterministic.
    """
    requested = clamp(multiplier, 0.8, 1.2)
    posterior_mean = clamp(0.5 + (requested - 1.0) / MULTIPLIER_SLOPE, 0.0, 1.0)
    posterior_total = PRIOR_ALPHA + PRIOR_BETA + total_credit
    success = posterior_mean * posterior_total - PRIOR_ALPHA
    success = clamp(success, 0.0, total_credit)
    failure = total_credit - success
    return round(success, 6), round(failure, 6)


def event(training_id: str, code: str, correct: bool, credit: float) -> Dict[str, Any]:
    suffix = "correct" if correct else "incorrect"
    return {
        "outcome_event_id": f"historical-calibrated:{training_id}:{code}:{suffix}",
        "evaluated_at": datetime.now(timezone.utc).date().isoformat(),
        "horizon_days": 0,
        "id": "historical_walk_forward",
        "asset_type": "HistoricalMixed",
        "market_regime": "all",
        "decision": "HISTORICAL_LESSON",
        "asset_return": None,
        "benchmark_return": None,
        "excess_return": None,
        "historical_seed": True,
        "historical_seed_calibrated": True,
        "training_id": training_id,
        "evidence_attribution": [{
            "evidence_id": f"historical:{code}:{suffix}",
            "code": code,
            "pillar": "risk" if code == "drawdown_52w" else "confirmation",
            "direction": 1,
            "signal_correct": bool(correct),
            "neutral_outcome": False,
            "credit": round(float(credit), 6),
            "importance": 1.0,
            "decision_changed_without_evidence": False,
            "marginal_score": None,
            "historical_seed": True,
        }],
        "append_only": True,
    }


def calibrate(memory: Dict[str, Any], training: Dict[str, Any]) -> List[Dict[str, Any]]:
    memory["outcome_events"] = [
        item for item in memory.get("outcome_events", [])
        if not item.get("historical_seed")
    ]
    created: List[Dict[str, Any]] = []
    if not training.get("activated"):
        return created
    training_id = str(training.get("training_id") or "historical")
    for code, multiplier in sorted((training.get("multipliers") or {}).items()):
        success, failure = credits_for_multiplier(float(multiplier))
        if success > 0:
            created.append(event(training_id, code, True, success))
        if failure > 0:
            created.append(event(training_id, code, False, failure))
    memory["outcome_events"].extend(created)
    return created


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--memory", type=Path, default=MEMORY_PATH)
    parser.add_argument("--training", type=Path, default=TRAINING_PATH)
    args = parser.parse_args()

    memory = learning.load_memory(args.memory)
    training = json.loads(args.training.read_text(encoding="utf-8"))
    created = calibrate(memory, training)
    historical = memory.setdefault("historical_training", {})
    historical["seed_calibrated"] = True
    historical["calibrated_events"] = len(created)
    historical["calibration_credit_cap"] = CREDIT_CAP
    learning.write_memory(args.memory, memory)
    print(f"BRACE historical seed calibrated: events={len(created)}")


if __name__ == "__main__":
    main()
