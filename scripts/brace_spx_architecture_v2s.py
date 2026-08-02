#!/usr/bin/env python3
"""BRACE-SPX Architecture 2S: governed long / short / flat extension.

Architecture 2S is a new research protocol. It does not rewrite or relabel the
frozen Architecture 2 long/flat evidence. It reuses the point-in-time feature,
multiple-testing and sealed-holdout machinery, but gives the candidate family a
new immutable signature and permits a single SPY exposure in [-1, 1].

No leverage is possible because absolute exposure is capped at 1. The unused
capital earns the observed risk-free return. Short exposure pays a conservative
1% annual borrow charge in addition to the existing turnover cost. The sealed
2022-08-01..2026-07-31 holdout is never downloaded or read.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

import brace_spx_architecture_v2 as base

ARCHITECTURE_ID = "spx-multisignal-regime-a2s"
PROTOCOL_VERSION = "2.0.0"
SHORT_BORROW_ANNUAL = 0.01
SHORT_BORROW_DAILY = (1.0 + SHORT_BORROW_ANNUAL) ** (1.0 / 252.0) - 1.0
RESEARCH = base.RESEARCH

_BASE_CANDIDATE_POOL = base.candidate_pool


def candidate_pool() -> list[base.Candidate]:
    """Return a fresh candidate family with a new immutable signature."""
    result: list[base.Candidate] = []
    for candidate in _BASE_CANDIDATE_POOL():
        result.append(
            base.Candidate(
                name=f"{candidate.name}-lsf",
                signal_sources=tuple(candidate.signal_sources),
                allocation=candidate.allocation,
                regime_policy=candidate.regime_policy,
                weekly_rebalance=candidate.weekly_rebalance,
                daily_shock_gate=candidate.daily_shock_gate,
                max_exposure=1.0,
            )
        )
    ids = [item.candidate_id() for item in result]
    if len(result) != base.EXPECTED_CANDIDATES or len(set(ids)) != len(result):
        raise RuntimeError("Architecture 2S must contain ten unique candidates")
    return result


def score_to_exposure(score: pd.Series, allocation: str) -> pd.Series:
    """Map a signed score to conservative long, short or flat exposure."""
    if allocation == "defensive":
        levels = np.select(
            [
                score <= -0.65,
                score <= -0.30,
                score <= 0.10,
                score <= 0.35,
                score <= 0.65,
            ],
            [-0.50, -0.25, 0.0, 0.35, 0.65],
            default=1.0,
        )
    else:
        levels = np.select(
            [
                score <= -0.65,
                score <= -0.35,
                score <= -0.10,
                score <= 0.10,
                score <= 0.35,
                score <= 0.65,
            ],
            [-1.0, -0.50, -0.25, 0.0, 0.50, 0.75],
            default=1.0,
        )
    return pd.Series(levels, index=score.index, dtype=float)


def _signed_scale(exposure: pd.Series, long_scale: pd.Series | float, short_scale: pd.Series | float) -> pd.Series:
    long_part = exposure.clip(lower=0.0) * long_scale
    short_part = exposure.clip(upper=0.0) * short_scale
    return pd.Series(long_part + short_part, index=exposure.index, dtype=float)


def candidate_exposure(
    frame: pd.DataFrame,
    signals: pd.DataFrame,
    regime: pd.Series,
    candidate: base.Candidate,
) -> tuple[pd.Series, pd.Series]:
    score = base._combine_candidate_signal(signals, candidate)
    desired = score_to_exposure(score, candidate.allocation)

    if candidate.regime_policy == "deterministic_regime":
        long_scale = regime.map(
            {"low_vol": 1.0, "high_vol": 0.65, "panic": 0.0, "recovery": 0.80}
        ).astype(float)
        short_scale = regime.map(
            {"low_vol": 0.75, "high_vol": 0.90, "panic": 1.0, "recovery": 0.40}
        ).astype(float)
        desired = _signed_scale(desired, long_scale, short_scale)
    elif candidate.regime_policy in {"liquidity", "options"}:
        policy_signal = signals[candidate.regime_policy].clip(-1.0, 1.0)
        confidence = (0.45 + 0.55 * policy_signal.abs()).clip(0.45, 1.0)
        desired = desired * confidence

    desired = pd.Series(desired, index=frame.index, dtype=float).clip(-1.0, 1.0)
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
    # A shock can remove a long and establish only a modest short. It never
    # jumps directly from full long to full short in one day.
    exposure.loc[shock] = np.minimum(exposure.loc[shock], -0.25)
    exposure.loc[extreme] = np.minimum(exposure.loc[extreme], -0.50)
    return exposure.clip(-1.0, 1.0), score


def portfolio_returns(
    frame: pd.DataFrame,
    target_exposure: pd.Series,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    target = target_exposure.reindex(frame.index).ffill().fillna(0.0).clip(-1.0, 1.0)
    applied = target.shift(1).fillna(0.0)
    turnover = applied.diff().abs().fillna(applied.abs())
    asset = frame["asset_return"].fillna(0.0)
    risk_free = frame["risk_free_return"].fillna(0.0)
    cash_weight = (1.0 - applied.abs()).clip(0.0, 1.0)
    short_borrow = applied.clip(upper=0.0).abs() * SHORT_BORROW_DAILY
    returns = (
        applied * asset
        + cash_weight * risk_free
        - short_borrow
        - turnover * base.COST_PER_UNIT_TURNOVER
    )
    return returns.astype(float), turnover.astype(float), applied.astype(float)


def evaluate(prices: pd.DataFrame, trace_path: Path | None = None) -> dict:
    report = base.evaluate(prices, trace_path=trace_path)
    report["schema_version"] = PROTOCOL_VERSION
    report["architecture_id"] = ARCHITECTURE_ID
    report["mandate"] = {
        "position_set": "long_short_flat",
        "minimum_exposure": -1.0,
        "maximum_exposure": 1.0,
        "long_allowed": True,
        "short_allowed": True,
        "flat_allowed": True,
        "leverage_allowed": False,
        "orders_allowed": False,
        "short_borrow_annual": SHORT_BORROW_ANNUAL,
    }
    report.setdefault("architecture_decisions", {}).update(
        {
            "architecture_2_long_flat_preserved_as_frozen_reference": True,
            "architecture_2s_is_new_candidate_family": True,
            "position_set": "long_short_flat",
            "single_asset_gross_exposure_cap": 1.0,
            "short_borrow_cost_included": True,
        }
    )
    report["research_protocol_note"] = (
        "Architecture 2S is independently revalidated. No metric from the "
        "frozen long/flat Architecture 2 is relabelled as long/short."
    )
    for row in report.get("experiments", []):
        row.setdefault("candidate", {})["mandate"] = "long_short_flat"
    return report


def install_patches() -> None:
    base.ARCHITECTURE_ID = ARCHITECTURE_ID
    base.PROTOCOL_VERSION = PROTOCOL_VERSION
    base.candidate_pool = candidate_pool
    base._score_to_exposure = score_to_exposure
    base.candidate_exposure = candidate_exposure
    base.portfolio_returns = portfolio_returns


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=base.START_DATE)
    parser.add_argument(
        "--output",
        type=Path,
        default=RESEARCH / "brace_spx_architecture_v2s_report.json",
    )
    parser.add_argument("--prices-csv", type=Path, default=None)
    parser.add_argument("--trace-csv", type=Path, default=None)
    args = parser.parse_args()

    install_patches()
    prices = (
        base.load_prices_csv(args.prices_csv)
        if args.prices_csv
        else base.download_prices(args.start, base.DEVELOPMENT_END_EXCLUSIVE)
    )
    report = evaluate(prices, trace_path=args.trace_csv)
    base.write_json(args.output, report)
    print(
        f"BRACE-SPX Architecture 2S: status={report['status']} "
        f"experiments={report['experiments_total']}/{report['candidate_space_size']} "
        f"champion={report['selected_candidate_id']}"
    )


if __name__ == "__main__":
    main()
