from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import brace_spx_architecture_v2 as architecture


def test_candidate_space_is_fixed_and_unique():
    pool = architecture.candidate_pool()
    assert len(pool) == 10
    assert len({candidate.candidate_id() for candidate in pool}) == 10
    assert architecture.candidate_signature(pool) == "c5f4ff626f96d29274f0a695b821c7a5a49ef4c2230fa890710d8f5cede990d9"


def test_single_champion_is_forbidden_when_rank_is_unstable():
    assert not architecture.authorize_single_champion(True, 0.29, 2, 0.10)
    assert not architecture.authorize_single_champion(True, 0.50, 4, 0.10)
    assert not architecture.authorize_single_champion(True, 0.50, 2, 0.21)
    assert architecture.authorize_single_champion(True, 0.50, 2, 0.10)


def test_research_download_cannot_enter_holdout():
    with pytest.raises(RuntimeError, match="sealed holdout"):
        architecture.download_prices(end="2022-08-02")


def test_daily_shock_gate_can_override_weekly_target():
    index = pd.bdate_range("2021-01-01", periods=12)
    frame = pd.DataFrame({
        "vix_change_5": 0.0,
        "spy_vol_20": 0.15,
        "spy_drawdown_126": -0.02
    }, index=index)
    signals = pd.DataFrame({
        "trend": 0.8,
        "breadth": 0.8,
        "liquidity": 0.8,
        "options": 0.8,
        "rates": 0.8
    }, index=index)
    regime = pd.Series("low_vol", index=index)
    candidate = architecture.candidate_pool()[-1]
    normal, _ = architecture.candidate_exposure(frame, signals, regime, candidate)
    shock_day = index[8]
    signals.loc[shock_day, "liquidity"] = -0.90
    shocked, _ = architecture.candidate_exposure(frame, signals, regime, candidate)
    assert normal.loc[shock_day] > 0.0
    assert shocked.loc[shock_day] == 0.0


def test_portfolio_credits_defensive_sleeve_with_risk_free_return():
    index = pd.bdate_range("2021-01-01", periods=4)
    frame = pd.DataFrame({
        "asset_return": [0.0, 0.02, -0.01, 0.01],
        "risk_free_return": [0.001, 0.001, 0.001, 0.001]
    }, index=index)
    target = pd.Series(0.0, index=index)
    returns, turnover, applied = architecture.portfolio_returns(frame, target)
    assert np.allclose(applied.values, 0.0)
    assert np.allclose(turnover.values, 0.0)
    assert np.allclose(returns.values, 0.001)


def test_shadow_features_reject_holdout_history():
    index = pd.bdate_range("2026-07-01", periods=3)
    prices = pd.DataFrame(index=index)
    for symbol in architecture.required_symbols():
        prices[symbol] = 100.0
    with pytest.raises(RuntimeError, match="strictly after"):
        architecture.build_features(prices, research_mode=False)
