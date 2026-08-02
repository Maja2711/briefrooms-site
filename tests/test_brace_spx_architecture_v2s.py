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

import brace_spx_architecture_v2 as base
import brace_spx_architecture_v2s as a2s


def test_new_candidate_family_has_new_signature():
    old = base.candidate_signature(base.candidate_pool())
    new = base.candidate_signature(a2s.candidate_pool())
    assert old != new
    assert len(a2s.candidate_pool()) == base.EXPECTED_CANDIDATES
    assert all(row.name.endswith("-lsf") for row in a2s.candidate_pool())


def test_score_mapping_contains_long_short_and_flat():
    index = pd.date_range("2020-01-01", periods=7, freq="D")
    score = pd.Series([-0.9, -0.5, -0.2, 0.0, 0.2, 0.5, 0.9], index=index)
    exposure = a2s.score_to_exposure(score, "graded")
    assert exposure.min() == -1.0
    assert exposure.max() == 1.0
    assert exposure.iloc[3] == 0.0
    assert set(np.sign(exposure)) == {-1.0, 0.0, 1.0}


def test_return_reconciliation_includes_cash_borrow_and_turnover_costs():
    index = pd.date_range("2020-01-01", periods=5, freq="D")
    frame = pd.DataFrame(
        {
            "asset_return": [0.0, 0.02, -0.01, 0.03, -0.02],
            "risk_free_return": [0.0001] * 5,
        },
        index=index,
    )
    target = pd.Series([0.0, -0.5, -0.5, 0.0, 1.0], index=index)
    returns, turnover, applied = a2s.portfolio_returns(frame, target)
    expected_applied = target.shift(1).fillna(0.0)
    expected_turnover = expected_applied.diff().abs().fillna(expected_applied.abs())
    expected = (
        expected_applied * frame["asset_return"]
        + (1.0 - expected_applied.abs()) * frame["risk_free_return"]
        - expected_applied.clip(upper=0.0).abs() * a2s.SHORT_BORROW_DAILY
        - expected_turnover * base.COST_PER_UNIT_TURNOVER
    )
    pd.testing.assert_series_equal(applied, expected_applied.astype(float))
    pd.testing.assert_series_equal(turnover, expected_turnover.astype(float))
    pd.testing.assert_series_equal(returns, expected.astype(float))


def test_no_leverage_is_possible():
    index = pd.date_range("2020-01-01", periods=4, freq="D")
    frame = pd.DataFrame(
        {"asset_return": [0.0] * 4, "risk_free_return": [0.0] * 4},
        index=index,
    )
    target = pd.Series([-5.0, -1.2, 1.5, 9.0], index=index)
    _returns, _turnover, applied = a2s.portfolio_returns(frame, target)
    assert applied.abs().max() <= 1.0


def test_shock_gate_can_establish_only_bounded_short():
    index = pd.date_range("2020-01-02", periods=8, freq="D")
    frame = pd.DataFrame(
        {
            "vix_change_5": [0.0, 0.0, 0.0, 0.0, 0.7, 0.0, 0.0, 0.0],
            "spy_vol_20": [0.2] * 8,
            "spy_drawdown_126": [-0.05] * 8,
        },
        index=index,
    )
    signals = pd.DataFrame(
        {
            "trend": [0.8] * 8,
            "breadth": [0.8] * 8,
            "liquidity": [0.5] * 8,
            "options": [0.5] * 8,
            "rates": [0.5] * 8,
        },
        index=index,
    )
    regime = pd.Series("low_vol", index=index)
    candidate = a2s.candidate_pool()[0]
    exposure, _score = a2s.candidate_exposure(frame, signals, regime, candidate)
    assert exposure.min() >= -1.0
    assert exposure.max() <= 1.0
    assert exposure.iloc[4] == pytest.approx(-0.25)
