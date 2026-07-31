#!/usr/bin/env python3
"""Evaluate BRACE-SPX generation 4 without opening any holdout."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


def m(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def i(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def evaluate(report: Mapping[str, Any], audit: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, Any]:
    selected = m(m(audit.get("selection")).get("selected"))
    pbo = m(m(report.get("multiple_testing")).get("pbo"))
    diversity = m(report.get("diversity"))
    baselines = m(report.get("development_baselines"))
    best_baseline = max(
        f(m(baselines.get("buy_and_hold")).get("sharpe_excess"), -999.0),
        f(m(baselines.get("trend_200d")).get("sharpe_excess"), -999.0),
    )
    sharpe = f(selected.get("sharpe_excess"))
    holdout = m(report.get("holdout"))
    checks = {
        "candidate_space_exhausted": i(report.get("experiments_remaining"), 1) == 0,
        "holdout_sealed": str(holdout.get("status", "")).lower() == "sealed" and not bool(holdout.get("accessed", False)),
        "five_of_six_folds_positive": i(selected.get("positive_folds")) >= 5 and i(selected.get("folds")) >= 6,
        "excess_sharpe_at_least_1_05": sharpe >= 1.05,
        "baseline_advantage_at_least_0_10": sharpe - best_baseline >= 0.10,
        "max_drawdown_not_worse_than_18pct": f(selected.get("max_drawdown"), -1.0) >= -0.18,
        "fold_sharpe_std_at_most_0_90": f(selected.get("fold_sharpe_std"), 99.0) <= 0.90,
        "turnover_at_most_3_50": f(selected.get("annualized_turnover"), 99.0) <= 3.50,
        "pbo_at_most_0_20": bool(pbo.get("available")) and f(pbo.get("probability"), 1.0) <= 0.20,
        "median_correlation_at_most_0_72": f(diversity.get("median_absolute_pairwise_correlation"), 1.0) <= 0.72,
        "effective_candidates_at_least_3_50": f(diversity.get("effective_independent_candidates")) >= 3.50,
        "largest_cluster_share_at_most_0_50": f(diversity.get("largest_cluster_share"), 1.0) <= 0.50,
        "selected_mean_correlation_at_most_0_75": f(selected.get("mean_absolute_correlation_to_pool"), 1.0) <= 0.75,
        "manifest_matches_generation": str(manifest.get("generation_id")) == "spx-diversified-v4",
    }
    exhausted = checks["candidate_space_exhausted"]
    passed = exhausted and all(checks.values())
    status = "passed_development_gate_holdout_still_sealed" if passed else ("failed_development_gate_holdout_still_sealed" if exhausted else "research_in_progress")
    return {
        "schema_version": "1.0.0",
        "generation_id": "spx-diversified-v4",
        "status": status,
        "strict_gate_passed": passed,
        "holdout_opened": False,
        "selected_candidate_id": selected.get("candidate_id"),
        "selected_development_metrics": dict(selected),
        "best_baseline_sharpe_excess": best_baseline,
        "sharpe_advantage_over_best_baseline": sharpe - best_baseline,
        "pbo_probability": f(pbo.get("probability"), 1.0),
        "diversity": dict(diversity),
        "checks": checks,
        "generation3_reopened": False,
        "live_activation": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verdict = evaluate(
        json.loads(args.report.read_text(encoding="utf-8")),
        json.loads(args.audit.read_text(encoding="utf-8")),
        json.loads(args.manifest.read_text(encoding="utf-8")),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(verdict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"BRACE-SPX v4 verdict: {verdict['status']}")


if __name__ == "__main__":
    main()
