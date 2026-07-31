#!/usr/bin/env python3
"""Append-only outcome learning and immutable challenger version manifests."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from brace_portfolio_data import canonical_sha256


def evaluate_outcome(
    decision: Mapping[str, Any],
    outcome: Mapping[str, Any],
) -> Dict[str, Any]:
    action = str(decision.get("action") or "NO_ACTION")
    realized = float(outcome.get("realized_return") or 0.0)
    benchmark = float(outcome.get("baseline_return") or 0.0)
    expected = float(decision.get("expected_benefit") or 0.0)
    direction_ok = (
        action == "NO_ACTION"
        or (action in {"ADD", "REPLACE"} and realized >= benchmark)
        or (action in {"REDUCE", "EXIT"} and realized <= benchmark)
    )
    calibration_error = abs(expected - (realized - benchmark))
    return {
        "decision_id": decision.get("decision_id"),
        "evaluated_at": outcome.get("evaluated_at"),
        "realized_return": realized,
        "baseline_return": benchmark,
        "excess_return": round(realized - benchmark, 8),
        "maximum_favorable_excursion": outcome.get(
            "maximum_favorable_excursion"
        ),
        "maximum_adverse_excursion": outcome.get(
            "maximum_adverse_excursion"
        ),
        "portfolio_impact": outcome.get("portfolio_impact"),
        "direction_correct": direction_ok,
        "calibration_error": round(calibration_error, 8),
    }


def update_learning_state(
    current: Optional[Mapping[str, Any]],
    decision: Mapping[str, Any],
    outcome: Mapping[str, Any],
    generated_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or datetime.now(timezone.utc)
    state = copy.deepcopy(
        dict(
            current
            or {
                "schema_version": "1.0.0",
                "methodology_version": "brace-portfolio-v3.0.0",
                "data_freshness": "current",
                "source_metadata": {
                    "engine": "brace_portfolio_learning.py",
                    "append_only": True,
                },
                "outcomes": [],
                "statistics": {},
            }
        )
    )
    evaluated = evaluate_outcome(decision, outcome)
    existing = {
        str(item.get("decision_id")): item for item in state.get("outcomes", [])
    }
    if evaluated["decision_id"] not in existing:
        state.setdefault("outcomes", []).append(evaluated)
    rows = state.get("outcomes", [])
    correct = sum(1 for item in rows if item.get("direction_correct"))
    errors = [float(item.get("calibration_error") or 0.0) for item in rows]
    excess = [float(item.get("excess_return") or 0.0) for item in rows]
    state["statistics"] = {
        "completed_outcomes": len(rows),
        "directional_accuracy": round(correct / len(rows), 6) if rows else None,
        "mean_calibration_error": round(sum(errors) / len(errors), 8)
        if errors
        else None,
        "mean_excess_return": round(sum(excess) / len(excess), 8)
        if excess
        else None,
    }
    state["generated_at"] = generated_at.isoformat(timespec="seconds")
    state["content_sha256"] = canonical_sha256(
        {key: value for key, value in state.items() if key != "content_sha256"}
    )
    return state


def create_challenger_manifest(
    methodology_id: str,
    version: str,
    parameters: Mapping[str, Any],
    code_sha: str,
    data_sha: str,
    training_window: Mapping[str, Any],
    testing_window: Mapping[str, Any],
    validation_results: Mapping[str, Any],
    created_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    created_at = created_at or datetime.now(timezone.utc)
    manifest = {
        "methodology_id": methodology_id,
        "version": version,
        "status": "CANDIDATE",
        "created_at": created_at.isoformat(timespec="seconds"),
        "parameters": copy.deepcopy(dict(parameters)),
        "code_sha256": str(code_sha),
        "data_sha256": str(data_sha),
        "training_window": copy.deepcopy(dict(training_window)),
        "testing_window": copy.deepcopy(dict(testing_window)),
        "validation_results": copy.deepcopy(dict(validation_results)),
        "immutable": True,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    return manifest


def append_challenger(
    registry: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> Dict[str, Any]:
    updated = copy.deepcopy(dict(registry))
    manifest_sha = str(manifest.get("manifest_sha256") or "")
    if not manifest_sha:
        raise ValueError("A challenger manifest must be hashed")
    existing = {
        str(item.get("manifest_sha256"))
        for item in updated.get("challenger_versions", [])
    }
    if manifest_sha not in existing:
        updated.setdefault("challenger_versions", []).append(copy.deepcopy(dict(manifest)))
    return updated
