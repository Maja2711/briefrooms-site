#!/usr/bin/env python3
"""Publish a sanitized BRACE-SPX Architecture 2 dashboard payload.

The public file contains only aggregate research, governance, external-validation,
orthogonality and shadow status. It intentionally excludes candidate parameters,
raw forecasts, daily target paths and the private experiment ledger.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "data" / "research"
SHADOW = ROOT / "data" / "shadow"
OUTPUT = ROOT / "data" / "public" / "brace_spx_public.json"


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


def metric_subset(source: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "cagr",
        "annualized_volatility",
        "sharpe_excess",
        "max_drawdown",
        "calmar",
        "annualized_turnover",
    )
    return {key: source.get(key) for key in keys}


def build_payload() -> dict[str, Any]:
    report = read_json(RESEARCH / "brace_spx_architecture_v2_report.json")
    external = read_json(RESEARCH / "brace_spx_architecture_v2_backtrader.json")
    orthogonality = read_json(RESEARCH / "brace_spx_signal_orthogonality_audit.json")
    shadow = read_json(SHADOW / "brace_spx_architecture_v2_shadow.json")

    raw_best = report.get("raw_best_diagnostic_only") or {}
    raw_metrics = raw_best.get("metrics") or {}
    baselines = report.get("baselines") or {}
    checks = report.get("checks") or {}
    recommendation = orthogonality.get("recommendation") or {}
    signal_diag = orthogonality.get("raw_signal_diagnostics") or {}
    rank = report.get("rank_stability") or {}
    bootstrap = report.get("bootstrap_raw_best_vs_buy_hold") or {}
    global_test = report.get("global_multiple_testing") or {}
    pbo = report.get("pbo") or {}

    public = {
        "schema_version": "2.0.0",
        "model": "BRACE-SPX Lab",
        "architecture": {
            "id": report.get("architecture_id"),
            "version": "A2",
            "labels": {
                "pl": "Architecture 2 — wieloźródłowy sygnał i reżimy",
                "en": "Architecture 2 — multi-source signals and regimes",
            },
            "candidate_signature": report.get("candidate_signature"),
        },
        "target_instrument": "SPY / S&P 500",
        "status": report.get("status"),
        "status_labels": {
            "pl": "Bramka rozwojowa niezaliczona — brak pojedynczego championa",
            "en": "Development gate not passed — no single champion",
        },
        "research_only": True,
        "live_activation": False,
        "single_champion_authorized": bool(report.get("single_champion_authorized", False)),
        "source_snapshot_at": max(
            str(report.get("generated_at", "")),
            str(orthogonality.get("generated_at", "")),
            str(shadow.get("updated_at", "")),
        ),
        "public_report_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "progress": {
            "experiments_completed": int(report.get("experiments_total", 0)),
            "candidate_space_size": int(report.get("candidate_space_size", 0)),
            "experiments_remaining": max(
                0,
                int(report.get("candidate_space_size", 0)) - int(report.get("experiments_total", 0)),
            ),
            "completion_ratio": (
                float(report.get("experiments_total", 0)) / max(1, int(report.get("candidate_space_size", 0)))
            ),
        },
        "development": {
            "start": (report.get("development") or {}).get("start"),
            "end": (report.get("development") or {}).get("end"),
            "days": (report.get("development") or {}).get("days"),
            "folds": (report.get("development") or {}).get("folds"),
            "frequency": (report.get("development") or {}).get("frequency"),
            "diagnostic_leader": {
                "authorized_as_champion": False,
                "metrics": metric_subset(raw_metrics),
                "positive_folds": raw_best.get("positive_folds"),
                "fold_sharpe_std": raw_best.get("fold_sharpe_std"),
            },
            "strict_gate": {
                "passed": bool(report.get("strict_gate_passed", False)),
                "pbo": pbo.get("probability"),
                "pbo_passed": bool(checks.get("pbo_at_most_0_20", False)),
                "global_dsr": global_test.get("probability"),
                "global_trials": report.get("global_trial_count"),
                "rank_correlation": rank.get("median_pairwise_fold_rank_correlation"),
                "unique_fold_winners": rank.get("unique_fold_winners"),
                "bootstrap_cagr_advantage_probability": bootstrap.get("probability_cagr_advantage_positive"),
                "bootstrap_sharpe_advantage_probability": bootstrap.get("probability_sharpe_advantage_positive"),
            },
        },
        "benchmarks": {
            "buy_and_hold": metric_subset(baselines.get("buy_and_hold") or {}),
            "trend_200d": metric_subset(baselines.get("trend_200d_weekly") or {}),
        },
        "diversity": {
            "signals": report.get("signal_diversity") or {},
            "returns": report.get("return_diversity") or {},
            "exposures": report.get("exposure_diversity") or {},
        },
        "external_validation": {
            "engine": external.get("engine"),
            "passed": bool(external.get("passed", False)),
            "target_path_exact": bool((external.get("checks") or {}).get("target_path_exact", False)),
            "return_correlation": external.get("return_correlation"),
            "total_return_difference": external.get("total_return_difference"),
        },
        "orthogonality_audit": {
            "method": "unsupervised_development_only",
            "selected_sources": recommendation.get("selected_raw_sources") or [],
            "selected_count": recommendation.get("selected_count"),
            "effective_rank": signal_diag.get("effective_rank"),
            "median_absolute_pairwise_correlation": signal_diag.get("median_absolute_pairwise_correlation"),
            "principal_components_for_85pct_variance": signal_diag.get("principal_components_for_85pct_variance"),
            "excluded_sources": recommendation.get("excluded_sources") or {},
            "uses_holdout": False,
            "uses_backtest_performance_for_selection": False,
        },
        "shadow": {
            "status": shadow.get("status"),
            "start": shadow.get("shadow_start"),
            "updated_at": shadow.get("updated_at"),
            "observations_collected": shadow.get("observations_collected"),
            "warmup_required": shadow.get("warmup_required"),
            "observations_remaining": shadow.get("observations_remaining"),
            "latest_market_date": shadow.get("latest_market_date"),
            "live_orders": False,
            "autonomous_trading": False,
            "single_champion_selected": False,
        },
        "sealed_holdout": {
            "start": (report.get("holdout") or {}).get("start"),
            "end": (report.get("holdout") or {}).get("end"),
            "status": (report.get("holdout") or {}).get("status"),
            "accessed": False,
            "access_count": 0,
            "labels": {
                "pl": "Zapieczętowany i niepobrany przez Architecture 2",
                "en": "Sealed and not downloaded by Architecture 2",
            },
        },
        "public_boundary": {
            "code_exposed": False,
            "parameters_exposed": False,
            "raw_predictions_exposed": False,
            "full_experiment_ledger_exposed": False,
            "candidate_snapshots_exposed": False,
        },
        "notes": {
            "pl": "Architecture 2 jest badaniem zarządzania ryzykiem, nie sygnałem transakcyjnym. PBO spadło poniżej 20%, lecz pełna bramka nie została zaliczona po globalnej korekcie 644 prób i ocenie stabilności rankingu.",
            "en": "Architecture 2 is risk-management research, not a trading signal. PBO fell below 20%, but the full gate did not pass after the cumulative 644-trial correction and rank-stability assessment.",
        },
    }
    if public["single_champion_authorized"]:
        raise RuntimeError("Public publisher refuses a payload that authorizes a single champion")
    if public["sealed_holdout"]["accessed"]:
        raise RuntimeError("Public publisher refuses a payload with an accessed holdout")
    return public


def main() -> None:
    payload = build_payload()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Published sanitized BRACE-SPX payload: {OUTPUT}")


if __name__ == "__main__":
    main()
