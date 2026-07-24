#!/usr/bin/env python3
"""Statistical governance for BRACE-SPX research.

Pure-Python/numpy helpers for conventional excess-return Sharpe, Deflated
Sharpe Ratio, Probability of Backtest Overfitting (CSCV), generation evidence
reconciliation, and a single-use holdout registry. No broker or live-order
integration is present.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Any, Mapping, Sequence

import numpy as np

_NORMAL = NormalDist()
_EULER_GAMMA = 0.5772156649015329


def annualized_excess_sharpe(
    returns: Sequence[float],
    risk_free_returns: Sequence[float] | float = 0.0,
    periods_per_year: int = 12,
) -> float:
    """Conventional Sharpe based on arithmetic excess periodic returns."""
    values = np.asarray(returns, dtype=float)
    if np.isscalar(risk_free_returns):
        excess = values - float(risk_free_returns)
    else:
        rf = np.asarray(risk_free_returns, dtype=float)
        if rf.shape != values.shape:
            raise ValueError("risk-free returns must match strategy returns")
        excess = values - rf
    excess = excess[np.isfinite(excess)]
    if excess.size < 2:
        return 0.0
    sigma = float(np.std(excess, ddof=1))
    if sigma <= 0.0:
        return 0.0
    return float(np.mean(excess) / sigma * math.sqrt(periods_per_year))


def probabilistic_sharpe_ratio(
    observed_sharpe: float,
    benchmark_sharpe: float,
    observations: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Probability that the true Sharpe exceeds a benchmark Sharpe."""
    if observations < 2:
        return 0.0
    denominator_sq = 1.0 - skewness * observed_sharpe + ((kurtosis - 1.0) / 4.0) * observed_sharpe**2
    if denominator_sq <= 0.0:
        return 0.0
    z = (observed_sharpe - benchmark_sharpe) * math.sqrt(observations - 1.0) / math.sqrt(denominator_sq)
    return float(_NORMAL.cdf(z))


def expected_max_sharpe(number_of_trials: int, sharpe_std: float) -> float:
    """Expected maximum Sharpe under repeated independent trials."""
    if number_of_trials <= 1 or sharpe_std <= 0.0:
        return 0.0
    n = float(number_of_trials)
    first = _NORMAL.inv_cdf(1.0 - 1.0 / n)
    second = _NORMAL.inv_cdf(1.0 - 1.0 / (n * math.e))
    return float(sharpe_std * ((1.0 - _EULER_GAMMA) * first + _EULER_GAMMA * second))


def deflated_sharpe_ratio(
    observed_sharpe: float,
    observations: int,
    number_of_trials: int,
    trial_sharpe_std: float,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """PSR after replacing the benchmark with the expected best false discovery."""
    benchmark = expected_max_sharpe(number_of_trials, trial_sharpe_std)
    return probabilistic_sharpe_ratio(
        observed_sharpe,
        benchmark,
        observations,
        skewness=skewness,
        kurtosis=kurtosis,
    )


def probability_of_backtest_overfitting(returns_matrix: Sequence[Sequence[float]], blocks: int = 8) -> float:
    """Estimate PBO with combinatorially symmetric cross-validation.

    Rows are chronological observations and columns are candidate strategies.
    The in-sample winner is ranked on the complementary out-of-sample sample.
    PBO is the fraction of splits where that winner ranks below the OOS median.
    """
    matrix = np.asarray(returns_matrix, dtype=float)
    if matrix.ndim != 2 or matrix.shape[1] < 2:
        raise ValueError("returns_matrix must have at least two strategy columns")
    if blocks < 4 or blocks % 2:
        raise ValueError("blocks must be an even integer >= 4")
    if matrix.shape[0] < blocks * 2:
        raise ValueError("insufficient observations for requested blocks")
    index_blocks = [part for part in np.array_split(np.arange(matrix.shape[0]), blocks) if len(part)]
    half = blocks // 2
    below_median = 0
    evaluated = 0
    for chosen in itertools.combinations(range(blocks), half):
        complement = tuple(i for i in range(blocks) if i not in chosen)
        if chosen > complement:
            continue
        train_idx = np.concatenate([index_blocks[i] for i in chosen])
        test_idx = np.concatenate([index_blocks[i] for i in complement])
        train_scores = np.array([annualized_excess_sharpe(matrix[train_idx, j]) for j in range(matrix.shape[1])])
        test_scores = np.array([annualized_excess_sharpe(matrix[test_idx, j]) for j in range(matrix.shape[1])])
        winner = int(np.nanargmax(train_scores))
        ranks = np.argsort(np.argsort(test_scores)) + 1
        if ranks[winner] <= matrix.shape[1] / 2.0:
            below_median += 1
        evaluated += 1
    return float(below_median / evaluated) if evaluated else 0.0


def declaration_hash(declaration: Mapping[str, Any]) -> str:
    encoded = json.dumps(declaration, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def authorize_single_use_holdout(
    registry: Mapping[str, Any],
    generation_id: str,
    predeclared_hash: str,
) -> dict[str, Any]:
    """Return an updated registry or reject repeated/undeclared holdout access."""
    if not generation_id.strip() or len(predeclared_hash) != 64:
        raise ValueError("generation and a SHA-256 predeclaration are required")
    updated = json.loads(json.dumps(dict(registry)))
    openings = updated.setdefault("holdout_openings", {})
    if generation_id in openings:
        raise RuntimeError(f"holdout already opened for generation {generation_id}")
    openings[generation_id] = {"predeclared_hash": predeclared_hash, "single_use": True}
    return updated


@dataclass(frozen=True)
class EvidenceState:
    source: str
    version: str
    experiments: int


def _version_tuple(value: str) -> tuple[int, ...]:
    cleaned = value.strip().lstrip("v")
    try:
        return tuple(int(part) for part in cleaned.split("."))
    except ValueError:
        return (0,)


def reconcile_evidence(active_report: Mapping[str, Any], public_snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Choose the strongest known evidence and explicitly flag missing raw memory."""
    active = EvidenceState(
        "active_branch",
        str(active_report.get("model_version", "0.0.0")),
        int(active_report.get("experiments_total", 0)),
    )
    generation = public_snapshot.get("generation") or {}
    progress = public_snapshot.get("progress") or {}
    public = EvidenceState(
        "public_verified_snapshot",
        str(generation.get("version", "0.0.0")),
        int(progress.get("experiments_completed", 0)),
    )
    strongest = max((active, public), key=lambda item: (_version_tuple(item.version), item.experiments))
    downgrade = (_version_tuple(active.version), active.experiments) < (_version_tuple(public.version), public.experiments)
    return {
        "strongest_known_evidence": strongest.__dict__,
        "active_branch": active.__dict__,
        "public_verified_snapshot": public.__dict__,
        "active_branch_is_downgrade": downgrade,
        "raw_experiment_memory_reconciled": not downgrade,
        "publication_allowed": not downgrade,
        "policy": "Never overwrite a stronger verified snapshot with an older or smaller active report.",
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--active-report", type=Path, required=True)
    parser.add_argument("--public-snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    active = json.loads(args.active_report.read_text(encoding="utf-8"))
    public = json.loads(args.public_snapshot.read_text(encoding="utf-8"))
    result = reconcile_evidence(active, public)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
