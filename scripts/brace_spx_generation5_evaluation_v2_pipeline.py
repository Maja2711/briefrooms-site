#!/usr/bin/env python3
"""Run BRACE-SPX Generation 5 Evaluation Protocol v2 end to end."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

import brace_spx_generation5_evaluation_v2 as evaluation
import brace_spx_generation5_evaluation_v2_verdict as verdicts
import brace_spx_generation5_research as gen5
import brace_spx_generation5_selection as selection
import brace_spx_generation_research as engine
import brace_spx_research as base

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "data" / "research"
PUBLIC = ROOT / "data" / "public"
AUDIT_PATH = RESEARCH / "brace_spx_generation5_evaluation_v2_selection.json"
VERDICT_PATH = RESEARCH / "brace_spx_generation5_evaluation_v2_verdict.json"
PUBLIC_PATH = PUBLIC / "brace_spx_generation5_public.json"
V1_REPORT_PATH = RESEARCH / "brace_spx_generation5_research.json"
V1_VERDICT_PATH = RESEARCH / "brace_spx_generation5_verdict.json"


def write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read(path: Path, default: Mapping[str, Any] | None = None) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else dict(default or {})
    except (OSError, json.JSONDecodeError):
        return dict(default or {})


def series_from_rows(rows: list[Mapping[str, Any]], value_key: str) -> pd.Series:
    return pd.Series(
        {
            pd.Timestamp(item["date"]): float(item[value_key])
            for item in rows
            if "date" in item and value_key in item
        },
        dtype=float,
    ).sort_index()


def comparison_to_v1(report: Mapping[str, Any], selected: Mapping[str, Any]) -> dict[str, Any]:
    v1_report = read(V1_REPORT_PATH)
    v1_verdict = read(V1_VERDICT_PATH)
    v1_selected = v1_verdict.get("selected_development_metrics") or {}
    v1_pbo = ((v1_report.get("multiple_testing") or {}).get("pbo") or {}).get("probability")
    v2_pbo = ((report.get("multiple_testing") or {}).get("pbo") or {}).get("probability")
    fields = ("cagr", "sharpe_excess", "max_drawdown", "annualized_turnover", "fold_sharpe_std")
    metric_changes: dict[str, Any] = {}
    for field in fields:
        before = v1_selected.get(field)
        after = selected.get(field)
        metric_changes[field] = {
            "v1": before,
            "v2": after,
            "change": None if before is None or after is None else float(after) - float(before),
        }
    return {
        "same_generation": True,
        "same_candidate_signature": report.get("candidate_signature") == v1_verdict.get("candidate_signature"),
        "candidate_parameters_changed": False,
        "gate_thresholds_changed": False,
        "v1_selected_candidate_id": v1_verdict.get("selected_candidate_id"),
        "v2_selected_candidate_id": selected.get("candidate_id"),
        "selection_changed": bool(
            v1_verdict.get("selected_candidate_id")
            and selected.get("candidate_id")
            and v1_verdict.get("selected_candidate_id") != selected.get("candidate_id")
        ),
        "candidate_level_pbo": {
            "v1": v1_pbo,
            "v2": v2_pbo,
            "change": None if v1_pbo is None or v2_pbo is None else float(v2_pbo) - float(v1_pbo),
        },
        "selected_metric_changes": metric_changes,
    }


def evaluation_manifest(
    original_manifest: Mapping[str, Any],
    report: Mapping[str, Any],
    audit: Mapping[str, Any],
    verdict: Mapping[str, Any],
) -> dict[str, Any]:
    selected = ((audit.get("selection") or {}).get("selected") or {})
    champion = report.get("champion") or {}
    return {
        "schema_version": "1.0.0",
        "generation_id": "spx-state-geometry-v5",
        "evaluation_protocol_version": 2,
        "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "candidate_signature": original_manifest.get("candidate_signature"),
        "evaluation_engine_signature": report.get("evaluation_engine_signature"),
        "candidate_space_size": 12,
        "candidate_parameters_changed": False,
        "selection_thresholds_changed": False,
        "original_generation_manifest_preserved": True,
        "holdout": dict(original_manifest.get("holdout") or {}),
        "methodological_repairs": dict(report.get("methodological_repairs") or {}),
        "raw_excess_sharpe_champion_id": champion.get("candidate_id"),
        "hypothesis_agnostic_selected_candidate_id": selected.get("candidate_id"),
        "strict_development_gate_passed": bool(verdict.get("strict_gate_passed")),
        "holdout_candidate": selected.get("candidate_id") if verdict.get("strict_gate_passed") else None,
        "holdout_authorization": (
            "pending_explicit_human_approval"
            if verdict.get("strict_gate_passed")
            else "none_strict_gate_failed"
        ),
        "policy": {
            "workflow_can_open_holdout": False,
            "human_approval_required_before_holdout": True,
            "live_activation": False,
            "leverage": False,
            "short_spy": False,
        },
    }


def build_public(
    report: Mapping[str, Any],
    audit: Mapping[str, Any],
    verdict: Mapping[str, Any],
    protocol_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    selected = ((audit.get("selection") or {}).get("selected") or {})
    return_diversity = report.get("return_diversity") or {}
    exposure_diversity = ((report.get("geometry") or {}).get("exposure_diversity") or {})
    family_pbo = ((report.get("family_multiple_testing") or {}).get("pbo") or {})
    comparison = report.get("comparison_to_evaluation_v1") or {}
    return {
        "schema_version": "2.0.0",
        "generation_id": "spx-state-geometry-v5",
        "generation_label": "BRACE-SPX Generation 5",
        "evaluation_protocol_version": 2,
        "target_instrument": "SPY / S&P 500",
        "status": verdict.get("status"),
        "updated_at": report.get("generated_at"),
        "candidate_signature": protocol_manifest.get("candidate_signature"),
        "evaluation_engine_signature": protocol_manifest.get("evaluation_engine_signature"),
        "research_only": True,
        "live_activation": False,
        "progress": {"completed": 12, "total": 12, "remaining": 0},
        "design": {
            "candidate_space_size": 12,
            "shared_signal_count": 1,
            "geometry_family_count": 4,
            "same_generation_as_v1": True,
            "candidate_parameters_changed": False,
            "fixed_holdout": True,
        },
        "methodological_repairs": {
            "defensive_sleeve_earns_risk_free": True,
            "state_warm_started": True,
            "fold_boundary_turnover_preserved": True,
        },
        "selected_development_result": {
            "candidate_name": selected.get("candidate_name"),
            "geometry_family": selected.get("geometry_family"),
            "cagr": selected.get("cagr"),
            "sharpe_excess": selected.get("sharpe_excess"),
            "max_drawdown": selected.get("max_drawdown"),
            "positive_folds": selected.get("positive_folds"),
            "folds": selected.get("folds"),
            "fold_sharpe_std": selected.get("fold_sharpe_std"),
            "active_exposure_buckets": selected.get("active_exposure_buckets"),
        },
        "overfitting_and_stability": {
            "candidate_pbo_probability": verdict.get("pbo_probability"),
            "family_pbo_probability": family_pbo.get("probability"),
            "selected_dsr_probability": (verdict.get("selected_deflated_sharpe_ratio") or {}).get("probability"),
            "median_absolute_return_correlation": return_diversity.get("median_absolute_pairwise_correlation"),
            "median_absolute_exposure_correlation": exposure_diversity.get("median_absolute_pairwise_correlation"),
            "effective_return_candidates": return_diversity.get("effective_independent_candidates"),
            "effective_exposure_candidates": exposure_diversity.get("effective_independent_candidates"),
            "median_fold_rank_correlation": (report.get("rank_stability") or {}).get("median_pairwise_fold_rank_correlation"),
            "selected_mean_fold_rank": (report.get("selected_stability") or {}).get("mean_fold_rank"),
            "selected_worst_fold_rank": (report.get("selected_stability") or {}).get("worst_fold_rank"),
        },
        "uncertainty": dict(report.get("bootstrap_uncertainty") or {}),
        "comparison_to_evaluation_v1": comparison,
        "strict_gate": {"passed": bool(verdict.get("strict_gate_passed"))},
        "holdout": {
            "status": "sealed",
            "months": 48,
            "start": "2022-08-31",
            "end": "2026-07-31",
            "accessed": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=base.DEFAULT_START)
    args = parser.parse_args()

    original_manifest = evaluation.validate_generation_identity()
    symbols = [*base.RICH_SYMBOLS.values(), *base.SECTOR_SYMBOLS, engine.RISK_FREE_SYMBOL]
    prices = base.download_prices(symbols, args.start)
    report = evaluation.run(prices)
    report["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report["design"] = {
        "scope": "generation5_evaluation_protocol_repair",
        "generation4_reopened": False,
        "generation5_candidate_universe_reopened": False,
        "candidate_parameters_changed": False,
        "gate_thresholds_changed": False,
        "fixed_holdout_start": "2022-08-31",
        "fixed_holdout_end": "2026-07-31",
    }
    write(evaluation.REPORT_PATH, report)

    audit = selection.run(evaluation.LEDGER_PATH, evaluation.REPORT_PATH, AUDIT_PATH)
    selected = ((audit.get("selection") or {}).get("selected") or {})
    selected_id = str(selected.get("candidate_id") or "")
    if not selected_id:
        raise RuntimeError("Evaluation Protocol v2 did not select a candidate")

    ledger = read(evaluation.LEDGER_PATH)
    experiments = [row for row in ledger.get("experiments", []) if isinstance(row, dict)]
    report["selected_stability"] = evaluation.selected_stability(
        selected_id,
        experiments,
        report.get("rank_stability") or {},
    )

    selected_row = next(row for row in experiments if str(row.get("candidate_id")) == selected_id)
    selected_returns = series_from_rows(selected_row.get("monthly_returns", []), "return")
    selected_turnover = series_from_rows(selected_row.get("monthly_turnover", []), "turnover")
    frame = base.monthly_dataset(prices)
    development, _sealed = gen5.fixed_holdout_split(frame)
    risk_free = engine.monthly_risk_free(prices, development.index)
    buy_hold = evaluation.evaluate_baseline(development, risk_free, "buy_and_hold")
    report["bootstrap_uncertainty"] = evaluation.bootstrap_metrics(
        selected_returns,
        selected_turnover,
        buy_hold["returns"],
        risk_free.reindex(selected_returns.index),
    )
    report["comparison_to_evaluation_v1"] = comparison_to_v1(report, selected)
    write(evaluation.REPORT_PATH, report)

    audit = selection.run(evaluation.LEDGER_PATH, evaluation.REPORT_PATH, AUDIT_PATH)
    final = verdicts.evaluate(report, audit, original_manifest, ledger)
    write(VERDICT_PATH, final)

    protocol_manifest = evaluation_manifest(original_manifest, report, audit, final)
    write(evaluation.MANIFEST_PATH, protocol_manifest)
    write(PUBLIC_PATH, build_public(report, audit, final, protocol_manifest))

    print(
        "BRACE-SPX G5 Evaluation Protocol v2: "
        f"status={final['status']} pbo={final['pbo_probability']} "
        f"family_pbo={(final.get('family_pbo') or {}).get('probability')}"
    )


if __name__ == "__main__":
    main()
