#!/usr/bin/env python3
"""Learning-integrity wrapper around PR35/PR36 methodology v2.

Adds three research-safety capabilities without changing the fixed-N gate:
1. hash-committed ValidationEpoch boundaries,
2. shadow-only anytime-valid confidence sequences,
3. strict verification that production promotion remains frozen.

The underlying PR35/PR36 v2 modules remain the formal research gate. This
module cannot make an anytime-valid result authoritative.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

try:
    import anytime_valid_inference as avi
    import autonomous_policy_promotion as ap
    import autonomous_policy_promotion_v2 as pr35v2
    import statistical_promotion_gate_v2 as pr36v2
    import validation_epoch as ve
except ModuleNotFoundError:  # pragma: no cover
    from scripts import anytime_valid_inference as avi
    from scripts import autonomous_policy_promotion as ap
    from scripts import autonomous_policy_promotion_v2 as pr35v2
    from scripts import statistical_promotion_gate_v2 as pr36v2
    from scripts import validation_epoch as ve

SCHEMA_VERSION = "briefrooms-promotion-learning-integrity-v1"
STATUS_FILENAME = "promotion_learning_integrity_status.json"
ACTIVE_STATUSES = {"SHADOW_VALIDATION", "PR36_HOLDOUT", "PROMOTION_ELIGIBLE_BUT_FROZEN"}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _now(value: Optional[datetime]) -> datetime:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _epoch_refs(candidate: dict[str, Any]) -> dict[str, Any]:
    refs = candidate.get("validation_epochs")
    if not isinstance(refs, dict):
        refs = {}
        candidate["validation_epochs"] = refs
    return refs


def _primary_plan(stage: str, repo_root: Path) -> dict[str, Any]:
    if stage == "PR35":
        return {
            "method": "fixed_n_single_look_pr35_v2",
            "fixed_n": pr35v2.VALIDATION_FIXED_N,
            "formal_test_performed_before_fixed_n": False,
            "authority": "research_gate_v2",
        }
    cfg = pr36v2.load_config(repo_root)
    frozen = {
        "method": "fixed_n_single_look_pr36_v2",
        "fixed_n": int(cfg["fixed_paired_n"]),
        "confidence_level": float(cfg["confidence_level"]),
        "bootstrap_samples": int(cfg["bootstrap_samples"]),
        "minimum_net_incremental_return_percent": float(cfg["minimum_net_incremental_return_percent"]),
        "minimum_net_positive_rate": float(cfg["minimum_net_positive_rate"]),
        "minimum_unique_symbols": int(cfg["minimum_unique_symbols"]),
        "minimum_span_days": int(cfg["minimum_span_days"]),
        "maximum_single_positive_contribution_share": float(cfg["maximum_single_positive_contribution_share"]),
        "engine_cost_stress": dict(cfg["engines"]),
        "fresh_holdout_required": True,
        "pr35_sample_reuse_allowed": False,
        "authority": "research_gate_v2",
    }
    frozen["plan_hash"] = _sha(frozen)
    return frozen


def _anytime_plan(stage: str) -> dict[str, Any]:
    estimand = (
        "winsorized_gross_marginal_return_percent"
        if stage == "PR35"
        else "winsorized_net_incremental_return_percent"
    )
    return avi.plan(estimand=estimand)


def _shadow_rows(state_dir: Path) -> list[dict[str, Any]]:
    path = state_dir / ap.SHADOW_FILENAME
    ap.verify_shadow(path)
    return ap._read_jsonl(path)


def _commit(
    state_dir: Path,
    candidate: dict[str, Any],
    *,
    stage: str,
    repo_root: Path,
    now: datetime,
) -> dict[str, Any]:
    ref = ve.commit_epoch(
        state_dir,
        candidate,
        stage=stage,
        committed_at=now,
        primary_inference_plan=_primary_plan(stage, repo_root),
        shadow_anytime_plan=_anytime_plan(stage),
        shadow_rows=_shadow_rows(state_dir),
    )
    _epoch_refs(candidate)[stage.lower()] = ref
    ap.append_audit(
        state_dir / ap.AUDIT_FILENAME,
        "validation_epoch_committed",
        {
            "candidate_id": candidate["candidate_id"],
            "stage": stage,
            "epoch": ref,
            "formal_reuse_of_precommit_evidence": False,
            "anytime_inference_authority": "shadow_only",
        },
        ref["committed_at"],
    )
    return ref


def _reset_to_pr35_boundary(candidate: dict[str, Any], ref: Mapping[str, Any], now: datetime) -> None:
    previous_status = str(candidate.get("status") or "")
    candidate["status"] = "SHADOW_VALIDATION"
    candidate["validation_start_at"] = str(ref["committed_at"])
    candidate["validation"] = ap._summary([])
    candidate["promotion_gate"] = {
        "status": "COLLECTING",
        "blocking_reasons": ["hash_committed_validation_epoch_fresh_sample_required"],
        "observed_n": 0,
        "target_n": pr35v2.VALIDATION_FIXED_N,
        "formal_test_performed": False,
        "evaluated_at": ap._iso(now),
    }
    candidate["pr35_passed_at"] = None
    candidate["confirmation_start_at"] = None
    candidate["confirmation_sample_reuse_allowed"] = None
    candidate.pop("statistical_gate", None)
    candidate.pop("promotion_eligible_at", None)
    candidate["validation_epoch_migration"] = {
        "migrated_at": ap._iso(now),
        "previous_status": previous_status,
        "historical_shadow_evidence_retained": True,
        "historical_shadow_evidence_formal_reuse": False,
    }


def _ensure_pr35_epoch(
    state_dir: Path, candidate: dict[str, Any], repo_root: Path, now: datetime, *, migrate: bool
) -> dict[str, Any]:
    refs = _epoch_refs(candidate)
    existing = refs.get("pr35")
    if isinstance(existing, Mapping):
        event = ve.verify_epoch_reference(state_dir, candidate, stage="PR35", reference=existing)
        candidate["validation_start_at"] = str(event["committed_at"])
        return dict(existing)
    ref = _commit(state_dir, candidate, stage="PR35", repo_root=repo_root, now=now)
    if migrate:
        _reset_to_pr35_boundary(candidate, ref, now)
    else:
        candidate["validation_start_at"] = str(ref["committed_at"])
    return ref


def _ensure_pr36_epoch(
    state_dir: Path, candidate: dict[str, Any], repo_root: Path, now: datetime
) -> dict[str, Any]:
    refs = _epoch_refs(candidate)
    existing = refs.get("pr36")
    if isinstance(existing, Mapping):
        event = ve.verify_epoch_reference(state_dir, candidate, stage="PR36", reference=existing)
        candidate["confirmation_start_at"] = str(event["committed_at"])
        candidate["confirmation_sample_reuse_allowed"] = False
        return dict(existing)
    ref = _commit(state_dir, candidate, stage="PR36", repo_root=repo_root, now=now)
    candidate["confirmation_start_at"] = str(ref["committed_at"])
    candidate["confirmation_sample_reuse_allowed"] = False
    candidate.pop("statistical_gate", None)
    return ref


def _eligible_rows(state_dir: Path, candidate: Mapping[str, Any], stage: str) -> list[Mapping[str, Any]]:
    refs = candidate.get("validation_epochs") if isinstance(candidate.get("validation_epochs"), Mapping) else {}
    ref = refs.get(stage.lower()) if isinstance(refs, Mapping) else None
    if not isinstance(ref, Mapping):
        return []
    event = ve.verify_epoch_reference(state_dir, candidate, stage=stage, reference=ref)
    rows = _shadow_rows(state_dir)
    return sorted(
        ap._marginal_rows(
            rows,
            engine_id=str(candidate["engine_id"]),
            gate=str(candidate["gate"]),
            current_value=float(candidate["from_value"]),
            candidate_value=float(candidate["to_value"]),
            after=ve.eligible_after(event),
        ),
        key=lambda row: str(row.get("decision_at") or ""),
    )


def _attach_anytime_monitor(state_dir: Path, candidate: dict[str, Any], repo_root: Path) -> None:
    monitors = candidate.get("anytime_valid_shadow")
    if not isinstance(monitors, dict):
        monitors = {}
        candidate["anytime_valid_shadow"] = monitors

    refs = candidate.get("validation_epochs") if isinstance(candidate.get("validation_epochs"), Mapping) else {}
    if isinstance(refs.get("pr35"), Mapping):
        rows = _eligible_rows(state_dir, candidate, "PR35")
        result = avi.confidence_sequence(avi.values_from_rows(rows))
        result.update({
            "stage": "PR35",
            "epoch_id": refs["pr35"]["epoch_id"],
            "observations_are_formal_gate_input": False,
            "fixed_n_gate_unchanged": True,
        })
        monitors["pr35"] = result

    if isinstance(refs.get("pr36"), Mapping):
        rows = _eligible_rows(state_dir, candidate, "PR36")
        cfg = pr36v2.load_config(repo_root)
        cost = float(cfg["engines"][candidate["engine_id"]]["round_trip_cost_stress_percent"])
        result = avi.confidence_sequence(avi.values_from_rows(rows, subtract=cost))
        result.update({
            "stage": "PR36",
            "epoch_id": refs["pr36"]["epoch_id"],
            "cost_stress_percent": cost,
            "observations_are_formal_gate_input": False,
            "fixed_n_gate_unchanged": True,
        })
        monitors["pr36"] = result


def _prepare_existing_candidates(state_dir: Path, repo_root: Path, now: datetime) -> dict[str, int]:
    registry_path = state_dir / ap.REGISTRY_FILENAME
    registry = ap._read_json(registry_path)
    if not isinstance(registry, dict):
        return {"pr35_epochs": 0, "pr36_epochs": 0, "migrated_candidates": 0}
    counts = {"pr35_epochs": 0, "pr36_epochs": 0, "migrated_candidates": 0}
    for candidate in (registry.get("candidates") or {}).values():
        if not isinstance(candidate, dict) or candidate.get("status") not in ACTIVE_STATUSES:
            continue
        refs = _epoch_refs(candidate)
        if not isinstance(refs.get("pr35"), Mapping):
            _ensure_pr35_epoch(state_dir, candidate, repo_root, now, migrate=True)
            counts["pr35_epochs"] += 1
            counts["migrated_candidates"] += 1
            ap.append_audit(
                state_dir / ap.AUDIT_FILENAME,
                "candidate_reset_for_hash_committed_validation",
                {
                    "candidate_id": candidate["candidate_id"],
                    "historical_evidence_retained": True,
                    "historical_evidence_formal_reuse": False,
                },
                ap._iso(now),
            )
        else:
            _ensure_pr35_epoch(state_dir, candidate, repo_root, now, migrate=False)

        if candidate.get("status") == "PR36_HOLDOUT":
            before = isinstance(_epoch_refs(candidate).get("pr36"), Mapping)
            _ensure_pr36_epoch(state_dir, candidate, repo_root, now)
            if not before:
                counts["pr36_epochs"] += 1
    registry["updated_at"] = ap._iso(now)
    ap._atomic_json(registry_path, registry)
    return counts


def _post_pr35(state_dir: Path, repo_root: Path, now: datetime) -> dict[str, int]:
    registry_path = state_dir / ap.REGISTRY_FILENAME
    registry = ap._read_json(registry_path)
    counts = {"pr35_epochs": 0, "pr36_epochs": 0}
    for candidate in (registry.get("candidates") or {}).values():
        if not isinstance(candidate, dict):
            continue
        if candidate.get("status") in ACTIVE_STATUSES:
            refs = _epoch_refs(candidate)
            if not isinstance(refs.get("pr35"), Mapping):
                _ensure_pr35_epoch(state_dir, candidate, repo_root, now, migrate=False)
                counts["pr35_epochs"] += 1
            else:
                _ensure_pr35_epoch(state_dir, candidate, repo_root, now, migrate=False)
        if candidate.get("status") == "PR36_HOLDOUT":
            before = isinstance(_epoch_refs(candidate).get("pr36"), Mapping)
            _ensure_pr36_epoch(state_dir, candidate, repo_root, now)
            if not before:
                counts["pr36_epochs"] += 1
        _attach_anytime_monitor(state_dir, candidate, repo_root)
    registry["updated_at"] = ap._iso(now)
    ap._atomic_json(registry_path, registry)
    return counts


def run_pr35(
    state_dir: Path,
    learning_state_dir: Path,
    repo_root: Path,
    *,
    allow_network: bool = True,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    now = _now(now)
    state_dir.mkdir(parents=True, exist_ok=True)
    ap.ensure_activation(state_dir, now)
    registry = ap.ensure_registry(state_dir, repo_root, now)
    pr35v2._governance(registry, now)
    ap._atomic_json(state_dir / ap.REGISTRY_FILENAME, registry)

    pre = _prepare_existing_candidates(state_dir, repo_root, now)
    base = pr35v2.run(
        state_dir,
        learning_state_dir,
        repo_root,
        allow_network=allow_network,
        now=now,
    )
    post = _post_pr35(state_dir, repo_root, now)
    verification = verify(state_dir, repo_root)
    result = {
        "schema_version": SCHEMA_VERSION,
        "stage": "PR35",
        "generated_at": ap._iso(now),
        "base_gate": base,
        "validation_epochs_before_run": pre,
        "validation_epochs_after_run": post,
        "verification": verification,
        "zero_authority": True,
    }
    ap._atomic_json(state_dir / STATUS_FILENAME, result)
    return result


def run_pr36(state_dir: Path, repo_root: Path, *, now: Optional[datetime] = None) -> dict[str, Any]:
    now = _now(now)
    registry_path = state_dir / ap.REGISTRY_FILENAME
    registry = ap._read_json(registry_path)
    if not isinstance(registry, dict):
        raise RuntimeError("PR36 learning integrity requires existing policy registry")
    committed = 0
    for candidate in (registry.get("candidates") or {}).values():
        if not isinstance(candidate, dict) or candidate.get("status") != "PR36_HOLDOUT":
            continue
        _ensure_pr35_epoch(state_dir, candidate, repo_root, now, migrate=False)
        before = isinstance(_epoch_refs(candidate).get("pr36"), Mapping)
        _ensure_pr36_epoch(state_dir, candidate, repo_root, now)
        if not before:
            committed += 1
    registry["updated_at"] = ap._iso(now)
    ap._atomic_json(registry_path, registry)

    base = pr36v2.run(state_dir, repo_root, now=now)
    registry = ap._read_json(registry_path)
    for candidate in (registry.get("candidates") or {}).values():
        if isinstance(candidate, dict):
            _attach_anytime_monitor(state_dir, candidate, repo_root)
    registry["updated_at"] = ap._iso(now)
    ap._atomic_json(registry_path, registry)

    verification = verify(state_dir, repo_root)
    result = {
        "schema_version": SCHEMA_VERSION,
        "stage": "PR36",
        "generated_at": ap._iso(now),
        "base_gate": base,
        "pr36_epochs_committed": committed,
        "verification": verification,
        "zero_authority": True,
    }
    ap._atomic_json(state_dir / STATUS_FILENAME, result)
    return result


def verify(state_dir: Path, repo_root: Path) -> dict[str, Any]:
    base = pr35v2.verify_state_v2(state_dir)
    governance = base["governance"]
    if governance.get("production_promotion_enabled") is not False:
        raise ValueError("learning integrity requires production promotion freeze")
    epoch_chain = ve.verify_chain(state_dir / ve.LEDGER_FILENAME)
    registry = ap._read_json(state_dir / ap.REGISTRY_FILENAME)
    active = 0
    monitors = 0
    for candidate in (registry.get("candidates") or {}).values():
        if not isinstance(candidate, dict):
            continue
        if candidate.get("status") in ACTIVE_STATUSES:
            active += 1
            refs = candidate.get("validation_epochs")
            if not isinstance(refs, Mapping) or not isinstance(refs.get("pr35"), Mapping):
                raise ValueError("active candidate has no hash-committed PR35 ValidationEpoch")
            pr35_event = ve.verify_epoch_reference(state_dir, candidate, stage="PR35", reference=refs["pr35"])
            if str(candidate.get("validation_start_at")) != str(pr35_event["committed_at"]):
                raise ValueError("PR35 mutable timestamp diverges from committed epoch")
            if candidate.get("status") in {"PR36_HOLDOUT", "PROMOTION_ELIGIBLE_BUT_FROZEN"}:
                if not isinstance(refs.get("pr36"), Mapping):
                    raise ValueError("post-PR35 candidate has no hash-committed PR36 ValidationEpoch")
                pr36_event = ve.verify_epoch_reference(state_dir, candidate, stage="PR36", reference=refs["pr36"])
                if str(candidate.get("confirmation_start_at")) != str(pr36_event["committed_at"]):
                    raise ValueError("PR36 mutable timestamp diverges from committed epoch")
                if candidate.get("confirmation_sample_reuse_allowed") is not False:
                    raise ValueError("PR36 sample reuse invariant violated")
        shadow = candidate.get("anytime_valid_shadow")
        if isinstance(shadow, Mapping):
            for result in shadow.values():
                if not isinstance(result, Mapping):
                    continue
                if result.get("authority") != "shadow_only" or result.get("formal_promotion_decision") is not False:
                    raise ValueError("anytime-valid shadow monitor gained authority")
                monitors += 1
    return {
        "validation_epoch_chain": epoch_chain,
        "active_candidates_verified": active,
        "anytime_shadow_monitors_verified": monitors,
        "production_promotion_enabled": False,
        "fixed_n_primary_gate_unchanged": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="BriefRooms promotion learning-integrity layer")
    sub = parser.add_subparsers(dest="command", required=True)

    pr35 = sub.add_parser("pr35")
    pr35.add_argument("--state-dir", required=True)
    pr35.add_argument("--learning-state-dir", required=True)
    pr35.add_argument("--repo-root", default=".")
    pr35.add_argument("--no-network", action="store_true")
    pr35.add_argument("--now")

    pr36 = sub.add_parser("pr36")
    pr36.add_argument("--state-dir", required=True)
    pr36.add_argument("--repo-root", default=".")
    pr36.add_argument("--now")

    verify_p = sub.add_parser("verify")
    verify_p.add_argument("--state-dir", required=True)
    verify_p.add_argument("--repo-root", default=".")

    args = parser.parse_args()
    state_dir = Path(args.state_dir)
    repo_root = Path(args.repo_root)
    if args.command == "verify":
        result = verify(state_dir, repo_root)
    elif args.command == "pr35":
        now = ap._parse_time(args.now) if args.now else datetime.now(timezone.utc)
        result = run_pr35(
            state_dir,
            Path(args.learning_state_dir),
            repo_root,
            allow_network=not args.no_network,
            now=now,
        )
    else:
        now = ap._parse_time(args.now) if args.now else datetime.now(timezone.utc)
        result = run_pr36(state_dir, repo_root, now=now)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
