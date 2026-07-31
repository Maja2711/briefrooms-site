#!/usr/bin/env python3
"""BRACE-SPX diversified sealed generation 4.

Generation 4 is not a parameter refinement of generation 3. It predeclares a
small set of structurally different, deterministic signal constructions and
fixed ensembles. The goal is to reduce candidate similarity, lower selection
instability and make PBO harder to pass by accident. The final holdout is never
opened by this module.
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
from brace_spx_generation3_research import development_baselines

ROOT = Path(__file__).resolve().parents[1]
GENERATION_ID = "spx-diversified-v4"
EXPECTED_CANDIDATES = 16
CORRELATION_CLUSTER_THRESHOLD = 0.80

ORIGINAL_FEATURE_COLUMNS = base.feature_columns
ORIGINAL_FIT_PREDICT = base.fit_predict_candidate
ORIGINAL_EXPOSURE = base.probabilities_to_exposure


def _series(frame: pd.DataFrame, name: str, default: float = 0.0) -> pd.Series:
    if name not in frame:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[name], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(default)


def _squash(value: pd.Series, scale: float) -> pd.Series:
    return pd.Series(np.tanh(value.astype(float) / max(scale, 1e-9)), index=value.index, dtype=float)


def _mean(parts: list[pd.Series]) -> pd.Series:
    return pd.concat(parts, axis=1).mean(axis=1).clip(-1.0, 1.0)


def module_scores(frame: pd.DataFrame) -> dict[str, pd.Series]:
    """Build six fixed, economically distinct development signals."""
    trend_fast = _mean([
        _squash(_series(frame, "spy_ma_gap_50"), 0.045),
        _squash(_series(frame, "spy_momentum_63"), 0.12),
        _squash(_series(frame, "spy_momentum_126"), 0.18),
    ])
    trend_slow = _mean([
        _squash(_series(frame, "spy_ma_gap_200"), 0.10),
        _squash(_series(frame, "spy_momentum_252"), 0.25),
        _squash(_series(frame, "spy_drawdown_252") + 0.10, 0.12),
    ])
    breadth_fast = _mean([
        (_series(frame, "breadth_above_ma50", 0.5) - 0.5) * 2.0,
        _squash(_series(frame, "sector_momentum_mean_63"), 0.10),
        _squash(_series(frame, "equal_weight_relative_63"), 0.06),
    ])
    breadth_slow = _mean([
        (_series(frame, "breadth_above_ma200", 0.5) - 0.5) * 2.0,
        _squash(_series(frame, "sector_momentum_mean_63"), 0.14),
        -_squash(_series(frame, "sector_momentum_dispersion_63"), 0.10),
    ])
    credit_fast = _mean([
        _squash(_series(frame, "credit_ratio_63"), 0.045),
        -_squash(_series(frame, "dollar_momentum_63"), 0.07),
        -_squash(_series(frame, "vix_change_21"), 0.35),
    ])
    credit_slow = _mean([
        _squash(_series(frame, "credit_ratio_63"), 0.065),
        _squash(_series(frame, "tlt_momentum_63"), 0.08),
        -_squash(_series(frame, "tnx_change_63"), 0.75),
    ])
    vol_fast = _mean([
        -_squash(_series(frame, "vix_level", 20.0) - 21.0, 8.0),
        -_squash(_series(frame, "vix_change_21"), 0.30),
        -_squash(_series(frame, "spy_vol_20", 0.18) - 0.18, 0.10),
    ])
    vol_slow = _mean([
        -_squash(_series(frame, "tnx_change_63"), 0.70),
        _squash(_series(frame, "tlt_momentum_63"), 0.10),
        -_squash(_series(frame, "spy_vol_60", 0.18) - 0.18, 0.09),
    ])

    drawdown = _series(frame, "spy_drawdown_126")
    recovery_depth = pd.Series(
        np.exp(-np.square((drawdown + 0.12) / 0.10)) * 2.0 - 1.0,
        index=frame.index,
        dtype=float,
    )
    recovery_fast = _mean([
        recovery_depth,
        _squash(_series(frame, "spy_momentum_21"), 0.08),
        _squash(_series(frame, "spy_momentum_63"), 0.12),
    ])
    recovery_slow = _mean([
        pd.Series(np.exp(-np.square((_series(frame, "spy_drawdown_252") + 0.18) / 0.14)) * 2.0 - 1.0, index=frame.index),
        _squash(_series(frame, "spy_momentum_63"), 0.14),
        (_series(frame, "breadth_above_ma50", 0.5) - 0.5) * 2.0,
    ])
    return {
        "trend_fast": trend_fast,
        "trend_slow": trend_slow,
        "breadth_fast": breadth_fast,
        "breadth_slow": breadth_slow,
        "credit_fast": credit_fast,
        "credit_slow": credit_slow,
        "vol_fast": vol_fast,
        "vol_slow": vol_slow,
        "recovery_fast": recovery_fast,
        "recovery_slow": recovery_slow,
    }


def candidate_score(frame: pd.DataFrame, candidate: base.Candidate) -> pd.Series:
    scores = module_scores(frame)
    archetype = str(candidate.params.get("archetype"))
    variant = str(candidate.params.get("variant"))
    key = f"{archetype}_{variant}"
    if key in scores:
        return scores[key]
    if archetype == "consensus":
        return _mean([scores["trend_slow"], scores["breadth_slow"], scores["credit_slow"], scores["vol_slow"]])
    if archetype == "majority":
        votes = pd.concat([
            scores["trend_fast"] > 0.0,
            scores["breadth_fast"] > 0.0,
            scores["credit_fast"] > 0.0,
            scores["vol_fast"] > 0.0,
        ], axis=1).astype(float)
        return (votes.mean(axis=1) * 2.0 - 1.0).clip(-1.0, 1.0)
    if archetype == "trend_breadth":
        return _mean([scores["trend_slow"], scores["breadth_slow"]])
    if archetype == "macro_defense":
        return _mean([scores["credit_fast"], scores["vol_fast"]])
    if archetype == "veto":
        core = pd.concat([scores["trend_slow"], scores["breadth_slow"], scores["credit_slow"], scores["vol_slow"]], axis=1)
        return core.min(axis=1).clip(-1.0, 1.0)
    if archetype == "recovery_barbell":
        growth = _mean([scores["trend_fast"], scores["breadth_fast"]])
        recovery = scores["recovery_fast"]
        defense = _mean([scores["credit_fast"], scores["vol_fast"]])
        return (0.45 * growth + 0.35 * recovery + 0.20 * defense).clip(-1.0, 1.0)
    raise ValueError(f"Unknown generation 4 archetype: {archetype}")


def fit_predict_candidate(
    frame: pd.DataFrame,
    candidate: base.Candidate,
    train_indices: np.ndarray,
    predict_indices: np.ndarray,
    seed: int,
) -> pd.Series:
    """Produce fixed-rule probabilities without fitting or inspecting targets."""
    del train_indices, seed
    if candidate.family != "diversified_rule_v4":
        return ORIGINAL_FIT_PREDICT(frame, candidate, train_indices, predict_indices, seed)
    predict = frame.iloc[predict_indices]
    score = candidate_score(predict, candidate)
    return ((score + 1.0) * 0.5).clip(0.0, 1.0).astype(float)


def probabilities_to_exposure(
    probabilities: pd.Series,
    realized_vol: pd.Series,
    candidate: base.Candidate,
) -> pd.Series:
    if candidate.family != "diversified_rule_v4":
        return ORIGINAL_EXPOSURE(probabilities, realized_vol, candidate)
    p = probabilities.astype(float)
    mode = str(candidate.params.get("allocation", "graded"))
    if mode == "binary":
        raw = (p >= candidate.threshold_high).astype(float)
    elif mode == "three_step":
        raw = pd.Series(0.0, index=p.index)
        raw[p >= candidate.threshold_low] = 0.5
        raw[p >= candidate.threshold_high] = 1.0
    else:
        spread = max(candidate.threshold_high - candidate.threshold_low, 1e-6)
        raw = ((p - candidate.threshold_low) / spread).clip(0.0, 1.0)
    vol = realized_vol.reindex(p.index).astype(float)
    scale = (candidate.volatility_target / vol).replace([np.inf, -np.inf], np.nan).clip(lower=0.30, upper=1.0).fillna(0.5)
    return (raw * scale * candidate.max_exposure).clip(0.0, 1.0)


def feature_columns(frame: pd.DataFrame, feature_set: str) -> List[str]:
    if feature_set.startswith("v4_"):
        return [column for column in frame.columns if column not in {"forward_return", "asset_return", "target_up"}]
    return ORIGINAL_FEATURE_COLUMNS(frame, feature_set)


def candidate_pool() -> List[base.Candidate]:
    """Return 16 predeclared candidates spanning six structural archetypes."""
    definitions: list[tuple[str, str, str, float, float, float]] = []
    for archetype in ("trend", "breadth", "credit", "vol", "recovery"):
        definitions.extend([
            (archetype, "fast", "graded", 0.58, 0.44, 0.14),
            (archetype, "slow", "three_step", 0.60, 0.47, 0.16),
        ])
    definitions.extend([
        ("consensus", "fixed", "graded", 0.56, 0.44, 0.14),
        ("majority", "fixed", "three_step", 0.62, 0.48, 0.15),
        ("trend_breadth", "fixed", "graded", 0.57, 0.45, 0.15),
        ("macro_defense", "fixed", "three_step", 0.59, 0.46, 0.13),
        ("veto", "fixed", "binary", 0.54, 0.50, 0.12),
        ("recovery_barbell", "fixed", "graded", 0.56, 0.43, 0.15),
    ])
    candidates = [
        base.Candidate(
            family="diversified_rule_v4",
            feature_set=f"v4_{archetype}",
            threshold_high=high,
            threshold_low=low,
            max_exposure=1.0,
            volatility_target=vol_target,
            params={
                "generation": GENERATION_ID,
                "archetype": archetype,
                "variant": variant,
                "allocation": allocation,
                "fixed_rule": True,
            },
        )
        for archetype, variant, allocation, high, low, vol_target in definitions
    ]
    ids = [candidate.candidate_id() for candidate in candidates]
    if len(candidates) != EXPECTED_CANDIDATES or len(set(ids)) != EXPECTED_CANDIDATES:
        raise RuntimeError("Generation 4 must contain exactly 16 unique candidates")
    return candidates


def _return_matrix(ledger: Mapping[str, Any]) -> pd.DataFrame:
    columns: dict[str, pd.Series] = {}
    for row in ledger.get("experiments", []):
        if not isinstance(row, Mapping):
            continue
        values = {
            pd.Timestamp(item["date"]): float(item["return"])
            for item in row.get("monthly_returns", [])
            if isinstance(item, Mapping) and "date" in item and "return" in item
        }
        if values:
            columns[str(row.get("candidate_id"))] = pd.Series(values, dtype=float)
    return pd.DataFrame(columns).sort_index()


def diversity_diagnostics(ledger: Mapping[str, Any]) -> dict[str, Any]:
    matrix = _return_matrix(ledger)
    matrix = matrix.loc[:, matrix.std(ddof=1) > 1e-12]
    if matrix.shape[1] < 2:
        return {"available": False, "reason": "insufficient_nonconstant_candidates"}
    correlation = matrix.corr().fillna(0.0)
    absolute = correlation.abs()
    triangle = absolute.values[np.triu_indices_from(absolute.values, k=1)]
    eigenvalues = np.linalg.eigvalsh(correlation.values)
    eigenvalues = np.clip(eigenvalues, 0.0, None)
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


def configure_engine() -> None:
    engine.GENERATION_ID = GENERATION_ID
    engine.OUTPUT_PATH = ROOT / "data/research/brace_spx_generation4_research.json"
    engine.LEDGER_PATH = ROOT / "data/research/brace_spx_generation4_experiments.json"
    engine.MANIFEST_PATH = ROOT / "data/research/brace_spx_generation4_manifest.json"
    base.feature_columns = feature_columns
    base.fit_predict_candidate = fit_predict_candidate
    base.probabilities_to_exposure = probabilities_to_exposure
    base.candidate_pool = candidate_pool


def run(prices: pd.DataFrame, budget: int, seed: int) -> dict[str, Any]:
    configure_engine()
    report = engine.run(prices, budget, seed)
    ledger = engine.read_json(engine.LEDGER_PATH, {})
    report["design"] = {
        "scope": "structurally_diversified_fixed_rules",
        "derived_from_generation": "spx-focused-v3",
        "candidate_space_size": EXPECTED_CANDIDATES,
        "archetype_count": 6,
        "parameter_grid": False,
        "target_used_for_rule_fitting": False,
        "generation3_holdout_used": False,
    }
    report["development_baselines"] = development_baselines(prices)
    report["diversity"] = diversity_diagnostics(ledger)
    engine.write_json(engine.OUTPUT_PATH, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=base.DEFAULT_START)
    parser.add_argument("--budget", type=int, default=EXPECTED_CANDIDATES)
    parser.add_argument("--seed", type=int, default=base.RANDOM_SEED + 4000)
    args = parser.parse_args()
    symbols: Iterable[str] = [*base.RICH_SYMBOLS.values(), *base.SECTOR_SYMBOLS, engine.RISK_FREE_SYMBOL]
    prices = base.download_prices(symbols, args.start)
    report = run(prices, args.budget, args.seed)
    print(
        f"BRACE-SPX v4: status={report['status']} "
        f"experiments={report['experiments_total']}/{report['candidate_space_size']} "
        f"pbo={report['multiple_testing']['pbo'].get('probability')}"
    )


if __name__ == "__main__":
    main()
