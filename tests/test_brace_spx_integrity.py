import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.brace_spx_integrity import (
    HoldoutRegistry,
    annualized_metrics,
    deflated_sharpe_probability,
    probability_of_backtest_overfitting,
)


def test_sharpe_uses_arithmetic_excess_returns():
    idx = pd.date_range("2020-01-31", periods=24, freq="ME")
    returns = pd.Series([0.01, 0.02, -0.005, 0.015] * 6, index=idx)
    rf = pd.Series(0.002, index=idx)
    expected = float((returns - rf).mean() / (returns - rf).std(ddof=1) * np.sqrt(12))
    metrics = annualized_metrics(returns, risk_free_returns=rf)
    assert metrics["sharpe_excess"] == pytest.approx(expected, abs=5e-5)


def test_deflated_sharpe_penalizes_more_trials():
    few = deflated_sharpe_probability(1.2, 120, 5)
    many = deflated_sharpe_probability(1.2, 120, 500)
    assert 0 <= many < few <= 1


def test_holdout_registry_is_single_use(tmp_path: Path):
    registry = HoldoutRegistry(tmp_path / "registry.json")
    registry.open_once("g1", "candidate-a")
    with pytest.raises(RuntimeError):
        registry.open_once("g1", "candidate-b")
    saved = json.loads((tmp_path / "registry.json").read_text())
    assert saved["generations"]["g1"]["candidate_id"] == "candidate-a"


def test_pbo_detects_available_matrix():
    rng = np.random.default_rng(7)
    matrix = pd.DataFrame(rng.normal(0.001, 0.02, size=(160, 5)))
    result = probability_of_backtest_overfitting(matrix, slices=8)
    assert result["available"] is True
    assert 0 <= result["pbo"] <= 1
    assert result["splits"] > 0
