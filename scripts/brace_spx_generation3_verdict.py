#!/usr/bin/env python3
"""Evaluate the predeclared BRACE-SPX generation 3 development gate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


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


def evaluate(report: Mapping[str, Any], audit: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, Any]:
    selected = _mapping(_mapping(audit.get("selection")).get("selected"))
    champion = _mapping(report.get("champion"))
    dsr = _mapping(champion.get("deflated_sharpe_ratio"))
    pbo = _mapping(_mapping(report.get("multiple_testing")).get("pbo"))
    baselines = _mapping(report.get("development_baselines"))
    best_baseline = max(
        _float(_mapping(baselines.get("buy_and_hold")).get("sharpe_excess"), -999.0),
        _float(_mapping(baselines.get("trend_200d")).get("sharpe_excess"), -999.0),
    )
    selected_sharpe = _float(selected.get("sharpe_excess"))
    holdout = _mapping(report.get("holdout"))
    checks = {
        "candidate_space_exhausted": _int(report.get("experiments_remaining"), 1) == 0,
        "holdout_sealed": str(holdout.get("status", "")).lower() == "sealed" and not bool(holdout.get("accessed", False)),
        "all_six_folds_positive": _int(selected.get("positive_folds")) >= 6 and _int(selected.get("folds")) >= 6,
        "excess_sharpe_at_least_1_20": selected_sharpe >= 1.20,
        "baseline_advantage_at_least_0_15": selected_sharpe - best_baseline >= 0.15,
        "max_drawdown_not_worse_than_15pct": _float(selected.get("max_drawdown"), -1.0) >= -0.15,
        "fold_sharpe_std_at_most_0_80": _float(selected.get("fold_sharpe_std"), 99.0) <= 0.80,
        "deflated_sharpe_probability_at_least_0_95": _float(dsr.get("probability")) >= 0.95,
        "pbo_at_most_0_20": bool(pbo.get("available")) and _float(pbo.get("probability"), 1.0) <= 0.20,
        "manifest_matches_generation": str(manifest.get("generation_id")) == "spx-focused-v3",
    }
    exhausted = checks["candidate_space_exhausted"]
    passed = exhausted and all(checks.values())
    status = "passed_development_gate_holdout_still_sealed" if passed else (
        "failed_development_gate_holdout_still_sealed" if exhausted else "research_in_progress"
    )
    return {
        "schema_version": "1.0.0",
        "generation_id": "spx-focused-v3",
        "status": status,
        "strict_gate_passed": passed,
        "holdout_opened": False,
        "candidate_signature": manifest.get("candidate_signature"),
        "experiments_total": _int(report.get("experiments_total")),
        "candidate_space_size": _int(report.get("candidate_space_size")),
        "selected_development_metrics": {
            "cagr": _float(selected.get("cagr")),
            "sharpe_excess": selected_sharpe,
            "max_drawdown": _float(selected.get("max_drawdown")),
            "calmar": _float(selected.get("calmar")),
            "positive_folds": _int(selected.get("positive_folds")),
            "folds": _int(selected.get("folds")),
            "fold_sharpe_std": _float(selected.get("fold_sharpe_std")),
        },
        "best_baseline_sharpe_excess": best_baseline,
        "sharpe_advantage_over_best_baseline": selected_sharpe - best_baseline,
        "deflated_sharpe_probability": _float(dsr.get("probability")),
        "pbo_probability": _float(pbo.get("probability"), 1.0),
        "checks": checks,
        "policy": {
            "workflow_can_open_holdout": False,
            "human_approval_required_before_holdout": True,
            "live_activation": False,
            "leverage": False
        }
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
    print(f"BRACE-SPX v3 verdict: {verdict['status']}")


if __name__ == "__main__":
    main()
