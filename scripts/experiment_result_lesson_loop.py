#!/usr/bin/env python3
"""Automatic Experiment -> Evidence -> Result -> Lesson loop.

The loop closes hypothesis research without granting production authority.
It consumes only prospective policy-shadow outcomes after the experiment's
immutable ValidationEpoch boundary. Once the preregistered fixed-N sample is
complete it performs the single formal evaluation, appends immutable Result and
Lesson ledger events, and marks the runtime experiment terminal.

Derived lessons intentionally use the Lesson/Hypothesis Registry lesson shape so
they can become inputs to the next Lesson -> Hypothesis cycle. This module never
writes the committed source registry and never promotes production policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable, Mapping, Sequence

try:
    import autonomous_policy_promotion as app
    import hypothesis_experiment_compiler as hec
    import validation_epoch as ve
except ModuleNotFoundError:  # pragma: no cover
    from scripts import autonomous_policy_promotion as app
    from scripts import hypothesis_experiment_compiler as hec
    from scripts import validation_epoch as ve

RESULT_SCHEMA = "briefrooms-experiment-result-v1"
LESSON_SCHEMA = "briefrooms-derived-research-lesson-v1"
SUMMARY_SCHEMA = "briefrooms-experiment-result-lesson-summary-v1"
RESULTS_FILENAME = "experiment_results.jsonl"
LESSONS_FILENAME = "derived_lessons.jsonl"
SUMMARY_FILENAME = "experiment_result_lesson_summary.json"
TERMINAL_STATUSES = {"SUPPORTED", "REJECTED", "INCONCLUSIVE"}

ZERO_AUTHORITY = {
    "production_policy_writeback": False,
    "production_ranking_writeback": False,
    "production_sizing_writeback": False,
    "trade_execution": False,
    "automatic_promotion": False,
    "source_registry_writeback": False,
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _stable_id(prefix: str, payload: Any) -> str:
    return f"{prefix}-{_sha(payload)[:24]}"


def _parse_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(value: str | datetime) -> str:
    return _parse_time(value).isoformat().replace("+00:00", "Z")


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        row = json.loads(raw)
        if not isinstance(row, dict):
            raise ValueError(f"non-object row {line_no} in {path}")
        rows.append(row)
    return rows


def _append_line(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _verify_chain(path: Path, schema: str) -> dict[str, Any]:
    rows = _read_jsonl(path)
    previous = "GENESIS"
    ids: set[str] = set()
    for index, row in enumerate(rows):
        if row.get("schema_version") != schema:
            raise ValueError(f"ledger schema mismatch at {index}: {path}")
        event_id = str(row.get("result_id") or row.get("lesson_id") or "")
        if not event_id or event_id in ids:
            raise ValueError(f"duplicate/empty ledger id at {index}: {path}")
        ids.add(event_id)
        if row.get("previous_hash") != previous:
            raise ValueError(f"ledger chain break at {index}: {path}")
        body = dict(row)
        stored = str(body.pop("row_sha256", ""))
        if not stored or stored != _sha(body):
            raise ValueError(f"ledger hash mismatch at {index}: {path}")
        previous = stored
    return {"ok": True, "events": len(rows), "head_hash": previous}


def verify_result_ledger(path: Path) -> dict[str, Any]:
    return _verify_chain(path, RESULT_SCHEMA)


def verify_lesson_ledger(path: Path) -> dict[str, Any]:
    return _verify_chain(path, LESSON_SCHEMA)


def _append_chained(path: Path, schema: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    chain = _verify_chain(path, schema)
    row = dict(payload)
    row["schema_version"] = schema
    row["previous_hash"] = chain["head_hash"]
    row["row_sha256"] = _sha(row)
    _append_line(path, row)
    _verify_chain(path, schema)
    return row


def _decision_time(row: Mapping[str, Any]) -> datetime | None:
    value = row.get("decision_at")
    if not value:
        return None
    try:
        return _parse_time(str(value))
    except ValueError:
        return None


def _eligible_marginal_row(row: Mapping[str, Any], experiment: Mapping[str, Any], boundary: datetime) -> bool:
    """Match the real PR35 marginal-shadow contract, not the synthetic hxcand id."""
    candidate = experiment.get("candidate")
    if not isinstance(candidate, Mapping):
        return False
    if str(experiment.get("sample_unit") or "") != "prospective_marginal_shadow_outcomes":
        raise RuntimeError(f"unsupported sample_unit: {experiment.get('sample_unit')}")
    decision_at = _decision_time(row)
    if decision_at is None or decision_at <= boundary:
        return False
    if str(row.get("engine_id") or "") != str(candidate.get("engine_id") or ""):
        return False
    if str(row.get("first_blocking_gate") or "") != str(candidate.get("gate") or ""):
        return False
    if row.get("other_hard_gates_passed") is not True:
        return False
    score = _finite(row.get("candidate_score"))
    from_value = _finite(candidate.get("from_value"))
    to_value = _finite(candidate.get("to_value"))
    source_threshold = _finite(row.get("source_threshold"))
    if score is None or from_value is None or to_value is None:
        return False
    if not to_value < from_value:
        raise RuntimeError("marginal-shadow sampler currently supports threshold reductions only")
    if source_threshold is not None and abs(source_threshold - from_value) > 1e-9:
        return False
    if not (to_value <= score < from_value):
        return False
    return _finite(row.get("return_percent")) is not None


def _summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "n": 0,
            "mean_return_percent": None,
            "positive_rate": None,
            "mean_r": None,
            "cumulative_r": None,
            "max_drawdown_r": None,
            "first_half_mean_return": None,
            "second_half_mean_return": None,
        }
    ordered = sorted(rows, key=lambda row: (str(row.get("decision_at") or ""), str(row.get("shadow_outcome_id") or "")))
    returns = [float(row["return_percent"]) for row in ordered]
    r_values = [float(row["r_multiple"]) for row in ordered if _finite(row.get("r_multiple")) is not None]
    cumulative = peak = max_dd = 0.0
    for value in r_values:
        cumulative += value
        peak = max(peak, cumulative)
        max_dd = min(max_dd, cumulative - peak)
    half = max(1, len(returns) // 2)
    return {
        "n": len(rows),
        "mean_return_percent": round(fmean(returns), 8),
        "positive_rate": round(sum(value > 0 for value in returns) / len(returns), 6),
        "mean_r": None if not r_values else round(fmean(r_values), 8),
        "cumulative_r": None if not r_values else round(sum(r_values), 8),
        "max_drawdown_r": None if not r_values else round(max_dd, 8),
        "first_half_mean_return": round(fmean(returns[:half]), 8),
        "second_half_mean_return": round(fmean(returns[half:]), 8) if returns[half:] else round(fmean(returns[:half]), 8),
    }


def _compare(actual: float, operator: str, expected: float) -> bool:
    return {
        ">": actual > expected,
        ">=": actual >= expected,
        "<": actual < expected,
        "<=": actual <= expected,
        "==": actual == expected,
        "!=": actual != expected,
    }[operator]


def _evaluate_rules(metrics: Mapping[str, Any], rules: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    evaluated: list[dict[str, Any]] = []
    for rule in rules:
        metric = str(rule.get("metric") or "")
        operator = str(rule.get("operator") or "")
        expected = float(rule["value"])
        actual = _finite(metrics.get(metric))
        passed = False if actual is None else _compare(actual, operator, expected)
        evaluated.append({"metric": metric, "operator": operator, "expected": expected, "actual": actual, "passed": passed})
    return evaluated


def _formal_verdict(experiment: Mapping[str, Any], metrics: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    plan = experiment.get("primary_inference_plan")
    if not isinstance(plan, Mapping):
        raise RuntimeError("primary_inference_plan missing")
    success = _evaluate_rules(metrics, [x for x in plan.get("success_criteria") or [] if isinstance(x, Mapping)])
    falsification = _evaluate_rules(metrics, [x for x in plan.get("falsification_criteria") or [] if isinstance(x, Mapping)])
    success_all = bool(success) and all(row["passed"] for row in success)
    falsified = any(row["passed"] for row in falsification)
    if success_all and falsified:
        verdict = "INCONCLUSIVE"
        reason = "criteria_conflict_fail_closed"
    elif success_all:
        verdict = "SUPPORTED"
        reason = "all_preregistered_success_criteria_passed"
    elif falsified:
        verdict = "REJECTED"
        reason = "at_least_one_preregistered_falsification_criterion_passed"
    else:
        verdict = "INCONCLUSIVE"
        reason = "neither_full_success_nor_falsification"
    return verdict, {"reason": reason, "success_criteria": success, "falsification_criteria": falsification}


def _lesson_from_result(result: Mapping[str, Any]) -> dict[str, Any]:
    verdict = str(result["verdict"])
    engine = str(result.get("engine_id") or "")
    candidate = result.get("candidate") if isinstance(result.get("candidate"), Mapping) else {}
    transition = f"{candidate.get('from_value')}→{candidate.get('to_value')}"
    metrics = result.get("metrics") if isinstance(result.get("metrics"), Mapping) else {}
    statement = (
        f"{engine} prospective shadow experiment {transition} finished {verdict} at its preregistered fixed-N boundary; "
        f"mean_return_percent={metrics.get('mean_return_percent')}, positive_rate={metrics.get('positive_rate')}, mean_r={metrics.get('mean_r')}."
    )
    lesson_id = _stable_id("lesson-result", {"result_id": result["result_id"], "statement": statement})
    return {
        "lesson_id": lesson_id,
        "derived_at": str(result["completed_at"])[:10],
        "status": "OBSERVED",
        "statement": statement,
        "production_authority": False,
        "evidence": [
            {
                "kind": "formal_experiment_result",
                "source": f"private:{RESULTS_FILENAME}#{result['result_id']}",
                "detail": f"ValidationEpoch={result['validation_epoch']['epoch_id']}; sample_n={result['sample_n']}; verdict={verdict}; sample_sha256={result['sample_sha256']}",
            }
        ],
        "lineage": {
            "source_result_id": result["result_id"],
            "source_experiment_id": result["experiment_id"],
            "source_hypothesis_id": result.get("hypothesis_id"),
            "source_lesson_ids": list(result.get("source_lesson_ids") or []),
        },
        "verdict": verdict,
        "hypothesis_input_ready": True,
        "authority": dict(ZERO_AUTHORITY),
        "created_at": result["completed_at"],
    }


def _update_runtime_terminal(state_dir: Path, runtime: dict[str, Any], result: Mapping[str, Any]) -> None:
    found = False
    for experiment in runtime.get("experiments", []):
        if not isinstance(experiment, dict) or experiment.get("experiment_id") != result.get("experiment_id"):
            continue
        experiment["status"] = result["verdict"]
        experiment["result_id"] = result["result_id"]
        experiment["completed_at"] = result["completed_at"]
        found = True
        break
    if not found:
        raise RuntimeError("result experiment disappeared from runtime registry")
    runtime["summary"] = {
        "total": len(runtime.get("experiments", [])),
        "compiled": sum(1 for row in runtime.get("experiments", []) if isinstance(row, Mapping) and row.get("status") == "COMPILED"),
        "running_shadow": sum(1 for row in runtime.get("experiments", []) if isinstance(row, Mapping) and row.get("status") == "RUNNING_SHADOW"),
        "supported": sum(1 for row in runtime.get("experiments", []) if isinstance(row, Mapping) and row.get("status") == "SUPPORTED"),
        "rejected": sum(1 for row in runtime.get("experiments", []) if isinstance(row, Mapping) and row.get("status") == "REJECTED"),
        "inconclusive": sum(1 for row in runtime.get("experiments", []) if isinstance(row, Mapping) and row.get("status") == "INCONCLUSIVE"),
    }
    runtime["updated_at"] = result["completed_at"]
    hec._write_registry(state_dir / hec.RUNTIME_FILENAME, runtime)
    hec.validate_compiled_registry(json.loads((state_dir / hec.RUNTIME_FILENAME).read_text(encoding="utf-8")))


def run_loop(state_dir: Path, *, now: str | datetime) -> dict[str, Any]:
    runtime_path = state_dir / hec.RUNTIME_FILENAME
    if not runtime_path.exists():
        raise FileNotFoundError(f"runtime experiment registry missing: {runtime_path}")
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    hec.validate_compiled_registry(runtime)
    shadow_path = state_dir / hec.POLICY_SHADOW_FILENAME
    app.verify_shadow(shadow_path)
    shadow_rows = _read_jsonl(shadow_path)
    result_path = state_dir / RESULTS_FILENAME
    lesson_path = state_dir / LESSONS_FILENAME
    verify_result_ledger(result_path)
    verify_lesson_ledger(lesson_path)
    results = _read_jsonl(result_path)
    lessons = _read_jsonl(lesson_path)
    result_by_experiment = {str(row.get("experiment_id")): row for row in results}
    lesson_result_ids = {
        str((row.get("lineage") or {}).get("source_result_id") or "")
        for row in lessons
        if isinstance(row.get("lineage"), Mapping)
    }
    created_results = 0
    created_lessons = 0
    waiting = 0

    for experiment in list(runtime.get("experiments", [])):
        if not isinstance(experiment, Mapping):
            continue
        experiment_id = str(experiment.get("experiment_id") or "")
        prior = result_by_experiment.get(experiment_id)
        if prior is not None:
            if str(prior.get("result_id") or "") not in lesson_result_ids:
                lesson = _lesson_from_result(prior)
                _append_chained(lesson_path, LESSON_SCHEMA, lesson)
                lesson_result_ids.add(str(prior["result_id"]))
                created_lessons += 1
            if experiment.get("status") == "RUNNING_SHADOW":
                _update_runtime_terminal(state_dir, runtime, prior)
            continue
        if experiment.get("status") in TERMINAL_STATUSES:
            raise RuntimeError(f"terminal experiment has no immutable result: {experiment_id}")
        if experiment.get("status") != "RUNNING_SHADOW":
            continue
        reference = experiment.get("validation_epoch")
        if not isinstance(reference, Mapping):
            raise RuntimeError(f"ValidationEpoch missing: {experiment_id}")
        event = ve.verify_epoch_reference(state_dir, experiment["candidate"], stage=str(experiment["stage"]), reference=reference)
        boundary = ve.eligible_after(event)
        plan = experiment.get("primary_inference_plan")
        if not isinstance(plan, Mapping):
            raise RuntimeError(f"primary inference plan missing: {experiment_id}")
        if plan.get("repeated_looks_allowed") is not False or plan.get("historical_backfill") is not False:
            raise RuntimeError(f"formal inference safety invariant violated: {experiment_id}")
        target_n = int(plan.get("fixed_n") or 0)
        if target_n <= 0:
            raise RuntimeError(f"fixed_n missing: {experiment_id}")
        eligible = [row for row in shadow_rows if _eligible_marginal_row(row, experiment, boundary)]
        eligible.sort(key=lambda row: (str(row.get("decision_at") or ""), str(row.get("shadow_outcome_id") or "")))
        if len(eligible) < target_n:
            waiting += 1
            continue
        sample = eligible[:target_n]
        metrics = _summary(sample)
        if int(metrics["n"]) != target_n:
            raise RuntimeError("formal sample size drift")
        verdict, criteria = _formal_verdict(experiment, metrics)
        sample_ids = [str(row["shadow_outcome_id"]) for row in sample]
        sample_hash = _sha(sample_ids)
        completed_at = _iso(now)
        result = {
            "result_id": _stable_id("eresult", {"experiment_id": experiment_id, "epoch_id": reference["epoch_id"], "sample_sha256": sample_hash}),
            "experiment_id": experiment_id,
            "hypothesis_id": experiment.get("hypothesis_id"),
            "source_lesson_ids": list(experiment.get("lesson_ids") or []),
            "engine_id": experiment.get("candidate", {}).get("engine_id"),
            "stage": experiment.get("stage"),
            "candidate": dict(experiment.get("candidate") or {}),
            "validation_epoch": dict(reference),
            "formal_inference_plan": dict(plan),
            "sample_n": target_n,
            "sample_sha256": sample_hash,
            "sample_outcome_ids": sample_ids,
            "sample_window": {"first_decision_at": sample[0]["decision_at"], "last_decision_at": sample[-1]["decision_at"]},
            "metrics": metrics,
            "criteria_evaluation": criteria,
            "verdict": verdict,
            "completed_at": completed_at,
            "formal_evaluation_number": 1,
            "authority": dict(ZERO_AUTHORITY),
        }
        stored_result = _append_chained(result_path, RESULT_SCHEMA, result)
        result_by_experiment[experiment_id] = stored_result
        created_results += 1
        lesson = _lesson_from_result(stored_result)
        _append_chained(lesson_path, LESSON_SCHEMA, lesson)
        lesson_result_ids.add(str(stored_result["result_id"]))
        created_lessons += 1
        _update_runtime_terminal(state_dir, runtime, stored_result)

    result_chain = verify_result_ledger(result_path)
    lesson_chain = verify_lesson_ledger(lesson_path)
    final_runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    hec.validate_compiled_registry(final_runtime)
    summary = {
        "schema_version": SUMMARY_SCHEMA,
        "updated_at": _iso(now),
        "experiments_total": len(final_runtime.get("experiments", [])),
        "running_shadow": sum(1 for row in final_runtime.get("experiments", []) if isinstance(row, Mapping) and row.get("status") == "RUNNING_SHADOW"),
        "waiting_for_fixed_n": waiting,
        "results_total": result_chain["events"],
        "results_created": created_results,
        "lessons_total": lesson_chain["events"],
        "lessons_created": created_lessons,
        "terminal": {
            status: sum(1 for row in final_runtime.get("experiments", []) if isinstance(row, Mapping) and row.get("status") == status)
            for status in sorted(TERMINAL_STATUSES)
        },
        "authority": dict(ZERO_AUTHORITY),
    }
    body = dict(summary)
    body["summary_sha256"] = _sha(summary)
    (state_dir / SUMMARY_FILENAME).write_text(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return body


def main() -> int:
    parser = argparse.ArgumentParser(description="Run automatic Experiment -> Result -> Lesson loop")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--now")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        print(json.dumps({
            "results": verify_result_ledger(args.state_dir / RESULTS_FILENAME),
            "lessons": verify_lesson_ledger(args.state_dir / LESSONS_FILENAME),
        }, ensure_ascii=False, sort_keys=True))
        return 0
    now = _parse_time(args.now) if args.now else datetime.now(timezone.utc)
    print(json.dumps(run_loop(args.state_dir, now=now), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
