#!/usr/bin/env python3
"""Evaluate the predeclared BRACE-SPX Generation 5 development gate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

import brace_spx_generation_research as engine


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def selected_dsr(
    selected_id: str,
    ledger: Mapping[str, Any],
    report: Mapping[str, Any],
) -> dict[str, Any]:
    experiments = [row for row in ledger.get("experiments", []) if isinstance(row, Mapping)]
    selected = next((row for row in experiments if str(row.get("candidate_id")) == selected_id), None)
    if selected is None:
        return {"probability": 0.0, "reason": "selected_candidate_missing"}
    metrics = _mapping(selected.get("metrics"))
    returns = pd.Series(
        {
            pd.Timestamp(item["date"]): float(item["return"])
            for item in selected.get("monthly_returns", [])
            if isinstance(item, Mapping) and "date" in item and "return" in item
        },
        dtype=float,
    )
    sharpe_std = _float(_mapping(report.get("multiple_testing")).get("sharpe_cross_section_std"))
    return engine.deflated_sharpe_ratio(
        _float(metrics.get("sharpe_excess")),
        returns,
        max(2, len(experiments)),
        sharpe_std,
    )


def evaluate(
    report: Mapping[str, Any],
    audit: Mapping[str, Any],
    manifest: Mapping[str, Any],
    ledger: Mapping[str, Any],
) -> dict[str, Any]:
    selected = _mapping(_mapping(audit.get("selection")).get("selected"))
    selected_id = str(selected.get("candidate_id", ""))
    holdout = _mapping(report.get("holdout"))
    baselines = _mapping(report.get("development_baselines"))
    best_baseline = max(
        _float(_mapping(baselines.get("buy_and_hold")).get("sharpe_excess"), -999.0),
        _float(_mapping(baselines.get("trend_200d")).get("sharpe_excess"), -999.0),
    )
    selected_sharpe = _float(selected.get("sharpe_excess"))
    pbo = _mapping(_mapping(report.get("multiple_testing")).get("pbo"))
    return_diversity = _mapping(report.get("return_diversity"))
    exposure_diversity = _mapping(_mapping(report.get("geometry")).get("exposure_diversity"))
    dsr = selected_dsr(selected_id, ledger, report)

    checks = {
        "candidate_space_exhausted": _int(report.get("experiments_remaining"), 1) == 0,
        "holdout_sealed": (
            str(holdout.get("status", "")).lower() == "sealed"
            and not bool(holdout.get("accessed", False))
            and _int(holdout.get("access_count"), 0) == 0
        ),
        "five_of_six_folds_positive": _int(selected.get("positive_folds")) >= 5 and _int(selected.get("folds")) >= 6,
        "excess_sharpe_at_least_1_00": selected_sharpe >= 1.00,
        "baseline_advantage_at_least_0_05": selected_sharpe - best_baseline >= 0.05,
        "max_drawdown_not_worse_than_16pct": _float(selected.get("max_drawdown"), -1.0) >= -0.16,
        "fold_sharpe_std_at_most_0_85": _float(selected.get("fold_sharpe_std"), 99.0) <= 0.85,
        "turnover_at_most_3_50": _float(selected.get("annualized_turnover"), 99.0) <= 3.50,
        "selected_dsr_probability_at_least_0_95": _float(dsr.get("probability")) >= 0.95,
        "pbo_at_most_0_20": bool(pbo.get("available")) and _float(pbo.get("probability"), 1.0) <= 0.20,
        "median_return_correlation_at_most_0_78": (
            bool(return_diversity.get("available"))
            and _float(return_diversity.get("median_absolute_pairwise_correlation"), 1.0) <= 0.78
        ),
        "median_exposure_correlation_at_most_0_75": (
            bool(exposure_diversity.get("available"))
            and _float(exposure_diversity.get("median_absolute_pairwise_correlation"), 1.0) <= 0.75
        ),
        "effective_return_candidates_at_least_3_00": _float(return_diversity.get("effective_independent_candidates")) >= 3.0,
        "effective_exposure_candidates_at_least_3_00": _float(exposure_diversity.get("effective_independent_candidates")) >= 3.0,
        "largest_return_cluster_share_at_most_0_67": _float(return_diversity.get("largest_cluster_share"), 1.0) <= 0.67,
        "largest_exposure_cluster_share_at_most_0_67": _float(exposure_diversity.get("largest_cluster_share"), 1.0) <= 0.67,
        "selected_uses_at_least_three_exposure_buckets": _int(selected.get("active_exposure_buckets")) >= 3,
        "selected_mean_return_correlation_at_most_0_75": _float(selected.get("mean_absolute_return_correlation_to_pool"), 1.0) <= 0.75,
        "selected_mean_exposure_correlation_at_most_0_75": _float(selected.get("mean_absolute_exposure_correlation_to_pool"), 1.0) <= 0.75,
        "manifest_matches_generation": str(manifest.get("generation_id")) == "spx-state-geometry-v5",
        "generation4_not_reopened": not bool(_mapping(report.get("design")).get("generation4_reopened", True)),
    }
    exhausted = checks["candidate_space_exhausted"]
    passed = exhausted and all(checks.values())
    status = "passed_development_gate_holdout_still_sealed" if passed else (
        "failed_development_gate_holdout_still_sealed" if exhausted else "research_in_progress"
    )
    return {
        "schema_version": "1.0.0",
        "generation_id": "spx-state-geometry-v5",
        "status": status,
        "strict_gate_passed": passed,
        "holdout_opened": False,
        "selected_candidate_id": selected_id or None,
        "candidate_signature": manifest.get("candidate_signature"),
        "experiments_total": _int(report.get("experiments_total")),
        "candidate_space_size": _int(report.get("candidate_space_size")),
        "selected_development_metrics": dict(selected),
        "best_baseline_sharpe_excess": best_baseline,
        "sharpe_advantage_over_best_baseline": selected_sharpe - best_baseline,
        "selected_deflated_sharpe_ratio": dsr,
        "pbo_probability": _float(pbo.get("probability"), 1.0),
        "return_diversity": dict(return_diversity),
        "exposure_diversity": dict(exposure_diversity),
        "checks": checks,
        "generation4_reopened": False,
        "policy": {
            "workflow_can_open_holdout": False,
            "human_approval_required_before_holdout": True,
            "live_activation": False,
            "leverage": False,
            "short_spy": False
        }
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verdict = evaluate(
        json.loads(args.report.read_text(encoding="utf-8")),
        json.loads(args.audit.read_text(encoding="utf-8")),
        json.loads(args.manifest.read_text(encoding="utf-8")),
        json.loads(args.ledger.read_text(encoding="utf-8")),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(verdict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"BRACE-SPX v5 verdict: {verdict['status']}")


if __name__ == "__main__":
    main()
