#!/usr/bin/env python3
"""Automatic Experiment -> Result -> Lesson loop.

Consumes prospective hypothesis-shadow runtime state plus policy shadow outcomes,
produces immutable experiment results once the preregistered fixed-N sample is
complete, and appends derived lessons to a private lesson ledger.

Zero authority: this loop never changes production policy, rankings, sizing,
execution, promotion state, or the source hypothesis registry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import hypothesis_experiment_compiler as hec
import validation_epoch as ve

SCHEMA_VERSION = "briefrooms-experiment-result-lesson-loop-v1"
RESULTS_FILENAME = "experiment_results.jsonl"
LESSONS_FILENAME = "derived_lessons.jsonl"
SUMMARY_FILENAME = "experiment_result_lesson_summary.json"

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


def _iso(value: str | datetime) -> str:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        value = json.loads(raw)
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _append_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    materialized = [dict(row) for row in rows]
    if not materialized:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in materialized:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _decision_time(row: Mapping[str, Any]) -> datetime | None:
    for key in ("decision_at", "evaluated_at", "observed_at", "timestamp", "created_at"):
        value = row.get(key)
        if value:
            try:
                return _parse(value)
            except Exception:
                continue
    return None


def _matches_candidate(row: Mapping[str, Any], experiment: Mapping[str, Any]) -> bool:
    candidate = experiment.get("candidate")
    if not isinstance(candidate, Mapping):
        return False
    engine_id = str(candidate.get("engine_id") or "")
    candidate_id = str(candidate.get("candidate_id") or "")
    row_engine = str(row.get("engine_id") or row.get("engine") or "")
    row_candidate = str(row.get("candidate_id") or row.get("policy_candidate_id") or "")
    if row_engine and row_engine != engine_id:
        return False
    if row_candidate and row_candidate != candidate_id:
        return False
    return bool(row_engine or row_candidate)


def _is_eligible(row: Mapping[str, Any], experiment: Mapping[str, Any], boundary: datetime) -> bool:
    ts = _decision_time(row)
    if ts is None or ts <= boundary:
        return False
    if not _matches_candidate(row, experiment):
        return False
    if row.get("formally_eligible") is False:
        return False
    return True


def _extract_metric(row: Mapping[str, Any], names: Sequence[str]) -> float | None:
    for name in names:
        value = row.get(name)
        if isinstance(value, (int, float)):
            return float(value)
    metrics = row.get("metrics")
    if isinstance(metrics, Mapping):
        for name in names:
            value = metrics.get(name)
            if isinstance(value, (int, float)):
                return float(value)
    return None


def _evaluate_sample(experiment: Mapping[str, Any], sample: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    deltas: list[float] = []
    wins = losses = ties = 0
    for row in sample:
        candidate = _extract_metric(row, ("candidate_utility", "candidate_score", "candidate_return"))
        baseline = _extract_metric(row, ("baseline_utility", "baseline_score", "baseline_return"))
        if candidate is None or baseline is None:
            continue
        delta = candidate - baseline
        deltas.append(delta)
        if delta > 0:
            wins += 1
        elif delta < 0:
            losses += 1
        else:
            ties += 1
    if not deltas:
        return {"verdict":"INCONCLUSIVE","reason":"eligible sample lacks paired candidate/baseline utility metrics","paired_n":0,"wins":0,"losses":0,"ties":0,"mean_delta":None}
    mean_delta = sum(deltas) / len(deltas)
    verdict = "SUPPORTED" if wins > losses and mean_delta > 0 else "REJECTED" if losses > wins and mean_delta < 0 else "INCONCLUSIVE"
    return {"verdict":verdict,"reason":"paired prospective fixed-N comparison","paired_n":len(deltas),"wins":wins,"losses":losses,"ties":ties,"mean_delta":mean_delta}


def _result_id(experiment_id: str, epoch_id: str, sample_hash: str) -> str:
    return "eresult-" + _sha({"experiment_id": experiment_id, "epoch_id": epoch_id, "sample_hash": sample_hash})[:24]


def _lesson_from_result(result: Mapping[str, Any]) -> dict[str, Any]:
    result_id = str(result["result_id"])
    verdict = str(result["verdict"])
    evaluation = result.get("evaluation") if isinstance(result.get("evaluation"), Mapping) else {}
    mean_delta = evaluation.get("mean_delta")
    statement = f"Experiment {result['experiment_id']} finished as {verdict} on prospective fixed-N evidence."
    if isinstance(mean_delta, (int, float)):
        statement += f" Mean paired delta={mean_delta:.8g}."
    lesson = {"lesson_id":"lesson-result-"+_sha(result_id)[:20],"source_type":"EXPERIMENT_RESULT","source_result_id":result_id,"source_experiment_id":result["experiment_id"],"source_hypothesis_id":result.get("hypothesis_id"),"status":"OBSERVED","statement":statement,"verdict":verdict,"evidence":{"validation_epoch":result["validation_epoch"],"sample_n":result["sample_n"],"sample_sha256":result["sample_sha256"],"evaluation":result["evaluation"]},"authority":dict(ZERO_AUTHORITY),"created_at":result["completed_at"]}
    lesson["lesson_sha256"] = _sha(lesson)
    return lesson


def run_loop(state_dir: Path, *, now: str | datetime) -> dict[str, Any]:
    runtime_path = state_dir / hec.RUNTIME_FILENAME
    if not runtime_path.exists():
        raise FileNotFoundError(f"runtime experiment registry missing: {runtime_path}")
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    hec.validate_compiled_registry(runtime)
    shadow_rows = _read_jsonl(state_dir / hec.POLICY_SHADOW_FILENAME)
    existing_results = _read_jsonl(state_dir / RESULTS_FILENAME)
    existing_lessons = _read_jsonl(state_dir / LESSONS_FILENAME)
    result_by_experiment = {str(r.get("experiment_id")): r for r in existing_results if r.get("experiment_id")}
    lesson_sources = {str(r.get("source_result_id")) for r in existing_lessons if r.get("source_result_id")}
    new_results: list[dict[str, Any]] = []
    new_lessons: list[dict[str, Any]] = []
    waiting = 0
    for experiment in runtime.get("experiments", []):
        if not isinstance(experiment, Mapping) or experiment.get("status") != "RUNNING_SHADOW":
            continue
        experiment_id = str(experiment.get("experiment_id") or "")
        if not experiment_id:
            continue
        prior_result = result_by_experiment.get(experiment_id)
        if prior_result is not None:
            if str(prior_result.get("result_id")) not in lesson_sources:
                new_lessons.append(_lesson_from_result(prior_result))
            continue
        reference = experiment.get("validation_epoch")
        if not isinstance(reference, Mapping):
            raise RuntimeError(f"experiment has no ValidationEpoch reference: {experiment_id}")
        event = ve.verify_epoch_reference(state_dir, experiment["candidate"], stage=str(experiment["stage"]), reference=reference)
        boundary = ve.eligible_after(event)
        primary = experiment.get("primary_inference_plan")
        primary_n = primary.get("n") if isinstance(primary, Mapping) else None
        target_n = int(experiment.get("validation_target_n") or primary_n or 0)
        if target_n <= 0:
            raise RuntimeError(f"invalid fixed-N target: {experiment_id}")
        eligible = [row for row in shadow_rows if _is_eligible(row, experiment, boundary)]
        eligible.sort(key=lambda row: (_decision_time(row) or datetime.max.replace(tzinfo=timezone.utc), _sha(row)))
        if len(eligible) < target_n:
            waiting += 1
            continue
        sample = eligible[:target_n]
        sample_hash = _sha(sample)
        evaluation = _evaluate_sample(experiment, sample)
        epoch_id = str(reference.get("epoch_id") or reference.get("validation_epoch_id") or "")
        result = {"schema_version":SCHEMA_VERSION,"result_id":_result_id(experiment_id,epoch_id,sample_hash),"experiment_id":experiment_id,"hypothesis_id":experiment.get("hypothesis_id"),"candidate_id":experiment.get("candidate",{}).get("candidate_id"),"stage":experiment.get("stage"),"validation_epoch":dict(reference),"sample_n":target_n,"sample_sha256":sample_hash,"sample_window":{"first_decision_at":_iso(_decision_time(sample[0]) or boundary),"last_decision_at":_iso(_decision_time(sample[-1]) or boundary)},"verdict":evaluation["verdict"],"evaluation":evaluation,"completed_at":_iso(now),"authority":dict(ZERO_AUTHORITY)}
        result["result_sha256"] = _sha(result)
        new_results.append(result)
        new_lessons.append(_lesson_from_result(result))
    _append_jsonl(state_dir / RESULTS_FILENAME, new_results)
    _append_jsonl(state_dir / LESSONS_FILENAME, new_lessons)
    all_results = existing_results + new_results
    all_lessons = existing_lessons + new_lessons
    summary = {"schema_version":SCHEMA_VERSION,"updated_at":_iso(now),"experiments_running":sum(1 for x in runtime.get("experiments",[]) if isinstance(x,Mapping) and x.get("status")=="RUNNING_SHADOW"),"experiments_waiting_for_fixed_n":waiting,"results_total":len(all_results),"results_created":len(new_results),"lessons_total":len(all_lessons),"lessons_created":len(new_lessons),"authority":dict(ZERO_AUTHORITY)}
    summary["summary_sha256"] = _sha(summary)
    (state_dir / SUMMARY_FILENAME).write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run automatic Experiment -> Result -> Lesson loop")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--now", default=datetime.now(timezone.utc).isoformat())
    args = parser.parse_args()
    print(json.dumps(run_loop(args.state_dir, now=args.now), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
