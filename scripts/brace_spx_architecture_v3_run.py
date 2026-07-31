#!/usr/bin/env python3
"""Run BRACE-SPX Architecture 3 on development-only market data."""
from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import brace_spx_architecture_v2 as a2
from brace_spx_architecture_v3 import *


def evaluate(prices: pd.DataFrame) -> dict[str, Any]:
    frame = a2.build_features(prices).dropna(subset=["asset_return", "risk_free_return"])
    if frame.index.max() >= pd.Timestamp(SEALED_HOLDOUT_START):
        raise RuntimeError("Architecture 3 entered the sealed holdout")
    signals = a2.signal_frame(frame).dropna()
    frame = frame.loc[signals.index]
    regime = a2.deterministic_regime(frame, signals)
    folds = a2.chronological_folds(frame.index)
    experiments, returns_by_id, exposures_by_id, fold_sharpes = [], {}, {}, {}

    for candidate in candidate_pool():
        target, _ = candidate_exposure(frame, signals, regime, candidate)
        returns, turnover, applied, parts = portfolio_returns(frame, target)
        overall = metrics(returns, turnover, frame["risk_free_return"], applied)
        diag = directional(frame, applied, parts)
        fold_rows, sharpes, positive_short = [], [], 0
        for number, (_train, valid) in enumerate(folds, 1):
            idx = frame.index[valid]
            row = metrics(returns.loc[idx], turnover.loc[idx], frame.loc[idx, "risk_free_return"], applied.loc[idx])
            fd = directional(frame.loc[idx], applied.loc[idx], parts.loc[idx])
            row.update({"fold_number": number, "short_excess_contribution_annualized": fd["short_excess_contribution_annualized"]})
            fold_rows.append(row)
            sharpes.append(float(row["sharpe_excess"]))
            positive_short += int(fd["short_excess_contribution_annualized"] > 0)
        experiments.append({
            "candidate_id": candidate.candidate_id(),
            "candidate": asdict(candidate),
            "metrics": overall,
            "directional_diagnostics": diag,
            "fold_metrics": fold_rows,
            "positive_folds": sum(x > 0 for x in sharpes),
            "positive_short_folds": positive_short,
            "fold_sharpe_std": round(float(np.std(sharpes, ddof=1)), 6),
        })
        returns_by_id[candidate.candidate_id()] = returns
        exposures_by_id[candidate.candidate_id()] = applied
        fold_sharpes[candidate.candidate_id()] = sharpes

    matrix = pd.DataFrame(returns_by_id).dropna()
    exposure_matrix = pd.DataFrame(exposures_by_id).dropna()
    buy = pd.Series(1.0, index=frame.index)
    buy_r, buy_t, buy_e, _ = portfolio_returns(frame, buy)
    trend_lf = (frame["spy_ma_gap_200"] > 0).astype(float).where(frame.index.dayofweek == 4).ffill().fillna(0.0)
    lf_r, lf_t, lf_e, _ = portfolio_returns(frame, trend_lf)
    trend_ls = pd.Series(np.where(frame["spy_ma_gap_200"] >= 0, 1.0, -1.0), index=frame.index).where(frame.index.dayofweek == 4).ffill().fillna(0.0)
    ls_r, ls_t, ls_e, ls_parts = portfolio_returns(frame, trend_ls)

    pbo = a2.probability_backtest_overfitting(matrix)
    ranks = pd.DataFrame(fold_sharpes).rank(axis=1, ascending=False)
    corr = ranks.T.corr(method="spearman")
    tri = corr.values[np.triu_indices_from(corr.values, 1)]
    winners = [str(row.idxmax()) for _, row in pd.DataFrame(fold_sharpes).iterrows()]
    stability = {
        "median_pairwise_fold_rank_correlation": round(float(np.nanmedian(tri)), 6),
        "unique_fold_winners": len(set(winners)),
        "fold_winner_ids": winners,
    }
    best = max(experiments, key=lambda row: float(row["metrics"]["sharpe_excess"]))
    best_id = best["candidate_id"]
    boot_lf = a2.block_bootstrap_advantage(matrix[best_id], lf_r.reindex(matrix.index))
    boot_ls = a2.block_bootstrap_advantage(matrix[best_id], ls_r.reindex(matrix.index))
    trials = PRIOR_TRIAL_COUNT + EXPECTED_CANDIDATES
    dsr = a2.global_dsr(float(best["metrics"]["sharpe_excess"]), matrix[best_id], trials, PRIOR_SHARPE_CROSS_SECTION_STD)
    diag = best["directional_diagnostics"]
    checks = {
        "candidate_space_complete": len(experiments) == EXPECTED_CANDIDATES,
        "sealed_holdout_not_downloaded": frame.index.max() < pd.Timestamp(SEALED_HOLDOUT_START),
        "signed_exposure_within_unlevered_bounds": exposure_matrix.min().min() >= -1 and exposure_matrix.max().max() <= 1,
        "meaningful_short_sample_at_least_63_days": diag["short_days"] >= 63,
        "short_excess_contribution_positive": diag["short_excess_contribution_annualized"] > 0,
        "short_hit_rate_at_least_0_50": diag["short_hit_rate"] is not None and diag["short_hit_rate"] >= 0.50,
        "positive_short_contribution_in_four_of_six_folds": best["positive_short_folds"] >= 4,
        "five_of_six_positive_folds": best["positive_folds"] >= 5,
        "pbo_at_most_0_20": bool(pbo.get("available")) and float(pbo.get("probability", 1)) <= 0.20,
        "global_dsr_at_least_0_95": float(dsr.get("probability", 0)) >= 0.95,
        "median_fold_rank_correlation_at_least_0_30": stability["median_pairwise_fold_rank_correlation"] >= 0.30,
        "unique_fold_winners_at_most_3": stability["unique_fold_winners"] <= 3,
        "bootstrap_cagr_advantage_vs_trend_lf_at_least_0_95": float(boot_lf.get("probability_cagr_advantage_positive", 0)) >= 0.95,
        "bootstrap_sharpe_advantage_vs_trend_lf_at_least_0_95": float(boot_lf.get("probability_sharpe_advantage_positive", 0)) >= 0.95,
        "bootstrap_sharpe_advantage_vs_trend_ls_at_least_0_95": float(boot_ls.get("probability_sharpe_advantage_positive", 0)) >= 0.95,
        "annualized_turnover_at_most_10": best["metrics"]["annualized_turnover"] <= 10,
        "independent_validation_passed": False,
    }
    strict = all(checks.values())
    champion = authorize_single_champion(strict, checks["short_excess_contribution_positive"], checks["independent_validation_passed"])
    return {
        "schema_version": PROTOCOL_VERSION,
        "architecture_id": ARCHITECTURE_ID,
        "base_architecture_id": BASE_ARCHITECTURE_ID,
        "base_candidate_signature": BASE_CANDIDATE_SIGNATURE,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "strict_gate_passed_holdout_still_sealed" if strict else "development_gate_not_passed_holdout_still_sealed",
        "research_only": True,
        "live_activation": False,
        "mandate": {"position_set":"long_short_flat","long_allowed":True,"short_allowed":True,"flat_allowed":True,"leverage_allowed":False,"max_abs_exposure":1.0,"orders_allowed":False,"fully_collateralized_short":True},
        "cost_model": {"transaction_cost_bps_per_unit_turnover":5.0,"annual_short_borrow_cost":ANNUAL_SHORT_BORROW_COST},
        "candidate_signature": candidate_signature(),
        "candidate_space_size": EXPECTED_CANDIDATES,
        "experiments_total": len(experiments),
        "prior_trial_count": PRIOR_TRIAL_COUNT,
        "global_trial_count": trials,
        "development": {"start":frame.index.min().date().isoformat(),"end":frame.index.max().date().isoformat(),"days":len(frame),"folds":len(folds),"frequency":"daily signals; weekly scheduled rebalance; daily crisis gate"},
        "holdout": {"start":SEALED_HOLDOUT_START,"end":SEALED_HOLDOUT_END,"status":"sealed_not_downloaded","accessed":False,"access_count":0},
        "raw_best_diagnostic_only": best,
        "selected_candidate_id": best_id if champion else None,
        "single_champion_authorized": champion,
        "baselines": {
            "buy_and_hold": metrics(buy_r,buy_t,frame["risk_free_return"],buy_e),
            "trend_200d_long_flat": metrics(lf_r,lf_t,frame["risk_free_return"],lf_e),
            "trend_200d_long_short": {**metrics(ls_r,ls_t,frame["risk_free_return"],ls_e),"directional_diagnostics":directional(frame,ls_e,ls_parts)},
        },
        "pbo": pbo,
        "global_multiple_testing": dsr,
        "rank_stability": stability,
        "bootstrap_raw_best_vs_trend_long_flat": boot_lf,
        "bootstrap_raw_best_vs_trend_long_short": boot_ls,
        "checks": checks,
        "strict_gate_passed": strict,
        "selection_policy": {"no_champion_without_positive_short_sleeve":True,"independent_validation_required":True,"human_approval_required_before_holdout_or_live_use":True,"shadow_all_candidates_in_parallel":True},
        "experiments": experiments,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prices-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(a2.load_prices_csv(args.prices_csv))
    write_json(args.output, report)
    print(f"BRACE-SPX A3: status={report['status']} champion={report['selected_candidate_id']}")


if __name__ == "__main__":
    main()
