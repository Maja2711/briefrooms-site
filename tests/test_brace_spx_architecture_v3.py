from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import brace_spx_architecture_v3 as architecture


def test_candidate_space_is_fixed_unique_and_signed():
    pool = architecture.candidate_pool()
    assert len(pool) == 12
    assert len({candidate.candidate_id() for candidate in pool}) == 12
    assert architecture.candidate_signature(pool) == "7e7c6a7fa8526c15534075c82e7f75fd58ce90ebebd2e8a9efc7b86cb74e0b61"
    assert all(candidate.max_abs_exposure == 1.0 for candidate in pool)


def test_score_mapping_supports_long_short_and_flat():
    score = pd.Series([-0.90, -0.45, 0.00, 0.30, 0.80])
    exposure = architecture._score_to_signed_exposure(score, "conservative_short")
    assert exposure.tolist() == [-1.0, -0.5, 0.0, 0.5, 1.0]


def test_portfolio_short_is_fully_collateralized_and_unlevered():
    index = pd.bdate_range("2021-01-01", periods=4)
    frame = pd.DataFrame({
        "asset_return": [0.0, -0.02, 0.01, -0.01],
        "risk_free_return": [0.001, 0.001, 0.001, 0.001],
    }, index=index)
    target = pd.Series(-1.0, index=index)
    returns, turnover, applied, _ = architecture.portfolio_returns(frame, target)
    assert applied.min() >= -1.0 and applied.max() <= 1.0
    expected_day_2 = 0.001 + 0.02 - architecture.DAILY_SHORT_BORROW_COST - architecture.COST_PER_UNIT_TURNOVER
    assert np.isclose(returns.iloc[1], expected_day_2)
    assert turnover.iloc[0] == 0.0 and turnover.iloc[1] == 1.0


def test_daily_crisis_gate_can_enter_short_between_weekly_rebalances():
    index = pd.bdate_range("2021-01-04", periods=10)
    frame = pd.DataFrame(index=index)
    signals = pd.DataFrame({
        "trend": 0.8, "breadth": 0.8, "liquidity": 0.8,
        "options": 0.8, "rates": 0.8,
    }, index=index)
    regime = pd.Series("low_vol", index=index)
    candidate = architecture.candidate_pool()[-1]
    normal, _ = architecture.candidate_exposure(frame, signals, regime, candidate)
    crisis_day = index[7]
    signals.loc[crisis_day, ["trend", "liquidity", "options"]] = -0.90
    crisis, _ = architecture.candidate_exposure(frame, signals, regime, candidate)
    assert normal.loc[crisis_day] >= 0.0
    assert crisis.loc[crisis_day] == -1.0


def test_champion_requires_short_value_and_independent_validation():
    assert not architecture.authorize_single_champion(True, False, True)
    assert not architecture.authorize_single_champion(True, True, False)
    assert not architecture.authorize_single_champion(False, True, True)
    assert architecture.authorize_single_champion(True, True, True)
