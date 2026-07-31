#!/usr/bin/env python3
"""BRACE-SPX Architecture 2: multi-signal, higher-frequency, regime-aware research.

This is a post-G5 architecture transition, not a parameter refinement of G5.
It never downloads, reads, evaluates, summarizes, or tunes against the sealed
2022-08-01..2026-07-31 holdout. Research data end before the holdout begins.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "data" / "research"
ARCHITECTURE_ID = "spx-multisignal-regime-a2"
PROTOCOL_VERSION = "1.0.0"
DEVELOPMENT_END_EXCLUSIVE = "2022-08-01"
SEALED_HOLDOUT_START = "2022-08-01"
SEALED_HOLDOUT_END = "2026-07-31"
SHADOW_START = "2026-08-03"
START_DATE = "2007-01-01"
TARGET_SYMBOL = "SPY"
VIX3M_SYMBOL = "^VIX3M"
RISK_FREE_SYMBOL = "^IRX"
COST_PER_UNIT_TURNOVER = 0.0005
MIN_TRAIN_DAYS = 756
PURGE_DAYS = 5
VALIDATION_DAYS = 252
MAX_FOLDS = 6
PBO_BLOCKS = 8
BOOTSTRAP_DRAWS = 1000
BOOTSTRAP_BLOCK_DAYS = 20
BOOTSTRAP_SEED = 91427
SIGNAL_CLUSTER_THRESHOLD = 0.70
RETURN_CLUSTER_THRESHOLD = 0.80
EXPECTED_CANDIDATES = 10
PRIOR_GENERATION_MANIFESTS = (
    "brace_spx_generation_manifest.json",
    "brace_spx_generation2_manifest.json",
    "brace_spx_generation3_manifest.json",
    "brace_spx_generation4_manifest.json",
    "brace_spx_generation5_manifest.json",
)
PRIOR_GENERATION_LEDGERS = (
    "brace_spx_generation_experiments.json",
    "brace_spx_generation2_experiments.json",
    "brace_spx_generation3_experiments.json",
    "brace_spx_generation4_experiments.json",
    "brace_spx_generation5_experiments.json",
)
RICH_SYMBOLS = {
    "spy": "SPY",
    "vix": "^VIX",
    "vix3m": VIX3M_SYMBOL,
    "tnx": "^TNX",
    "tlt": "TLT",
    "hyg": "HYG",
    "lqd": "LQD",
    "uup": "UUP",
    "rsp": "RSP",
    "rf": RISK_FREE_SYMBOL,
}
SECTOR_SYMBOLS = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")


@dataclass(frozen=True)
class Candidate:
    name: str
    signal_sources: tuple[str, ...]
    allocation: str
    regime_policy: str
    weekly_rebalance: bool = True
    daily_shock_gate: bool = True
    max_exposure: float = 1.0

    def candidate_id(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Mapping[str, Any] | None = None) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else dict(default or {})
    except (OSError, json.JSONDecodeError):
        return dict(default or {})


def candidate_pool() -> list[Candidate]:
    candidates = [
        Candidate("trend-weekly", ("trend",), "graded", "trend"),
        Candidate("breadth-weekly", ("breadth",), "graded", "breadth"),
        Candidate("liquidity-shock", ("liquidity",), "defensive", "liquidity"),
        Candidate("options-term", ("options",), "graded", "options"),
        Candidate("rates-macro-market", ("rates",), "graded", "rates"),
        Candidate("regime-trend-liquidity", ("trend", "liquidity"), "graded", "deterministic_regime"),
        Candidate("regime-breadth-options", ("breadth", "options"), "graded", "deterministic_regime"),
        Candidate("cross-signal-median", ("trend", "breadth", "liquidity", "options", "rates"), "median", "deterministic_regime"),
        Candidate("defensive-veto", ("trend", "breadth", "liquidity", "options"), "veto", "deterministic_regime"),
        Candidate("diverse-equal", ("trend", "breadth", "liquidity", "options", "rates"), "equal", "deterministic_regime"),
    ]
    ids = [candidate.candidate_id() for candidate in candidates]
    if len(candidates) != EXPECTED_CANDIDATES or len(set(ids)) != EXPECTED_CANDIDATES:
        raise RuntimeError("Architecture 2 must contain exactly ten unique candidates")
    return candidates


def candidate_signature(candidates: Sequence[Candidate] | None = None) -> str:
    pool = list(candidates or candidate_pool())
    raw = json.dumps([candidate.candidate_id() for candidate in pool], separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def required_symbols() -> list[str]:
    return list(dict.fromkeys([*RICH_SYMBOLS.values(), *SECTOR_SYMBOLS]))


def download_prices(start: str = START_DATE, end: str = DEVELOPMENT_END_EXCLUSIVE) -> pd.DataFrame:
    """Download development data only; the sealed holdout is never requested."""
    if pd.Timestamp(end) > pd.Timestamp(SEALED_HOLDOUT_START):
        raise RuntimeError("Research download end would enter the sealed holdout")
    import yfinance as yf

    symbols = required_symbols()
    data = yf.download(
        symbols,
        start=start,
        end=end,
        auto_adjust=True,
        progress=False,
        group_by="column",
        threads=True,
    )
    if data.empty:
        raise RuntimeError("No development market data downloaded")
    if isinstance(data.columns, pd.MultiIndex):
        if "Close" in data.columns.get_level_values(0):
            close = data["Close"]
        elif "Close" in data.columns.get_level_values(1):
            close = data.xs("Close", axis=1, level=1)
        else:
            raise RuntimeError("Market response has no Close field")
    else:
        close = data[["Close"]].rename(columns={"Close": symbols[0]})
    if isinstance(close, pd.Series):
        close = close.to_frame(symbols[0])
    close.index = pd.to_datetime(close.index).tz_localize(None)
    close = close.sort_index()
    if close.index.max() >= pd.Timestamp(SEALED_HOLDOUT_START):
        raise RuntimeError("Downloaded frame contains a sealed-holdout observation")
    essential = ["SPY", "^VIX", VIX3M_SYMBOL, "HYG", "LQD", "^TNX", "TLT", "UUP", "RSP", RISK_FREE_SYMBOL]
    missing = [symbol for symbol in essential if symbol not in close or close[symbol].notna().sum() < 750]
    if missing:
        raise RuntimeError(f"Missing essential point-in-time market series: {missing}")
    return close


def load_prices_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    frame.index = pd.to_datetime(frame.index).tz_localize(None)
    frame = frame.sort_index()
    if frame.empty:
        raise RuntimeError("Price CSV is empty")
    if frame.index.max() >= pd.Timestamp(SEALED_HOLDOUT_START):
        raise RuntimeError("Price CSV contains a sealed-holdout observation")
    return frame


def _series(prices: pd.DataFrame, symbol: str) -> pd.Series:
    if symbol not in prices:
        return pd.Series(np.nan, index=prices.index, dtype=float)
    return pd.to_numeric(prices[symbol], errors="coerce").reindex(prices.index).ffill()


def _tanh(series: pd.Series, scale: float) -> pd.Series:
    return pd.Series(np.tanh(series.astype(float) / max(scale, 1e-12)), index=series.index, dtype=float)


def _mean(parts: Sequence[pd.Series]) -> pd.Series:
    return pd.concat(list(parts), axis=1).mean(axis=1).clip(-1.0, 1.0)


def build_features(prices: pd.DataFrame, research_mode: bool = True) -> pd.DataFrame:
    spy = _series(prices, "SPY")
    ret = spy.pct_change(fill_method=None)
    frame = pd.DataFrame(index=spy.index)
    frame["asset_return"] = ret
    for window in (21, 63, 126, 252):
        frame[f"spy_momentum_{window}"] = spy / spy.shift(window) - 1.0
    for window in (50, 200):
        frame[f"spy_ma_gap_{window}"] = spy / spy.rolling(window, min_periods=int(window * 0.75)).mean() - 1.0
    for window in (20, 60):
        frame[f"spy_vol_{window}"] = ret.rolling(window, min_periods=int(window * 0.75)).std(ddof=1) * math.sqrt(252.0)
    high126 = spy.rolling(126, min_periods=63).max()
    frame["spy_drawdown_126"] = spy / high126 - 1.0

    vix = _series(prices, "^VIX")
    vix3m = _series(prices, VIX3M_SYMBOL)
    frame["vix_level"] = vix
    frame["vix_change_5"] = vix / vix.shift(5) - 1.0
    frame["vix_change_21"] = vix / vix.shift(21) - 1.0
    frame["vix_term_ratio"] = vix / vix3m - 1.0

    tnx = _series(prices, "^TNX")
    tlt = _series(prices, "TLT")
    hyg = _series(prices, "HYG")
    lqd = _series(prices, "LQD")
    uup = _series(prices, "UUP")
    rsp = _series(prices, "RSP")
    credit = hyg / lqd
    frame["tnx_change_21"] = tnx - tnx.shift(21)
    frame["tnx_change_63"] = tnx - tnx.shift(63)
    frame["tlt_momentum_63"] = tlt / tlt.shift(63) - 1.0
    frame["credit_ratio_21"] = credit / credit.shift(21) - 1.0
    frame["credit_ratio_63"] = credit / credit.shift(63) - 1.0
    frame["dollar_momentum_63"] = uup / uup.shift(63) - 1.0
    frame["equal_weight_relative_63"] = (rsp / spy) / (rsp / spy).shift(63) - 1.0

    sectors = [symbol for symbol in SECTOR_SYMBOLS if symbol in prices]
    if len(sectors) < 8:
        raise RuntimeError("Insufficient sector breadth series")
    sector_prices = prices[sectors].reindex(frame.index).ffill()
    ma50 = sector_prices.rolling(50, min_periods=35).mean()
    ma200 = sector_prices.rolling(200, min_periods=150).mean()
    sector_mom63 = sector_prices / sector_prices.shift(63) - 1.0
    frame["breadth_above_ma50"] = (sector_prices > ma50).mean(axis=1)
    frame["breadth_above_ma200"] = (sector_prices > ma200).mean(axis=1)
    frame["sector_momentum_mean_63"] = sector_mom63.mean(axis=1)
    frame["sector_momentum_dispersion_63"] = sector_mom63.std(axis=1, ddof=1)

    annual_yield = _series(prices, RISK_FREE_SYMBOL).clip(lower=0.0)
    frame["risk_free_return"] = (1.0 + annual_yield / 100.0) ** (1.0 / 252.0) - 1.0
    frame = frame.replace([np.inf, -np.inf], np.nan)
    if research_mode:
        return frame.loc[frame.index < pd.Timestamp(SEALED_HOLDOUT_START)].copy()
    if len(frame.index) and frame.index.min() < pd.Timestamp(SHADOW_START):
        raise RuntimeError("Shadow data must begin strictly after the sealed holdout")
    return frame.copy()


def signal_frame(frame: pd.DataFrame) -> pd.DataFrame:
    signals = pd.DataFrame(index=frame.index)
    signals["trend"] = _mean([
        _tanh(frame["spy_ma_gap_50"], 0.05),
        _tanh(frame["spy_ma_gap_200"], 0.10),
        _tanh(frame["spy_momentum_63"], 0.12),
        _tanh(frame["spy_momentum_252"], 0.25),
    ])
    signals["breadth"] = _mean([
        (frame["breadth_above_ma50"] - 0.5) * 2.0,
        (frame["breadth_above_ma200"] - 0.5) * 2.0,
        _tanh(frame["sector_momentum_mean_63"], 0.12),
        _tanh(frame["equal_weight_relative_63"], 0.06),
        -_tanh(frame["sector_momentum_dispersion_63"], 0.10),
    ])
    signals["liquidity"] = _mean([
        _tanh(frame["credit_ratio_21"], 0.03),
        _tanh(frame["credit_ratio_63"], 0.05),
        -_tanh(frame["dollar_momentum_63"], 0.07),
        -_tanh(frame["tnx_change_63"], 0.75),
    ])
    signals["options"] = _mean([
        -_tanh(frame["vix_level"] - 21.0, 8.0),
        -_tanh(frame["vix_change_5"], 0.25),
        -_tanh(frame["vix_change_21"], 0.40),
        -_tanh(frame["vix_term_ratio"], 0.12),
    ])
    signals["rates"] = _mean([
        -_tanh(frame["tnx_change_21"], 0.40),
        -_tanh(frame["tnx_change_63"], 0.75),
        _tanh(frame["tlt_momentum_63"], 0.08),
        -_tanh(frame["dollar_momentum_63"], 0.08),
    ])
    return signals.clip(-1.0, 1.0)


def deterministic_regime(frame: pd.DataFrame, signals: pd.DataFrame) -> pd.Series:
    panic = (
        (signals["liquidity"] <= -0.55)
        | (signals["options"] <= -0.60)
        | (frame["spy_vol_20"] >= 0.35)
        | (frame["spy_drawdown_126"] <= -0.18)
    )
    high_vol = (
        (frame["spy_vol_20"] >= frame["spy_vol_20"].rolling(252, min_periods=126).median() * 1.20)
        | (frame["vix_level"] >= 25.0)
    ) & ~panic
    recent_panic = panic.shift(1).rolling(20, min_periods=1).max().fillna(0.0).astype(bool)
    recovery = recent_panic & (signals["trend"] > 0.05) & (signals["liquidity"] > -0.20) & ~panic
    regime = pd.Series("low_vol", index=frame.index, dtype="object")
    regime.loc[high_vol] = "high_vol"
    regime.loc[panic] = "panic"
    regime.loc[recovery] = "recovery"
    return regime


def _combine_candidate_signal(signals: pd.DataFrame, candidate: Candidate) -> pd.Series:
    subset = signals[list(candidate.signal_sources)]
    if candidate.allocation == "median":
        return subset.median(axis=1).clip(-1.0, 1.0)
    if candidate.allocation == "veto":
        return (0.65 * subset.mean(axis=1) + 0.35 * subset.min(axis=1)).clip(-1.0, 1.0)
    return subset.mean(axis=1).clip(-1.0, 1.0)


def _score_to_exposure(score: pd.Series, allocation: str) -> pd.Series:
    if allocation == "defensive":
        levels = np.select(
            [score <= -0.35, score <= -0.05, score <= 0.25, score <= 0.55],
            [0.0, 0.20, 0.45, 0.70],
            default=1.0,
        )
    else:
        levels = np.select(
            [score <= -0.45, score <= -0.10, score <= 0.20, score <= 0.50],
            [0.0, 0.25, 0.50, 0.75],
            default=1.0,
        )
    return pd.Series(levels, index=score.index, dtype=float)


def candidate_exposure(
    frame: pd.DataFrame,
    signals: pd.DataFrame,
    regime: pd.Series,
    candidate: Candidate,
) -> tuple[pd.Series, pd.Series]:
    score = _combine_candidate_signal(signals, candidate)
    desired = _score_to_exposure(score, candidate.allocation)

    if candidate.regime_policy == "deterministic_regime":
        multiplier = regime.map({"low_vol": 1.0, "high_vol": 0.65, "panic": 0.15, "recovery": 0.80}).astype(float)
        desired = desired * multiplier
    elif candidate.regime_policy == "liquidity":
        desired = np.minimum(desired, ((signals["liquidity"] + 1.0) / 2.0).clip(0.0, 1.0))
    elif candidate.regime_policy == "options":
        desired = np.minimum(desired, ((signals["options"] + 1.0) / 2.0).clip(0.0, 1.0))

    desired = pd.Series(desired, index=frame.index, dtype=float).clip(0.0, candidate.max_exposure)
    weekly = desired.where(frame.index.dayofweek == 4).ffill().fillna(0.0)
    shock = (
        (signals["liquidity"] <= -0.65)
        | (signals["options"] <= -0.70)
        | (frame["vix_change_5"] >= 0.60)
        | (frame["spy_vol_20"] >= 0.45)
    )
    extreme = (
        (signals["liquidity"] <= -0.80)
        | (signals["options"] <= -0.85)
        | (frame["spy_drawdown_126"] <= -0.25)
    )
    exposure = weekly.copy()
    exposure.loc[shock] = np.minimum(exposure.loc[shock], 0.25)
    exposure.loc[extreme] = 0.0
    return exposure.clip(0.0, 1.0), score


def portfolio_returns(frame: pd.DataFrame, target_exposure: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    target = target_exposure.reindex(frame.index).ffill().fillna(0.0).clip(0.0, 1.0)
    applied = target.shift(1).fillna(0.0)
    turnover = applied.diff().abs().fillna(applied.abs())
    asset = frame["asset_return"].fillna(0.0)
    rf = frame["risk_free_return"].fillna(0.0)
    returns = applied * asset + (1.0 - applied) * rf - turnover * COST_PER_UNIT_TURNOVER
    return returns.astype(float), turnover.astype(float), applied.astype(float)


def metrics(returns: pd.Series, turnover: pd.Series, risk_free: pd.Series) -> dict[str, float]:
    aligned = pd.concat([returns.rename("r"), turnover.rename("t"), risk_free.rename("rf")], axis=1).dropna()
    if aligned.empty:
        return {}
    r = aligned["r"]
    years = max(len(r) / 252.0, 1.0 / 252.0)
    total = float((1.0 + r).prod() - 1.0)
    cagr = float((1.0 + total) ** (1.0 / years) - 1.0)
    vol = float(r.std(ddof=1) * math.sqrt(252.0)) if len(r) > 1 else 0.0
    excess = r - aligned["rf"]
    excess_std = float(excess.std(ddof=1)) if len(excess) > 1 else 0.0
    sharpe = float(excess.mean() / excess_std * math.sqrt(252.0)) if excess_std > 0 else 0.0
    equity = (1.0 + r).cumprod()
    drawdown = float((equity / equity.cummax() - 1.0).min())
    return {
        "total_return": round(total, 6),
        "cagr": round(cagr, 6),
        "annualized_volatility": round(vol, 6),
        "sharpe_excess": round(sharpe, 6),
        "max_drawdown": round(drawdown, 6),
        "calmar": round(cagr / abs(drawdown), 6) if drawdown < 0 else 0.0,
        "annualized_turnover": round(float(aligned["t"].mean() * 252.0), 6),
        "average_exposure": 0.0,
        "days": int(len(r)),
    }


def chronological_folds(index: pd.DatetimeIndex) -> list[tuple[np.ndarray, np.ndarray]]:
    count = len(index)
    folds: list[tuple[np.ndarray, np.ndarray]] = []
    train_end = MIN_TRAIN_DAYS
    while train_end + PURGE_DAYS + VALIDATION_DAYS <= count:
        valid_start = train_end + PURGE_DAYS
        valid_end = valid_start + VALIDATION_DAYS
        folds.append((np.arange(0, train_end), np.arange(valid_start, valid_end)))
        train_end += VALIDATION_DAYS
    return folds[-MAX_FOLDS:]


def probability_backtest_overfitting(return_matrix: pd.DataFrame, blocks: int = PBO_BLOCKS) -> dict[str, Any]:
    clean = return_matrix.dropna(axis=1, how="all").dropna(axis=0, how="any")
    if clean.shape[1] < 2 or clean.shape[0] < blocks * 20 or blocks % 2:
        return {"available": False, "probability": None, "splits": 0}
    block_positions = np.array_split(np.arange(len(clean)), blocks)
    logits: list[float] = []
    for train_blocks in combinations(range(blocks), blocks // 2):
        if 0 not in train_blocks:
            continue
        train_idx = np.concatenate([block_positions[i] for i in train_blocks])
        test_idx = np.concatenate([block_positions[i] for i in range(blocks) if i not in train_blocks])
        train = clean.iloc[train_idx]
        test = clean.iloc[test_idx]
        train_sharpe = train.mean().div(train.std(ddof=1).replace(0.0, np.nan)) * math.sqrt(252.0)
        if train_sharpe.dropna().empty:
            continue
        winner = str(train_sharpe.idxmax())
        test_sharpe = (test.mean().div(test.std(ddof=1).replace(0.0, np.nan)) * math.sqrt(252.0)).dropna().sort_values()
        if winner not in test_sharpe.index or len(test_sharpe) < 2:
            continue
        rank = int(test_sharpe.index.get_loc(winner)) + 1
        percentile = min(1.0 - 1e-9, max(1e-9, rank / (len(test_sharpe) + 1.0)))
        logits.append(math.log(percentile / (1.0 - percentile)))
    return {
        "available": bool(logits),
        "probability": round(float(np.mean(np.asarray(logits) <= 0.0)), 6) if logits else None,
        "splits": len(logits),
        "median_logit": round(float(np.median(logits)), 6) if logits else None,
        "interpretation": "lower_is_better",
    }


def matrix_diagnostics(matrix: pd.DataFrame, cluster_threshold: float) -> dict[str, Any]:
    clean = matrix.loc[:, matrix.std(ddof=1) > 1e-12].dropna(axis=0, how="any")
    if clean.shape[1] < 2:
        return {"available": False}
    corr = clean.corr().fillna(0.0)
    absolute = corr.abs()
    triangle = absolute.values[np.triu_indices_from(absolute.values, k=1)]
    eigenvalues = np.clip(np.linalg.eigvalsh(corr.values), 0.0, None)
    weights = eigenvalues / max(float(eigenvalues.sum()), 1e-12)
    positive = weights[weights > 1e-12]
    effective_rank = float(math.exp(-float(np.sum(positive * np.log(positive)))))
    remaining = set(corr.columns)
    clusters: list[list[str]] = []
    while remaining:
        seed = remaining.pop()
        cluster = {seed}
        frontier = [seed]
        while frontier:
            current = frontier.pop()
            neighbours = {other for other in list(remaining) if float(absolute.loc[current, other]) >= cluster_threshold}
            remaining.difference_update(neighbours)
            cluster.update(neighbours)
            frontier.extend(neighbours)
        clusters.append(sorted(cluster))
    sizes = sorted((len(cluster) for cluster in clusters), reverse=True)
    return {
        "available": True,
        "mean_absolute_pairwise_correlation": round(float(np.mean(triangle)), 6),
        "median_absolute_pairwise_correlation": round(float(np.median(triangle)), 6),
        "maximum_absolute_pairwise_correlation": round(float(np.max(triangle)), 6),
        "effective_independent_series": round(effective_rank, 6),
        "cluster_threshold": cluster_threshold,
        "cluster_count": len(clusters),
        "largest_cluster_size": sizes[0] if sizes else 0,
        "largest_cluster_share": round((sizes[0] / clean.shape[1]) if sizes else 0.0, 6),
    }


def prior_trial_count() -> int:
    total = 0
    for name in PRIOR_GENERATION_MANIFESTS:
        manifest = read_json(RESEARCH / name)
        total += int(manifest.get("candidate_space_size", 0))
    return total


def historical_sharpes() -> list[float]:
    values: list[float] = []
    for name in PRIOR_GENERATION_LEDGERS:
        ledger = read_json(RESEARCH / name)
        experiments = ledger.get("experiments") if isinstance(ledger.get("experiments"), list) else []
        for row in experiments:
            if not isinstance(row, Mapping):
                continue
            metrics_row = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {}
            value = metrics_row.get("sharpe_excess")
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(numeric):
                values.append(numeric)
    return values


def normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def global_dsr(observed_sharpe: float, returns: pd.Series, global_trials: int, sharpe_std: float) -> dict[str, Any]:
    clean = returns.dropna().astype(float)
    if len(clean) < 30:
        return {"probability": 0.0, "reason": "insufficient_observations"}
    trials = max(2, int(global_trials))
    gamma = 0.5772156649015329
    z1 = math.sqrt(max(0.0, 2.0 * math.log(trials)))
    z2 = z1 - (math.log(max(math.log(trials), 1e-12)) + math.log(4.0 * math.pi)) / max(2.0 * z1, 1e-12)
    expected_max = sharpe_std * ((1.0 - gamma) * z2 + gamma * z1)
    skew = float(clean.skew()) if len(clean) >= 4 else 0.0
    kurt = float(clean.kurt() + 3.0) if len(clean) >= 4 else 3.0
    denominator_sq = max(1e-12, 1.0 - skew * observed_sharpe + ((kurt - 1.0) / 4.0) * observed_sharpe**2)
    z_score = (observed_sharpe - expected_max) * math.sqrt(len(clean) - 1.0) / math.sqrt(denominator_sq)
    return {
        "probability": round(normal_cdf(z_score), 6),
        "expected_max_sharpe": round(float(expected_max), 6),
        "z_score": round(float(z_score), 6),
        "global_trials": trials,
    }


def authorize_single_champion(
    strict_gate_passed: bool,
    median_rank_correlation: float | None,
    unique_fold_winners: int,
    pbo_probability: float | None,
) -> bool:
    return bool(
        strict_gate_passed
        and median_rank_correlation is not None
        and float(median_rank_correlation) >= 0.30
        and int(unique_fold_winners) <= 3
        and pbo_probability is not None
        and float(pbo_probability) <= 0.20
    )


def block_bootstrap_advantage(candidate: pd.Series, baseline: pd.Series) -> dict[str, Any]:
    aligned = pd.concat([candidate.rename("c"), baseline.rename("b")], axis=1).dropna()
    n = len(aligned)
    if n < BOOTSTRAP_BLOCK_DAYS * 5:
        return {"available": False}
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    blocks = [np.arange(start, min(start + BOOTSTRAP_BLOCK_DAYS, n)) for start in range(0, n, BOOTSTRAP_BLOCK_DAYS)]
    cagr_advantages: list[float] = []
    sharpe_advantages: list[float] = []
    for _ in range(BOOTSTRAP_DRAWS):
        pieces: list[np.ndarray] = []
        while sum(len(piece) for piece in pieces) < n:
            pieces.append(blocks[int(rng.integers(0, len(blocks)))])
        indices = np.concatenate(pieces)[:n]
        sample = aligned.iloc[indices]
        years = n / 252.0
        c_total = float((1.0 + sample["c"]).prod())
        b_total = float((1.0 + sample["b"]).prod())
        c_cagr = c_total ** (1.0 / years) - 1.0
        b_cagr = b_total ** (1.0 / years) - 1.0
        c_std = float(sample["c"].std(ddof=1))
        b_std = float(sample["b"].std(ddof=1))
        c_sharpe = float(sample["c"].mean() / c_std * math.sqrt(252.0)) if c_std > 0 else 0.0
        b_sharpe = float(sample["b"].mean() / b_std * math.sqrt(252.0)) if b_std > 0 else 0.0
        cagr_advantages.append(c_cagr - b_cagr)
        sharpe_advantages.append(c_sharpe - b_sharpe)
    cagr_array = np.asarray(cagr_advantages)
    sharpe_array = np.asarray(sharpe_advantages)
    return {
        "available": True,
        "draws": BOOTSTRAP_DRAWS,
        "block_days": BOOTSTRAP_BLOCK_DAYS,
        "probability_cagr_advantage_positive": round(float(np.mean(cagr_array > 0.0)), 6),
        "probability_sharpe_advantage_positive": round(float(np.mean(sharpe_array > 0.0)), 6),
        "cagr_advantage_p05": round(float(np.quantile(cagr_array, 0.05)), 6),
        "cagr_advantage_median": round(float(np.median(cagr_array)), 6),
        "cagr_advantage_p95": round(float(np.quantile(cagr_array, 0.95)), 6),
        "sharpe_advantage_p05": round(float(np.quantile(sharpe_array, 0.05)), 6),
        "sharpe_advantage_median": round(float(np.median(sharpe_array)), 6),
        "sharpe_advantage_p95": round(float(np.quantile(sharpe_array, 0.95)), 6),
    }


def evaluate(prices: pd.DataFrame, trace_path: Path | None = None) -> dict[str, Any]:
    frame = build_features(prices).dropna(subset=["asset_return", "risk_free_return"])
    signals = signal_frame(frame)
    required_signal_days = signals.dropna().index
    frame = frame.loc[required_signal_days]
    signals = signals.loc[required_signal_days]
    regime = deterministic_regime(frame, signals)
    folds = chronological_folds(frame.index)
    if len(folds) < 4:
        raise RuntimeError("Insufficient development history for chronological daily folds")

    experiments: list[dict[str, Any]] = []
    return_columns: dict[str, pd.Series] = {}
    signal_columns: dict[str, pd.Series] = {}
    exposure_columns: dict[str, pd.Series] = {}
    target_columns: dict[str, pd.Series] = {}
    turnover_columns: dict[str, pd.Series] = {}
    fold_sharpes: dict[str, list[float]] = {}

    for candidate in candidate_pool():
        target, score = candidate_exposure(frame, signals, regime, candidate)
        returns, turnover, applied = portfolio_returns(frame, target)
        overall = metrics(returns, turnover, frame["risk_free_return"])
        overall["average_exposure"] = round(float(applied.mean()), 6)
        candidate_folds: list[dict[str, Any]] = []
        sharpe_values: list[float] = []
        for fold_number, (_train, valid) in enumerate(folds, start=1):
            valid_index = frame.index[valid]
            fold_metric = metrics(
                returns.loc[valid_index],
                turnover.loc[valid_index],
                frame.loc[valid_index, "risk_free_return"],
            )
            fold_metric["fold_number"] = fold_number
            candidate_folds.append(fold_metric)
            sharpe_values.append(float(fold_metric.get("sharpe_excess", 0.0)))
        fold_std = float(np.std(sharpe_values, ddof=1)) if len(sharpe_values) > 1 else 0.0
        experiments.append({
            "candidate_id": candidate.candidate_id(),
            "candidate": asdict(candidate),
            "metrics": overall,
            "fold_metrics": candidate_folds,
            "positive_folds": int(sum(value > 0 for value in sharpe_values)),
            "fold_sharpe_std": round(fold_std, 6),
        })
        return_columns[candidate.candidate_id()] = returns
        signal_columns[candidate.candidate_id()] = score
        exposure_columns[candidate.candidate_id()] = applied
        target_columns[candidate.candidate_id()] = target
        turnover_columns[candidate.candidate_id()] = turnover
        fold_sharpes[candidate.candidate_id()] = sharpe_values

    return_matrix = pd.DataFrame(return_columns).dropna(axis=0, how="any")
    signal_matrix = pd.DataFrame(signal_columns).dropna(axis=0, how="any")
    exposure_matrix = pd.DataFrame(exposure_columns).dropna(axis=0, how="any")

    buy_target = pd.Series(1.0, index=frame.index)
    buy_returns, buy_turnover, _ = portfolio_returns(frame, buy_target)
    buy_metrics = metrics(buy_returns, buy_turnover, frame["risk_free_return"])
    trend_target = (frame["spy_ma_gap_200"] > 0.0).astype(float).where(frame.index.dayofweek == 4).ffill().fillna(0.0)
    trend_returns, trend_turnover, _ = portfolio_returns(frame, trend_target)
    trend_metrics = metrics(trend_returns, trend_turnover, frame["risk_free_return"])

    pbo = probability_backtest_overfitting(return_matrix)
    signal_diversity = matrix_diagnostics(signal_matrix, SIGNAL_CLUSTER_THRESHOLD)
    return_diversity = matrix_diagnostics(return_matrix, RETURN_CLUSTER_THRESHOLD)
    exposure_diversity = matrix_diagnostics(exposure_matrix, RETURN_CLUSTER_THRESHOLD)

    rank_table = pd.DataFrame(fold_sharpes).rank(axis=1, ascending=False, method="average")
    fold_rank_corr = rank_table.T.corr(method="spearman") if not rank_table.empty else pd.DataFrame()
    triangle = fold_rank_corr.values[np.triu_indices_from(fold_rank_corr.values, k=1)] if len(fold_rank_corr) > 1 else np.asarray([])
    fold_winners = [str(row.idxmax()) for _, row in pd.DataFrame(fold_sharpes).iterrows()]
    rank_stability = {
        "median_pairwise_fold_rank_correlation": round(float(np.nanmedian(triangle)), 6) if triangle.size else None,
        "mean_pairwise_fold_rank_correlation": round(float(np.nanmean(triangle)), 6) if triangle.size else None,
        "unique_fold_winners": len(set(fold_winners)),
        "fold_winner_ids": fold_winners,
    }

    raw_best = max(experiments, key=lambda row: float(row["metrics"].get("sharpe_excess", -999.0)))
    raw_id = str(raw_best["candidate_id"])
    bootstrap = block_bootstrap_advantage(return_matrix[raw_id], buy_returns.reindex(return_matrix.index))
    prior_trials = prior_trial_count()
    global_trials = prior_trials + EXPECTED_CANDIDATES
    new_sharpes = [float(row["metrics"].get("sharpe_excess", 0.0)) for row in experiments]
    all_sharpes = historical_sharpes() + new_sharpes
    sharpe_std = float(np.std(all_sharpes, ddof=1)) if len(all_sharpes) > 1 else 0.0
    dsr = global_dsr(float(raw_best["metrics"].get("sharpe_excess", 0.0)), return_matrix[raw_id], global_trials, sharpe_std)

    median_rank_corr = rank_stability["median_pairwise_fold_rank_correlation"]
    drawdown_improvement = (
        abs(float(buy_metrics.get("max_drawdown", 0.0))) - abs(float(raw_best["metrics"].get("max_drawdown", 0.0)))
    ) / max(abs(float(buy_metrics.get("max_drawdown", 0.0))), 1e-12)
    checks = {
        "candidate_space_complete": len(experiments) == EXPECTED_CANDIDATES,
        "sealed_holdout_not_downloaded": frame.index.max() < pd.Timestamp(SEALED_HOLDOUT_START),
        "signal_median_correlation_at_most_0_70": float(signal_diversity.get("median_absolute_pairwise_correlation", 1.0)) <= 0.70,
        "signal_effective_rank_at_least_3": float(signal_diversity.get("effective_independent_series", 0.0)) >= 3.0,
        "pbo_at_most_0_20": bool(pbo.get("available")) and float(pbo.get("probability", 1.0)) <= 0.20,
        "global_dsr_at_least_0_95": float(dsr.get("probability", 0.0)) >= 0.95,
        "five_of_six_positive_folds": int(raw_best.get("positive_folds", 0)) >= 5,
        "median_fold_rank_correlation_at_least_0_30": median_rank_corr is not None and float(median_rank_corr) >= 0.30,
        "unique_fold_winners_at_most_3": int(rank_stability.get("unique_fold_winners", 99)) <= 3,
        "bootstrap_cagr_advantage_probability_at_least_0_95": float(bootstrap.get("probability_cagr_advantage_positive", 0.0)) >= 0.95,
        "bootstrap_sharpe_advantage_probability_at_least_0.95": float(bootstrap.get("probability_sharpe_advantage_positive", 0.0)) >= 0.95,
        "drawdown_improvement_at_least_20pct": drawdown_improvement >= 0.20,
    }
    strict_gate_passed = all(checks.values())
    champion_authorized = authorize_single_champion(
        strict_gate_passed,
        median_rank_corr,
        int(rank_stability.get("unique_fold_winners", 99)),
        float(pbo.get("probability")) if pbo.get("probability") is not None else None,
    )
    selected_candidate_id = raw_id if champion_authorized else None

    if trace_path is not None:
        trace = pd.DataFrame(index=frame.index)
        trace["spy_return"] = frame["asset_return"]
        trace["risk_free_return"] = frame["risk_free_return"]
        trace["regime"] = regime
        for source in signals.columns:
            trace[f"source_{source}"] = signals[source]
        for candidate in candidate_pool():
            candidate_id = candidate.candidate_id()
            prefix = candidate.name.replace("-", "_")
            trace[f"{prefix}_score"] = signal_columns[candidate_id]
            trace[f"{prefix}_target"] = target_columns[candidate_id]
            trace[f"{prefix}_applied"] = exposure_columns[candidate_id]
            trace[f"{prefix}_turnover"] = turnover_columns[candidate_id]
            trace[f"{prefix}_return"] = return_columns[candidate_id]
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace.to_csv(trace_path, index_label="date")

    report = {
        "schema_version": PROTOCOL_VERSION,
        "architecture_id": ARCHITECTURE_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "strict_gate_passed_holdout_still_sealed" if strict_gate_passed else "development_gate_not_passed_holdout_still_sealed",
        "research_only": True,
        "live_activation": False,
        "candidate_signature": candidate_signature(),
        "candidate_space_size": EXPECTED_CANDIDATES,
        "experiments_total": len(experiments),
        "development": {
            "start": frame.index.min().date().isoformat(),
            "end": frame.index.max().date().isoformat(),
            "days": len(frame),
            "folds": len(folds),
            "frequency": "daily signals; weekly scheduled rebalance; daily shock gate",
        },
        "holdout": {
            "start": SEALED_HOLDOUT_START,
            "end": SEALED_HOLDOUT_END,
            "status": "sealed_not_downloaded",
            "accessed": False,
            "access_count": 0,
        },
        "architecture_decisions": {
            "g5_v2_is_terminal_reference": True,
            "single_champion_prohibited_when_rank_unstable": True,
            "single_signal_long_flat_architecture_closed": True,
            "hmm_core": False,
            "hmm_reason": "Complexity and state-label instability are not justified before deterministic regimes pass the strict gate.",
            "revised_macro_core": False,
            "macro_reason": "Revised macro series are excluded; only point-in-time market-implied rates/liquidity inputs are active.",
            "options_source": "VIX spot versus VIX3M term structure with fail-closed data-quality gate",
        },
        "prior_trial_count": prior_trials,
        "global_trial_count": global_trials,
        "global_sharpe_cross_section_observations": len(all_sharpes),
        "global_sharpe_cross_section_std": round(sharpe_std, 6),
        "global_multiple_testing": dsr,
        "raw_best_diagnostic_only": raw_best,
        "selected_candidate_id": selected_candidate_id,
        "single_champion_authorized": selected_candidate_id is not None,
        "selection_policy": {
            "no_champion_if_rank_near_random": True,
            "shadow_all_candidates_in_parallel": True,
            "human_approval_required_before_any_holdout_or_live_use": True,
        },
        "baselines": {"buy_and_hold": buy_metrics, "trend_200d_weekly": trend_metrics},
        "pbo": pbo,
        "signal_diversity": signal_diversity,
        "return_diversity": return_diversity,
        "exposure_diversity": exposure_diversity,
        "rank_stability": rank_stability,
        "bootstrap_raw_best_vs_buy_hold": bootstrap,
        "drawdown_improvement_vs_buy_hold": round(float(drawdown_improvement), 6),
        "checks": checks,
        "strict_gate_passed": strict_gate_passed,
        "experiments": experiments,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=START_DATE)
    parser.add_argument("--output", type=Path, default=RESEARCH / "brace_spx_architecture_v2_report.json")
    parser.add_argument("--prices-csv", type=Path, default=None)
    parser.add_argument("--trace-csv", type=Path, default=None)
    args = parser.parse_args()
    prices = load_prices_csv(args.prices_csv) if args.prices_csv else download_prices(args.start, DEVELOPMENT_END_EXCLUSIVE)
    report = evaluate(prices, trace_path=args.trace_csv)
    write_json(args.output, report)
    print(
        f"BRACE-SPX Architecture 2: status={report['status']} "
        f"experiments={report['experiments_total']}/{report['candidate_space_size']} "
        f"champion={report['selected_candidate_id']}"
    )


if __name__ == "__main__":
    main()
