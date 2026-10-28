#!/usr/bin/env python3
"""Read-only learning progress monitor; only its own health report is writable.

Artifact generation times are not evidence of learning. Stage watermarks use
source identities, compared groups and evaluated samples, independently.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_experience_store as experience
    from scripts import stock_trading_v2_outcome_replay as outcomes
    from scripts import stock_trading_v2_regret_engine as regret
    from scripts import stock_trading_v2_challenger_eval as evaluation
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_experience_store as experience
    import stock_trading_v2_outcome_replay as outcomes
    import stock_trading_v2_regret_engine as regret
    import stock_trading_v2_challenger_eval as evaluation

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "data/investments/stock_trading_v2_learning/learning_health.json"
SCHEMA_VERSION = "stock-trading-v2-learning-health-v1"


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise contracts.ContractError(f"{path} must contain an object")
    return payload


def _rows(root: Path) -> list[dict[str, Any]]:
    return [_read(path) for path in sorted(root.rglob("*.json"))] if root.exists() else []


def _evaluation_rows(root: Path) -> list[dict[str, Any]]:
    # Evaluators write reports at the root. The reserved .cohorts subtree is an
    # enrollment ledger, not an evaluation; its presence must not break health.
    return [_read(path) for path in sorted(root.glob("*.json"))] if root.exists() else []


def _utc(raw: str) -> datetime:
    value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise contracts.ContractError("health timestamp requires timezone")
    return value.astimezone(timezone.utc)


def _stage(name: str, identities: Iterable[Any], *, source_time: str | None, previous: Mapping[str, Any], now: str, stale_after_hours: float, details: Mapping[str, Any]) -> dict[str, Any]:
    keys = sorted({contracts.canonical_json(value) for value in identities})
    digest = contracts.payload_sha256(keys)
    old = (previous.get("stages") or {}).get(name) or {}
    if not old.get("has_valid_baseline", True):
        old = {}
    old_keys = set(old.get("seen_progress_keys") or old.get("progress_keys") or [])
    added = len(set(keys) - old_keys)
    # A rewrite, a changed PASS flag or removal of old evidence is not progress.
    progressed = bool(old) and added > 0
    last_progress = now if progressed else old.get("last_progress_observed_at")
    baseline = old.get("baseline_observed_at") or now
    age = max(0.0, (_utc(now) - _utc(last_progress or baseline)).total_seconds() / 3600)
    return {
        "progress_count": len(keys), "new_progress_items": added if old else 0,
        "has_valid_baseline": True,
        "progress_keys": keys, "seen_progress_keys": sorted(old_keys | set(keys)), "progress_sha256": digest,
        "latest_source_decision_at": source_time,
        "last_progress_observed_at": last_progress,
        "baseline_observed_at": baseline,
        "hours_since_progress": round(age, 3) if last_progress else None,
        "hours_without_new_evidence": round(age, 3),
        "state": ("EMPTY_STALLED" if details.get("expected_work") and age >= stale_after_hours else "EMPTY") if not keys else "BASELINE_OBSERVED" if not old else "STALLED" if age >= stale_after_hours else "ADVANCING" if progressed else "UNCHANGED",
        **dict(details),
    }


def build_report(
    events: Iterable[Mapping[str, Any]], outcome_rows: Iterable[Mapping[str, Any]],
    regret_report: Mapping[str, Any] | None, evaluations: Iterable[Mapping[str, Any]],
    *, horizons: Iterable[int], previous: Mapping[str, Any] | None = None,
    now: str | None = None, sessions_by_market: Mapping[str, list[str]] | None = None,
    stale_after_hours: float = 72,
    workflow_status: str = "unknown",
) -> dict[str, Any]:
    stamp = now or contracts.iso_utc()
    if workflow_status not in {"success", "failure", "cancelled", "unknown"}:
        raise contracts.ContractError("unsupported workflow status")
    _utc(stamp)
    previous = previous or {}
    if previous:
        validate_report(previous)
    by_id: dict[str, Mapping[str, Any]] = {}
    for event in events:
        contracts.validate_experience_event(event)
        if str(event["event_id"]) in by_id:
            raise contracts.ContractError("duplicate learning health source event")
        by_id[str(event["event_id"])] = event
    settled: dict[tuple[str, int], Mapping[str, Any]] = {}
    observed_sessions: dict[str, set[str]] = {"GPW": set(), "US": set()}
    for row in outcome_rows:
        outcomes.validate_outcome(row)
        source_id = str(row["source_event_id"])
        event = by_id.get(source_id)
        if event is None:
            raise contracts.ContractError("learning health outcome missing source event")
        outcomes.validate_source_binding(row, event)
        key = (source_id, int(row["horizon_sessions"]))
        if key in settled:
            raise contracts.ContractError("duplicate learning health outcome source/horizon")
        settled[key] = row
        replay = row.get("replay") or {}
        for field in ("entry_session", "exit_session"):
            day = str(replay.get(field) or "")[:10]
            if day and day < stamp[:10]:
                observed_sessions[str(event["market"])].add(day)
    calendar = {market: sorted(set(days)) for market, days in (sessions_by_market or observed_sessions).items()}
    pending = mature = 0
    pending_by_horizon: dict[str, Any] = {}
    for horizon in sorted({int(value) for value in horizons}):
        counts = Counter()
        for event_id, event in by_id.items():
            if (event_id, horizon) in settled:
                counts["resolved"] += 1
                continue
            available = sum(str(event["session_date"]) < day < stamp[:10] for day in calendar.get(str(event["market"]), []))
            if available >= horizon:
                counts["mature_unresolved"] += 1
                mature += 1
            else:
                counts["pending_or_maturity_unknown"] += 1
                pending += 1
        pending_by_horizon[str(horizon)] = {key: counts[key] for key in ("resolved", "mature_unresolved", "pending_or_maturity_unknown")}
    outcome_times = [str(by_id[key[0]]["decision_at"]) for key in settled if key[0] in by_id]
    outcome_stage = _stage("outcomes", settled.keys(), source_time=max(outcome_times, key=_utc) if outcome_times else None,
        previous=previous, now=stamp, stale_after_hours=stale_after_hours,
        details={"records": len(settled), "expected_work": bool(by_id), "mature_unresolved": mature, "pending_or_maturity_unknown": pending,
            "horizons": pending_by_horizon, "maturity_basis": "provided_completed_sessions" if sessions_by_market is not None else "observed_completed_sessions_lower_bound"})

    regret_keys: list[Any] = []
    regret_sources: set[str] = set()
    if regret_report:
        regret.validate_report(regret_report)
        for horizon, report in (regret_report.get("horizons") or {}).items():
            for row in report.get("comparisons") or []:
                source = str(row.get("source_group") or "")
                if source:
                    regret_sources.add(source)
                    regret_keys.append((str(horizon), source))
    group_times = [str(event["decision_at"]) for event in by_id.values() if str((event.get("source") or {}).get("payload_sha256")) in regret_sources]
    regret_stage = _stage("regret", regret_keys, source_time=max(group_times, key=_utc) if group_times else None,
        previous=previous, now=stamp, stale_after_hours=stale_after_hours,
        details={"compared_groups": len(regret_sources), "expected_work": bool(settled), "generated_at_is_progress": False})

    evaluation_keys: list[Any] = []
    evaluation_times: list[str] = []
    states: Counter[str] = Counter()
    evaluation_count = 0
    prospective_collection: list[dict[str, Any]] = []
    waiting_continuous = active_other = 0
    for row in evaluations:
        evaluation.validate_evaluation(row)
        evaluation_count += 1
        states[str(row["state"])] += 1
        protocol = (row.get("formal_proof") or {}).get("protocol") or {}
        continuous = protocol.get("schema_version") == "stock-trading-cash-edge-protocol-v2"
        if continuous:
            metrics = row.get("metrics") or {}
            coverage = metrics.get("coverage") or {}
            cohort = (row.get("formal_proof") or {}).get("cohort") or {}
            prospective_collection.append({
                "challenger_id": row["challenger_id"], "evaluation_state": row["state"],
                "awaiting_production_trial_receipt": bool(metrics.get("awaiting_production_trial_receipt")),
                "frozen_anchors": len(cohort.get("members") or []),
                "required_regular_sessions": protocol.get("fixed_regular_sessions"),
                "coverage": {key: coverage[key] for key in (
                    "status", "reason", "expected_sessions", "observed_closes", "regular_end", "calendar_sha256"
                ) if key in coverage},
                "formal_evaluation_progress_credited": False,
            })
            if row["state"] == "COLLECTING_FRESH_HOLDOUT":
                expected = coverage.get("expected_sessions")
                observed = coverage.get("observed_closes")
                waiting_for_future = (coverage.get("status") == "PENDING"
                    and coverage.get("reason") == "waiting_for_all_predeclared_session_closes"
                    and expected == protocol.get("fixed_regular_sessions")
                    and isinstance(expected, int) and not isinstance(expected, bool) and expected > 0
                    and isinstance(observed, int) and not isinstance(observed, bool) and 0 <= observed < expected
                    and bool(coverage.get("calendar_sha256")) and bool(cohort.get("members")))
                waiting_continuous += int(waiting_for_future)
                active_other += int(not waiting_for_future)
        elif row["state"] == "COLLECTING_FRESH_HOLDOUT":
            active_other += 1
        for sample in row.get("formal_samples") or []:
            source = sample.get("source_event_id") or sample.get("event_id") or sample.get("source_group")
            if sample.get("execution_semantics") == "continuous_production_lifecycle_v2":
                if not source or not sample.get("block_id"):
                    raise contracts.ContractError("continuous evaluation sample requires source and stable block_id")
                # Every fixed window shares one source anchor. Its immutable
                # block identifies the distinct actual evaluation observation.
                source = (source, sample["block_id"])
            if not source:
                # Full sample content, excluding generated timestamps, remains a
                # deterministic fallback for preserved legacy evaluations.
                source = contracts.payload_sha256({key: value for key, value in sample.items() if key not in {"generated_at", "settled_at", "recorded_at"}})
            evaluation_keys.append((row["challenger_id"], source))
            if sample.get("decision_at"):
                evaluation_times.append(str(sample["decision_at"]))
    evaluation_stage = _stage("evaluations", evaluation_keys,
        source_time=max(evaluation_times, key=_utc) if evaluation_times else None,
        previous=previous, now=stamp, stale_after_hours=stale_after_hours,
        details={"challengers": evaluation_count, "expected_work": evaluation_count > 0, "states": dict(states),
            "generated_at_is_progress": False, "prospective_collection": prospective_collection})
    if waiting_continuous and not active_other and not evaluation_stage["new_progress_items"]:
        # A fixed 60-session tape cannot produce formal samples on each daily
        # run. Preserve the true watermark and expose collection separately.
        evaluation_stage["state"] = "COLLECTING_PROSPECTIVE_TAPE"
    report = {
        "schema_version": SCHEMA_VERSION, "generated_at": stamp,
        "events": len(by_id), "stages": {"outcomes": outcome_stage, "regret": regret_stage, "evaluations": evaluation_stage},
        "workflow_status": workflow_status,
        "source_integrity": {"status": "VALID"},
        "attention_required": workflow_status in {"failure", "cancelled"} or mature > 0 or any(stage["state"] in {"STALLED", "EMPTY_STALLED"} for stage in (outcome_stage, regret_stage, evaluation_stage)),
        "governance": {"production_decision_influence": False, "source_artifacts_read_only": True, "automatic_policy_writeback": False},
    }
    report["health_sha256"] = contracts.payload_sha256(report)
    validate_report(report)
    return report


def build_failure_report(error: Exception, *, previous: Mapping[str, Any] | None = None,
                         now: str | None = None, workflow_status: str = "failure") -> dict[str, Any]:
    """Persist diagnostics without crediting unreadable evidence as progress."""
    stamp = now or contracts.iso_utc()
    _utc(stamp)
    previous = previous or {}
    if previous:
        validate_report(previous)
    stages = {}
    for name in ("outcomes", "regret", "evaluations"):
        old = (previous.get("stages") or {}).get(name)
        stage = dict(old) if old else _stage(name, [], source_time=None, previous={}, now=stamp,
            stale_after_hours=72, details={"expected_work": False})
        stage.update(state="UNAVAILABLE", new_progress_items=0,
                     has_valid_baseline=bool(old and old.get("has_valid_baseline", True)))
        stages[name] = stage
    report = {
        "schema_version": SCHEMA_VERSION, "generated_at": stamp,
        "events": previous.get("events", 0), "stages": stages,
        "workflow_status": "cancelled" if workflow_status == "cancelled" else "failure",
        "source_integrity": {"status": "FAILED", "error_type": type(error).__name__, "message": str(error)[:2000]},
        "attention_required": True,
        "governance": {"production_decision_influence": False, "source_artifacts_read_only": True, "automatic_policy_writeback": False},
    }
    report["health_sha256"] = contracts.payload_sha256(report)
    validate_report(report)
    return report


def validate_report(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION or set(payload.get("stages") or {}) != {"outcomes", "regret", "evaluations"}:
        raise contracts.ContractError("learning health schema/stages mismatch")
    if payload.get("governance") != {"production_decision_influence": False, "source_artifacts_read_only": True, "automatic_policy_writeback": False}:
        raise contracts.ContractError("learning health governance mismatch")
    body = dict(payload)
    digest = body.pop("health_sha256", None)
    if digest != contracts.payload_sha256(body):
        raise contracts.ContractError("learning health hash mismatch")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experience-root", type=Path, default=experience.DEFAULT_STORE_ROOT)
    parser.add_argument("--outcome-root", type=Path, default=outcomes.DEFAULT_ROOT)
    parser.add_argument("--regret", type=Path, default=regret.REPORT_PATH)
    parser.add_argument("--evaluations", type=Path, default=evaluation.DEFAULT_ROOT)
    parser.add_argument("--config", type=Path, default=outcomes.CONFIG_PATH)
    parser.add_argument("--session-calendar", type=Path, help="JSON market -> completed session dates; never fetches data")
    parser.add_argument("--output", "--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--stale-after-hours", type=float, default=72)
    parser.add_argument("--workflow-status", choices=("success", "failure", "cancelled", "unknown"), default="unknown")
    args = parser.parse_args()
    # Prevent an accidentally chosen output from replacing a source artifact.
    output = args.output.resolve()
    source_files = {args.regret.resolve(), args.config.resolve()}
    if args.session_calendar:
        source_files.add(args.session_calendar.resolve())
    if output in source_files or any(output.is_relative_to(path.resolve()) for path in (args.experience_root, args.outcome_root, args.evaluations)):
        raise contracts.ContractError("health output overlaps read-only source artifacts")
    previous = None
    exit_code = 0
    try:
        if args.output.exists():
            candidate = _read(args.output)
            validate_report(candidate)
            previous = candidate
        report = build_report(_rows(args.experience_root), _rows(args.outcome_root), _read(args.regret) if args.regret.exists() else None,
            _evaluation_rows(args.evaluations), horizons=outcomes.load_config(args.config)["horizons_sessions"], previous=previous,
            sessions_by_market=_read(args.session_calendar) if args.session_calendar else None, stale_after_hours=args.stale_after_hours, workflow_status=args.workflow_status)
    except (contracts.ContractError, ValueError, KeyError, TypeError, OSError) as error:
        report = build_failure_report(error, previous=previous, workflow_status=args.workflow_status)
        exit_code = 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"events": report["events"], "attention_required": report["attention_required"], "stages": {key: {field: value[field] for field in ("state", "progress_count", "latest_source_decision_at", "last_progress_observed_at")} for key, value in report["stages"].items()}}, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
