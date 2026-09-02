#!/usr/bin/env python3
"""PR-A / PR35 methodology v2: fixed-N shadow validation with production frozen.

This module deliberately reuses the proven PR35 prospective shadow-settlement,
hash-chain and rollback primitives while replacing the repeated-look promotion
state machine. A candidate is formally evaluated exactly once on a pre-registered
fixed validation sample. A PR35 pass starts a fresh PR36 holdout; it never changes
the active production policy.
"""
from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    import autonomous_policy_promotion as ap
    from learning_ledger import read_events, verify_chain
except ModuleNotFoundError:  # pragma: no cover
    from scripts import autonomous_policy_promotion as ap
    from scripts.learning_ledger import read_events, verify_chain

SCHEMA_VERSION = "briefrooms-autonomous-policy-promotion-v2"
PROMOTION_METHODOLOGY_VERSION = 2
VALIDATION_FIXED_N = 30
FREEZE_MODE = "FROZEN_FOR_PROSPECTIVE_VALIDATION"
NONTERMINAL_STATUSES = {"SHADOW_VALIDATION", "PR36_HOLDOUT", "PROMOTION_ELIGIBLE_BUT_FROZEN"}


def _governance(registry: dict[str, Any], now: datetime) -> dict[str, Any]:
    existing = registry.get("governance") if isinstance(registry.get("governance"), Mapping) else {}
    boundary = existing.get("migration_boundary_at") or ap._iso(now)
    governance = {
        "promotion_methodology_version": PROMOTION_METHODOLOGY_VERSION,
        "promotion_mode": FREEZE_MODE,
        "production_promotion_enabled": False,
        "production_materialization_enabled": False,
        "shadow_candidate_evaluation_enabled": True,
        "migration_boundary_at": boundary,
    }
    registry["governance"] = governance
    return governance


def _restore_legacy_active_policy(registry: dict[str, Any], state_dir: Path, now: datetime) -> int:
    """Restore any pre-v2 nonbaseline policy before collecting v2 evidence.

    Current production is already baseline, but this is a fail-closed migration
    guard for any durable artifact created immediately before PR-A deployment.
    """
    restored_count = 0
    for engine_id, state in list((registry.get("engines") or {}).items()):
        if int(state.get("revision") or 0) <= 0:
            continue
        parent = state.get("parent") if isinstance(state.get("parent"), Mapping) else None
        if parent:
            restored = copy.deepcopy(dict(parent))
            restored.setdefault("engine_id", engine_id)
            restored["status"] = "ACTIVE"
            restored["blocked_until"] = None
        else:
            baseline_version = str(state.get("baseline_policy_version") or "")
            restored = {
                "engine_id": engine_id,
                "status": "ACTIVE",
                "policy_id": f"{engine_id}:baseline:{baseline_version}",
                "revision": 0,
                "baseline_policy_version": baseline_version,
                "effective_policy_version": baseline_version,
                "overrides": {},
                "activated_at": None,
                "source_candidate_id": None,
                "parent": None,
                "blocked_until": None,
            }
        candidate_id = str(state.get("source_candidate_id") or "")
        if candidate_id in (registry.get("candidates") or {}):
            candidate = registry["candidates"][candidate_id]
            candidate["status"] = "LEGACY_PROMOTION_FROZEN"
            candidate["frozen_at"] = ap._iso(now)
        registry["engines"][engine_id] = restored
        ap.append_audit(
            state_dir / ap.AUDIT_FILENAME,
            "legacy_policy_frozen_for_methodology_v2",
            {"engine_id": engine_id, "previous_policy": ap._policy_snapshot(state), "restored_policy": restored},
            ap._iso(now),
        )
        restored_count += 1
    return restored_count


def _migrate_candidates(registry: dict[str, Any], state_dir: Path, now: datetime) -> int:
    migrated = 0
    for candidate in (registry.get("candidates") or {}).values():
        if int(candidate.get("promotion_methodology_version") or 0) == PROMOTION_METHODOLOGY_VERSION:
            continue
        if candidate.get("status") not in {"SHADOW_VALIDATION", "PROMOTED"}:
            continue
        prior = {
            "status": candidate.get("status"),
            "validation_start_at": candidate.get("validation_start_at"),
            "validation": copy.deepcopy(candidate.get("validation") or {}),
            "promotion_gate": copy.deepcopy(candidate.get("promotion_gate") or {}),
        }
        candidate.update({
            "promotion_methodology_version": PROMOTION_METHODOLOGY_VERSION,
            "validation_target_n": VALIDATION_FIXED_N,
            "validation_start_at": ap._iso(now),
            "validation": ap._summary([]),
            "promotion_gate": {
                "status": "COLLECTING",
                "blocking_reasons": ["methodology_v2_fresh_fixed_n_validation_required"],
                "evaluated_at": ap._iso(now),
            },
            "status": "SHADOW_VALIDATION",
            "pr35_passed_at": None,
            "confirmation_start_at": None,
            "methodology_migration": {
                "migrated_at": ap._iso(now),
                "historical_evidence_retained": True,
                "historical_evidence_formal_reuse": False,
                "prior": prior,
            },
        })
        ap.append_audit(state_dir / ap.AUDIT_FILENAME, "candidate_methodology_v2_reset", candidate, ap._iso(now))
        migrated += 1
    return migrated


def _fixed_gate(stats: Mapping[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if int(stats.get("n") or 0) != VALIDATION_FIXED_N:
        raise ValueError("PR35 fixed gate requires the exact pre-registered sample size")
    if float(stats.get("mean_return_percent") or 0.0) < 0.15:
        reasons.append("mean_return_below_0.15pct")
    if float(stats.get("positive_rate") or 0.0) < 0.55:
        reasons.append("positive_rate_below_55pct")
    if stats.get("mean_r") is not None and float(stats["mean_r"]) < 0.10:
        reasons.append("mean_r_below_0.10")
    if stats.get("max_drawdown_r") is not None and float(stats["max_drawdown_r"]) < -3.0:
        reasons.append("shadow_drawdown_below_minus_3R")
    if float(stats.get("first_half_mean_return") or 0.0) <= 0.0:
        reasons.append("first_half_not_positive")
    if float(stats.get("second_half_mean_return") or 0.0) <= 0.0:
        reasons.append("second_half_not_positive")
    return ("PASS", []) if not reasons else ("FAIL", reasons)


def update_candidates_fixed_n(registry: dict[str, Any], state_dir: Path, now: datetime) -> tuple[int, int]:
    rows = ap._read_jsonl(state_dir / ap.SHADOW_FILENAME)
    passed = rejected = 0
    for candidate in list((registry.get("candidates") or {}).values()):
        if candidate.get("status") != "SHADOW_VALIDATION":
            continue
        if int(candidate.get("promotion_methodology_version") or 0) != PROMOTION_METHODOLOGY_VERSION:
            raise RuntimeError("non-v2 candidate reached fixed-N evaluator")
        target_n = int(candidate.get("validation_target_n") or 0)
        if target_n != VALIDATION_FIXED_N:
            raise RuntimeError("PR35 validation target changed after preregistration")
        validation = sorted(
            ap._marginal_rows(
                rows,
                engine_id=str(candidate["engine_id"]),
                gate=str(candidate["gate"]),
                current_value=float(candidate["from_value"]),
                candidate_value=float(candidate["to_value"]),
                after=ap._parse_time(str(candidate["validation_start_at"])),
            ),
            key=lambda row: str(row.get("decision_at") or ""),
        )
        if len(validation) < target_n:
            candidate["validation"] = ap._summary(validation)
            candidate["promotion_gate"] = {
                "status": "COLLECTING",
                "blocking_reasons": [f"fixed_validation_n_below_{target_n}"],
                "observed_n": len(validation),
                "target_n": target_n,
                "formal_test_performed": False,
                "evaluated_at": ap._iso(now),
            }
            continue

        frozen_rows = validation[:target_n]
        stats = ap._summary(frozen_rows)
        status, reasons = _fixed_gate(stats)
        candidate["validation"] = stats
        candidate["promotion_gate"] = {
            "status": status,
            "blocking_reasons": reasons,
            "observed_n": len(validation),
            "target_n": target_n,
            "formal_sample_n": target_n,
            "formal_test_performed": True,
            "sample_shadow_outcome_ids": [str(row.get("shadow_outcome_id") or "") for row in frozen_rows],
            "evaluated_at": ap._iso(now),
        }
        if status == "PASS":
            candidate["status"] = "PR36_HOLDOUT"
            candidate["pr35_passed_at"] = ap._iso(now)
            candidate["confirmation_start_at"] = ap._iso(now)
            candidate["confirmation_sample_reuse_allowed"] = False
            ap.append_audit(state_dir / ap.AUDIT_FILENAME, "pr35_fixed_validation_pass", candidate, ap._iso(now))
            passed += 1
        else:
            candidate["status"] = "REJECTED"
            candidate["rejected_at"] = ap._iso(now)
            blocked_until = now + timedelta(days=30)
            registry.setdefault("rejected_transitions", []).append({
                "engine_id": candidate["engine_id"],
                "parameter": candidate["parameter"],
                "from": candidate["from_value"],
                "to": candidate["to_value"],
                "reason": "pr35_fixed_validation_fail",
                "blocked_until": ap._iso(blocked_until),
            })
            ap.append_audit(state_dir / ap.AUDIT_FILENAME, "pr35_fixed_validation_rejected", candidate, ap._iso(now))
            rejected += 1
    return passed, rejected


def create_candidates_v2(registry: dict[str, Any], state_dir: Path, repo_root: Path, now: datetime) -> int:
    rows = ap._read_jsonl(state_dir / ap.SHADOW_FILENAME)
    created = 0
    for engine_id, spec in ap.POLICY_SPECS.items():
        engine = registry["engines"][engine_id]
        blocked_until = engine.get("blocked_until")
        if blocked_until and ap._parse_time(str(blocked_until)) > now:
            continue
        if any(c.get("engine_id") == engine_id and c.get("status") in NONTERMINAL_STATUSES for c in registry["candidates"].values()):
            continue
        current = ap._current_value(engine_id, registry, repo_root)
        proposed = max(float(spec["lower"]), current - float(spec["step"]))
        if proposed >= current - 1e-9:
            continue
        if ap._transition_recently_blocked(registry, engine_id, spec["parameter"], current, proposed, now):
            continue
        training = ap._marginal_rows(
            rows,
            engine_id=engine_id,
            gate=spec["gate"],
            current_value=current,
            candidate_value=proposed,
        )
        stats = ap._summary(training)
        if stats["n"] < ap.TRAIN_MIN_N:
            continue
        if float(stats["mean_return_percent"] or 0.0) < 0.15 or float(stats["positive_rate"] or 0.0) < 0.55:
            continue
        if stats["mean_r"] is not None and float(stats["mean_r"]) < 0.10:
            continue
        candidate_id = ap._stable_id("policycand", engine_id, spec["parameter"], current, proposed, ap._iso(now))
        candidate = {
            "candidate_id": candidate_id,
            "engine_id": engine_id,
            "parameter": spec["parameter"],
            "gate": spec["gate"],
            "from_value": current,
            "to_value": proposed,
            "created_at": ap._iso(now),
            "promotion_methodology_version": PROMOTION_METHODOLOGY_VERSION,
            "validation_target_n": VALIDATION_FIXED_N,
            "validation_start_at": ap._iso(now),
            "status": "SHADOW_VALIDATION",
            "training": stats,
            "validation": ap._summary([]),
            "promotion_gate": {
                "status": "COLLECTING",
                "observed_n": 0,
                "target_n": VALIDATION_FIXED_N,
                "formal_test_performed": False,
            },
            "pr35_passed_at": None,
            "confirmation_start_at": None,
        }
        registry["candidates"][candidate_id] = candidate
        ap.append_audit(state_dir / ap.AUDIT_FILENAME, "candidate_created_methodology_v2", candidate, ap._iso(now))
        created += 1
    return created


def verify_state_v2(state_dir: Path) -> dict[str, Any]:
    base = ap.verify_state(state_dir)
    registry = ap._read_json(state_dir / ap.REGISTRY_FILENAME)
    governance = registry.get("governance") if isinstance(registry.get("governance"), Mapping) else {}
    if governance.get("production_promotion_enabled") is not False:
        raise ValueError("production promotion freeze is not active")
    if governance.get("promotion_mode") != FREEZE_MODE:
        raise ValueError("unexpected promotion mode")
    if int(governance.get("promotion_methodology_version") or 0) != PROMOTION_METHODOLOGY_VERSION:
        raise ValueError("promotion methodology version mismatch")
    for candidate in (registry.get("candidates") or {}).values():
        if candidate.get("status") in {"SHADOW_VALIDATION", "PR36_HOLDOUT", "PROMOTION_ELIGIBLE_BUT_FROZEN"}:
            if int(candidate.get("promotion_methodology_version") or 0) != PROMOTION_METHODOLOGY_VERSION:
                raise ValueError("active candidate is not methodology v2")
    return {**base, "governance": governance}


def run(
    state_dir: Path,
    learning_state_dir: Path,
    repo_root: Path,
    *,
    allow_network: bool = True,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    state_dir.mkdir(parents=True, exist_ok=True)
    activation = ap.ensure_activation(state_dir, now)
    registry = ap.ensure_registry(state_dir, repo_root, now)
    governance = _governance(registry, now)
    legacy_policies_frozen = _restore_legacy_active_policy(registry, state_dir, now)
    migrated_candidates = _migrate_candidates(registry, state_dir, now)

    ledger = learning_state_dir / "learning_ledger.jsonl"
    events: list[Mapping[str, Any]] = []
    if ledger.exists():
        chain = verify_chain(ledger)
        if not chain.get("ok"):
            raise RuntimeError("invalid Learning Ledger")
        events = read_events(ledger)

    shadow_stats = ap.collect_shadow_outcomes(state_dir, events, activation, allow_network=allow_network, now=now)
    rollbacks = ap.monitor_rollbacks(registry, state_dir, repo_root, now)
    pr35_passed, rejected = update_candidates_fixed_n(registry, state_dir, now)
    created = create_candidates_v2(registry, state_dir, repo_root, now)

    registry["updated_at"] = ap._iso(now)
    ap._atomic_json(state_dir / ap.REGISTRY_FILENAME, registry)
    registry = ap._read_json(state_dir / ap.REGISTRY_FILENAME)
    verified = verify_state_v2(state_dir)
    status = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": ap._iso(now),
        "mode": "shadow_policy_research_v2",
        "promotion_mode": FREEZE_MODE,
        "production_promotion_enabled": False,
        "promotion_methodology_version": PROMOTION_METHODOLOGY_VERSION,
        "validation_fixed_n": VALIDATION_FIXED_N,
        "governance": governance,
        "shadow_settlement": shadow_stats,
        "candidates_created": created,
        "candidates_pr35_passed": pr35_passed,
        "candidates_rejected": rejected,
        "legacy_candidates_migrated": migrated_candidates,
        "legacy_policies_frozen": legacy_policies_frozen,
        "automatic_rollbacks": rollbacks,
        "active_policies": {
            engine: {
                "revision": state.get("revision"),
                "effective_policy_version": state.get("effective_policy_version"),
                "overrides": state.get("overrides"),
            }
            for engine, state in registry["engines"].items()
        },
        "verification": verified,
    }
    ap._atomic_json(state_dir / ap.STATUS_FILENAME, status)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description="BriefRooms PR-A fixed-N policy research v2")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--learning-state-dir")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--no-network", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--now")
    args = parser.parse_args()
    state_dir = Path(args.state_dir)
    if args.verify:
        print(json.dumps(verify_state_v2(state_dir), ensure_ascii=False, sort_keys=True))
        return 0
    if not args.learning_state_dir:
        raise SystemExit("--learning-state-dir is required unless --verify")
    now = ap._parse_time(args.now) if args.now else datetime.now(timezone.utc)
    status = run(
        state_dir,
        Path(args.learning_state_dir),
        Path(args.repo_root),
        allow_network=not args.no_network,
        now=now,
    )
    print(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
