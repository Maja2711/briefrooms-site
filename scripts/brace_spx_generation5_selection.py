#!/usr/bin/env python3
"""Hypothesis-agnostic, geometry-aware selection for BRACE-SPX Generation 5."""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _return_matrix(experiments: list[Mapping[str, Any]]) -> pd.DataFrame:
    columns: dict[str, pd.Series] = {}
    for row in experiments:
        values = {
            pd.Timestamp(item["date"]): float(item["return"])
            for item in row.get("monthly_returns", [])
            if isinstance(item, Mapping) and "date" in item and "return" in item
        }
        if values:
            columns[str(row.get("candidate_id"))] = pd.Series(values, dtype=float)
    return pd.DataFrame(columns).sort_index()


def _standard_error(sharpe: float, months: int) -> float:
    if months < 3:
        return float("inf")
    return math.sqrt(max(0.0, 1.0 + 0.5 * sharpe * sharpe) / (months - 1.0))


def evidence(
    row: Mapping[str, Any],
    mean_return_correlation: float,
    geometry: Mapping[str, Any],
) -> dict[str, Any]:
    metrics = _mapping(row.get("metrics"))
    folds = [item for item in row.get("fold_metrics", []) if isinstance(item, Mapping)]
    fold_sharpes = [float(item.get("sharpe_excess", 0.0)) for item in folds]
    positive_folds = sum(value > 0.0 for value in fold_sharpes)
    fold_std = float(np.std(fold_sharpes, ddof=1)) if len(fold_sharpes) > 1 else 99.0
    candidate = _mapping(row.get("candidate"))
    params = _mapping(candidate.get("params"))
    sharpe = float(metrics.get("sharpe_excess", -999.0))
    months = int(metrics.get("months", row.get("months", 0)))
    turnover = float(metrics.get("annualized_turnover", 999.0))
    max_drawdown = float(metrics.get("max_drawdown", -1.0))
    active_buckets = int(geometry.get("active_exposure_buckets", 0))
    transition_rate = float(geometry.get("annualized_transition_rate", 999.0))
    stable = (
        len(folds) >= 6
        and positive_folds >= 5
        and fold_std <= 0.95
        and max_drawdown >= -0.20
        and turnover <= 3.5
        and active_buckets >= 2
        and transition_rate <= 8.0
    )
    return {
        "candidate_id": str(row.get("candidate_id", "")),
        "candidate_name": str(params.get("candidate_name", "unknown")),
        "geometry_family": str(params.get("geometry_family", "unknown")),
        "sharpe_excess": sharpe,
        "sharpe_standard_error": round(_standard_error(sharpe, months), 6),
        "cagr": float(metrics.get("cagr", 0.0)),
        "max_drawdown": max_drawdown,
        "calmar": float(metrics.get("calmar", 0.0)),
        "annualized_turnover": turnover,
        "positive_folds": positive_folds,
        "folds": len(folds),
        "fold_sharpe_std": round(fold_std, 6),
        "mean_absolute_return_correlation_to_pool": round(mean_return_correlation, 6),
        "active_exposure_buckets": active_buckets,
        "annualized_transition_rate": transition_rate,
        "average_exposure": float(geometry.get("average_exposure", 0.0)),
        "exposure_std": float(geometry.get("exposure_std", 0.0)),
        "stable": stable,
    }


def select(experiments: list[Mapping[str, Any]], report: Mapping[str, Any]) -> dict[str, Any]:
    matrix = _return_matrix(experiments)
    correlations = matrix.corr().abs() if matrix.shape[1] >= 2 else pd.DataFrame()
    geometry_rows = _mapping(_mapping(report.get("geometry")).get("candidate_stats"))
    rows: list[dict[str, Any]] = []
    for row in experiments:
        candidate_id = str(row.get("candidate_id", ""))
        mean_corr = 1.0
        if candidate_id in correlations:
            peers = correlations[candidate_id].drop(candidate_id, errors="ignore").dropna()
            mean_corr = float(peers.mean()) if len(peers) else 1.0
        rows.append(evidence(row, mean_corr, _mapping(geometry_rows.get(candidate_id))))
    eligible = [row for row in rows if row["stable"]]
    pool = eligible or rows
    if not pool:
        return {"selected": None, "ranked": [], "eligible_count": 0}
    raw_best = max(pool, key=lambda row: row["sharpe_excess"])
    tolerance = raw_best["sharpe_standard_error"]
    equivalent = [row for row in pool if row["sharpe_excess"] >= raw_best["sharpe_excess"] - tolerance]
    selected = min(
        equivalent,
        key=lambda row: (
            row["mean_absolute_return_correlation_to_pool"],
            -row["active_exposure_buckets"],
            row["fold_sharpe_std"],
            row["annualized_turnover"],
            -row["sharpe_excess"],
            row["candidate_id"],
        ),
    )
    ranked = sorted(
        pool,
        key=lambda row: (
            not row["stable"],
            -row["sharpe_excess"],
            row["mean_absolute_return_correlation_to_pool"],
            row["fold_sharpe_std"],
        ),
    )
    return {
        "selected": selected,
        "raw_best": raw_best,
        "equivalent_candidate_count": len(equivalent),
        "eligible_count": len(eligible),
        "ranked": ranked,
        "selection_rule": {
            "primary": "stable chronological excess Sharpe",
            "equivalence_band": "within one approximate Sharpe standard error of the best stable candidate",
            "tie_break": "lowest return correlation, broader state usage, fold stability, turnover and Sharpe",
            "shared_signal_for_all_candidates": True,
            "human_idea_bonus": False,
            "post_hoc_parameter_tuning": False,
        },
    }


def run(ledger_path: Path, report_path: Path, output_path: Path) -> dict[str, Any]:
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    experiments = ledger.get("experiments")
    if not isinstance(experiments, list):
        raise ValueError("Generation 5 ledger must contain an experiments list")
    result = select(experiments, report)
    audit = {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generation_id": report.get("generation_id"),
        "experiments_evaluated": len(experiments),
        "holdout_status": _mapping(report.get("holdout")).get("status"),
        "hypothesis_agnostic": True,
        "state_geometry_aware": True,
        "selected_candidate_id": _mapping(result.get("selected")).get("candidate_id"),
        "raw_sharpe_champion_id": _mapping(report.get("champion")).get("candidate_id"),
        "selection": result,
        "promotion_allowed": False,
        "promotion_reason": "strict gate and explicit human approval are required before a single holdout use",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit = run(args.ledger, args.report, args.output)
    print(f"BRACE-SPX v5 selection: {audit['selected_candidate_id']}")


if __name__ == "__main__":
    main()
