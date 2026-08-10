import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import brace_spx_generation6 as g6
import brace_spx_generation6_shadow as shadow


class BraceSpxGeneration6Tests(unittest.TestCase):
    def synthetic_prices(self, periods=240, start="2019-01-02"):
        idx = pd.bdate_range(start, periods=periods)
        x = np.arange(periods, dtype=float)
        data = {
            "SPY": 250.0 + 0.35 * x + 2.0 * np.sin(x / 8.0),
            "^VIX": 18.0 + 1.5 * np.sin(x / 11.0),
            g6.VIX3M_SYMBOL: 20.0 + 1.2 * np.sin(x / 13.0),
            "^TNX": 2.0 + 0.002 * x,
            "TLT": 120.0 - 0.03 * x + np.sin(x / 17.0),
            "HYG": 85.0 + 0.02 * x,
            "LQD": 115.0 + 0.01 * x,
            "UUP": 25.0 + 0.005 * x,
            "RSP": 95.0 + 0.12 * x,
            g6.RISK_FREE_SYMBOL: 1.5 + 0.001 * x,
        }
        return pd.DataFrame(data, index=idx)

    def test_exactly_four_source_families_and_eight_candidates(self):
        pool = g6.candidate_pool()
        self.assertEqual(8, len(pool))
        used = {source for candidate in pool for source in candidate.signal_sources}
        self.assertEqual(set(g6.SOURCE_FAMILIES), used)
        self.assertEqual(4, len(used))

    def test_shared_geometry_has_real_flat_band(self):
        idx = pd.date_range("2020-01-01", periods=5)
        score = pd.Series([-0.8, -0.3, 0.0, 0.3, 0.8], index=idx)
        exposure = g6._score_to_exposure(score)
        self.assertEqual([-1.0, -0.5, 0.0, 0.5, 1.0], exposure.tolist())

    def test_candidate_signals_are_bounded(self):
        frame = g6.build_features(self.synthetic_prices(), research_mode=True)
        signals = g6.signal_frame(frame).dropna()
        self.assertFalse(signals.empty)
        self.assertEqual(list(g6.SOURCE_FAMILIES), list(signals.columns))
        self.assertLessEqual(float(signals.max().max()), 1.0)
        self.assertGreaterEqual(float(signals.min().min()), -1.0)

    def test_research_rejects_holdout_input(self):
        prices = self.synthetic_prices(20, start="2022-08-01")
        with self.assertRaises(RuntimeError):
            g6.build_features(prices, research_mode=True)

    def test_shadow_rejects_pre_shadow_input(self):
        prices = self.synthetic_prices(20, start="2026-07-01")
        with self.assertRaises(RuntimeError):
            g6.build_features(prices, research_mode=False)

    def test_shadow_counts_observations_before_activation(self):
        prices = self.synthetic_prices(6, start="2026-08-03")
        payload = shadow.run(prices)
        self.assertEqual("warming_up", payload["status"])
        self.assertEqual(6, payload["observations_collected"])
        self.assertEqual(g6.SHADOW_WARMUP_OBSERVATIONS - 6, payload["observations_remaining"])
        self.assertFalse(payload["holdout_accessed"])
        self.assertFalse(payload["live_orders"])


if __name__ == "__main__":
    unittest.main()
