#!/usr/bin/env python3
"""Build a sanitized public BRACE-SPX Architecture 3 snapshot.

Only aggregate development, benchmark and governance evidence is published.
Candidate identities, parameters, daily paths, raw predictions and the private
experiment ledger remain on the research branch.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def metric_subset(value: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "cagr",
        "annualized_volatility",
        "sharpe_excess",
        "max_drawdown",
        "calmar",
        "annualized_turnover",
        "average_net_exposure",
        "average_gross_exposure",
        "time_long",
        "time_short",
        "time_flat",
        "days",
    )
    return {key: value.get(key) for key in keys}


def directional_subset(value: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "long_excess_contribution_annualized",
        "short_excess_contribution_annualized",
        "transaction_cost_contribution_annualized",
        "directional_hit_rate",
        "short_hit_rate",
        "long_days",
        "short_days",
        "flat_days",
    )
    return {key: value.get(key) for key in keys}


def build_public(report: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    best = report.get("raw_best_diagnostic_only") or {}
    best_metrics = metric_subset(best.get("metrics") or {})
    directional = directional_subset(best.get("directional_diagnostics") or {})
    baselines = report.get("baselines") or {}
    trend_ls = baselines.get("trend_200d_long_short") or {}

    public = {
        "schema_version": "1.0.0",
        "model": "BRACE-SPX Lab",
        "architecture": {
            "id": report.get("architecture_id"),
            "version": "A3",
            "labels": {
                "pl": "Architecture 3 — kierunkowy mandat Long / Short / Flat",
                "en": "Architecture 3 — directional Long / Short / Flat mandate",
            },
        },
        "status": report.get("status"),
        "status_labels": {
            "pl": "Bramka rozwojowa niezaliczona — brak championa",
            "en": "Development gate not passed — no champion",
        },
        "generated_at": report.get("generated_at") or status.get("finished_at"),
        "research_only": bool(report.get("research_only", True)),
        "live_activation": bool(report.get("live_activation", False)),
        "single_champion_authorized": bool(report.get("single_champion_authorized", False)),
        "mandate": report.get("mandate") or {},
        "cost_model": report.get("cost_model") or {},
        "progress": {
            "experiments_completed": report.get("experiments_total"),
            "candidate_space_size": report.get("candidate_space_size"),
            "global_trial_count": report.get("global_trial_count"),
        },
        "development": report.get("development") or {},
        "diagnostic_leader": {
            "authorized_as_champion": False,
            "metrics": best_metrics,
            "directional_diagnostics": directional,
            "positive_folds": best.get("positive_folds"),
            "positive_short_folds": best.get("positive_short_folds"),
        },
        "benchmarks": {
            "buy_and_hold": metric_subset(baselines.get("buy_and_hold") or {}),
            "trend_200d_long_flat": metric_subset(baselines.get("trend_200d_long_flat") or {}),
            "trend_200d_long_short": {
                **metric_subset(trend_ls),
                "directional_diagnostics": directional_subset(trend_ls.get("directional_diagnostics") or {}),
            },
        },
        "validation": {
            "strict_gate_passed": bool(report.get("strict_gate_passed", False)),
            "pbo": (report.get("pbo") or {}).get("probability"),
            "global_dsr": (report.get("global_multiple_testing") or {}).get("probability"),
            "global_trials": (report.get("global_multiple_testing") or {}).get("global_trials"),
            "rank_correlation": (report.get("rank_stability") or {}).get("median_pairwise_fold_rank_correlation"),
            "unique_fold_winners": (report.get("rank_stability") or {}).get("unique_fold_winners"),
            "bootstrap_vs_trend_long_flat": {
                "cagr_advantage_probability": (report.get("bootstrap_raw_best_vs_trend_long_flat") or {}).get("probability_cagr_advantage_positive"),
                "sharpe_advantage_probability": (report.get("bootstrap_raw_best_vs_trend_long_flat") or {}).get("probability_sharpe_advantage_positive"),
            },
            "bootstrap_vs_trend_long_short": {
                "cagr_advantage_probability": (report.get("bootstrap_raw_best_vs_trend_long_short") or {}).get("probability_cagr_advantage_positive"),
                "sharpe_advantage_probability": (report.get("bootstrap_raw_best_vs_trend_long_short") or {}).get("probability_sharpe_advantage_positive"),
            },
            "checks": {
                "meaningful_short_sample": bool((report.get("checks") or {}).get("meaningful_short_sample_at_least_63_days", False)),
                "positive_short_contribution": bool((report.get("checks") or {}).get("short_excess_contribution_positive", False)),
                "short_hit_rate_passed": bool((report.get("checks") or {}).get("short_hit_rate_at_least_0_50", False)),
                "positive_short_folds_passed": bool((report.get("checks") or {}).get("positive_short_contribution_in_four_of_six_folds", False)),
                "independent_validation_passed": bool((report.get("checks") or {}).get("independent_validation_passed", False)),
            },
        },
        "sealed_holdout": {
            "start": (report.get("holdout") or {}).get("start"),
            "end": (report.get("holdout") or {}).get("end"),
            "status": (report.get("holdout") or {}).get("status"),
            "accessed": bool((report.get("holdout") or {}).get("accessed", False)),
            "access_count": (report.get("holdout") or {}).get("access_count", 0),
        },
        "public_boundary": {
            "candidate_identity_exposed": False,
            "parameters_exposed": False,
            "raw_predictions_exposed": False,
            "daily_paths_exposed": False,
            "full_ledger_exposed": False,
        },
        "notes": {
            "pl": "Architecture 3 jest oddzielnym eksperymentem Long / Short / Flat. Wartość 0% short pokazywana dla Architecture 2 dotyczy wyłącznie zamrożonego benchmarku Long / Flat.",
            "en": "Architecture 3 is a separate Long / Short / Flat experiment. The 0% short value shown for Architecture 2 applies only to the frozen Long / Flat reference.",
        },
    }

    serialized = json.dumps(public, ensure_ascii=False)
    forbidden = ("candidate_id", "candidate_signature", "experiments", "candidate_snapshots")
    if any(token in serialized for token in forbidden):
        raise RuntimeError("Sanitized Architecture 3 snapshot crossed the public boundary")
    return public


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=Path("data/research/brace_spx_architecture_v3_report.json"))
    parser.add_argument("--status", type=Path, default=Path("data/research/brace_spx_architecture_v3_status.json"))
    parser.add_argument("--output", type=Path, default=Path("data/public/brace_spx_architecture_v3_public.json"))
    args = parser.parse_args()

    payload = build_public(read_json(args.report), read_json(args.status))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
