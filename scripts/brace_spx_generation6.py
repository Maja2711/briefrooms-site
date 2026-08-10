#!/usr/bin/env python3
"""BRACE-SPX Generation 6: orthogonal four-family research.

Generation 6 deliberately tests a small, predeclared candidate space built from
four economically distinct point-in-time market families:
rates, liquidity/credit, options/VIX and price/trend.

It is not a parameter sweep. Every candidate shares the same exposure geometry
and governance. The sealed 2022-08-01..2026-07-31 holdout is never downloaded,
read or used for design. No broker orders are possible.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

import brace_spx_architecture_v2 as base
import brace_spx_architecture_v2s as a2s

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "data" / "research"

GENERATION_ID = "spx-orthogonal-core-v6"
ARCHITECTURE_ID = GENERATION_ID
PROTOCOL_VERSION = "6.0.0"
EXPECTED_CANDIDATES = 8
DEVELOPMENT_END_EXCLUSIVE = base.DEVELOPMENT_END_EXCLUSIVE
SEALED_HOLDOUT_START = base.SEALED_HOLDOUT_START
SEALED_HOLDOUT_END = base.SEALED_HOLDOUT_END
SHADOW_START = base.SHADOW_START
TARGET_SYMBOL = "SPY"
RISK_FREE_SYMBOL = base.RISK_FREE_SYMBOL
VIX3M_SYMBOL = base.VIX3M_SYMBOL
SHORT_BORROW_ANNUAL = a2s.SHORT_BORROW_ANNUAL
SOURCE_FAMILIES = ("price_trend", "rates", "liquidity", "options_vix")
SHADOW_WARMUP_OBSERVATIONS = 70

SYMBOLS = (
    "SPY", "^VIX", VIX3M_SYMBOL, "^TNX", "TLT", "HYG", "LQD", "UUP", "RSP", RISK_FREE_SYMBOL
)


def required_symbols() -> list[str]:
    return list(SYMBOLS)


def candidate_pool() -> list[base.Candidate]:
    specs = [
        ("g6-price-trend", ("price_trend",)),
        ("g6-rates", ("rates",)),
        ("g6-liquidity", ("liquidity",)),
        ("g6-options-vix", ("options_vix",)),
        ("g6-trend-rates", ("price_trend", "rates")),
        ("g6-trend-liquidity", ("price_trend", "liquidity")),
        ("g6-defensive-macro", ("rates", "liquidity", "options_vix")),
        ("g6-equal-four", SOURCE_FAMILIES),
    ]
    result = [
        base.Candidate(
            name=name,
            signal_sources=tuple(sources),
            allocation="graded",
            regime_policy="orthogonal_v6",
            weekly_rebalance=True,
            daily_shock_gate=True,
            max_exposure=1.0,
        )
        for name, sources in specs
    ]
    ids = [item.candidate_id() for item in result]
    if len(result) != EXPECTED_CANDIDATES or len(set(ids)) != EXPECTED_CANDIDATES:
        raise RuntimeError("Generation 6 must contain exactly eight unique candidates")
    used = {source for item in result for source in item.signal_sources}
    if used != set(SOURCE_FAMILIES):
        raise RuntimeError(f"Generation 6 source families changed: {sorted(used)}")
    return result


def candidate_signature(candidates: Sequence[base.Candidate] | None = None) -> str:
    return base.candidate_signature(list(candidates or candidate_pool()))


def _series(prices: pd.DataFrame, symbol: str) -> pd.Series:
    if symbol not in prices:
        return pd.Series(np.nan, index=prices.index, dtype=float)
    return pd.to_numeric(prices[symbol], errors="coerce").reindex(prices.index).ffill()


def _tanh(series: pd.Series, scale: float) -> pd.Series:
    return pd.Series(np.tanh(series.astype(float) / max(scale, 1e-12)), index=series.index, dtype=float)


def _mean(parts: Sequence[pd.Series]) -> pd.Series:
    return pd.concat(list(parts), axis=1).mean(axis=1).clip(-1.0, 1.0)


def build_features(prices: pd.DataFrame, research_mode: bool = True) -> pd.DataFrame:
    prices = prices.copy()
    prices.index = pd.to_datetime(prices.index).tz_localize(None)
    prices = prices.sort_index()
    if prices.empty:
        raise RuntimeError("Generation 6 price frame is empty")
    if research_mode and prices.index.max() >= pd.Timestamp(SEALED_HOLDOUT_START):
        raise RuntimeError("Generation 6 research input entered the sealed holdout")
    if not research_mode and prices.index.min() < pd.Timestamp(SHADOW_START):
        raise RuntimeError("Generation 6 shadow input entered the sealed holdout")

    spy = _series(prices, "SPY")
    ret = spy.pct_change(fill_method=None)
    frame = pd.DataFrame(index=spy.index)
    frame["asset_return"] = ret

    frame["spy_momentum_21"] = spy / spy.shift(21) - 1.0
    frame["spy_momentum_63"] = spy / spy.shift(63) - 1.0
    frame["spy_ma_gap_20"] = spy / spy.rolling(20, min_periods=20).mean() - 1.0
    frame["spy_ma_gap_63"] = spy / spy.rolling(63, min_periods=63).mean() - 1.0
    # Frozen benchmark only; never an input to a Generation 6 candidate.
    frame["spy_ma_gap_200"] = spy / spy.rolling(200, min_periods=150).mean() - 1.0
    frame["spy_vol_20"] = ret.rolling(20, min_periods=20).std(ddof=1) * math.sqrt(252.0)
    high63 = spy.rolling(63, min_periods=63).max()
    frame["spy_drawdown_63"] = spy / high63 - 1.0

    vix = _series(prices, "^VIX")
    vix3m = _series(prices, VIX3M_SYMBOL)
    frame["vix_level"] = vix
    frame["vix_change_5"] = vix / vix.shift(5) - 1.0
    frame["vix_change_21"] = vix / vix.shift(21) - 1.0
    frame["vix_term_ratio"] = vix / vix3m - 1.0

    tnx = _series(prices, "^TNX")
    tlt = _series(prices, "TLT")
    frame["tnx_change_21"] = tnx - tnx.shift(21)
    frame["tnx_change_63"] = tnx - tnx.shift(63)
    frame["tlt_momentum_63"] = tlt / tlt.shift(63) - 1.0

    hyg = _series(prices, "HYG")
    lqd = _series(prices, "LQD")
    credit = hyg / lqd
    frame["credit_ratio_21"] = credit / credit.shift(21) - 1.0
    frame["credit_ratio_63"] = credit / credit.shift(63) - 1.0
    uup = _series(prices, "UUP")
    frame["dollar_momentum_63"] = uup / uup.shift(63) - 1.0

    annual_yield = _series(prices, RISK_FREE_SYMBOL).clip(lower=0.0)
    frame["risk_free_return"] = (1.0 + annual_yield / 100.0) ** (1.0 / 252.0) - 1.0
    return frame.replace([np.inf, -np.inf], np.nan)


def signal_frame(frame: pd.DataFrame) -> pd.DataFrame:
    signals = pd.DataFrame(index=frame.index)
    # Fixed normalization scales, not a searched grid.
    signals["price_trend"] = _mean([
        _tanh(frame["spy_ma_gap_20"], 0.04),
        _tanh(frame["spy_ma_gap_63"], 0.08),
        _tanh(frame["spy_momentum_21"], 0.08),
        _tanh(frame["spy_momentum_63"], 0.15),
    ])
    signals["rates"] = _mean([
        -_tanh(frame["tnx_change_21"], 0.35),
        -_tanh(frame["tnx_change_63"], 0.65),
        _tanh(frame["tlt_momentum_63"], 0.08),
    ])
    signals["liquidity"] = _mean([
        _tanh(frame["credit_ratio_21"], 0.025),
        _tanh(frame["credit_ratio_63"], 0.05),
        -_tanh(frame["dollar_momentum_63"], 0.06),
    ])
    signals["options_vix"] = _mean([
        -_tanh(frame["vix_level"] - 20.0, 7.0),
        -_tanh(frame["vix_change_5"], 0.20),
        -_tanh(frame["vix_change_21"], 0.35),
        -_tanh(frame["vix_term_ratio"], 0.10),
    ])
    return signals.clip(-1.0, 1.0)


def deterministic_regime(frame: pd.DataFrame, signals: pd.DataFrame) -> pd.Series:
    stress = (
        (signals["liquidity"] <= -0.55)
        | (signals["options_vix"] <= -0.55)
        | (frame["spy_vol_20"] >= 0.32)
        | (frame["spy_drawdown_63"] <= -0.12)
    )
    risk_on = (
        (signals["price_trend"] >= 0.25)
        & (signals["liquidity"] > -0.20)
        & (signals["options_vix"] > -0.25)
    )
    values = np.select([stress, risk_on], ["stress", "risk_on"], default="neutral")
    return pd.Series(values, index=frame.index, dtype="object")


def _candidate_score(signals: pd.DataFrame, candidate: base.Candidate) -> pd.Series:
    missing = [source for source in candidate.signal_sources if source not in signals]
    if missing:
        raise RuntimeError(f"Generation 6 candidate uses unknown sources: {missing}")
    return signals[list(candidate.signal_sources)].mean(axis=1).clip(-1.0, 1.0)


def _score_to_exposure(score: pd.Series) -> pd.Series:
    # One immutable geometry for all candidates: test information, not thresholds.
    levels = np.select(
        [score <= -0.45, score <= -0.15, score < 0.15, score < 0.45],
        [-1.0, -0.50, 0.0, 0.50],
        default=1.0,
    )
    return pd.Series(levels, index=score.index, dtype=float)


def candidate_exposure(
    frame: pd.DataFrame,
    signals: pd.DataFrame,
    regime: pd.Series,
    candidate: base.Candidate,
) -> tuple[pd.Series, pd.Series]:
    score = _candidate_score(signals, candidate)
    desired = _score_to_exposure(score)
    weekly = desired.where(frame.index.dayofweek == 4).ffill().fillna(0.0)
    shock = (
        (signals["liquidity"] <= -0.65)
        | (signals["options_vix"] <= -0.65)
        | (frame["spy_vol_20"] >= 0.40)
    )
    exposure = weekly.copy()
    # A shock may remove a long, but may not invent a short by itself.
    exposure.loc[shock] = np.minimum(exposure.loc[shock], 0.0)
    return exposure.clip(-1.0, 1.0), score


def install_patches() -> None:
    base.ARCHITECTURE_ID = ARCHITECTURE_ID
    base.PROTOCOL_VERSION = PROTOCOL_VERSION
    base.EXPECTED_CANDIDATES = EXPECTED_CANDIDATES
    base.required_symbols = required_symbols
    base.build_features = build_features
    base.signal_frame = signal_frame
    base.deterministic_regime = deterministic_regime
    base.candidate_pool = candidate_pool
    base.candidate_exposure = candidate_exposure
    base.portfolio_returns = a2s.portfolio_returns


def evaluate(prices: pd.DataFrame, trace_path: Path | None = None) -> dict[str, Any]:
    install_patches()
    report = base.evaluate(prices, trace_path=trace_path)
    report["schema_version"] = PROTOCOL_VERSION
    report["generation_id"] = GENERATION_ID
    report["architecture_id"] = ARCHITECTURE_ID
    report["candidate_signature"] = candidate_signature()
    report["design"] = {
        "scope": "orthogonal_source_families_not_parameter_sweep",
        "source_families": list(SOURCE_FAMILIES),
        "source_family_count": len(SOURCE_FAMILIES),
        "candidate_space_size": EXPECTED_CANDIDATES,
        "shared_exposure_geometry": True,
        "max_candidate_lookback_sessions": 63,
        "trend_200d_used_only_as_benchmark": True,
        "derived_from_best_g5_parameters": False,
        "holdout_used_for_design": False,
    }
    report["mandate"] = {
        "position_set": "long_short_flat",
        "minimum_exposure": -1.0,
        "maximum_exposure": 1.0,
        "leverage_allowed": False,
        "orders_allowed": False,
        "short_borrow_annual": SHORT_BORROW_ANNUAL,
    }
    report["governance_v6"] = {
        "human_approval_required_before_holdout": True,
        "holdout_must_remain_sealed": True,
        "candidate_definitions_predeclared": True,
        "no_parameter_mutation_after_first_run": True,
        "all_candidates_shadowed_in_parallel": True,
        "no_live_orders": True,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=base.START_DATE)
    parser.add_argument("--prices-csv", type=Path, default=None)
    parser.add_argument("--trace-csv", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=RESEARCH / "brace_spx_generation6_report.json")
    args = parser.parse_args()

    install_patches()
    prices = base.load_prices_csv(args.prices_csv) if args.prices_csv else base.download_prices(args.start, DEVELOPMENT_END_EXCLUSIVE)
    report = evaluate(prices, trace_path=args.trace_csv)
    base.write_json(args.output, report)
    print(
        f"BRACE-SPX G6 status={report['status']} "
        f"experiments={report['experiments_total']}/{report['candidate_space_size']} "
        f"strict_gate={report['strict_gate_passed']} champion={report.get('selected_candidate_id')}"
    )


if __name__ == "__main__":
    main()
