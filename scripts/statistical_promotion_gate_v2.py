#!/usr/bin/env python3
"""PR-A / PR36 methodology v2: fresh fixed-N confirmation holdout.

PR36 evaluates only observations strictly after the PR35 pass timestamp. The
formal sample size is pre-registered and evaluated exactly once. A pass marks the
candidate eligible for promotion but production remains frozen; no engine policy
is activated and no checked-in configuration is materialized.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

try:
    import autonomous_policy_promotion as ap
    import autonomous_policy_promotion_v2 as pr35v2
    import statistical_promotion_gate as legacy
except ModuleNotFoundError:  # pragma: no cover
    from scripts import autonomous_policy_promotion as ap
    from scripts import autonomous_policy_promotion_v2 as pr35v2
    from scripts import statistical_promotion_gate as legacy

CONFIG_PATH = "data/investments/statistical_promotion_gate_v2_config.json"
REPORT_FILENAME = "statistical_promotion_gate_v2_report.json"
CONFIG_SCHEMA = "briefrooms-statistical-promotion-gate-config-v2"
REPORT_SCHEMA = "briefrooms-statistical-promotion-gate-report-v2"
FREEZE_MODE = pr35v2.FREEZE_MODE


def load_config(repo_root: Path) -> dict[str, Any]:
    payload = json.loads((repo_root / CONFIG_PATH).read_text(encoding="utf-8"))
    if payload.get("schema_version") != CONFIG_SCHEMA:
        raise ValueError("PR36 v2 config schema mismatch")
    if int(payload.get("promotion_methodology_version") or 0) != pr35v2.PROMOTION_METHODOLOGY_VERSION:
        raise ValueError("PR36 methodology version mismatch")
    if payload.get("production_promotion_enabled") is not False:
        raise ValueError("PR36 production promotion must remain frozen")
    fixed_n = int(payload.get("fixed_paired_n") or 0)
    if fixed_n < pr35v2.VALIDATION_FIXED_N:
        raise ValueError("PR36 fresh holdout cannot be smaller than PR35 validation")
    confidence = float(payload.get("confidence_level") or 0.0)
    if not 0.8 <= confidence < 1.0:
        raise ValueError("PR36 confidence level out of bounds")
    if int(payload.get("bootstrap_samples") or 0) < 1000:
        raise ValueError("PR36 bootstrap sample count too small")
    if set(payload.get("engines") or {}) != {"gpw_daily", "us_daily"}:
        raise ValueError("PR36 engine config mismatch")
    return payload


def _fresh_rows(state_dir: Path, candidate: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    start = candidate.get("confirmation_start_at")
    if not start:
        raise RuntimeError("PR36 candidate has no fresh holdout boundary")
    if candidate.get("confirmation_sample_reuse_allowed") is not False:
        raise RuntimeError("PR36 candidate does not forbid PR35 sample reuse")
    rows = ap._read_jsonl(state_dir / ap.SHADOW_FILENAME)
    selected = ap._marginal_rows(
        rows,
        engine_id=str(candidate["engine_id"]),
        gate=str(candidate["gate"]),
        current_value=float(candidate["from_value"]),
        candidate_value=float(candidate["to_value"]),
        after=ap._parse_time(str(start)),
    )
    return sorted(selected, key=lambda row: str(row.get("decision_at") or ""))


def _fixed_gate(metrics: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[str, list[str]]:
    fixed_n = int(config["fixed_paired_n"])
    if int(metrics.get("n") or 0) != fixed_n:
        raise ValueError("PR36 fixed gate requires the exact pre-registered sample size")
    reasons: list[str] = []
    if float(metrics.get("paired_net_incremental_mean_percent") or 0.0) < float(config["minimum_net_incremental_return_percent"]):
        reasons.append("net_incremental_mean_below_minimum")
    if float(metrics.get("paired_net_positive_rate") or 0.0) < float(config["minimum_net_positive_rate"]):
        reasons.append("net_positive_rate_below_minimum")
    if float(metrics.get("bootstrap_ci_low_percent") or 0.0) <= 0.0:
        reasons.append("bootstrap_lower_bound_not_positive")
    if int(metrics.get("unique_symbols") or 0) < int(config["minimum_unique_symbols"]):
        reasons.append("insufficient_symbol_diversity")
    if int(metrics.get("span_days") or 0) < int(config["minimum_span_days"]):
        reasons.append("validation_span_too_short")
    if float(metrics.get("first_half_net_mean_percent") or 0.0) <= 0.0:
        reasons.append("first_half_net_not_positive")
    if float(metrics.get("second_half_net_mean_percent") or 0.0) <= 0.0:
        reasons.append("second_half_net_not_positive")
    concentration = metrics.get("largest_positive_contribution_share")
    if concentration is not None and float(concentration) > float(config["maximum_single_positive_contribution_share"]):
        reasons.append("single_observation_concentration_too_high")
    return ("PASS", []) if not reasons else ("FAIL", reasons)


def run(state_dir: Path, repo_root: Path, *, now: Optional[datetime] = None) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    config = load_config(repo_root)
    registry_path = state_dir / ap.REGISTRY_FILENAME
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    ap.validate_registry(registry)
    governance = registry.get("governance") if isinstance(registry.get("governance"), Mapping) else {}
    if governance.get("production_promotion_enabled") is not False or governance.get("promotion_mode") != FREEZE_MODE:
        raise RuntimeError("PR36 v2 requires the production promotion freeze")

    fixed_n = int(config["fixed_paired_n"])
    evaluated: list[dict[str, Any]] = []
    for candidate in list((registry.get("candidates") or {}).values()):
        if candidate.get("status") != "PR36_HOLDOUT":
            continue
        if int(candidate.get("promotion_methodology_version") or 0) != pr35v2.PROMOTION_METHODOLOGY_VERSION:
            raise RuntimeError("PR36 encountered a non-v2 candidate")
        confirmation_start = ap._parse_time(str(candidate.get("confirmation_start_at") or ""))
        pr35_passed = ap._parse_time(str(candidate.get("pr35_passed_at") or ""))
        if confirmation_start < pr35_passed:
            raise RuntimeError("PR36 holdout begins before PR35 pass")

        available = _fresh_rows(state_dir, candidate)
        if len(available) < fixed_n:
            candidate["statistical_gate"] = {
                "status": "COLLECTING",
                "observed_n": len(available),
                "target_n": fixed_n,
                "fresh_holdout": True,
                "formal_test_performed": False,
                "evaluated_at": ap._iso(now),
            }
            evaluated.append({
                "candidate_id": candidate["candidate_id"],
                "engine_id": candidate["engine_id"],
                "status": "COLLECTING",
                "observed_n": len(available),
                "target_n": fixed_n,
            })
            continue

        frozen_rows = available[:fixed_n]
        engine_cfg = config["engines"][candidate["engine_id"]]
        metrics = legacy.paired_metrics(
            frozen_rows,
            candidate_id=str(candidate["candidate_id"]),
            cost_stress_percent=float(engine_cfg["round_trip_cost_stress_percent"]),
            bootstrap_samples=int(config["bootstrap_samples"]),
            confidence_level=float(config["confidence_level"]),
        )
        status, reasons = _fixed_gate(metrics, config)
        gate = {
            "status": status,
            "blocking_reasons": reasons,
            "fresh_holdout": True,
            "holdout_start_at": candidate["confirmation_start_at"],
            "observed_n": len(available),
            "target_n": fixed_n,
            "formal_sample_n": fixed_n,
            "formal_test_performed": True,
            "sample_shadow_outcome_ids": [str(row.get("shadow_outcome_id") or "") for row in frozen_rows],
            "metrics": metrics,
            "evaluated_at": ap._iso(now),
        }
        candidate["statistical_gate"] = gate

        if status == "PASS":
            candidate["status"] = "PROMOTION_ELIGIBLE_BUT_FROZEN"
            candidate["promotion_eligible_at"] = ap._iso(now)
            candidate["production_promotion_enabled"] = False
            ap.append_audit(
                state_dir / ap.AUDIT_FILENAME,
                "pr36_fresh_holdout_pass_promotion_frozen",
                {"candidate_id": candidate["candidate_id"], "statistical_gate": gate},
                ap._iso(now),
            )
        else:
            candidate["status"] = "STATISTICAL_REJECTED"
            candidate["statistical_rejected_at"] = ap._iso(now)
            registry.setdefault("rejected_transitions", []).append({
                "engine_id": candidate["engine_id"],
                "parameter": candidate["parameter"],
                "from": candidate["from_value"],
                "to": candidate["to_value"],
                "reason": "pr36_fresh_fixed_holdout_fail",
                "blocked_until": ap._iso(now + timedelta(days=30)),
                "candidate_id": candidate["candidate_id"],
            })
            ap.append_audit(
                state_dir / ap.AUDIT_FILENAME,
                "pr36_fresh_holdout_rejected",
                {"candidate_id": candidate["candidate_id"], "statistical_gate": gate},
                ap._iso(now),
            )
        evaluated.append({
            "candidate_id": candidate["candidate_id"],
            "engine_id": candidate["engine_id"],
            "status": status,
            "observed_n": len(available),
            "formal_sample_n": fixed_n,
        })

    registry["updated_at"] = ap._iso(now)
    ap._atomic_json(registry_path, registry)
    pr35v2.verify_state_v2(state_dir)
    report = {
        "schema_version": REPORT_SCHEMA,
        "generated_at": ap._iso(now),
        "promotion_methodology_version": pr35v2.PROMOTION_METHODOLOGY_VERSION,
        "promotion_mode": FREEZE_MODE,
        "production_promotion_enabled": False,
        "fixed_paired_n": fixed_n,
        "fresh_holdout_required": True,
        "pr35_sample_reuse_allowed": False,
        "evaluated": evaluated,
        "zero_trade_execution": True,
    }
    ap._atomic_json(state_dir / REPORT_FILENAME, report)
    return report


def verify(state_dir: Path, repo_root: Path) -> dict[str, Any]:
    config = load_config(repo_root)
    report_path = state_dir / REPORT_FILENAME
    if not report_path.exists():
        return {"report": "NOT_YET_GENERATED", "fixed_paired_n": config["fixed_paired_n"]}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("schema_version") != REPORT_SCHEMA:
        raise ValueError("PR36 v2 report schema mismatch")
    if report.get("production_promotion_enabled") is not False:
        raise ValueError("PR36 report violates promotion freeze")
    if report.get("fresh_holdout_required") is not True or report.get("pr35_sample_reuse_allowed") is not False:
        raise ValueError("PR36 fresh-holdout invariant violated")
    return {"report": "OK", "fixed_paired_n": report["fixed_paired_n"], "evaluated": len(report.get("evaluated") or [])}


def main() -> int:
    parser = argparse.ArgumentParser(description="BriefRooms PR36 fresh fixed-N holdout v2")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--now")
    args = parser.parse_args()
    state_dir = Path(args.state_dir)
    repo_root = Path(args.repo_root)
    if args.verify:
        print(json.dumps(verify(state_dir, repo_root), ensure_ascii=False, sort_keys=True))
        return 0
    now = ap._parse_time(args.now) if args.now else datetime.now(timezone.utc)
    print(json.dumps(run(state_dir, repo_root, now=now), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
