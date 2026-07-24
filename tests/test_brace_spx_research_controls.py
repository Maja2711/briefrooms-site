import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.brace_spx_research_controls import (
    declare_generation,
    deflated_sharpe_ratio,
    excess_return_sharpe,
    open_holdout_once,
    probability_of_backtest_overfitting,
)


class ResearchControlsTests(unittest.TestCase):
    def test_excess_return_sharpe_uses_arithmetic_periodic_returns(self):
        returns = pd.Series([0.02, 0.01, -0.005, 0.015, 0.0, 0.012])
        rf = pd.Series([0.001] * len(returns))
        excess = returns - rf
        expected = excess.mean() / excess.std(ddof=1) * np.sqrt(12)
        self.assertAlmostEqual(excess_return_sharpe(returns, rf), expected)

    def test_deflated_sharpe_penalizes_many_trials(self):
        rng = np.random.default_rng(7)
        returns = pd.Series(rng.normal(0.01, 0.04, 120))
        few = deflated_sharpe_ratio(returns, 2, 0.15)
        many = deflated_sharpe_ratio(returns, 500, 0.15)
        self.assertGreater(many["selection_adjusted_sharpe"], few["selection_adjusted_sharpe"])
        self.assertLessEqual(many["deflated_sharpe_probability"], few["deflated_sharpe_probability"])

    def test_pbo_returns_bounded_probability(self):
        rng = np.random.default_rng(11)
        frame = pd.DataFrame(
            {
                "stable": rng.normal(0.008, 0.03, 160),
                "noise_a": rng.normal(0.0, 0.04, 160),
                "noise_b": rng.normal(0.0, 0.04, 160),
                "noise_c": rng.normal(0.0, 0.04, 160),
            }
        )
        result = probability_of_backtest_overfitting(frame, partitions=8)
        self.assertGreater(result["splits"], 0)
        self.assertGreaterEqual(result["pbo"], 0.0)
        self.assertLessEqual(result["pbo"], 1.0)

    def test_holdout_is_single_use_per_declared_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "holdout.json"
            generation = declare_generation(
                registry,
                "g1",
                {"models": ["ridge"], "features": ["trend"]},
                "2022-07-01",
                "2026-06-30",
            )
            self.assertIsNone(generation.opened_at)
            opened = open_holdout_once(registry, "g1", {"cagr": 0.1})
            self.assertIsNotNone(opened.opened_at)
            with self.assertRaises(RuntimeError):
                open_holdout_once(registry, "g1", {"cagr": 0.2})

    def test_generation_id_cannot_be_redefined(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "holdout.json"
            declare_generation(registry, "g1", {"a": 1}, "2022-01-01", "2023-01-01")
            with self.assertRaises(ValueError):
                declare_generation(registry, "g1", {"a": 2}, "2022-01-01", "2023-01-01")


if __name__ == "__main__":
    unittest.main()
