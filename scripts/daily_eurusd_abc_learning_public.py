#!/usr/bin/env python3
"""Build sanitized public evidence for the private EUR/USD A/B/C learning loop.

Only aggregate evidence leaves the private research artifact. LearningEpisode
entry theses, component snapshots, decision fingerprints and episode IDs remain
private. The projection is content-stable when no new episode is learned so the
source workflow cannot create timestamp-only repository churn.
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


def _challenger(row: Mapping[str, Any] | None) -> dict[str, Any]:
    row = row or {}
    lesson = row.get("source_lesson") if isinstance(row.get("source_lesson"), Mapping) else {}
    baseline = row.get("frozen_baseline") if isinstance(row.get("frozen_baseline"), Mapping) else {}
    contract = row.get("shadow_contract") if isinstance(row.get("shadow_contract"), Mapping) else {}
    evidence = row.get("prospective_evidence") if isinstance(row.get("prospective_evidence"), Mapping) else {}
    return {
        "challenger_id": row.get("challenger_id"),
        "arm_id": row.get("arm_id"),
        "created_at": row.get("created_at"),
        "status": row.get("status"),
        "source_error_pattern": lesson.get("error_pattern"),
        "lesson_confidence": _number(lesson.get("confidence"), 4),
        "proposed_action": lesson.get("proposed_action"),
        "frozen_baseline": {
            "episode_count": int(baseline.get("episode_count") or 0),
            "hit_rate": _number(baseline.get("hit_rate")),
            "mean_r": _number(baseline.get("mean_r")),
        },
        "shadow_contract": {
            "automatic_creation": bool(contract.get("automatic_creation")),
            "executable_rule_status": contract.get("executable_rule_status"),
            "prospective_only": bool(contract.get("prospective_only")),
            "historical_backfill": False,
            "decision_influence": False,
            "production_execution": False,
            "automatic_promotion": False,
            "automatic_policy_mutation": False,
        },
        "prospective_evidence": {
            "starts_after_episode_count": int(evidence.get("starts_after_episode_count") or 0),
            "challenger_observations": int(evidence.get("challenger_observations") or 0),
            "status": evidence.get("status"),
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
    episode_count = int((report.get("sample") or {}).get("episodes") or 0)
    challengers = [_challenger(row) for row in (report.get("challengers") or []) if isinstance(row, Mapping)]
    payload = {
        "schema_version": PUBLIC_LEARNING_SCHEMA,
        "evidence_revision": episode_count,
        "experiment_id": "eurusd-abc-live-shadow",
        "mode": "PROSPECTIVE_SHARED_LEARNING_LOOP",
        "shared_learning_episode_contract": str(report.get("shared_contract") or ""),
        "prospective_only": True,
        "historical_backfill": False,
        "decision_influence": False,
        "automatic_policy_mutation": False,
        "automatic_challenger_creation": bool(governance.get("automatic_challenger_creation")),
        "cross_arm_writeback": False,
        "episode_count": episode_count,
        "challenger_count": len(challengers),
        "challengers": challengers,
        "arms": {arm: _arm(arms.get(arm) if isinstance(arms, Mapping) else None) for arm in ARMS},
        "governance": {
            "minimum_episodes_for_lesson": int(governance.get("minimum_episodes_for_lesson") or 0),
            "minimum_losses_for_error_lesson": int(governance.get("minimum_losses_for_error_lesson") or 0),
            "minimum_dominant_error_recurrence": _number(governance.get("minimum_dominant_error_recurrence")),
            "max_active_challengers_per_arm": int(governance.get("max_active_challengers_per_arm") or 0),
            "human_or_promotion_gate_required_before_policy_application": bool(
                governance.get("human_or_promotion_gate_required_before_policy_application")
            ),
        },
    }
    validate(payload)
    return payload


def validate(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != PUBLIC_LEARNING_SCHEMA:
        raise ValueError("unexpected A/B/C public learning schema")
    if payload.get("experiment_id") != "eurusd-abc-live-shadow":
        raise ValueError("public learning evidence must bind to the existing A/B/C experiment")
    if int(payload.get("evidence_revision") or 0) != int(payload.get("episode_count") or 0):
        raise ValueError("public learning evidence revision must equal episode count")
    for key in ("historical_backfill", "decision_influence", "automatic_policy_mutation", "cross_arm_writeback"):
        if payload.get(key) is not False:
            raise ValueError(f"learning public boundary violated: {key}")
    if set(payload.get("arms") or {}) != set(ARMS):
        raise ValueError("learning summary must contain A/B/C")
    for arm in ARMS:
        lesson = (payload["arms"][arm].get("lesson_candidate") or {})
        if lesson.get("policy_change_applied") is not False:
            raise ValueError("public learning summary cannot apply policy")
    if payload.get("automatic_challenger_creation") is not True:
        raise ValueError("public learning must expose automatic challenger creation")
    for row in payload.get("challengers") or []:
        contract = row.get("shadow_contract") if isinstance(row.get("shadow_contract"), Mapping) else {}
        if contract.get("automatic_creation") is not True or contract.get("prospective_only") is not True:
            raise ValueError("public challenger contract must remain prospective")
        for key in ("historical_backfill", "decision_influence", "production_execution", "automatic_promotion", "automatic_policy_mutation"):
            if contract.get(key) is not False:
                raise ValueError(f"public challenger boundary violated: {key}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build sanitized public A/B/C learning evidence")
    parser.add_argument("--learning-report", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    if args.validate:
        validate(_load(args.output))
        print("EURUSD_ABC_LEARNING_PUBLIC_OK", args.output)
        return 0
    if args.learning_report is None:
        parser.error("--learning-report is required unless --validate is used")
    payload = build_public_learning(_load(args.learning_report))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("EURUSD_ABC_LEARNING_PUBLIC_WRITTEN", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
