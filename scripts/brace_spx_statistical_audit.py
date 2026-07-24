#!/usr/bin/env python3
"""Independent statistical-integrity audit for BRACE-SPX research outputs.

The audit is deliberately fail-closed. It does not promote models and does not
place orders. It checks whether the persisted evidence is sufficient to claim
robust out-of-sample progress and estimates backtest-overfitting risk from the
stored walk-forward folds.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "data" / "research" / "brace_spx_research.json"
DEFAULT_LEDGER = ROOT / "data" / "research" / "brace_spx_experiments.json"
DEFAULT_OUTPUT = ROOT / "data" / "research" / "brace_spx_statistical_audit.json"
DEFAULT_HOLDOUT_REGISTRY = ROOT / "data" / "research" / "brace_spx_holdout_registry.json"


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def estimate_pbo(experiments: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Estimate PBO with combinatorially symmetric cross-validation on folds.

    Each candidate contributes its objective advantage over the strongest
    baseline in every stored chronological fold. For each half-fold split, the
    in-sample winner is selected and its out-of-sample rank is measured. PBO is
    the fraction of splits where that winner lands in the bottom half OOS.
    """
    rows: list[tuple[str, list[float]]] = []
    for item in experiments:
        candidate_id = str(item.get("candidate_id") or "")
        folds = (((item.get("walk_forward") or {}).get("fold_metrics")) or [])
        advantages = [_finite(fold.get("objective_advantage")) for fold in folds if isinstance(fold, Mapping)]
        clean = [value for value in advantages if value is not None]
        if candidate_id and len(clean) >= 4:
            rows.append((candidate_id, clean))

    if len(rows) < 3:
        return {"available": False, "reason": "At least three candidates with four comparable folds are required."}

    fold_count = min(len(values) for _, values in rows)
    if fold_count < 4:
        return {"available": False, "reason": "Insufficient comparable fold count."}
    if fold_count % 2:
        fold_count -= 1
    rows = [(candidate_id, values[-fold_count:]) for candidate_id, values in rows]

    half = fold_count // 2
    splits = list(itertools.combinations(range(fold_count), half))
    bottom_half = 0
    evaluated = 0
    logits: list[float] = []

    for train_idx in splits:
        train = set(train_idx)
        test_idx = [idx for idx in range(fold_count) if idx not in train]
        train_scores = {
            candidate_id: sum(values[idx] for idx in train_idx) / half
            for candidate_id, values in rows
        }
        winner = max(train_scores, key=train_scores.get)
        test_scores = {
            candidate_id: sum(values[idx] for idx in test_idx) / half
            for candidate_id, values in rows
        }
        ordered = sorted(test_scores, key=test_scores.get)
        rank = ordered.index(winner) + 1
        percentile = rank / (len(ordered) + 1.0)
        percentile = min(max(percentile, 1e-6), 1.0 - 1e-6)
        logits.append(math.log(percentile / (1.0 - percentile)))
        bottom_half += int(percentile <= 0.5)
        evaluated += 1

    return {
        "available": True,
        "method": "CSCV on chronological fold objective advantages",
        "candidate_count": len(rows),
        "fold_count": fold_count,
        "split_count": evaluated,
        "probability_of_backtest_overfitting": round(bottom_half / evaluated, 6),
        "median_logit": round(sorted(logits)[len(logits) // 2], 6),
        "passes_conservative_gate": (bottom_half / evaluated) <= 0.25,
    }


def estimate_deflated_sharpe(report: Mapping[str, Any], trial_count: int) -> dict[str, Any]:
    """Conservative DSR approximation under normal-return assumptions.

    A full DSR requires the underlying return series, skewness and kurtosis.
    Until those are persisted, this calculation is explicitly provisional and
    uses the number of tried candidates to raise the expected maximum Sharpe.
    """
    champion = report.get("champion") or {}
    metrics = ((champion.get("walk_forward") or {}).get("metrics")) or {}
    reported = _finite(metrics.get("sharpe_excess"))
    source = "sharpe_excess"
    if reported is None:
        reported = _finite(metrics.get("sharpe_zero_rf"))
        source = "legacy_sharpe_zero_rf"
    observations = int(metrics.get("months") or 0)
    if reported is None or observations < 24 or trial_count < 1:
        return {"available": False, "reason": "Sharpe, observations or trial count unavailable."}

    normal = NormalDist()
    adjusted_trials = max(2, int(trial_count))
    expected_max = normal.inv_cdf(1.0 - 1.0 / adjusted_trials)
    standard_error = math.sqrt(max(1e-12, (1.0 + 0.5 * reported * reported) / max(1, observations - 1)))
    probability = normal.cdf((reported - expected_max) / standard_error)
    return {
        "available": True,
        "provisional": True,
        "source_metric": source,
        "reported_sharpe": round(reported, 6),
        "observations": observations,
        "trial_count": trial_count,
        "expected_max_sharpe_under_multiple_testing": round(expected_max, 6),
        "deflated_sharpe_probability": round(probability, 6),
        "passes_conservative_gate": source == "sharpe_excess" and probability >= 0.95,
        "limitation": "Normal-return approximation; full DSR requires persisted excess-return series, skewness and kurtosis.",
    }


def audit_holdout(report: Mapping[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    generation = str(report.get("model_version") or "unknown")
    champion = report.get("champion") or {}
    candidate_id = str(champion.get("candidate_id") or "none")
    holdout_result = champion.get("holdout")
    holdout_baselines_exposed = bool(report.get("holdout_baselines"))
    accessed = isinstance(holdout_result, Mapping)
    fingerprint = f"{generation}:{candidate_id}"
    prior = registry.get(generation)

    repeated = bool(accessed and prior and prior.get("fingerprint") != fingerprint)
    if accessed and not prior:
        registry[generation] = {
            "fingerprint": fingerprint,
            "first_accessed_at": report.get("generated_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "candidate_id": candidate_id,
        }

    return {
        "generation": generation,
        "candidate_id": candidate_id,
        "champion_holdout_accessed": accessed,
        "holdout_baselines_exposed_during_search": holdout_baselines_exposed,
        "repeated_generation_access": repeated,
        "passes_single_use_gate": not repeated and not holdout_baselines_exposed,
        "required_change": (
            "Do not calculate or serialize holdout baselines during candidate search; open the holdout once for a predeclared generation."
            if holdout_baselines_exposed else None
        ),
    }


def run_audit(report_path: Path, ledger_path: Path, output_path: Path, registry_path: Path) -> dict[str, Any]:
    report = load_json(report_path)
    ledger = load_json(ledger_path)
    experiments = ledger.get("experiments") or []
    if not isinstance(experiments, list):
        raise ValueError("Experiment ledger must contain a list named 'experiments'.")
    registry = load_json(registry_path) if registry_path.exists() else {}

    holdout = audit_holdout(report, registry)
    pbo = estimate_pbo(item for item in experiments if isinstance(item, Mapping))
    dsr = estimate_deflated_sharpe(report, len(experiments))
    excess_sharpe_available = dsr.get("source_metric") == "sharpe_excess"

    blockers: list[str] = []
    if not holdout["passes_single_use_gate"]:
        blockers.append("sealed_holdout_integrity")
    if not excess_sharpe_available:
        blockers.append("conventional_excess_return_sharpe_missing")
    if not pbo.get("available") or not pbo.get("passes_conservative_gate"):
        blockers.append("pbo_not_passed")
    if not dsr.get("available") or not dsr.get("passes_conservative_gate"):
        blockers.append("deflated_sharpe_not_passed")

    audit = {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "research_only": True,
        "live_activation": False,
        "source_report_generated_at": report.get("generated_at"),
        "model_version": report.get("model_version"),
        "experiments_audited": len(experiments),
        "holdout_integrity": holdout,
        "probability_of_backtest_overfitting": pbo,
        "deflated_sharpe": dsr,
        "promotion_allowed": not blockers,
        "promotion_blockers": blockers,
    }
    write_json(output_path, audit)
    write_json(registry_path, registry)
    return audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--registry", type=Path, default=DEFAULT_HOLDOUT_REGISTRY)
    args = parser.parse_args()
    audit = run_audit(args.report, args.ledger, args.output, args.registry)
    print(json.dumps({"promotion_allowed": audit["promotion_allowed"], "blockers": audit["promotion_blockers"]}))


if __name__ == "__main__":
    main()
