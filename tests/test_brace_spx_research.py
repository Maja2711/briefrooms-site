from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "brace_spx_research.py"
SPEC = importlib.util.spec_from_file_location("brace_spx_research", MODULE_PATH)
brace = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = brace
assert SPEC.loader
SPEC.loader.exec_module(brace)


def synthetic_prices(periods: int = 7000) -> pd.DataFrame:
    dates = pd.bdate_range("2000-01-03", periods=periods)
    rng = np.random.default_rng(123)
    market_shock = rng.normal(0.00025, 0.009, periods)
    spy = 100.0 * np.exp(np.cumsum(market_shock))
    data = {"SPY": spy}
    for symbol in ["^VIX", "^TNX", "TLT", "HYG", "LQD", "UUP", "RSP", *brace.SECTOR_SYMBOLS]:
        if symbol == "^VIX":
            values = np.maximum(10.0, 22.0 - 350.0 * pd.Series(market_shock).rolling(20, min_periods=1).mean().to_numpy())
        elif symbol == "^TNX":
            values = np.maximum(0.5, 4.0 + np.cumsum(rng.normal(0.0, 0.004, periods)))
        else:
            values = 50.0 * np.exp(np.cumsum(0.45 * market_shock + rng.normal(0.0001, 0.006, periods)))
        data[symbol] = values
    return pd.DataFrame(data, index=dates)


def candidate() -> brace.Candidate:
    return brace.Candidate(
        family="logistic",
        feature_set="core",
        threshold_high=0.60,
        threshold_low=0.50,
        max_exposure=1.0,
        volatility_target=0.16,
        params={"C": 0.2, "class_weight": None},
    )


def test_chronological_folds_are_purged_and_ordered() -> None:
    index = pd.date_range("2000-01-31", periods=240, freq="ME")
    folds = brace.chronological_folds(index)
    assert folds
    for train, valid in folds:
        assert train.max() + brace.PURGE_MONTHS < valid.min()
        assert train.max() < valid.min()
        assert len(valid) == brace.VALIDATION_MONTHS


def test_exposure_never_uses_leverage() -> None:
    index = pd.date_range("2020-01-31", periods=8, freq="ME")
    probability = pd.Series([0.2, 0.55, 0.7, 0.95, 0.51, 0.49, 0.8, 0.1], index=index)
    vol = pd.Series([0.05, 0.10, 0.20, 0.40, 0.16, 0.18, 0.12, 0.25], index=index)
    exposure = brace.probabilities_to_exposure(probability, vol, candidate())
    assert exposure.min() >= 0.0
    assert exposure.max() <= 1.0


def test_strategy_applies_signal_to_next_month() -> None:
    index = pd.date_range("2022-01-31", periods=4, freq="ME")
    asset_return = pd.Series([0.10, -0.10, 0.20, 0.05], index=index)
    exposure = pd.Series([1.0, 0.0, 1.0, 1.0], index=index)
    returns, turnover = brace.strategy_returns(asset_return, exposure, cost=0.0)
    assert returns.iloc[0] == 0.0
    assert returns.iloc[1] == -0.10
    assert returns.iloc[2] == 0.0
    assert returns.iloc[3] == 0.05
    assert turnover.max() <= 1.0


def test_wow_gate_rejects_cosmetic_change() -> None:
    benchmark = {
        "cagr": 0.10,
        "sharpe_zero_rf": 0.80,
        "max_drawdown": -0.20,
        "calmar": 0.50,
        "positive_year_ratio": 0.70,
        "annualized_turnover": 0.20,
    }
    cosmetic = {
        "cagr": 0.101,
        "sharpe_zero_rf": 0.81,
        "max_drawdown": -0.20,
        "calmar": 0.51,
        "positive_year_ratio": 0.70,
        "annualized_turnover": 0.40,
    }
    assert brace.wow_gate(cosmetic, benchmark)["passed"] is False


def test_research_writes_auditable_outputs(tmp_path: Path) -> None:
    prices = synthetic_prices()
    output = tmp_path / "research.json"
    ledger = tmp_path / "ledger.json"
    report = brace.run_research(prices, budget=2, output_path=output, ledger_path=ledger, seed=5)
    assert output.exists()
    assert ledger.exists()
    assert report["research_only"] is True
    assert report["live_activation"] is False
    assert report["experiments_total"] == 2
    stored = json.loads(output.read_text(encoding="utf-8"))
    assert stored["governance"]["no_lookahead"] is True
    assert stored["sealed_holdout_start"] > stored["development_end"]
