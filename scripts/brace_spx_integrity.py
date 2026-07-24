#!/usr/bin/env python3
"""Statistical and governance safeguards for BRACE-SPX.

This module is deliberately independent from any model family. It provides
conventional excess-return metrics, an approximate Deflated Sharpe probability,
a combinatorial PBO diagnostic when a return matrix is available, and a
single-use holdout registry.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist
from typing import Any, Mapping

import numpy as np
import pandas as pd

PERIODS_PER_YEAR = 12.0


def annualized_metrics(
    returns: pd.Series,
    turnover: pd.Series | None = None,
    risk_free_returns: pd.Series | None = None,
) -> dict[str, float]:
    clean = returns.dropna().astype(float)
    if clean.empty:
        return {}
    rf = (
        risk_free_returns.reindex(clean.index).fillna(0.0).astype(float)
        if risk_free_returns is not None
        else pd.Series(0.0, index=clean.index, dtype=float)
    )
    excess = clean - rf
    years = max(len(clean) / PERIODS_PER_YEAR, 1.0 / PERIODS_PER_YEAR)
    total = float((1.0 + clean).prod() - 1.0)
    cagr = float((1.0 + total) ** (1.0 / years) - 1.0)
    vol = float(clean.std(ddof=1) * math.sqrt(PERIODS_PER_YEAR)) if len(clean) > 1 else 0.0
    excess_vol = float(excess.std(ddof=1) * math.sqrt(PERIODS_PER_YEAR)) if len(excess) > 1 else 0.0
    sharpe = float(excess.mean() / excess.std(ddof=1) * math.sqrt(PERIODS_PER_YEAR)) if len(excess) > 1 and excess.std(ddof=1) > 0 else 0.0
    downside = excess[excess < 0]
    downside_vol = float(downside.std(ddof=1) * math.sqrt(PERIODS_PER_YEAR)) if len(downside) > 1 else 0.0
    sortino = float(excess.mean() * PERIODS_PER_YEAR / downside_vol) if downside_vol > 0 else 0.0
    equity = (1.0 + clean).cumprod()
    max_drawdown = float((equity / equity.cummax() - 1.0).min())
    calmar = cagr / abs(max_drawdown) if max_drawdown < 0 else 0.0
    annual_turnover = float(turnover.reindex(clean.index).fillna(0.0).mean() * PERIODS_PER_YEAR) if turnover is not None else 0.0
    positive_years = clean.groupby(clean.index.year).apply(lambda x: float((1.0 + x).prod() - 1.0) > 0)
    return {
        "total_return": round(total, 6),
        "cagr": round(cagr, 6),
        "annualized_volatility": round(vol, 6),
        "annualized_excess_volatility": round(excess_vol, 6),
        "sharpe_excess": round(sharpe, 4),
        "sharpe_zero_rf": round(sharpe, 4),
        "sortino_excess": round(sortino, 4),
        "sortino_zero_rf": round(sortino, 4),
        "max_drawdown": round(max_drawdown, 6),
        "calmar": round(calmar, 4),
        "annualized_turnover": round(annual_turnover, 4),
        "positive_year_ratio": round(float(positive_years.mean()) if len(positive_years) else 0.0, 4),
        "months": int(len(clean)),
    }


def expected_max_sharpe(number_of_trials: int, sharpe_std: float = 1.0) -> float:
    """Expected maximum under independent Gaussian trials (conservative proxy)."""
    n = max(1, int(number_of_trials))
    if n == 1:
        return 0.0
    gamma = 0.5772156649015329
    normal = NormalDist()
    a = normal.inv_cdf(1.0 - 1.0 / n)
    b = normal.inv_cdf(1.0 - 1.0 / (n * math.e))
    return float(sharpe_std * ((1.0 - gamma) * a + gamma * b))


def deflated_sharpe_probability(
    observed_sharpe: float,
    observations: int,
    number_of_trials: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
    benchmark_sharpe: float | None = None,
) -> float:
    """Approximate probability that Sharpe exceeds a multiple-testing benchmark."""
    n = int(observations)
    if n < 3:
        return 0.0
    sr = float(observed_sharpe)
    benchmark = expected_max_sharpe(number_of_trials) if benchmark_sharpe is None else float(benchmark_sharpe)
    variance_term = 1.0 - skewness * sr + ((kurtosis - 1.0) / 4.0) * sr * sr
    if variance_term <= 0:
        return 0.0
    z = (sr - benchmark) * math.sqrt(n - 1.0) / math.sqrt(variance_term)
    return float(min(1.0, max(0.0, NormalDist().cdf(z))))


def probability_of_backtest_overfitting(strategy_returns: pd.DataFrame, slices: int = 8) -> dict[str, Any]:
    """Combinatorial symmetric cross-validation estimate of PBO."""
    data = strategy_returns.dropna(axis=0, how="any").astype(float)
    if data.shape[1] < 2 or len(data) < slices * 2 or slices < 4 or slices % 2:
        return {"available": False, "reason": "requires >=2 strategies and an even number of sufficiently populated slices"}
    blocks = [block for block in np.array_split(np.arange(len(data)), slices) if len(block)]
    half = slices // 2
    logits: list[float] = []
    combinations = list(itertools.combinations(range(slices), half))
    seen: set[tuple[int, ...]] = set()
    for chosen in combinations:
        complement = tuple(i for i in range(slices) if i not in chosen)
        key = min(tuple(chosen), complement)
        if key in seen:
            continue
        seen.add(key)
        is_idx = np.concatenate([blocks[i] for i in chosen])
        oos_idx = np.concatenate([blocks[i] for i in complement])
        is_scores = data.iloc[is_idx].mean() / data.iloc[is_idx].std(ddof=1).replace(0.0, np.nan)
        oos_scores = data.iloc[oos_idx].mean() / data.iloc[oos_idx].std(ddof=1).replace(0.0, np.nan)
        if is_scores.dropna().empty or oos_scores.dropna().empty:
            continue
        winner = is_scores.idxmax()
        ranks = oos_scores.rank(method="average", pct=True)
        relative_rank = float(ranks.get(winner, np.nan))
        if not math.isfinite(relative_rank):
            continue
        relative_rank = min(max(relative_rank, 1e-9), 1.0 - 1e-9)
        logits.append(math.log(relative_rank / (1.0 - relative_rank)))
    if not logits:
        return {"available": False, "reason": "no valid CSCV splits"}
    return {
        "available": True,
        "pbo": round(float(np.mean(np.asarray(logits) <= 0.0)), 6),
        "splits": len(logits),
        "median_logit": round(float(np.median(logits)), 6),
    }


def generation_fingerprint(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


class HoldoutRegistry:
    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {"schema_version": "1.0.0", "generations": {}}
        self.payload = payload if isinstance(payload, dict) else {"schema_version": "1.0.0", "generations": {}}
        self.payload.setdefault("generations", {})

    def is_opened(self, generation_id: str) -> bool:
        row = self.payload["generations"].get(generation_id) or {}
        return row.get("status") == "opened"

    def open_once(self, generation_id: str, candidate_id: str, metadata: Mapping[str, Any] | None = None) -> None:
        if self.is_opened(generation_id):
            raise RuntimeError(f"sealed holdout for generation {generation_id} has already been opened")
        self.payload["generations"][generation_id] = {
            "status": "opened",
            "candidate_id": candidate_id,
            "opened_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "metadata": dict(metadata or {}),
        }
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
