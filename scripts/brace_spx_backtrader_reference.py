#!/usr/bin/env python3
"""Independent Backtrader reference for BRACE-SPX Architecture 2.

The module intentionally does not import BRACE-SPX research code. It independently
recomputes the five source signals, deterministic regime, weekly rebalance and
daily shock gate for the predeclared `diverse-equal` reference candidate, then
executes that target path in Backtrader. It compares results with a primary-engine
trace produced from the same raw price matrix.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SEALED_HOLDOUT_START = pd.Timestamp("2022-08-01")
COST = 0.0005
SECTORS = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")


def _series(prices: pd.DataFrame, symbol: str) -> pd.Series:
    return pd.to_numeric(prices[symbol], errors="coerce").reindex(prices.index).ffill()


def _tanh(series: pd.Series, scale: float) -> pd.Series:
    return pd.Series(np.tanh(series.astype(float) / max(scale, 1e-12)), index=series.index)


def _mean(parts: list[pd.Series]) -> pd.Series:
    return pd.concat(parts, axis=1).mean(axis=1).clip(-1.0, 1.0)


def independent_target(prices: pd.DataFrame) -> pd.DataFrame:
    if prices.index.max() >= SEALED_HOLDOUT_START:
        raise RuntimeError("Independent validator received sealed-holdout data")
    spy = _series(prices, "SPY")
    ret = spy.pct_change(fill_method=None)
    frame = pd.DataFrame(index=prices.index)
    frame["close"] = spy
    frame["spy_return"] = ret
    frame["ma50"] = spy / spy.rolling(50, min_periods=37).mean() - 1.0
    frame["ma200"] = spy / spy.rolling(200, min_periods=150).mean() - 1.0
    frame["mom63"] = spy / spy.shift(63) - 1.0
    frame["mom252"] = spy / spy.shift(252) - 1.0
    frame["vol20"] = ret.rolling(20, min_periods=15).std(ddof=1) * math.sqrt(252.0)
    frame["drawdown126"] = spy / spy.rolling(126, min_periods=63).max() - 1.0

    vix = _series(prices, "^VIX")
    vix3m = _series(prices, "^VIX3M")
    frame["vix"] = vix
    frame["vix_change5"] = vix / vix.shift(5) - 1.0
    frame["vix_change21"] = vix / vix.shift(21) - 1.0
    frame["vix_term"] = vix / vix3m - 1.0

    tnx = _series(prices, "^TNX")
    tlt = _series(prices, "TLT")
    hyg = _series(prices, "HYG")
    lqd = _series(prices, "LQD")
    uup = _series(prices, "UUP")
    rsp = _series(prices, "RSP")
    credit = hyg / lqd
    frame["tnx21"] = tnx - tnx.shift(21)
    frame["tnx63"] = tnx - tnx.shift(63)
    frame["tlt63"] = tlt / tlt.shift(63) - 1.0
    frame["credit21"] = credit / credit.shift(21) - 1.0
    frame["credit63"] = credit / credit.shift(63) - 1.0
    frame["uup63"] = uup / uup.shift(63) - 1.0
    frame["rsp63"] = (rsp / spy) / (rsp / spy).shift(63) - 1.0

    sector_prices = prices[list(SECTORS)].reindex(prices.index).ffill()
    sector_mom63 = sector_prices / sector_prices.shift(63) - 1.0
    frame["breadth50"] = (sector_prices > sector_prices.rolling(50, min_periods=35).mean()).mean(axis=1)
    frame["breadth200"] = (sector_prices > sector_prices.rolling(200, min_periods=150).mean()).mean(axis=1)
    frame["sector_mom63"] = sector_mom63.mean(axis=1)
    frame["sector_disp63"] = sector_mom63.std(axis=1, ddof=1)

    annual_yield = _series(prices, "^IRX").clip(lower=0.0)
    frame["rf"] = (1.0 + annual_yield / 100.0) ** (1.0 / 252.0) - 1.0

    signals = pd.DataFrame(index=frame.index)
    signals["trend"] = _mean([
        _tanh(frame["ma50"], 0.05), _tanh(frame["ma200"], 0.10),
        _tanh(frame["mom63"], 0.12), _tanh(frame["mom252"], 0.25)
    ])
    signals["breadth"] = _mean([
        (frame["breadth50"] - 0.5) * 2.0,
        (frame["breadth200"] - 0.5) * 2.0,
        _tanh(frame["sector_mom63"], 0.12),
        _tanh(frame["rsp63"], 0.06),
        -_tanh(frame["sector_disp63"], 0.10)
    ])
    signals["liquidity"] = _mean([
        _tanh(frame["credit21"], 0.03), _tanh(frame["credit63"], 0.05),
        -_tanh(frame["uup63"], 0.07), -_tanh(frame["tnx63"], 0.75)
    ])
    signals["options"] = _mean([
        -_tanh(frame["vix"] - 21.0, 8.0), -_tanh(frame["vix_change5"], 0.25),
        -_tanh(frame["vix_change21"], 0.40), -_tanh(frame["vix_term"], 0.12)
    ])
    signals["rates"] = _mean([
        -_tanh(frame["tnx21"], 0.40), -_tanh(frame["tnx63"], 0.75),
        _tanh(frame["tlt63"], 0.08), -_tanh(frame["uup63"], 0.08)
    ])
    score = signals.mean(axis=1).clip(-1.0, 1.0)

    panic = (
        (signals["liquidity"] <= -0.55) | (signals["options"] <= -0.60)
        | (frame["vol20"] >= 0.35) | (frame["drawdown126"] <= -0.18)
    )
    high_vol = (
        (frame["vol20"] >= frame["vol20"].rolling(252, min_periods=126).median() * 1.20)
        | (frame["vix"] >= 25.0)
    ) & ~panic
    recent_panic = panic.shift(1).rolling(20, min_periods=1).max().fillna(0.0).astype(bool)
    recovery = recent_panic & (signals["trend"] > 0.05) & (signals["liquidity"] > -0.20) & ~panic
    regime = pd.Series("low_vol", index=frame.index, dtype="object")
    regime.loc[high_vol] = "high_vol"
    regime.loc[panic] = "panic"
    regime.loc[recovery] = "recovery"

    desired = pd.Series(np.select(
        [score <= -0.45, score <= -0.10, score <= 0.20, score <= 0.50],
        [0.0, 0.25, 0.50, 0.75], default=1.0
    ), index=frame.index, dtype=float)
    desired *= regime.map({"low_vol": 1.0, "high_vol": 0.65, "panic": 0.15, "recovery": 0.80}).astype(float)
    target = desired.where(frame.index.dayofweek == 4).ffill().fillna(0.0)
    shock = (
        (signals["liquidity"] <= -0.65) | (signals["options"] <= -0.70)
        | (frame["vix_change5"] >= 0.60) | (frame["vol20"] >= 0.45)
    )
    extreme = (
        (signals["liquidity"] <= -0.80) | (signals["options"] <= -0.85)
        | (frame["drawdown126"] <= -0.25)
    )
    target.loc[shock] = np.minimum(target.loc[shock], 0.25)
    target.loc[extreme] = 0.0
    return pd.DataFrame({
        "close": frame["close"], "rf": frame["rf"], "score": score,
        "target": target.clip(0.0, 1.0), "regime": regime
    }).dropna()


def run_backtrader(reference: pd.DataFrame) -> pd.Series:
    import backtrader as bt

    class SignalFeed(bt.feeds.PandasData):
        lines = ("target", "rf",)
        params = (
            ("datetime", None), ("open", "close"), ("high", "close"),
            ("low", "close"), ("close", "close"), ("volume", -1),
            ("openinterest", -1), ("target", "target"), ("rf", "rf")
        )

    class Strategy(bt.Strategy):
        def __init__(self):
            self.values: list[tuple[pd.Timestamp, float]] = []

        def next(self):
            cash = float(self.broker.getcash())
            rf = float(self.data.rf[0])
            if cash > 0 and rf != 0:
                self.broker.add_cash(cash * rf)
            self.order_target_percent(target=float(self.data.target[0]))
            date = pd.Timestamp(self.data.datetime.date(0))
            self.values.append((date, float(self.broker.getvalue())))

    cerebro = bt.Cerebro(stdstats=False)
    cerebro.broker.setcash(1_000_000.0)
    cerebro.broker.setcommission(commission=COST)
    cerebro.broker.set_coc(True)
    cerebro.adddata(SignalFeed(dataname=reference))
    cerebro.addstrategy(Strategy)
    result = cerebro.run(runonce=False)[0]
    values = pd.Series({date: value for date, value in result.values}, dtype=float).sort_index()
    return values.pct_change(fill_method=None).fillna(0.0)


def reconcile(prices: pd.DataFrame, primary_trace: pd.DataFrame) -> dict[str, Any]:
    reference = independent_target(prices)
    primary_trace.index = pd.to_datetime(primary_trace.index).tz_localize(None)
    joined = pd.concat([
        reference["target"].rename("independent_target"),
        primary_trace["diverse_equal_target"].rename("primary_target"),
        primary_trace["diverse_equal_return"].rename("primary_return")
    ], axis=1).dropna()
    target_diff = (joined["independent_target"] - joined["primary_target"]).abs()
    bt_returns = run_backtrader(reference.loc[joined.index]).rename("backtrader_return")
    joined = joined.join(bt_returns, how="inner").dropna()
    return_diff = (joined["backtrader_return"] - joined["primary_return"]).abs()
    correlation = float(joined[["backtrader_return", "primary_return"]].corr().iloc[0, 1])
    primary_total = float((1.0 + joined["primary_return"]).prod() - 1.0)
    backtrader_total = float((1.0 + joined["backtrader_return"]).prod() - 1.0)
    checks = {
        "target_path_exact": float(target_diff.max()) <= 1e-10,
        "return_correlation_at_least_0_995": correlation >= 0.995,
        "total_return_difference_at_most_1pct": abs(primary_total - backtrader_total) <= 0.01
    }
    return {
        "schema_version": "1.0.0",
        "engine": "Backtrader independent reference",
        "candidate": "diverse-equal",
        "observations": len(joined),
        "maximum_target_difference": round(float(target_diff.max()), 12),
        "maximum_daily_return_difference": round(float(return_diff.max()), 8),
        "return_correlation": round(correlation, 8),
        "primary_total_return": round(primary_total, 6),
        "backtrader_total_return": round(backtrader_total, 6),
        "total_return_difference": round(backtrader_total - primary_total, 6),
        "checks": checks,
        "passed": all(checks.values()),
        "holdout_accessed": False
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prices-csv", type=Path, required=True)
    parser.add_argument("--primary-trace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prices = pd.read_csv(args.prices_csv, index_col=0, parse_dates=True)
    prices.index = pd.to_datetime(prices.index).tz_localize(None)
    trace = pd.read_csv(args.primary_trace, index_col=0, parse_dates=True)
    report = reconcile(prices.sort_index(), trace.sort_index())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Backtrader reconciliation: passed={report['passed']} observations={report['observations']}")
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
