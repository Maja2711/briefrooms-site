#!/usr/bin/env python3
"""BRACE-SPX sealed state-geometry generation 5.

Generation 5 keeps one shared, fixed market-information signal and changes the
portfolio state geometry only. Candidates differ through staircase exposure,
hysteresis, explicit state machines, or continuous volatility targeting. The
Generation 4 holdout window is inherited immutably and is never evaluated here.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any, Iterable, List, Mapping

import numpy as np
import pandas as pd

import brace_spx_generation_research as engine
import brace_spx_research as base
import brace_spx_generation4_research as gen4
from brace_spx_generation3_research import development_baselines

ROOT = Path(__file__).resolve().parents[1]
GENERATION_ID = "spx-state-geometry-v5"
EXPECTED_CANDIDATES = 12
FIXED_HOLDOUT_START = pd.Timestamp("2022-08-31")
FIXED_HOLDOUT_END = pd.Timestamp("2026-07-31")
CORRELATION_CLUSTER_THRESHOLD = 0.80

ORIGINAL_FEATURE_COLUMNS = base.feature_columns
ORIGINAL_FIT_PREDICT = base.fit_predict_candidate
ORIGINAL_EXPOSURE = base.probabilities_to_exposure
ORIGINAL_HOLDOUT_SPLIT = base.holdout_split


def fixed_holdout_split(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Keep the inherited Generation 4 holdout boundaries immutable."""
    development = frame.loc[frame.index < FIXED_HOLDOUT_START].copy()
    holdout = frame.loc[(frame.index >= FIXED_HOLDOUT_START) & (frame.index <= FIXED_HOLDOUT_END)].copy()
    if len(holdout) != base.HOLDOUT_MONTHS:
        raise RuntimeError(
            f"Generation 5 requires exactly {base.HOLDOUT_MONTHS} sealed months; found {len(holdout)}"
        )
    if development.empty:
        raise RuntimeError("Generation 5 development sample is empty")
    return development, holdout


def shared_probability(frame: pd.DataFrame) -> pd.Series:
    """Fixed balanced regime score inherited without tuning from Generation 4."""
    scores = gen4.module_scores(frame)
    composite = pd.concat(
        [
            scores["trend_slow"],
            scores["breadth_slow"],
            scores["credit_slow"],
            scores["vol_slow"],
        ],
        axis=1,
    ).mean(axis=1).clip(-1.0, 1.0)
    return ((composite + 1.0) * 0.5).clip(0.0, 1.0).astype(float)


def feature_columns(frame: pd.DataFrame, feature_set: str) -> List[str]:
    if feature_set.startswith("v5_"):
        return [column for column in frame.columns if column not in {"forward_return", "asset_return", "target_up"}]
    return ORIGINAL_FEATURE_COLUMNS(frame, feature_set)


def fit_predict_candidate(
    frame: pd.DataFrame,
    candidate: base.Candidate,
    train_indices: np.ndarray,
    predict_indices: np.ndarray,
    seed: int,
) -> pd.Series:
    """Return the same target-independent information signal for all v5 candidates."""
    if candidate.family != "state_geometry_v5":
        return ORIGINAL_FIT_PREDICT(frame, candidate, train_indices, predict_indices, seed)
    predict = frame.iloc[predict_indices]
    return shared_probability(predict)


def _segment_reset(index: pd.DatetimeIndex, position: int) -> bool:
    if position == 0:
        return True
    current = index[position].to_period("M").ordinal
    previous = index[position - 1].to_period("M").ordinal
    return current - previous > 1


def _vol_scale(realized_vol: pd.Series, target: float, floor: float = 0.30) -> pd.Series:
    vol = pd.to_numeric(realized_vol, errors="coerce").replace([np.inf, -np.inf], np.nan)
    return (target / vol).clip(lower=floor, upper=1.0).fillna(0.5)


def _staircase_exposure(p: pd.Series, vol: pd.Series, candidate: base.Candidate) -> pd.Series:
    params = candidate.params
    cutoffs = np.asarray(params["cutoffs"], dtype=float)
    levels = np.asarray(params["levels"], dtype=float)
    if len(levels) != len(cutoffs) + 1:
        raise RuntimeError("Staircase levels must contain one more element than cutoffs")
    buckets = np.digitize(p.to_numpy(dtype=float), cutoffs, right=False)
    raw = pd.Series(levels[buckets], index=p.index, dtype=float)
    return (raw * _vol_scale(vol, candidate.volatility_target)).clip(0.0, 1.0)


def _hysteresis_exposure(p: pd.Series, vol: pd.Series, candidate: base.Candidate) -> pd.Series:
    params = candidate.params
    levels = np.asarray(params.get("levels", [0.0, 0.25, 0.5, 0.75, 1.0]), dtype=float)
    entry = float(params["entry"])
    exit_level = float(params["exit"])
    up_step = int(params.get("up_step", 1))
    down_step = int(params.get("down_step", 1))
    confirm_up = int(params.get("confirm_up", 1))
    confirm_down = int(params.get("confirm_down", 1))
    start_state = int(params.get("start_state", len(levels) // 2))

    state = start_state
    up_count = 0
    down_count = 0
    values: list[float] = []
    for position, value in enumerate(p.astype(float).to_numpy()):
        if _segment_reset(p.index, position):
            state = start_state
            up_count = 0
            down_count = 0
        if value <= exit_level:
            down_count += 1
            up_count = 0
        elif value >= entry:
            up_count += 1
            down_count = 0
        else:
            up_count = 0
            down_count = 0
        if down_count >= confirm_down:
            state = max(0, state - down_step)
            down_count = 0
        elif up_count >= confirm_up:
            state = min(len(levels) - 1, state + up_step)
            up_count = 0
        values.append(float(levels[state]))
    raw = pd.Series(values, index=p.index, dtype=float)
    return (raw * _vol_scale(vol, candidate.volatility_target)).clip(0.0, 1.0)


def _state_machine_exposure(p: pd.Series, vol: pd.Series, candidate: base.Candidate) -> pd.Series:
    params = candidate.params
    risk_on = float(params["risk_on"])
    neutral = float(params["neutral"])
    recovery = float(params["recovery"])
    risk_off = float(params["risk_off"])
    recovery_slope = float(params["recovery_slope"])
    state_exposure = {key: float(value) for key, value in params["state_exposure"].items()}

    state = "neutral"
    previous_probability: float | None = None
    values: list[float] = []
    for position, value in enumerate(p.astype(float).to_numpy()):
        if _segment_reset(p.index, position):
            state = "neutral"
            previous_probability = None
        slope = 0.0 if previous_probability is None else value - previous_probability
        if value <= risk_off:
            state = "risk_off"
        elif state == "risk_off" and value >= recovery and slope >= recovery_slope:
            state = "recovery"
        elif state == "recovery":
            if value >= risk_on:
                state = "risk_on"
            elif value < recovery - 0.04 or slope <= -recovery_slope:
                state = "neutral"
        elif value >= risk_on:
            state = "risk_on"
        elif value >= neutral:
            state = "neutral"
        elif state == "risk_on" and value < neutral - 0.04:
            state = "neutral"
        values.append(state_exposure[state])
        previous_probability = value
    raw = pd.Series(values, index=p.index, dtype=float)
    return (raw * _vol_scale(vol, candidate.volatility_target)).clip(0.0, 1.0)


def _continuous_vol_exposure(p: pd.Series, vol: pd.Series, candidate: base.Candidate) -> pd.Series:
    params = candidate.params
    low = float(params["signal_low"])
    high = float(params["signal_high"])
    alpha = float(params["smoothing_alpha"])
    floor_exposure = float(params.get("floor_exposure", 0.0))
    brake_level = float(params.get("brake_level", -1.0))
    brake_cap = float(params.get("brake_cap", 1.0))
    adaptive = bool(params.get("adaptive_target", False))

    strength = ((p.astype(float) - low) / max(high - low, 1e-9)).clip(0.0, 1.0)
    if adaptive:
        split = float(params["target_split"])
        low_target = float(params["target_low"])
        high_target = float(params["target_high"])
        target = pd.Series(np.where(p >= split, high_target, low_target), index=p.index, dtype=float)
    else:
        target = pd.Series(candidate.volatility_target, index=p.index, dtype=float)
    clean_vol = pd.to_numeric(vol, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.20)
    desired = (strength * target / clean_vol).clip(lower=floor_exposure, upper=1.0)
    desired = desired.where(p > brake_level, np.minimum(desired, brake_cap))

    smoothed: list[float] = []
    previous = float(floor_exposure)
    for position, value in enumerate(desired.to_numpy(dtype=float)):
        if _segment_reset(p.index, position):
            previous = float(floor_exposure)
        previous = alpha * float(value) + (1.0 - alpha) * previous
        smoothed.append(previous)
    return pd.Series(smoothed, index=p.index, dtype=float).clip(0.0, 1.0)


def probabilities_to_exposure(
    probabilities: pd.Series,
    realized_vol: pd.Series,
    candidate: base.Candidate,
) -> pd.Series:
    if candidate.family != "state_geometry_v5":
        return ORIGINAL_EXPOSURE(probabilities, realized_vol, candidate)
    p = probabilities.sort_index().astype(float)
    vol = realized_vol.reindex(p.index).astype(float)
    family = str(candidate.params["geometry_family"])
    if family == "staircase":
        return _staircase_exposure(p, vol, candidate)
    if family == "hysteresis":
        return _hysteresis_exposure(p, vol, candidate)
    if family == "state_machine":
        return _state_machine_exposure(p, vol, candidate)
    if family == "continuous_vol_target":
        return _continuous_vol_exposure(p, vol, candidate)
    raise ValueError(f"Unknown Generation 5 geometry family: {family}")


def candidate_pool() -> List[base.Candidate]:
    """Return twelve immutable state-geometry candidates."""
    definitions: list[dict[str, Any]] = [
        {
            "name": "staircase-balanced", "family": "staircase", "vol": 0.15,
            "cutoffs": [0.38, 0.48, 0.58, 0.68], "levels": [0.0, 0.25, 0.5, 0.75, 1.0],
        },
        {
            "name": "staircase-defensive", "family": "staircase", "vol": 0.13,
            "cutoffs": [0.44, 0.52, 0.60, 0.70], "levels": [0.0, 0.2, 0.4, 0.7, 1.0],
        },
        {
            "name": "staircase-participation", "family": "staircase", "vol": 0.16,
            "cutoffs": [0.34, 0.44, 0.54, 0.64], "levels": [0.2, 0.4, 0.6, 0.8, 1.0],
        },
        {
            "name": "hysteresis-fast-exit", "family": "hysteresis", "vol": 0.14,
            "entry": 0.58, "exit": 0.46, "up_step": 1, "down_step": 2,
            "confirm_up": 1, "confirm_down": 1, "start_state": 2,
        },
        {
            "name": "hysteresis-fast-entry", "family": "hysteresis", "vol": 0.15,
            "entry": 0.54, "exit": 0.40, "up_step": 2, "down_step": 1,
            "confirm_up": 1, "confirm_down": 1, "start_state": 2,
        },
        {
            "name": "hysteresis-confirmed", "family": "hysteresis", "vol": 0.14,
            "entry": 0.60, "exit": 0.42, "up_step": 1, "down_step": 1,
            "confirm_up": 2, "confirm_down": 2, "start_state": 2,
        },
        {
            "name": "state-cycle-balanced", "family": "state_machine", "vol": 0.15,
            "risk_on": 0.62, "neutral": 0.50, "recovery": 0.46, "risk_off": 0.38,
            "recovery_slope": 0.03,
            "state_exposure": {"risk_off": 0.0, "neutral": 0.5, "recovery": 0.75, "risk_on": 1.0},
        },
        {
            "name": "state-cycle-defensive", "family": "state_machine", "vol": 0.13,
            "risk_on": 0.66, "neutral": 0.54, "recovery": 0.48, "risk_off": 0.44,
            "recovery_slope": 0.025,
            "state_exposure": {"risk_off": 0.0, "neutral": 0.35, "recovery": 0.65, "risk_on": 1.0},
        },
        {
            "name": "state-cycle-recovery", "family": "state_machine", "vol": 0.16,
            "risk_on": 0.60, "neutral": 0.48, "recovery": 0.42, "risk_off": 0.34,
            "recovery_slope": 0.04,
            "state_exposure": {"risk_off": 0.1, "neutral": 0.45, "recovery": 0.8, "risk_on": 1.0},
        },
        {
            "name": "vol-target-10", "family": "continuous_vol_target", "vol": 0.10,
            "signal_low": 0.40, "signal_high": 0.68, "smoothing_alpha": 0.35,
            "floor_exposure": 0.0, "brake_level": 0.35, "brake_cap": 0.25,
        },
        {
            "name": "vol-target-14", "family": "continuous_vol_target", "vol": 0.14,
            "signal_low": 0.38, "signal_high": 0.66, "smoothing_alpha": 0.50,
            "floor_exposure": 0.05, "brake_level": 0.32, "brake_cap": 0.35,
        },
        {
            "name": "vol-target-adaptive", "family": "continuous_vol_target", "vol": 0.16,
            "signal_low": 0.36, "signal_high": 0.64, "smoothing_alpha": 0.25,
            "floor_exposure": 0.10, "brake_level": 0.30, "brake_cap": 0.30,
            "adaptive_target": true, "target_split": 0.58, "target_low": 0.08, "target_high": 0.16,
        },
    ]
    candidates: list[base.Candidate] = []
    for definition in definitions:
        params = dict(definition)
        name = str(params.pop("name"))
        family = str(params.pop("family"))
        vol_target = float(params.pop("vol"))
        cutoffs = params.get("cutoffs", [0.40, 0.60])
        low = float(cutoffs[0] if isinstance(cutoffs, list) else params.get("signal_low", 0.40))
        high = float(cutoffs[-1] if isinstance(cutoffs, list) else params.get("signal_high", 0.60))
        params.update({
            "generation": GENERATION_ID,
            "candidate_name": name,
            "geometry_family": family,
            "shared_signal": "balanced_regime_score_v1",
            "target_fitted": False,
        })
        candidates.append(base.Candidate(
            family="state_geometry_v5",
            feature_set=f"v5_{family}",
            threshold_high=high,
            threshold_low=low,
            max_exposure=1.0,
            volatility_target=vol_target,
            params=params,
        ))
    ids = [candidate.candidate_id() for candidate in candidates]
    if len(candidates) != EXPECTED_CANDIDATES or len(set(ids)) != EXPECTED_CANDIDATES:
        raise RuntimeError("Generation 5 must contain exactly twelve unique candidates")
    return candidates


def _matrix_diagnostics(matrix: pd.DataFrame) -> dict[str, Any]:
    matrix = matrix.loc[:, matrix.std(ddof=1) > 1e-12]
    if matrix.shape[1] < 2:
        return {"available": False, "reason": "insufficient_nonconstant_candidates"}
    correlation = matrix.corr().fillna(0.0)
    absolute = correlation.abs()
    triangle = absolute.values[np.triu_indices_from(absolute.values, k=1)]
    eigenvalues = np.clip(np.linalg.eigvalsh(correlation.values), 0.0, None)
    weights = eigenvalues / max(float(eigenvalues.sum()), 1e-12)
    positive = weights[weights > 1e-12]
    effective_rank = float(math.exp(-float(np.sum(positive * np.log(positive)))))

    remaining = set(correlation.columns)
    clusters: list[list[str]] = []
    while remaining:
        seed = remaining.pop()
        cluster = {seed}
        frontier = [seed]
        while frontier:
            current = frontier.pop()
            neighbours = {
                other for other in list(remaining)
                if float(absolute.loc[current, other]) >= CORRELATION_CLUSTER_THRESHOLD
            }
            remaining.difference_update(neighbours)
            cluster.update(neighbours)
            frontier.extend(neighbours)
        clusters.append(sorted(cluster))
    sizes = sorted((len(cluster) for cluster in clusters), reverse=True)
    return {
        "available": True,
        "candidate_count": int(matrix.shape[1]),
        "months": int(matrix.shape[0]),
        "mean_absolute_pairwise_correlation": round(float(np.mean(triangle)), 6),
        "median_absolute_pairwise_correlation": round(float(np.median(triangle)), 6),
        "maximum_absolute_pairwise_correlation": round(float(np.max(triangle)), 6),
        "effective_independent_candidates": round(effective_rank, 6),
        "cluster_threshold": CORRELATION_CLUSTER_THRESHOLD,
        "cluster_count": len(clusters),
        "largest_cluster_size": sizes[0] if sizes else 0,
        "largest_cluster_share": round((sizes[0] / matrix.shape[1]) if sizes else 0.0, 6),
    }


def _validation_exposure(development: pd.DataFrame, candidate: base.Candidate, seed: int) -> pd.Series:
    parts: list[pd.Series] = []
    for fold_number, (train_idx, valid_idx) in enumerate(base.chronological_folds(development.index)):
        probability = fit_predict_candidate(development, candidate, train_idx, valid_idx, seed + fold_number)
        valid = development.loc[probability.index]
        exposure = probabilities_to_exposure(probability, valid["realized_vol_20"], candidate)
        parts.append(exposure)
    if not parts:
        return pd.Series(dtype=float)
    return pd.concat(parts).sort_index()


def geometry_diagnostics(development: pd.DataFrame, candidates: list[base.Candidate], seed: int) -> dict[str, Any]:
    exposures: dict[str, pd.Series] = {}
    candidate_stats: dict[str, dict[str, Any]] = {}
    bins = [-0.001, 0.125, 0.375, 0.625, 0.875, 1.001]
    for offset, candidate in enumerate(candidates):
        candidate_id = candidate.candidate_id()
        exposure = _validation_exposure(development, candidate, seed + offset * 101)
        exposures[candidate_id] = exposure
        bucket_share = pd.cut(exposure, bins=bins, labels=False, include_lowest=True).value_counts(normalize=True)
        active_buckets = int((bucket_share >= 0.05).sum())
        transitions = int((exposure.diff().abs() >= 0.10).sum())
        candidate_stats[candidate_id] = {
            "candidate_name": candidate.params.get("candidate_name"),
            "geometry_family": candidate.params.get("geometry_family"),
            "average_exposure": round(float(exposure.mean()), 6),
            "exposure_std": round(float(exposure.std(ddof=1)), 6),
            "minimum_exposure": round(float(exposure.min()), 6),
            "maximum_exposure": round(float(exposure.max()), 6),
            "active_exposure_buckets": active_buckets,
            "transitions": transitions,
            "annualized_transition_rate": round(transitions / max(len(exposure), 1) * 12.0, 6),
            "near_flat_share": round(float((exposure <= 0.10).mean()), 6),
            "near_full_share": round(float((exposure >= 0.90).mean()), 6),
        }
    matrix = pd.DataFrame(exposures).sort_index()
    return {
        "exposure_diversity": _matrix_diagnostics(matrix),
        "candidate_stats": candidate_stats,
    }


def configure_engine() -> None:
    engine.GENERATION_ID = GENERATION_ID
    engine.OUTPUT_PATH = ROOT / "data/research/brace_spx_generation5_research.json"
    engine.LEDGER_PATH = ROOT / "data/research/brace_spx_generation5_experiments.json"
    engine.MANIFEST_PATH = ROOT / "data/research/brace_spx_generation5_manifest.json"
    base.feature_columns = feature_columns
    base.fit_predict_candidate = fit_predict_candidate
    base.probabilities_to_exposure = probabilities_to_exposure
    base.holdout_split = fixed_holdout_split
    base.candidate_pool = candidate_pool


def run(prices: pd.DataFrame, budget: int, seed: int) -> dict[str, Any]:
    configure_engine()
    report = engine.run(prices, budget, seed)
    ledger = engine.read_json(engine.LEDGER_PATH, {})
    frame = base.monthly_dataset(prices)
    development, _sealed_holdout = fixed_holdout_split(frame)
    pool = candidate_pool()
    report["design"] = {
        "scope": "shared_signal_state_geometry",
        "derived_from_generation": "spx-diversified-v4",
        "candidate_space_size": EXPECTED_CANDIDATES,
        "geometry_family_count": 4,
        "shared_signal_count": 1,
        "target_used_for_rule_fitting": False,
        "generation4_reopened": False,
        "fixed_holdout_start": FIXED_HOLDOUT_START.date().isoformat(),
        "fixed_holdout_end": FIXED_HOLDOUT_END.date().isoformat(),
    }
    report["development_baselines"] = development_baselines(prices)
    report["return_diversity"] = gen4.diversity_diagnostics(ledger)
    report["geometry"] = geometry_diagnostics(development, pool, seed)
    engine.write_json(engine.OUTPUT_PATH, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=base.DEFAULT_START)
    parser.add_argument("--budget", type=int, default=EXPECTED_CANDIDATES)
    parser.add_argument("--seed", type=int, default=base.RANDOM_SEED + 5000)
    args = parser.parse_args()
    symbols: Iterable[str] = [*base.RICH_SYMBOLS.values(), *base.SECTOR_SYMBOLS, engine.RISK_FREE_SYMBOL]
    prices = base.download_prices(symbols, args.start)
    report = run(prices, args.budget, args.seed)
    print(
        f"BRACE-SPX v5: status={report['status']} "
        f"experiments={report['experiments_total']}/{report['candidate_space_size']} "
        f"pbo={report['multiple_testing']['pbo'].get('probability')}"
    )


if __name__ == "__main__":
    main()
