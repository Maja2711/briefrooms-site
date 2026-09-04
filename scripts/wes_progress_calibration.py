#!/usr/bin/env python3
"""WES-local progress detector -> calibration challenger pipeline.

This module belongs to the WES engine-local learning loop. It detects sustained
prospective incremental-value progress, converts that progress into a WES-local
Lesson and Hypothesis, creates a frozen SHADOW calibration challenger, and commits
a new engine-local ValidationEpoch before any challenger validation evidence may
be consumed.

It never mutates active WES configuration, scores, thresholds, TP/SL, exposure,
production policy, or another engine's state. Promotion remains a separate
governance action.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    import engine_learning_framework as elf
except ModuleNotFoundError:  # pragma: no cover
    from scripts import engine_learning_framework as elf

POLICY_SCHEMA = "briefrooms-wes-calibration-policy-v1"
PROGRESS_SCHEMA = "briefrooms-wes-progress-signal-v1"
CANDIDATE_SCHEMA = "briefrooms-wes-calibration-candidate-v1"
STATUS_SCHEMA = "briefrooms-wes-calibration-status-v1"

DEFAULT_POLICY = Path("data/investments/wes_calibration_policy_v1.json")
PROGRESS_FILENAME = "progress_signals.jsonl"
CANDIDATE_FILENAME = "calibration_candidates.jsonl"
DETECTOR_STATE_FILENAME = "progress_detector_state.json"
STATUS_FILENAME = "calibration_status.json"

ENGINE_ID = "wes"
ALLOWED_AXIS = {"incremental_decision_value"}
POSITIVE_CONCLUSIONS = {"DESCRIPTIVE_SIGNAL", "SUSTAINED_POSITIVE_PROGRESS"}

ZERO_AUTHORITY = dict(elf.ZERO_AUTHORITY)
ZERO_AUTHORITY.update({
    "active_wes_writeback": False,
    "candidate_to_production_writeback": False,
})


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int(value: Any) -> int | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object: {path}")
    return payload


def _write_hashed_json(path: Path, payload: Mapping[str, Any], *, hash_field: str) -> dict[str, Any]:
    body = dict(payload)
    body.pop(hash_field, None)
    body[hash_field] = elf._sha(body)
    elf._write_json(path, body)
    return body


def validate_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    if policy.get("schema_version") != POLICY_SCHEMA:
        raise ValueError("WES calibration policy schema mismatch")
    if policy.get("engine_id") != ENGINE_ID:
        raise ValueError("WES calibration policy engine_id mismatch")
    detector = policy.get("detector")
    candidate = policy.get("candidate")
    authority = policy.get("authority")
    if not isinstance(detector, Mapping) or not isinstance(candidate, Mapping) or not isinstance(authority, Mapping):
        raise ValueError("WES calibration policy sections missing")
    if int(detector.get("minimum_resolved_pairs") or 0) < 12:
        raise ValueError("WES progress detector minimum_resolved_pairs must be >= 12")
    if float(detector.get("minimum_effective_samples") or 0.0) < 12.0:
        raise ValueError("WES progress detector minimum_effective_samples must be >= 12")
    if detector.get("require_sample_growth") is not True:
        raise ValueError("WES progress detector must require sample growth")
    if detector.get("require_metric_improvement") is not True:
        raise ValueError("WES progress detector must require metric improvement")
    if float(detector.get("minimum_mean_incremental_alpha_percent_exclusive") or 0.0) != 0.0:
        raise ValueError("WES v1 detector positive-alpha boundary must remain > 0")
    if float(detector.get("minimum_wes_better_than_v5_rate_exclusive") or 0.0) != 0.5:
        raise ValueError("WES v1 detector better-than-V5 boundary must remain > 0.5")

    if candidate.get("mode") != "SHADOW_CHALLENGER_ONLY":
        raise ValueError("WES calibration candidate must be SHADOW_CHALLENGER_ONLY")
    if candidate.get("calibration_axis") not in ALLOWED_AXIS:
        raise ValueError("unsupported WES calibration axis")
    if int(candidate.get("validation_target_n") or 0) < 12:
        raise ValueError("WES validation_target_n too small")
    if int(candidate.get("formal_evaluation_count") or 0) != 1:
        raise ValueError("WES formal_evaluation_count must equal 1")
    if candidate.get("prospective_only") is not True:
        raise ValueError("WES challenger must be prospective_only")
    if candidate.get("historical_backfill") is not False:
        raise ValueError("WES challenger must forbid historical backfill")
    if candidate.get("requires_new_validation_epoch") is not True:
        raise ValueError("WES challenger must require a new ValidationEpoch")
    if candidate.get("automatic_active_wes_writeback") is not False:
        raise ValueError("WES challenger may not write to active WES")
    if candidate.get("requires_promotion_gate") is not True:
        raise ValueError("WES challenger must require a promotion gate")
    if int(candidate.get("max_active_candidates") or 0) != 1:
        raise ValueError("WES v1 permits exactly one active calibration challenger")

    for key, expected in ZERO_AUTHORITY.items():
        if authority.get(key) is not expected:
            raise ValueError(f"WES calibration authority violation: {key}")
    stored = str(policy.get("policy_sha256") or "")
    body = dict(policy)
    body.pop("policy_sha256", None)
    if not stored or stored != elf._sha(body):
        raise ValueError("WES calibration policy hash mismatch")
    return {
        "ok": True,
        "engine_id": ENGINE_ID,
        "minimum_resolved_pairs": int(detector["minimum_resolved_pairs"]),
        "validation_target_n": int(candidate["validation_target_n"]),
        "zero_authority": True,
    }


def load_policy(path: Path = DEFAULT_POLICY) -> dict[str, Any]:
    policy = _read_json(path)
    validate_policy(policy)
    return policy


def _wes_partition(state_root: Path) -> Path:
    return state_root / ENGINE_ID


def _progress_path(state_root: Path) -> Path:
    return _wes_partition(state_root) / PROGRESS_FILENAME


def _candidate_path(state_root: Path) -> Path:
    return _wes_partition(state_root) / CANDIDATE_FILENAME


def verify_progress_ledger(state_root: Path) -> dict[str, Any]:
    return elf._verify_chain(_progress_path(state_root), schema=PROGRESS_SCHEMA, id_field="progress_signal_id")


def verify_candidate_ledger(state_root: Path) -> dict[str, Any]:
    return elf._verify_chain(_candidate_path(state_root), schema=CANDIDATE_SCHEMA, id_field="calibration_candidate_id")


def _validate_wes_evidence(row: Mapping[str, Any]) -> None:
    if row.get("schema_version") != elf.EVIDENCE_SCHEMA:
        raise ValueError("WES evidence schema mismatch")
    if str(row.get("engine_id") or "") != ENGINE_ID:
        raise ValueError("cross-engine evidence presented to WES progress detector")
    if str(row.get("sample_unit") or "") != "resolved_counterfactual_pairs":
        raise ValueError("unsupported WES evidence sample_unit")
    facts = row.get("facts") if isinstance(row.get("facts"), Mapping) else {}
    if facts.get("historical_backfill_allowed") is not False:
        raise ValueError("WES detector fail-closed: historical_backfill_allowed must be false")
    if facts.get("active_decision_influence") is not False:
        raise ValueError("WES detector fail-closed: active_decision_influence must be false")


def _gate(row: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
    _validate_wes_evidence(row)
    detector = policy["detector"]
    metrics = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {}
    resolved = _int(metrics.get("resolved_pairs"))
    effective = _finite(metrics.get("effective_samples"))
    alpha = _finite(metrics.get("mean_incremental_alpha_percent"))
    better = _finite(metrics.get("wes_better_than_v5_rate"))

    checks = {
        "minimum_resolved_pairs": resolved is not None and resolved >= int(detector["minimum_resolved_pairs"]),
        "minimum_effective_samples": effective is not None and effective >= float(detector["minimum_effective_samples"]),
        "positive_mean_incremental_alpha": alpha is not None and alpha > float(detector["minimum_mean_incremental_alpha_percent_exclusive"]),
        "better_than_v5_majority": better is not None and better > float(detector["minimum_wes_better_than_v5_rate_exclusive"]),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "resolved_pairs": resolved,
        "effective_samples": effective,
        "mean_incremental_alpha_percent": alpha,
        "wes_better_than_v5_rate": better,
    }


def _progress_signals(state_root: Path) -> list[dict[str, Any]]:
    verify_progress_ledger(state_root)
    return elf._read_jsonl(_progress_path(state_root))


def _last_positive_signal(state_root: Path) -> dict[str, Any] | None:
    signals = _progress_signals(state_root)
    positive = [row for row in signals if row.get("conclusion") in POSITIVE_CONCLUSIONS]
    return positive[-1] if positive else None


def _append_progress_signal(
    state_root: Path,
    *,
    evidence: Mapping[str, Any],
    gate: Mapping[str, Any],
    prior: Mapping[str, Any] | None,
    conclusion: str,
    now: str,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    prior_metrics = prior.get("observed_metrics") if isinstance((prior or {}).get("observed_metrics"), Mapping) else {}
    prior_n = _int(prior_metrics.get("resolved_pairs"))
    prior_alpha = _finite(prior_metrics.get("mean_incremental_alpha_percent"))
    prior_better = _finite(prior_metrics.get("wes_better_than_v5_rate"))
    current_n = _int(gate.get("resolved_pairs"))
    current_alpha = _finite(gate.get("mean_incremental_alpha_percent"))
    current_better = _finite(gate.get("wes_better_than_v5_rate"))
    delta = {
        "resolved_pairs": None if prior_n is None or current_n is None else current_n - prior_n,
        "mean_incremental_alpha_percent": None if prior_alpha is None or current_alpha is None else current_alpha - prior_alpha,
        "wes_better_than_v5_rate": None if prior_better is None or current_better is None else current_better - prior_better,
    }
    signal_id = elf._stable_id("wprogress", {
        "evidence_id": evidence.get("evidence_id"),
        "prior_progress_signal_id": (prior or {}).get("progress_signal_id"),
        "conclusion": conclusion,
        "policy_sha256": policy.get("policy_sha256"),
    })
    path = _progress_path(state_root)
    existing = elf._ids(path, "progress_signal_id")
    if signal_id in existing:
        return next(row for row in elf._read_jsonl(path) if row.get("progress_signal_id") == signal_id)
    payload = {
        "progress_signal_id": signal_id,
        "engine_id": ENGINE_ID,
        "detected_at": now,
        "source_evidence_id": evidence.get("evidence_id"),
        "source_observed_at": evidence.get("observed_at"),
        "prior_progress_signal_id": (prior or {}).get("progress_signal_id"),
        "conclusion": conclusion,
        "gate": dict(gate),
        "observed_metrics": {
            "resolved_pairs": current_n,
            "effective_samples": gate.get("effective_samples"),
            "mean_incremental_alpha_percent": current_alpha,
            "wes_better_than_v5_rate": current_better,
        },
        "delta_vs_prior_positive_signal": delta,
        "detector_policy_sha256": policy.get("policy_sha256"),
        "calibration_candidate_authorized": conclusion == "SUSTAINED_POSITIVE_PROGRESS",
        "formal_validation_authority": False,
        "authority": dict(ZERO_AUTHORITY),
    }
    return elf._append_chained(path, schema=PROGRESS_SCHEMA, id_field="progress_signal_id", payload=payload)


def _detector_state_path(state_root: Path) -> Path:
    return _wes_partition(state_root) / DETECTOR_STATE_FILENAME


def _load_detector_state(state_root: Path) -> dict[str, Any] | None:
    path = _detector_state_path(state_root)
    if not path.exists():
        return None
    payload = _read_json(path)
    stored = str(payload.get("state_sha256") or "")
    body = dict(payload)
    body.pop("state_sha256", None)
    if not stored or stored != elf._sha(body):
        raise ValueError("WES progress detector state hash mismatch")
    return payload


def _save_detector_state(state_root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    return _write_hashed_json(_detector_state_path(state_root), payload, hash_field="state_sha256")


def _results_by_experiment(state_root: Path) -> set[str]:
    path = _wes_partition(state_root) / "results.jsonl"
    return {
        str(row.get("experiment_id") or "")
        for row in elf._read_jsonl(path)
        if row.get("experiment_id")
    }


def _active_candidates(state_root: Path) -> list[dict[str, Any]]:
    verify_candidate_ledger(state_root)
    completed = _results_by_experiment(state_root)
    return [
        row for row in elf._read_jsonl(_candidate_path(state_root))
        if str(row.get("experiment_id") or "") not in completed
    ]


def _create_progress_lesson(state_root: Path, signal: Mapping[str, Any], *, now: str) -> dict[str, Any]:
    signal_id = str(signal["progress_signal_id"])
    metrics = signal.get("observed_metrics") if isinstance(signal.get("observed_metrics"), Mapping) else {}
    lesson_id = elf._stable_id("lesson-wes-progress", {
        "progress_signal_id": signal_id,
        "metrics": dict(metrics),
    })
    path = elf._lesson_path(state_root, ENGINE_ID)
    if lesson_id in elf._ids(path, "lesson_id"):
        return next(row for row in elf._read_jsonl(path) if row.get("lesson_id") == lesson_id)
    statement = (
        "WES shows sustained prospective incremental-value progress against the frozen V5 baseline "
        f"at resolved_pairs={metrics.get('resolved_pairs')}, "
        f"mean_incremental_alpha_percent={metrics.get('mean_incremental_alpha_percent')}, "
        f"wes_better_than_v5_rate={metrics.get('wes_better_than_v5_rate')}; "
        "this is a research signal for a WES-local shadow calibration challenger, not production authority."
    )
    payload = {
        "lesson_id": lesson_id,
        "engine_id": ENGINE_ID,
        "derived_at": now[:10],
        "status": "OBSERVED",
        "statement": statement,
        "evidence": [{
            "kind": "wes_progress_signal",
            "source": f"local:{PROGRESS_FILENAME}#{signal_id}",
            "detail": f"conclusion={signal.get('conclusion')}; source_evidence_id={signal.get('source_evidence_id')}",
        }],
        "lineage": {
            "source_progress_signal_id": signal_id,
            "source_evidence_id": signal.get("source_evidence_id"),
        },
        "hypothesis_input_ready": True,
        "production_authority": False,
        "authority": dict(elf.ZERO_AUTHORITY),
        "created_at": now,
    }
    return elf._append_chained(path, schema=elf.LESSON_SCHEMA, id_field="lesson_id", payload=payload)


def _create_calibration_candidate(
    state_root: Path,
    *,
    signal: Mapping[str, Any],
    policy: Mapping[str, Any],
    now: str,
) -> dict[str, Any] | None:
    if _active_candidates(state_root):
        return None

    candidate_policy = policy["candidate"]
    lesson = _create_progress_lesson(state_root, signal, now=now)
    hypothesis = elf.record_local_hypothesis(
        state_root,
        engine_id=ENGINE_ID,
        lesson_ids=[str(lesson["lesson_id"])],
        claim=(
            "A frozen WES shadow challenger calibrated on the allowlisted incremental_decision_value "
            "research objective will preserve positive prospective incremental alpha and beat the frozen V5 "
            "baseline on a majority of new counterfactual pairs."
        ),
        experiment_family="incremental_decision_value_calibration",
        success_criteria=[
            {"metric": "mean_incremental_alpha_percent", "operator": ">", "value": 0.0},
            {"metric": "wes_better_than_v5_rate", "operator": ">", "value": 0.5},
        ],
        falsification_criteria=[
            {"metric": "mean_incremental_alpha_percent", "operator": "<=", "value": 0.0},
            {"metric": "wes_better_than_v5_rate", "operator": "<=", "value": 0.5},
        ],
        created_at=now,
    )

    source_evidence = next(
        row for row in elf._read_jsonl(elf._evidence_path(state_root, ENGINE_ID))
        if row.get("evidence_id") == signal.get("source_evidence_id")
    )
    facts = source_evidence.get("facts") if isinstance(source_evidence.get("facts"), Mapping) else {}
    experiment = elf.record_local_experiment(
        state_root,
        engine_id=ENGINE_ID,
        hypothesis_id=str(hypothesis["hypothesis_id"]),
        contract={
            "sample_unit": "resolved_counterfactual_pairs",
            "fixed_n": int(candidate_policy["validation_target_n"]),
            "prospective_only": True,
            "historical_backfill": False,
            "formal_evaluation_count": 1,
            "calibration_mode": "SHADOW_CHALLENGER_ONLY",
            "calibration_axis": str(candidate_policy["calibration_axis"]),
            "calibration_scope": str(candidate_policy["scope"]),
            "source_progress_signal_id": signal.get("progress_signal_id"),
            "source_evidence_id": signal.get("source_evidence_id"),
            "frozen_baseline_definition": facts.get("baseline_definition"),
            "active_wes_writeback": False,
            "requires_promotion_gate": True,
        },
        created_at=now,
    )
    epoch = elf.commit_local_validation_epoch(
        state_root,
        engine_id=ENGINE_ID,
        experiment_id=str(experiment["experiment_id"]),
        committed_at=now,
    )
    candidate_id = elf._stable_id("wcal", {
        "progress_signal_id": signal.get("progress_signal_id"),
        "hypothesis_id": hypothesis.get("hypothesis_id"),
        "experiment_id": experiment.get("experiment_id"),
        "epoch_id": epoch.get("epoch_id"),
        "policy_sha256": policy.get("policy_sha256"),
    })
    path = _candidate_path(state_root)
    if candidate_id in elf._ids(path, "calibration_candidate_id"):
        return next(row for row in elf._read_jsonl(path) if row.get("calibration_candidate_id") == candidate_id)
    payload = {
        "calibration_candidate_id": candidate_id,
        "engine_id": ENGINE_ID,
        "created_at": now,
        "status": "VALIDATING_SHADOW",
        "mode": "SHADOW_CHALLENGER_ONLY",
        "calibration_axis": candidate_policy["calibration_axis"],
        "calibration_scope": candidate_policy["scope"],
        "source_progress_signal_id": signal.get("progress_signal_id"),
        "source_evidence_id": signal.get("source_evidence_id"),
        "lesson_id": lesson.get("lesson_id"),
        "hypothesis_id": hypothesis.get("hypothesis_id"),
        "experiment_id": experiment.get("experiment_id"),
        "validation_epoch_id": epoch.get("epoch_id"),
        "validation_target_n": int(candidate_policy["validation_target_n"]),
        "validation_evidence_rule": {
            "strict_observed_at_after": epoch["evidence_boundary"]["strict_observed_at_after"],
            "older_evidence_formally_eligible": False,
            "prospective_only": True,
            "historical_backfill": False,
        },
        "active_wes_writeback": False,
        "requires_promotion_gate": True,
        "promotion_gate": candidate_policy.get("promotion_gate"),
        "automatic_promotion": False,
        "authority": dict(ZERO_AUTHORITY),
    }
    return elf._append_chained(
        path,
        schema=CANDIDATE_SCHEMA,
        id_field="calibration_candidate_id",
        payload=payload,
    )


def _classify_progress(
    state_root: Path,
    *,
    evidence: Mapping[str, Any],
    gate: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    if not gate["passed"]:
        return "INSUFFICIENT_PROGRESS", _last_positive_signal(state_root)

    prior = _last_positive_signal(state_root)
    if prior is None:
        return "DESCRIPTIVE_SIGNAL", None

    prior_metrics = prior.get("observed_metrics") if isinstance(prior.get("observed_metrics"), Mapping) else {}
    prior_n = _int(prior_metrics.get("resolved_pairs"))
    current_n = _int(gate.get("resolved_pairs"))
    if prior_n is None or current_n is None or current_n <= prior_n:
        return "POSITIVE_NO_SAMPLE_GROWTH", prior

    prior_alpha = _finite(prior_metrics.get("mean_incremental_alpha_percent"))
    current_alpha = _finite(gate.get("mean_incremental_alpha_percent"))
    prior_better = _finite(prior_metrics.get("wes_better_than_v5_rate"))
    current_better = _finite(gate.get("wes_better_than_v5_rate"))
    improved = (
        prior_alpha is not None and current_alpha is not None and current_alpha > prior_alpha
    ) or (
        prior_better is not None and current_better is not None and current_better > prior_better
    )
    if policy["detector"].get("require_metric_improvement") is True and not improved:
        return "POSITIVE_NO_METRIC_IMPROVEMENT", prior
    return "SUSTAINED_POSITIVE_PROGRESS", prior


def _status(
    state_root: Path,
    *,
    policy: Mapping[str, Any],
    now: str,
    detector_state: Mapping[str, Any],
) -> dict[str, Any]:
    progress = verify_progress_ledger(state_root)
    candidates = verify_candidate_ledger(state_root)
    active = _active_candidates(state_root)
    payload = {
        "schema_version": STATUS_SCHEMA,
        "engine_id": ENGINE_ID,
        "updated_at": now,
        "detector": {
            "activated_at": detector_state.get("activated_at"),
            "processed_evidence_events": detector_state.get("processed_evidence_events"),
            "progress_signal_events": progress["events"],
        },
        "calibration": {
            "candidate_events": candidates["events"],
            "active_candidates": len(active),
            "active_candidate_ids": [row.get("calibration_candidate_id") for row in active],
            "mode": "SHADOW_CHALLENGER_ONLY",
            "active_wes_writeback": False,
            "requires_promotion_gate": True,
        },
        "policy_sha256": policy.get("policy_sha256"),
        "authority": dict(ZERO_AUTHORITY),
    }
    return _write_hashed_json(_wes_partition(state_root) / STATUS_FILENAME, payload, hash_field="status_sha256")


def advance_wes_calibration(
    state_root: Path,
    *,
    policy: Mapping[str, Any],
    now: str,
) -> dict[str, Any]:
    validate_policy(policy)
    partition = _wes_partition(state_root)
    partition.mkdir(parents=True, exist_ok=True)
    evidence_path = elf._evidence_path(state_root, ENGINE_ID)
    evidence_chain = elf._verify_chain(evidence_path, schema=elf.EVIDENCE_SCHEMA, id_field="evidence_id")
    evidence_rows = elf._read_jsonl(evidence_path)
    verify_progress_ledger(state_root)
    verify_candidate_ledger(state_root)

    detector_state = _load_detector_state(state_root)
    if detector_state is None:
        detector_state = _save_detector_state(state_root, {
            "schema_version": "briefrooms-wes-progress-detector-state-v1",
            "engine_id": ENGINE_ID,
            "activated_at": now,
            "activation_evidence_events": evidence_chain["events"],
            "activation_evidence_head_hash": evidence_chain["head_hash"],
            "processed_evidence_events": evidence_chain["events"],
            "last_processed_evidence_id": evidence_rows[-1]["evidence_id"] if evidence_rows else None,
            "policy_sha256": policy.get("policy_sha256"),
            "authority": dict(ZERO_AUTHORITY),
        })
        status = _status(state_root, policy=policy, now=now, detector_state=detector_state)
        return {
            "activated": True,
            "new_evidence_processed": 0,
            "progress_signals_created": 0,
            "calibration_candidates_created": 0,
            "status": status,
        }

    processed = int(detector_state.get("processed_evidence_events") or 0)
    if len(evidence_rows) < processed:
        raise ValueError("WES evidence ledger shrank after detector activation")
    if detector_state.get("policy_sha256") != policy.get("policy_sha256"):
        raise ValueError("WES calibration policy changed for an active detector; explicit migration required")

    signals_created = 0
    candidates_created = 0
    new_rows = evidence_rows[processed:]
    for evidence in new_rows:
        gate = _gate(evidence, policy)
        conclusion, prior = _classify_progress(
            state_root,
            evidence=evidence,
            gate=gate,
            policy=policy,
        )
        signal = _append_progress_signal(
            state_root,
            evidence=evidence,
            gate=gate,
            prior=prior,
            conclusion=conclusion,
            now=now,
            policy=policy,
        )
        signals_created += 1
        if conclusion == "SUSTAINED_POSITIVE_PROGRESS":
            candidate = _create_calibration_candidate(
                state_root,
                signal=signal,
                policy=policy,
                now=now,
            )
            if candidate is not None:
                candidates_created += 1

    detector_state = _save_detector_state(state_root, {
        "schema_version": "briefrooms-wes-progress-detector-state-v1",
        "engine_id": ENGINE_ID,
        "activated_at": detector_state.get("activated_at"),
        "activation_evidence_events": detector_state.get("activation_evidence_events"),
        "activation_evidence_head_hash": detector_state.get("activation_evidence_head_hash"),
        "processed_evidence_events": len(evidence_rows),
        "last_processed_evidence_id": evidence_rows[-1]["evidence_id"] if evidence_rows else None,
        "policy_sha256": policy.get("policy_sha256"),
        "authority": dict(ZERO_AUTHORITY),
    })
    status = _status(state_root, policy=policy, now=now, detector_state=detector_state)
    return {
        "activated": False,
        "new_evidence_processed": len(new_rows),
        "progress_signals_created": signals_created,
        "calibration_candidates_created": candidates_created,
        "active_candidates": status["calibration"]["active_candidates"],
        "status": status,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Advance WES progress detector and calibration challenger pipeline")
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--now")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    policy = load_policy(args.policy)
    if args.verify:
        print(json.dumps({
            "policy": validate_policy(policy),
            "progress": verify_progress_ledger(args.state_root),
            "candidates": verify_candidate_ledger(args.state_root),
        }, ensure_ascii=False, sort_keys=True))
        return 0
    now = elf._iso(args.now) if args.now else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    print(json.dumps(advance_wes_calibration(args.state_root, policy=policy, now=now), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
