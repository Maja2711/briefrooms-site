#!/usr/bin/env python3
"""Auditable statistical controls for BRACE-SPX research.

This module is deliberately independent of the strategy engine so historical
experiment ledgers remain readable.  It provides conventional excess-return
Sharpe, a Deflated Sharpe Ratio approximation, CSCV-style Probability of
Backtest Overfitting, and a generation registry that makes a final holdout
single-use.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from statistics import NormalDist
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

_NORMAL = NormalDist()


def excess_return_sharpe(
    returns: pd.Series,
    risk_free_returns: pd.Series | float = 0.0,
    periods_per_year: int = 12,
) -> float:
    """Annualized Sharpe based on arithmetic excess returns.

    ``risk_free_returns`` must use the same periodicity as ``returns``.  A
    scalar is interpreted as a per-period return, never an annual yield.
    """
    clean = returns.astype(float).dropna()
    if clean.empty:
        return 0.0
    if isinstance(risk_free_returns, pd.Series):
        rf = risk_free_returns.astype(float).reindex(clean.index).ffill().fillna(0.0)
    else:
        rf = pd.Series(float(risk_free_returns), index=clean.index)
    excess = clean - rf
    sigma = float(excess.std(ddof=1)) if len(excess) > 1 else 0.0
    if sigma <= 0.0 or not math.isfinite(sigma):
        return 0.0
    return float(excess.mean() / sigma * math.sqrt(periods_per_year))


def probabilistic_sharpe_ratio(
    observed_sharpe: float,
    benchmark_sharpe: float,
    observations: int,
    skewness: float = 0.0,
    excess_kurtosis: float = 0.0,
) -> float:
    """Probability that true Sharpe exceeds ``benchmark_sharpe``.

    Implements the Bailey/Lopez de Prado finite-sample adjustment.  Sharpe
    inputs use the same annualization convention.
    """
    if observations < 3:
        return 0.0
    variance_term = 1.0 - skewness * observed_sharpe + ((excess_kurtosis + 2.0) / 4.0) * observed_sharpe**2
    if variance_term <= 0.0:
        return 0.0
    z = (observed_sharpe - benchmark_sharpe) * math.sqrt(observations - 1.0) / math.sqrt(variance_term)
    return float(_NORMAL.cdf(z))


def expected_max_sharpe(trials: int, sharpe_std: float) -> float:
    """Expected maximum Sharpe under multiple independent null trials."""
    if trials <= 1 or sharpe_std <= 0.0:
        return 0.0
    euler_gamma = 0.5772156649015329
    first = _NORMAL.inv_cdf(1.0 - 1.0 / trials)
    second = _NORMAL.inv_cdf(1.0 - 1.0 / (trials * math.e))
    return float(sharpe_std * ((1.0 - euler_gamma) * first + euler_gamma * second))


def deflated_sharpe_ratio(
    returns: pd.Series,
    number_of_trials: int,
    trial_sharpe_std: float,
    risk_free_returns: pd.Series | float = 0.0,
    periods_per_year: int = 12,
) -> Mapping[str, float]:
    """Return observed Sharpe, selection-adjusted benchmark and DSR."""
    clean = returns.astype(float).dropna()
    observed = excess_return_sharpe(clean, risk_free_returns, periods_per_year)
    benchmark = expected_max_sharpe(max(1, number_of_trials), max(0.0, trial_sharpe_std))
    skew = float(clean.skew()) if len(clean) > 2 else 0.0
    kurt = float(clean.kurt()) if len(clean) > 3 else 0.0
    probability = probabilistic_sharpe_ratio(observed, benchmark, len(clean), skew, kurt)
    return {
        "observed_sharpe": round(observed, 6),
        "selection_adjusted_sharpe": round(benchmark, 6),
        "deflated_sharpe_probability": round(probability, 6),
    }


def probability_of_backtest_overfitting(
    strategy_returns: pd.DataFrame,
    partitions: int = 8,
) -> Mapping[str, float | int]:
    """Estimate PBO with Combinatorially Symmetric Cross-Validation.

    Columns are candidate strategies and rows are chronological returns.  The
    sample is divided into contiguous partitions; every symmetric in/out split
    selects the best in-sample strategy and records its out-of-sample rank.
    """
    frame = strategy_returns.replace([np.inf, -np.inf], np.nan).dropna(how="all")
    if frame.shape[1] < 2 or len(frame) < partitions * 3 or partitions < 4 or partitions % 2:
        return {"pbo": 1.0, "splits": 0, "median_oos_rank": 0.0}
    blocks = [block for block in np.array_split(frame, partitions) if not block.empty]
    half = len(blocks) // 2
    logits: list[float] = []
    ranks: list[float] = []
    for selected in combinations(range(len(blocks)), half):
        if 0 not in selected:  # each complementary pair once
            continue
        inside = pd.concat([blocks[i] for i in selected])
        outside = pd.concat([blocks[i] for i in range(len(blocks)) if i not in selected])
        in_scores = inside.mean() / inside.std(ddof=1).replace(0.0, np.nan)
        out_scores = outside.mean() / outside.std(ddof=1).replace(0.0, np.nan)
        in_scores = in_scores.replace([np.inf, -np.inf], np.nan).dropna()
        out_scores = out_scores.reindex(in_scores.index).dropna()
        if len(out_scores) < 2:
            continue
        winner = in_scores.reindex(out_scores.index).idxmax()
        ascending_rank = float(out_scores.rank(method="average", pct=True).loc[winner])
        ranks.append(ascending_rank)
        clipped = min(max(ascending_rank, 1e-9), 1.0 - 1e-9)
        logits.append(math.log(clipped / (1.0 - clipped)))
    if not logits:
        return {"pbo": 1.0, "splits": 0, "median_oos_rank": 0.0}
    return {
        "pbo": round(float(np.mean(np.asarray(logits) < 0.0)), 6),
        "splits": len(logits),
        "median_oos_rank": round(float(np.median(ranks)), 6),
    }


@dataclass(frozen=True)
class HoldoutGeneration:
    generation_id: str
    specification_hash: str
    holdout_start: str
    holdout_end: str
    declared_at: str
    opened_at: str | None = None
    result_hash: str | None = None


def specification_hash(specification: Mapping[str, object]) -> str:
    raw = json.dumps(specification, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def declare_generation(
    registry_path: Path,
    generation_id: str,
    specification: Mapping[str, object],
    holdout_start: str,
    holdout_end: str,
) -> HoldoutGeneration:
    registry = _read_registry(registry_path)
    if generation_id in registry["generations"]:
        existing = registry["generations"][generation_id]
        if existing["specification_hash"] != specification_hash(specification):
            raise ValueError("Generation identifier already exists with a different specification")
        return HoldoutGeneration(**existing)
    generation = HoldoutGeneration(
        generation_id=generation_id,
        specification_hash=specification_hash(specification),
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        declared_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    registry["generations"][generation_id] = generation.__dict__
    _write_registry(registry_path, registry)
    return generation


def open_holdout_once(registry_path: Path, generation_id: str, result_payload: Mapping[str, object]) -> HoldoutGeneration:
    registry = _read_registry(registry_path)
    if generation_id not in registry["generations"]:
        raise KeyError("Generation must be declared before opening the holdout")
    current = registry["generations"][generation_id]
    if current.get("opened_at"):
        raise RuntimeError("This generation's holdout has already been opened")
    payload_hash = specification_hash(result_payload)
    current["opened_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    current["result_hash"] = payload_hash
    registry["generations"][generation_id] = current
    _write_registry(registry_path, registry)
    return HoldoutGeneration(**current)


def _read_registry(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {"schema_version": "1.0.0", "generations": {}}
    if not isinstance(payload.get("generations"), dict):
        raise ValueError("Invalid holdout registry")
    return payload


def _write_registry(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
