#!/usr/bin/env python3
"""Attach a sanitized A/B/C learning summary to the existing public projection.

Only aggregate evidence leaves the private research artifact. LearningEpisode
entry theses, component snapshots, decision fingerprints and episode IDs remain
private.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

PUBLIC_LEARNING_SCHEMA = "eurusd-abc-learning-public-v1"
ARMS = ("A", "B", "C")


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain an object")
    return payload


def _number(value: Any, digits: int = 6) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _arm(row: Mapping[str, Any] | None) -> dict[str, Any]:
    row = row or {}
    lesson = row.get("lesson_candidate") if isinstance(row.get("lesson_candidate"), Mapping) else {}
    return {
        "episode_count": int(row.get("episode_count") or 0),
        "wins": int(row.get("wins") or 0),
        "losses": int(row.get("losses") or 0),
        "hit_rate": _number(row.get("hit_rate")),
        "mean_r": _number(row.get("mean_r")),
        "mean_mfe_r": _number(row.get("mean_mfe_r")),
        "mean_mae_r": _number(row.get("mean_mae_r")),
        "dominant_error": row.get("dominant_error"),
        "error_recurrence_rate": _number(row.get("error_recurrence_rate")),
        "recent_vs_prior_mean_r_delta": _number(row.get("recent_vs_prior_mean_r_delta")),
        "policy_stability": _number(row.get("policy_stability")),
        "lesson_candidate": {
            "eligible": bool(lesson.get("eligible")),
            "error_pattern": lesson.get("error_pattern"),
            "confidence": _number(lesson.get("confidence"), 4),
            "proposed_action": lesson.get("proposed_action"),
            "policy_change_proposed": bool(lesson.get("policy_change_proposed")),
            "policy_change_applied": False,
        },
    }


def build_public_learning(report: Mapping[str, Any]) -> dict[str, Any]:
    authority = report.get("authority") if isinstance(report.get("authority"), Mapping) else {}
    governance = report.get("governance") if isinstance(report.get("governance"), Mapping) else {}
    if authority.get("decision_influence") is not False or authority.get("automatic_policy_mutation") is not False:
        raise ValueError("private learning report violates zero-authority contract")
    if governance.get("single_trade_can_change_policy") is not False:
        raise ValueError("single-trade mutation must remain disabled")
    arms = report.get("arms") if isinstance(report.get("arms"), Mapping) else {}
    return {
        "schema_version": PUBLIC_LEARNING_SCHEMA,
        "mode": "PROSPECTIVE_SHARED_LEARNING_LOOP",
        "shared_learning_episode_contract": str(report.get("shared_contract") or ""),
        "prospective_only": True,
        "historical_backfill": False,
        "decision_influence": False,
        "automatic_policy_mutation": False,
        "cross_arm_writeback": False,
        "episode_count": int((report.get("sample") or {}).get("episodes") or 0),
        "arms": {arm: _arm(arms.get(arm) if isinstance(arms, Mapping) else None) for arm in ARMS},
        "governance": {
            "minimum_episodes_for_lesson": int(governance.get("minimum_episodes_for_lesson") or 0),
            "minimum_losses_for_error_lesson": int(governance.get("minimum_losses_for_error_lesson") or 0),
            "minimum_dominant_error_recurrence": _number(governance.get("minimum_dominant_error_recurrence")),
            "human_or_promotion_gate_required_before_policy_application": bool(
                governance.get("human_or_promotion_gate_required_before_policy_application")
            ),
        },
    }


def attach_projection(projection: Mapping[str, Any], report: Mapping[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(projection))
    payload["learning"] = build_public_learning(report)
    validate(payload)
    return payload


def validate(payload: Mapping[str, Any]) -> None:
    learning = payload.get("learning") if isinstance(payload.get("learning"), Mapping) else None
    if not learning or learning.get("schema_version") != PUBLIC_LEARNING_SCHEMA:
        raise ValueError("A/B/C public projection missing learning contract")
    for key in ("historical_backfill", "decision_influence", "automatic_policy_mutation", "cross_arm_writeback"):
        if learning.get(key) is not False:
            raise ValueError(f"learning public boundary violated: {key}")
    if set(learning.get("arms") or {}) != set(ARMS):
        raise ValueError("learning summary must contain A/B/C")
    for arm in ARMS:
        lesson = (learning["arms"][arm].get("lesson_candidate") or {})
        if lesson.get("policy_change_applied") is not False:
            raise ValueError("public learning summary cannot apply policy")


def main() -> int:
    parser = argparse.ArgumentParser(description="Attach sanitized A/B/C learning summary")
    parser.add_argument("--projection", required=True, type=Path)
    parser.add_argument("--learning-report", type=Path)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    projection = _load(args.projection)
    if args.validate:
        validate(projection)
        print("EURUSD_ABC_LEARNING_PUBLIC_OK", args.projection)
        return 0
    if args.learning_report is None:
        parser.error("--learning-report is required unless --validate is used")
    payload = attach_projection(projection, _load(args.learning_report))
    args.projection.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("EURUSD_ABC_LEARNING_PUBLIC_WRITTEN", args.projection)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
