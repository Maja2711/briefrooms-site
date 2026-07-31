from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import brace_spx_generation4_research as gen4
import brace_spx_generation5_research as gen5


class Generation5Tests(unittest.TestCase):
    def test_candidate_space_is_predeclared_unique_and_new(self):
        pool = gen5.candidate_pool()
        self.assertEqual(len(pool), 12)
        self.assertEqual(len({candidate.candidate_id() for candidate in pool}), 12)
        self.assertTrue(all(candidate.family == "state_geometry_v5" for candidate in pool))
        self.assertTrue(all(candidate.params.get("generation") == "spx-state-geometry-v5" for candidate in pool))
        self.assertTrue(
            {candidate.candidate_id() for candidate in pool}.isdisjoint(
                {candidate.candidate_id() for candidate in gen4.candidate_pool()}
            )
        )
        self.assertEqual(
            {candidate.params.get("geometry_family") for candidate in pool},
            {"staircase", "hysteresis", "state_machine", "continuous_vol_target"},
        )

    def test_holdout_boundaries_are_fixed(self):
        index = pd.date_range("2021-01-31", "2026-08-31", freq="ME")
        frame = pd.DataFrame({"x": range(len(index))}, index=index)
        development, holdout = gen5.fixed_holdout_split(frame)
        self.assertEqual(holdout.index.min(), pd.Timestamp("2022-08-31"))
        self.assertEqual(holdout.index.max(), pd.Timestamp("2026-07-31"))
        self.assertEqual(len(holdout), 48)
        self.assertLess(development.index.max(), holdout.index.min())
        self.assertNotIn(pd.Timestamp("2026-08-31"), development.index)
        self.assertNotIn(pd.Timestamp("2026-08-31"), holdout.index)

    def test_all_geometries_remain_long_only_and_unlevered(self):
        index = pd.date_range("2020-01-31", periods=24, freq="ME")
        probability = pd.Series(np.linspace(0.20, 0.80, len(index)), index=index)
        volatility = pd.Series(np.linspace(0.08, 0.35, len(index)), index=index)
        for candidate in gen5.candidate_pool():
            exposure = gen5.probabilities_to_exposure(probability, volatility, candidate)
            self.assertEqual(list(exposure.index), list(index))
            self.assertGreaterEqual(float(exposure.min()), 0.0)
            self.assertLessEqual(float(exposure.max()), 1.0)
            self.assertFalse(exposure.isna().any())

    def test_hysteresis_state_resets_after_validation_gap(self):
        candidate = next(
            item for item in gen5.candidate_pool()
            if item.params.get("candidate_name") == "hysteresis-fast-entry"
        )
        index = pd.DatetimeIndex([
            "2020-01-31", "2020-02-29", "2020-03-31",
            "2020-06-30", "2020-07-31", "2020-08-31",
        ])
        probability = pd.Series([0.80, 0.80, 0.80, 0.45, 0.45, 0.45], index=index)
        volatility = pd.Series(0.10, index=index)
        exposure = gen5.probabilities_to_exposure(probability, volatility, candidate)
        self.assertGreater(float(exposure.iloc[2]), float(exposure.iloc[3]))
        self.assertGreater(float(exposure.iloc[3]), 0.0)

    def test_shared_signal_does_not_depend_on_candidate(self):
        index = pd.date_range("2020-01-31", periods=6, freq="ME")
        frame = pd.DataFrame({
            "spy_ma_gap_200": np.linspace(-0.10, 0.10, 6),
            "spy_momentum_252": np.linspace(-0.20, 0.20, 6),
            "spy_drawdown_252": np.linspace(-0.25, 0.0, 6),
            "breadth_above_ma200": np.linspace(0.2, 0.8, 6),
            "sector_momentum_mean_63": np.linspace(-0.10, 0.10, 6),
            "sector_momentum_dispersion_63": np.linspace(0.15, 0.05, 6),
            "credit_ratio_63": np.linspace(-0.05, 0.05, 6),
            "tlt_momentum_63": np.linspace(-0.05, 0.05, 6),
            "tnx_change_63": np.linspace(0.50, -0.20, 6),
            "spy_vol_60": np.linspace(0.30, 0.12, 6),
        }, index=index)
        candidate_a, candidate_b = gen5.candidate_pool()[0], gen5.candidate_pool()[-1]
        predicted_a = gen5.fit_predict_candidate(frame, candidate_a, np.array([], dtype=int), np.arange(6), 1)
        predicted_b = gen5.fit_predict_candidate(frame, candidate_b, np.array([], dtype=int), np.arange(6), 999)
        pd.testing.assert_series_equal(predicted_a, predicted_b)

    def test_geometry_diagnostics_report_distinct_state_usage(self):
        index = pd.date_range("2000-01-31", periods=240, freq="ME")
        development = pd.DataFrame({
            "realized_vol_20": 0.16 + 0.06 * np.sin(np.arange(240) / 6.0),
            "spy_ma_gap_200": 0.08 * np.sin(np.arange(240) / 12.0),
            "spy_momentum_252": 0.20 * np.sin(np.arange(240) / 14.0),
            "spy_drawdown_252": -0.12 + 0.10 * np.sin(np.arange(240) / 10.0),
            "breadth_above_ma200": 0.5 + 0.3 * np.sin(np.arange(240) / 9.0),
            "sector_momentum_mean_63": 0.10 * np.sin(np.arange(240) / 7.0),
            "sector_momentum_dispersion_63": 0.08 + 0.03 * np.cos(np.arange(240) / 8.0),
            "credit_ratio_63": 0.04 * np.sin(np.arange(240) / 5.0),
            "tlt_momentum_63": 0.06 * np.cos(np.arange(240) / 11.0),
            "tnx_change_63": 0.40 * np.cos(np.arange(240) / 13.0),
            "spy_vol_60": 0.18 + 0.07 * np.cos(np.arange(240) / 6.0),
        }, index=index)
        diagnostics = gen5.geometry_diagnostics(development, gen5.candidate_pool(), 100)
        self.assertIn("exposure_diversity", diagnostics)
        self.assertEqual(len(diagnostics["candidate_stats"]), 12)
        self.assertTrue(all(row["active_exposure_buckets"] >= 1 for row in diagnostics["candidate_stats"].values()))


if __name__ == "__main__":
    unittest.main()
