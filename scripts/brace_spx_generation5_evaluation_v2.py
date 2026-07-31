#!/usr/bin/env python3
"""BRACE-SPX Generation 5 Evaluation Protocol v2.

This module re-evaluates the exact immutable Generation 5 candidate universe.
It does not create a new generation, change candidate parameters, or inspect the
sealed holdout. The repairs are limited to portfolio accounting, stateful fold
warm-starts and additional stability diagnostics.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

import brace_spx_generation5_research as gen5
import brace_spx_generation_research as engine
import brace_spx_research as base

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "data" / "research"
GENERATION_ID = "spx-state-geometry-v5"
PROTOCOL_VERSION = 2
EXPECTED_CANDIDATES = 12
REPORT_PATH = RESEARCH / "brace_spx_generation5_evaluation_v2_report.json"
LEDGER_PATH = RESEARCH / "brace_spx_generation5_evaluation_v2_experiments.json"
MANIFEST_PATH = RESEARCH / "brace_spx_generation5_evaluation_v2_manifest.json"
ORIGINAL_MANIFEST_PATH = RESEARCH / "brace_spx_generation5_manifest.json"
COST = base.MONTHLY_COST
BOOTSTRAP_DRAWS = 1000
BOOTSTRAP_BLOCK_MONTHS = 6
BOOTSTRAP_SEED = 78291


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def candidate_signature() -> str:
    return engine.generation_signature(gen5.candidate_pool())


def evaluation_engine_signature() -> str:
    payload = {
        "generation_id": GENERATION_ID,
        "protocol_version": PROTOCOL_VERSION,
        "candidate_signature": candidate_signature(),
        "accounting": "spy_plus_risk_free_defensive_sleeve",
        "state_initialization": "continuous_observable_history_before_validation",
        "turnover": "continuous_context_before_validation_slice",
        "bootstrap_draws": BOOTSTRAP_DRAWS,
        "bootstrap_block_months": BOOTSTRAP_BLOCK_MONTHS,
        "gate_thresholds": "unchanged_from_generation5_v1",
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_generation_identity() -> dict[str, Any]:
    manifest = _read(ORIGINAL_MANIFEST_PATH)
    holdout = manifest.get("holdout") or {}
    if manifest.get("generation_id") != GENERATION_ID:
        raise RuntimeError("Unexpected Generation 5 identity")
    if manifest.get("candidate_signature") != candidate_signature():
        raise RuntimeError("Generation 5 candidate signature changed; refusing evaluation")
    if int(manifest.get("candidate_space_size", 0)) != EXPECTED_CANDIDATES:
        raise RuntimeError("Generation 5 must contain exactly twelve candidates")
    if holdout.get("start") != "2022-08-31" or holdout.get("end") != "2026-07-31":
        raise RuntimeError("Generation 5 holdout boundary changed")
    if bool(holdout.get("accessed", False)) or int(holdout.get("access_count", 0)) != 0:
        raise RuntimeError("Generation 5 holdout is not sealed")
    return manifest


def defensive_portfolio_returns(
    asset_returns: pd.Series,
    target_exposure: pd.Series,
    risk_free: pd.Series,
    cost: float = COST,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return portfolio returns, turnover and applied SPY weight.

    A signal decided at month t is applied at month t+1. Uninvested capital earns
    the contemporaneous monthly risk-free return. Calculating on a continuous
    context preserves the true first-validation-month weight and turnover.
    """
    index = asset_returns.index
    target = target_exposure.reindex(index).ffill().fillna(0.0).clip(0.0, 1.0)
    applied = target.shift(1).fillna(0.0).clip(0.0, 1.0)
    turnover = applied.diff().abs().fillna(applied.abs())
    rf = risk_free.reindex(index).ffill().fillna(0.0)
    asset = asset_returns.reindex(index).fillna(0.0)
    returns = applied * asset + (1.0 - applied) * rf - turnover * float(cost)
    return returns.astype(float), turnover.astype(float), applied.astype(float)


def _candidate_context_paths(
    development: pd.DataFrame,
    candidate: base.Candidate,
    risk_free: pd.Series,
) -> tuple[pd.Series, pd.Series, pd.Series, list[dict[str, Any]]]:
    return_parts: list[pd.Series] = []
    turnover_parts: list[pd.Series] = []
    exposure_parts: list[pd.Series] = []
    fold_metrics: list[dict[str, Any]] = []

    for fold_number, (_train_positions, valid_positions) in enumerate(base.chronological_folds(development.index)):
        valid_positions = np.asarray(valid_positions, dtype=int)
        if valid_positions.size == 0:
            continue
        valid_index = development.index[valid_positions]
        context = development.iloc[: int(valid_positions[-1]) + 1]
        probability = gen5.shared_probability(context)
        target_exposure = gen5.probabilities_to_exposure(
            probability,
            context["realized_vol_20"],
            candidate,
        )
        context_returns, context_turnover, context_applied = defensive_portfolio_returns(
            context["asset_return"],
            target_exposure,
            risk_free.reindex(context.index),
        )
        fold_returns = context_returns.loc[valid_index]
        fold_turnover = context_turnover.loc[valid_index]
        fold_applied = context_applied.loc[valid_index]
        metrics = engine.excess_metrics(
            fold_returns,
            risk_free.reindex(valid_index).fillna(0.0),
            fold_turnover,
        )
        metrics["fold_number"] = fold_number + 1
        metrics["first_applied_exposure"] = round(float(fold_applied.iloc[0]), 6)
        metrics["last_applied_exposure"] = round(float(fold_applied.iloc[-1]), 6)
        fold_metrics.append(metrics)
        return_parts.append(fold_returns)
        turnover_parts.append(fold_turnover)
        exposure_parts.append(fold_applied)

    if not return_parts:
        raise RuntimeError("No chronological validation folds available")
    returns = pd.concat(return_parts).sort_index()
    turnover = pd.concat(turnover_parts).sort_index()
    exposure = pd.concat(exposure_parts).sort_index()
    return returns, turnover, exposure, fold_metrics


def evaluate_candidate(
    development: pd.DataFrame,
    candidate: base.Candidate,
    risk_free: pd.Series,
) -> dict[str, Any]:
    returns, turnover, exposure, fold_metrics = _candidate_context_paths(
        development,
        candidate,
        risk_free,
    )
    metrics = engine.excess_metrics(returns, risk_free.reindex(returns.index), turnover)
    params = dict(candidate.params)
    bucket = pd.cut(
        exposure,
        bins=[-0.001, 0.125, 0.375, 0.625, 0.875, 1.001],
        labels=False,
        include_lowest=True,
    ).value_counts(normalize=True)
    active_buckets = int((bucket >= 0.05).sum())
    transitions = int((exposure.diff().abs() >= 0.10).sum())
    return {
        "candidate_id": candidate.candidate_id(),
        "candidate": asdict(candidate),
        "candidate_name": params.get("candidate_name"),
        "geometry_family": params.get("geometry_family"),
        "metrics": metrics,
        "fold_metrics": fold_metrics,
        "months": int(len(returns)),
        "monthly_returns": [
            {"date": index.date().isoformat(), "return": round(float(value), 10)}
            for index, value in returns.items()
        ],
        "monthly_turnover": [
            {"date": index.date().isoformat(), "turnover": round(float(value), 10)}
            for index, value in turnover.items()
        ],
        "monthly_exposures": [
            {"date": index.date().isoformat(), "exposure": round(float(value), 10)}
            for index, value in exposure.items()
        ],
        "geometry": {
            "active_exposure_buckets": active_buckets,
            "average_exposure": round(float(exposure.mean()), 6),
            "exposure_std": round(float(exposure.std(ddof=1)), 6),
            "minimum_exposure": round(float(exposure.min()), 6),
            "maximum_exposure": round(float(exposure.max()), 6),
            "transitions": transitions,
            "annualized_transition_rate": round(transitions / max(len(exposure), 1) * 12.0, 6),
            "near_flat_share": round(float((exposure <= 0.10).mean()), 6),
            "near_full_share": round(float((exposure >= 0.90).mean()), 6),
        },
    }


def _baseline_target(context: pd.DataFrame, name: str) -> pd.Series:
    if name == "buy_and_hold":
        return pd.Series(1.0, index=context.index, dtype=float)
    if name == "trend_200d":
        return (context["spy_ma_gap_200"].fillna(-1.0) > 0.0).astype(float)
    raise ValueError(f"Unknown baseline: {name}")


def evaluate_baseline(
    development: pd.DataFrame,
    risk_free: pd.Series,
    name: str,
) -> dict[str, Any]:
    return_parts: list[pd.Series] = []
    turnover_parts: list[pd.Series] = []
    fold_metrics: list[dict[str, Any]] = []
    for fold_number, (_train_positions, valid_positions) in enumerate(base.chronological_folds(development.index)):
        valid_positions = np.asarray(valid_positions, dtype=int)
        valid_index = development.index[valid_positions]
        context = development.iloc[: int(valid_positions[-1]) + 1]
        returns, turnover, _applied = defensive_portfolio_returns(
            context["asset_return"],
            _baseline_target(context, name),
            risk_free.reindex(context.index),
        )
        fold_returns = returns.loc[valid_index]
        fold_turnover = turnover.loc[valid_index]
        metric = engine.excess_metrics(
            fold_returns,
            risk_free.reindex(valid_index).fillna(0.0),
            fold_turnover,
        )
        metric["fold_number"] = fold_number + 1
        fold_metrics.append(metric)
        return_parts.append(fold_returns)
        turnover_parts.append(fold_turnover)
    joined_returns = pd.concat(return_parts).sort_index()
    joined_turnover = pd.concat(turnover_parts).sort_index()
    return {
        "metrics": engine.excess_metrics(
            joined_returns,
            risk_free.reindex(joined_returns.index).fillna(0.0),
            joined_turnover,
        ),
        "fold_metrics": fold_metrics,
        "returns": joined_returns,
        "turnover": joined_turnover,
    }


def _series_from_rows(rows: list[Mapping[str, Any]], value_key: str) -> pd.Series:
    return pd.Series(
        {
            pd.Timestamp(item["date"]): float(item[value_key])
            for item in rows
            if isinstance(item, Mapping) and "date" in item and value_key in item
        },
        dtype=float,
    ).sort_index()


def _matrix(experiments: list[Mapping[str, Any]], field: str, value_key: str) -> pd.DataFrame:
    return pd.DataFrame({
        str(row["candidate_id"]): _series_from_rows(list(row.get(field, [])), value_key)
        for row in experiments
    }).sort_index()


def _family_matrix(experiments: list[Mapping[str, Any]], return_matrix: pd.DataFrame) -> pd.DataFrame:
    groups: dict[str, list[str]] = {}
    for row in experiments:
        family = str(row.get("geometry_family") or "unknown")
        groups.setdefault(family, []).append(str(row.get("candidate_id")))
    return pd.DataFrame({
        family: return_matrix[[candidate_id for candidate_id in ids if candidate_id in return_matrix]].mean(axis=1)
        for family, ids in groups.items()
    }).sort_index()


def _correlation_enrichment(
    experiments: list[dict[str, Any]],
    return_matrix: pd.DataFrame,
    exposure_matrix: pd.DataFrame,
) -> None:
    return_corr = return_matrix.corr().abs()
    exposure_corr = exposure_matrix.corr().abs()
    for row in experiments:
        candidate_id = str(row["candidate_id"])
        return_peers = return_corr[candidate_id].drop(candidate_id, errors="ignore").dropna()
        exposure_peers = exposure_corr[candidate_id].drop(candidate_id, errors="ignore").dropna()
        row["geometry"]["mean_absolute_return_correlation_to_pool"] = round(
            float(return_peers.mean()) if len(return_peers) else 1.0,
            6,
        )
        row["geometry"]["mean_absolute_exposure_correlation_to_pool"] = round(
            float(exposure_peers.mean()) if len(exposure_peers) else 1.0,
            6,
        )


def rank_stability(experiments: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not experiments:
        return {"available": False, "reason": "no_experiments"}
    fold_count = min(len(row.get("fold_metrics", [])) for row in experiments)
    if fold_count < 2:
        return {"available": False, "reason": "insufficient_folds"}
    sharpe_table = pd.DataFrame(
        {
            str(row["candidate_id"]): [
                float(row["fold_metrics"][index].get("sharpe_excess", 0.0))
                for index in range(fold_count)
            ]
            for row in experiments
        },
        index=[f"fold_{index + 1}" for index in range(fold_count)],
    ).T
    ranks = sharpe_table.rank(axis=0, ascending=False, method="average")
    fold_corr = ranks.corr(method="spearman")
    triangle = fold_corr.values[np.triu_indices_from(fold_corr.values, k=1)]
    winners = [str(sharpe_table[column].idxmax()) for column in sharpe_table.columns]
    return {
        "available": True,
        "folds": fold_count,
        "median_pairwise_fold_rank_correlation": round(float(np.nanmedian(triangle)), 6),
        "mean_pairwise_fold_rank_correlation": round(float(np.nanmean(triangle)), 6),
        "unique_fold_winners": len(set(winners)),
        "fold_winner_ids": winners,
        "rank_table": {
            candidate_id: [round(float(value), 4) for value in ranks.loc[candidate_id].tolist()]
            for candidate_id in ranks.index
        },
    }


def selected_stability(
    selected_id: str,
    experiments: list[Mapping[str, Any]],
    ranking: Mapping[str, Any],
) -> dict[str, Any]:
    rank_table = ranking.get("rank_table") or {}
    ranks = [float(value) for value in rank_table.get(selected_id, [])]
    selected = next(row for row in experiments if str(row.get("candidate_id")) == selected_id)
    fold_count = len(selected.get("fold_metrics", []))
    regrets: list[float] = []
    for fold_index in range(fold_count):
        sharpes = {
            str(row["candidate_id"]): float(row["fold_metrics"][fold_index].get("sharpe_excess", 0.0))
            for row in experiments
        }
        regrets.append(max(sharpes.values()) - sharpes[selected_id])
    return {
        "selected_candidate_id": selected_id,
        "mean_fold_rank": round(float(np.mean(ranks)), 6) if ranks else None,
        "worst_fold_rank": round(float(np.max(ranks)), 6) if ranks else None,
        "best_fold_rank": round(float(np.min(ranks)), 6) if ranks else None,
        "mean_selection_regret_sharpe": round(float(np.mean(regrets)), 6),
        "maximum_selection_regret_sharpe": round(float(np.max(regrets)), 6),
        "fold_selection_regret_sharpe": [round(float(value), 6) for value in regrets],
    }


def _moving_block_positions(length: int, block: int, rng: np.random.Generator) -> np.ndarray:
    starts = rng.integers(0, max(1, length - block + 1), size=math.ceil(length / block))
    positions = np.concatenate([np.arange(start, min(start + block, length)) for start in starts])
    while len(positions) < length:
        start = int(rng.integers(0, max(1, length - block + 1)))
        positions = np.concatenate([positions, np.arange(start, min(start + block, length))])
    return positions[:length]


def bootstrap_metrics(
    selected_returns: pd.Series,
    selected_turnover: pd.Series,
    baseline_returns: pd.Series,
    risk_free: pd.Series,
    draws: int = BOOTSTRAP_DRAWS,
    block_months: int = BOOTSTRAP_BLOCK_MONTHS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    aligned = pd.concat(
        [
            selected_returns.rename("selected"),
            selected_turnover.rename("turnover"),
            baseline_returns.rename("baseline"),
            risk_free.rename("rf"),
        ],
        axis=1,
    ).dropna()
    if len(aligned) < block_months * 3:
        return {"available": False, "reason": "insufficient_history"}
    rng = np.random.default_rng(seed)
    records: list[dict[str, float]] = []
    synthetic_index = pd.date_range("2000-01-31", periods=len(aligned), freq="ME")
    for _ in range(draws):
        positions = _moving_block_positions(len(aligned), block_months, rng)
        sample = aligned.iloc[positions].copy()
        sample.index = synthetic_index
        selected_metrics = engine.excess_metrics(
            sample["selected"],
            sample["rf"],
            sample["turnover"],
        )
        baseline_metrics = engine.excess_metrics(
            sample["baseline"],
            sample["rf"],
            pd.Series(0.0, index=sample.index),
        )
        records.append({
            "cagr": float(selected_metrics.get("cagr", 0.0)),
            "sharpe_excess": float(selected_metrics.get("sharpe_excess", 0.0)),
            "max_drawdown": float(selected_metrics.get("max_drawdown", 0.0)),
            "sharpe_advantage": float(selected_metrics.get("sharpe_excess", 0.0))
                - float(baseline_metrics.get("sharpe_excess", 0.0)),
            "cagr_advantage": float(selected_metrics.get("cagr", 0.0))
                - float(baseline_metrics.get("cagr", 0.0)),
        })
    frame = pd.DataFrame(records)

    def interval(column: str) -> dict[str, float]:
        values = frame[column]
        return {
            "p05": round(float(values.quantile(0.05)), 6),
            "median": round(float(values.quantile(0.50)), 6),
            "p95": round(float(values.quantile(0.95)), 6),
        }

    return {
        "available": True,
        "draws": draws,
        "block_months": block_months,
        "seed": seed,
        "cagr_interval": interval("cagr"),
        "sharpe_excess_interval": interval("sharpe_excess"),
        "max_drawdown_interval": interval("max_drawdown"),
        "sharpe_advantage_interval": interval("sharpe_advantage"),
        "cagr_advantage_interval": interval("cagr_advantage"),
        "probability_sharpe_advantage_positive": round(float((frame["sharpe_advantage"] > 0.0).mean()), 6),
        "probability_cagr_advantage_positive": round(float((frame["cagr_advantage"] > 0.0).mean()), 6),
    }


def run(prices: pd.DataFrame) -> dict[str, Any]:
    original_manifest = validate_generation_identity()
    frame = base.monthly_dataset(prices)
    development, sealed_holdout = gen5.fixed_holdout_split(frame)
    if sealed_holdout.index.min().date().isoformat() != "2022-08-31":
        raise RuntimeError("Holdout boundary mismatch")
    risk_free = engine.monthly_risk_free(prices, development.index)
    pool = gen5.candidate_pool()
    experiments = [evaluate_candidate(development, candidate, risk_free) for candidate in pool]
    return_matrix = _matrix(experiments, "monthly_returns", "return")
    exposure_matrix = _matrix(experiments, "monthly_exposures", "exposure")
    _correlation_enrichment(experiments, return_matrix, exposure_matrix)

    ranked = sorted(
        experiments,
        key=lambda row: float(row.get("metrics", {}).get("sharpe_excess", -999.0)),
        reverse=True,
    )
    sharpe_values = pd.Series([
        float(row.get("metrics", {}).get("sharpe_excess", 0.0)) for row in ranked
    ], dtype=float)
    champion = ranked[0]
    champion_returns = _series_from_rows(champion["monthly_returns"], "return")
    champion_dsr = engine.deflated_sharpe_ratio(
        float(champion["metrics"].get("sharpe_excess", 0.0)),
        champion_returns,
        len(ranked),
        float(sharpe_values.std(ddof=1)),
    )
    baseline_buy = evaluate_baseline(development, risk_free, "buy_and_hold")
    baseline_trend = evaluate_baseline(development, risk_free, "trend_200d")
    family_matrix = _family_matrix(ranked, return_matrix)
    ledger = {
        "schema_version": "2.0.0",
        "generation_id": GENERATION_ID,
        "evaluation_protocol_version": PROTOCOL_VERSION,
        "candidate_signature": original_manifest["candidate_signature"],
        "evaluation_engine_signature": evaluation_engine_signature(),
        "experiments": ranked,
    }
    _write(LEDGER_PATH, ledger)

    report = {
        "schema_version": "3.0.0",
        "generation_id": GENERATION_ID,
        "evaluation_protocol_version": PROTOCOL_VERSION,
        "status": "generation_exhausted_holdout_still_sealed",
        "research_only": True,
        "live_activation": False,
        "candidate_signature": original_manifest["candidate_signature"],
        "evaluation_engine_signature": evaluation_engine_signature(),
        "candidate_space_size": EXPECTED_CANDIDATES,
        "experiments_total": EXPECTED_CANDIDATES,
        "experiments_remaining": 0,
        "development_end": "2022-07-31",
        "holdout": dict(original_manifest["holdout"]),
        "methodological_repairs": {
            "defensive_sleeve_earns_risk_free": True,
            "state_warm_started_from_observable_history": True,
            "fold_boundary_turnover_preserved": True,
            "candidate_parameters_changed": False,
            "selection_thresholds_changed": False,
        },
        "champion": {
            "candidate_id": champion["candidate_id"],
            "metrics": champion["metrics"],
            "fold_metrics": champion["fold_metrics"],
            "deflated_sharpe_ratio": champion_dsr,
            "candidate_hash": hashlib.sha256(
                json.dumps(champion["candidate"], sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        },
        "multiple_testing": {
            "trials_evaluated": EXPECTED_CANDIDATES,
            "sharpe_cross_section_std": round(float(sharpe_values.std(ddof=1)), 6),
            "pbo": engine.probability_backtest_overfitting(return_matrix),
        },
        "family_multiple_testing": {
            "family_count": int(family_matrix.shape[1]),
            "families": list(family_matrix.columns),
            "pbo": engine.probability_backtest_overfitting(family_matrix),
        },
        "development_baselines": {
            "buy_and_hold": baseline_buy["metrics"],
            "trend_200d": baseline_trend["metrics"],
        },
        "return_diversity": gen5._matrix_diagnostics(return_matrix),
        "family_return_diversity": gen5._matrix_diagnostics(family_matrix),
        "geometry": {
            "exposure_diversity": gen5._matrix_diagnostics(exposure_matrix),
            "candidate_stats": {
                row["candidate_id"]: {
                    **row["geometry"],
                    "candidate_name": row["candidate_name"],
                    "geometry_family": row["geometry_family"],
                }
                for row in ranked
            },
        },
        "rank_stability": rank_stability(ranked),
        "governance": {
            "generation5_v1_evidence_preserved": True,
            "candidate_universe_unchanged": True,
            "candidate_signature_unchanged": True,
            "holdout_opened_by_protocol": False,
            "human_approval_required": True,
            "no_live_orders": True,
            "no_leverage": True,
        },
    }
    _write(REPORT_PATH, report)
    return report
