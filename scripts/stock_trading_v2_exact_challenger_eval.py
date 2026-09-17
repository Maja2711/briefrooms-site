#!/usr/bin/env python3
"""Fresh-holdout evaluator for exact executable Stock Trading v2 Challengers."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts import stock_trading_v2_challenger_eval as evaluator
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_exact_replay as exact
    from scripts import stock_trading_v2_policy_learner as learner
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_v2_challenger_eval as evaluator
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_exact_replay as exact
    import stock_trading_v2_policy_learner as learner


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise contracts.ContractError(f"{path} must contain a JSON object")
    return payload


def _iter(root: Path) -> list[dict[str, Any]]:
    return [_read(path) for path in sorted(root.rglob("*.json"))] if root.exists() else []


def _exact_candidate(challenger: Mapping[str, Any]) -> dict[str, Any] | None:
    production = challenger.get("production_candidate")
    replay = challenger.get("exact_replay")
    if not isinstance(production, Mapping) or not isinstance(replay, Mapping):
        return None
    spec = production.get("deployment_spec")
    if not isinstance(spec, Mapping):
        raise contracts.ContractError("exact Challenger deployment_spec missing")
    return {
        "horizon_sessions": challenger["horizon_sessions"],
        "deployment_spec": dict(spec),
        "replay_contract": dict(replay),
    }


def evaluate_one(challenger: Mapping[str, Any], *, events: list[dict[str, Any]], admissions: list[dict[str, Any]], outcomes: list[dict[str, Any]]) -> dict[str, Any] | None:
    learner.validate_challenger(challenger)
    replay_candidate = _exact_candidate(challenger)
    if replay_candidate is None:
        return None
    start = exact.parse_dt(challenger["validation_start_at"])
    samples = exact.collect_entry_threshold_samples(candidate=replay_candidate, events=events, admissions=admissions, outcomes=outcomes, after=start)
    report = evaluator.evaluate_samples(challenger, samples)
    production = dict(challenger["production_candidate"])
    report["evaluated_production_candidate"] = {
        "component": production["component"],
        "deployment_id": production["deployment_id"],
        "base_manifest_revision": production["base_manifest_revision"],
        "base_component_version": production["base_component_version"],
        "deployment_sha256": production["deployment_sha256"],
        "execution_semantics": "exact_shadow_replay",
    }
    report["exact_replay_adapter"] = str((challenger.get("exact_replay") or {}).get("adapter") or "")
    body = dict(report)
    body.pop("evaluation_sha256", None)
    report["evaluation_sha256"] = contracts.payload_sha256(body)
    evaluator.validate_evaluation(report)
    return report


def run(*, challenger_root: Path, experience_root: Path, admission_root: Path, outcome_root: Path, output_root: Path) -> dict[str, Any]:
    events, admissions, outcomes = _iter(experience_root), _iter(admission_root), _iter(outcome_root)
    output_root.mkdir(parents=True, exist_ok=True)
    exact_count = pass_count = 0
    for path in sorted(challenger_root.glob("*.json")) if challenger_root.exists() else []:
        challenger = _read(path)
        report = evaluate_one(challenger, events=events, admissions=admissions, outcomes=outcomes)
        if report is None:
            continue
        exact_count += 1
        if (report.get("metrics") or {}).get("formal_pass") is True:
            pass_count += 1
        (output_root / path.name).write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"schema_version": "stock-trading-v2-exact-eval-run-v1", "exact_challengers": exact_count, "formal_passes": pass_count, "production_decision_influence": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--challengers", type=Path, required=True)
    parser.add_argument("--experience-root", type=Path, required=True)
    parser.add_argument("--admission-root", type=Path, required=True)
    parser.add_argument("--outcome-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = run(challenger_root=args.challengers, experience_root=args.experience_root, admission_root=args.admission_root, outcome_root=args.outcome_root, output_root=args.output_root)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
