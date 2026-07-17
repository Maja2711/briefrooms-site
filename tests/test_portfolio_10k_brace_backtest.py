from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import portfolio_10k_brace_backtest as bt


def synthetic_prices(weeks=140):
    index = pd.date_range("2020-01-03", periods=weeks, freq="W-FRI")
    a = 100 * np.cumprod(np.repeat(1.002, weeks))
    b = 100 * np.cumprod(np.where(np.arange(weeks) < 70, 1.004, 0.997))
    prices = pd.DataFrame({"A": a, "B": b}, index=index)
    benchmark = pd.Series(100 * np.cumprod(np.repeat(1.0015, weeks)), index=index)
    return prices, benchmark


def test_feature_at_date_is_unchanged_by_future_mutation():
    prices, benchmark = synthetic_prices()
    first = bt.feature_frames(prices, benchmark)
    when = prices.index[80]
    original = first["mom_26"].loc[when].copy()
    mutated = prices.copy()
    mutated.loc[prices.index[100]:, "A"] *= 10
    second = bt.feature_frames(mutated, benchmark)
    pd.testing.assert_series_equal(original, second["mom_26"].loc[when])


def test_weights_do_not_exceed_full_investment():
    target = pd.Series({"A": 0.6, "B": 0.4})
    scores = pd.Series({"A": 90.0, "B": 20.0})
    weights = bt.weights_from_scores(target, scores)
    assert weights.sum() <= 1.0000001
    assert weights["A"] > weights["B"]


def test_transaction_cost_reduces_strategy_result():
    prices, benchmark = synthetic_prices()
    target = pd.Series({"A": 0.5, "B": 0.5})
    free = bt.run_strategy(prices, benchmark, target, "brace", transaction_cost=0.0)
    costly = bt.run_strategy(prices, benchmark, target, "brace", transaction_cost=0.01)
    assert costly.equity.iloc[-1] < free.equity.iloc[-1]


def test_metrics_capture_drawdown_and_turnover():
    prices, benchmark = synthetic_prices()
    target = pd.Series({"A": 0.5, "B": 0.5})
    result = bt.run_strategy(prices, benchmark, target, "brace")
    metrics = bt.metrics(result)
    assert metrics["weeks"] > 50
    assert metrics["annualized_turnover"] >= 0
    assert -1 < metrics["max_drawdown"] <= 0
