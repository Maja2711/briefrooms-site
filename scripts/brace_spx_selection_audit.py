#!/usr/bin/env python3
"""Hypothesis-agnostic selection audit for BRACE-SPX candidates.

The search engine may propose any defensible feature family. This audit does not
reward a human idea or model label; it evaluates only out-of-sample evidence and
prefers the simpler candidate when performance is statistically indistinguishable.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "data" / "research" / "brace_spx_generation_experiments.json"
DEFAULT_REPORT = ROOT / "data" / "research" / "brace_spx_generation_research.json"
DEFAULT_OUTPUT = ROOT / "data" / "research" / "brace_spx_selection_audit.json"

FAMILY_COMPLEXITY = {"logistic": 1.0, "hist_gb": 2.5, "random_forest": 3.0}
FEATURE_COMPLEXITY = {"core": 1.0, "risk": 1.2, "rich": 1.8}


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected object in {path}")
    return payload


def candidate_complexity(candidate: Mapping[str, Any]) -> float:
    family = str(candidate.get("family", "unknown"))
    feature_set = str(candidate.get("feature_set", "unknown"))
    params = candidate.get("params") if isinstance(candidate.get("params"), Mapping) else {}
    parameter_count = len(params)
    exposure_levels = 1 + int(float(candidate.get("max_exposure", 1.0)) not in {0.0, 1.0})
    score = (
        FAMILY_COMPLEXITY.get(family, 4.0)
        + FEATURE_COMPLEXITY.get(feature_set, 2.5)
        + 0.12 * parameter_count
        + 0.10 * exposure_levels
    )
    return round(float(score), 6)


def sharpe_standard_error(sharpe: float, observations: int) -> float:
    if observations < 3:
        return float("inf")
    return math.sqrt(max(0.0, 1.0 + 0.5 * sharpe * sharpe) / (observations - 1.0))


def evidence_row(row: Mapping[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {}
    folds = row.get("fold_metrics") if isinstance(row.get("fold_metrics"), list) else []
    sharpe = float(metrics.get("sharpe_excess", -999.0))
    months = int(metrics.get("months", 0))
    fold_sharpes = [float(item.get("sharpe_excess", 0.0)) for item in folds if isinstance(item, Mapping)]
    positive_folds = sum(value > 0.0 for value in fold_sharpes)
    fold_std = 0.0
    if len(fold_sharpes) > 1:
        mean = sum(fold_sharpes) / len(fold_sharpes)
        fold_std = math.sqrt(sum((value - mean) ** 2 for value in fold_sharpes) / (len(fold_sharpes) - 1))
    max_drawdown = float(metrics.get("max_drawdown", -1.0))
    turnover = float(metrics.get("annualized_turnover", 999.0))
    stable = (
        len(fold_sharpes) >= 4
        and positive_folds >= math.ceil(0.67 * len(fold_sharpes))
        and max_drawdown >= -0.32
        and turnover <= 3.0
    )
    return {
        "candidate_id": str(row.get("candidate_id", "")),
        "candidate": dict(row.get("candidate") or {}),
        "sharpe_excess": sharpe,
        "sharpe_standard_error": round(sharpe_standard_error(sharpe, months), 6),
        "cagr": float(metrics.get("cagr", 0.0)),
        "max_drawdown": max_drawdown,
        "calmar": float(metrics.get("calmar", 0.0)),
        "annualized_turnover": turnover,
        "positive_folds": positive_folds,
        "folds": len(fold_sharpes),
        "fold_sharpe_std": round(fold_std, 6),
        "stable": stable,
        "complexity": candidate_complexity(row.get("candidate") or {}),
    }


def select_candidate(experiments: list[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [evidence_row(row) for row in experiments]
    eligible = [row for row in rows if row["stable"]]
    pool = eligible or rows
    if not pool:
        return {"selected": None, "ranked": [], "eligible_count": 0}
    best = max(pool, key=lambda row: row["sharpe_excess"])
    tolerance = best["sharpe_standard_error"]
    equivalent = [
        row for row in pool
        if row["sharpe_excess"] >= best["sharpe_excess"] - tolerance
    ]
    selected = min(
        equivalent,
        key=lambda row: (
            row["complexity"],
            row["fold_sharpe_std"],
            -row["calmar"],
            -row["sharpe_excess"],
            row["candidate_id"],
        ),
    )
    ranked = sorted(
        pool,
        key=lambda row: (
            not row["stable"],
            -row["sharpe_excess"],
            row["complexity"],
            row["fold_sharpe_std"],
        ),
    )
    return {
        "selected": selected,
        "raw_best": best,
        "equivalent_candidate_count": len(equivalent),
        "eligible_count": len(eligible),
        "ranked": ranked[:20],
        "selection_rule": {
            "primary": "stable chronological out-of-sample excess Sharpe",
            "equivalence_band": "within one approximate Sharpe standard error of the best eligible candidate",
            "tie_break": "lowest structural complexity, then fold stability, Calmar and Sharpe",
            "human_idea_bonus": False,
        },
    }


def run(ledger_path: Path, report_path: Path, output_path: Path) -> dict[str, Any]:
    ledger = read_json(ledger_path)
    report = read_json(report_path)
    experiments = ledger.get("experiments")
    if not isinstance(experiments, list):
        raise ValueError("Ledger experiments must be a list")
    selection = select_candidate(experiments)
    raw_champion_id = str((report.get("champion") or {}).get("candidate_id", ""))
    selected_id = str((selection.get("selected") or {}).get("candidate_id", ""))
    audit = {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generation_id": report.get("generation_id"),
        "experiments_evaluated": len(experiments),
        "holdout_status": (report.get("holdout") or {}).get("status"),
        "hypothesis_agnostic": True,
        "selected_candidate_id": selected_id or None,
        "raw_sharpe_champion_id": raw_champion_id or None,
        "simplicity_rule_changed_selection": bool(selected_id and raw_champion_id and selected_id != raw_champion_id),
        "selection": selection,
        "promotion_allowed": False,
        "promotion_reason": "final holdout remains sealed and routine search cannot open it",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    audit = run(args.ledger, args.report, args.output)
    print(
        f"BRACE-SPX selection audit: selected={audit['selected_candidate_id']} "
        f"experiments={audit['experiments_evaluated']} holdout={audit['holdout_status']}"
    )


if __name__ == "__main__":
    main()
