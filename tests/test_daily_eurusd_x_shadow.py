from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from belief_market_data_adapter import Bar
import daily_eurusd_x_shadow as x


class EurusdXShadowTests(unittest.TestCase):
    def test_fixed_core_contract_is_exact(self):
        self.assertEqual(x.FIXED_CORE["ma_windows"], [30, 60, 100, 200])
        self.assertEqual(x.FIXED_CORE["ma_timeframes"], ["H1", "D1", "W1", "M1"])
        self.assertEqual(
            x.FIXED_CORE["bollinger"],
            {"window": 20, "stddevs": 2.5, "timeframes": ["H1", "D1"]},
        )
        self.assertIn("classic_daily_floor", x.FIXED_CORE["pivot"])

    def test_initial_state_has_zero_external_authority(self):
        state = x._initial_state(datetime(2026, 9, 27, tzinfo=timezone.utc))
        authority = state["authority"]
        self.assertFalse(authority["production_execution"])
        self.assertFalse(authority["active_daily_engine_writeback"])
        self.assertFalse(authority["abc_writeback"])
        self.assertFalse(authority["belief_writeback"])
        self.assertFalse(authority["automatic_production_promotion"])
        self.assertTrue(authority["x_local_shadow_promotion"])
        self.assertTrue(authority["x_local_rollback"])

    def test_prediction_has_neutral_no_trade_zone(self):
        setup = x.DEFAULT_SETUPS["X-BASE"]
        pred = x._predict(
            setup,
            0.0,
            0.0,
            {name: 0.0 for name in x.SUPPLEMENT_FEATURES},
        )
        self.assertEqual(pred["direction"], "FLAT")
        self.assertEqual(pred["probability_long"], 0.5)

    def test_resample_builds_weekly_and_monthly_ohlc(self):
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        rows = [
            Bar(
                timestamp=start + timedelta(days=i),
                open=1 + i * .001,
                high=1.01 + i * .001,
                low=.99 + i * .001,
                close=1.005 + i * .001,
                volume=None,
            )
            for i in range(70)
        ]
        weekly = x._resample(rows, "W1")
        monthly = x._resample(rows, "M1")
        self.assertGreaterEqual(len(weekly), 10)
        self.assertEqual(len(monthly), 3)
        self.assertGreaterEqual(weekly[0].high, weekly[0].close)
        self.assertLessEqual(weekly[0].low, weekly[0].close)

    def test_challenger_can_promote_only_on_prospective_common_sample(self):
        state = x._initial_state(datetime(2026, 9, 1, tzinfo=timezone.utc))
        state["active_challenger_id"] = "X-TREND"
        captures = []
        for _ in range(x.MIN_COMMON_EVAL):
            captures.append({
                "predictions": {
                    "X-BASE": {"probability_long": .60, "direction": "LONG"},
                    "X-TREND": {"probability_long": .82, "direction": "LONG"},
                },
                "outcomes": {"4h": {"resolved": {"up": True, "return_bps": 8.0}}},
            })
        state["captures"] = captures
        x._recalibrate(state, datetime(2026, 9, 20, tzinfo=timezone.utc))
        self.assertEqual(state["champion_setup_id"], "X-TREND")
        self.assertEqual(state["last_best_setup_id"], "X-BASE")
        self.assertEqual(
            state["calibration_events"][-1]["action"],
            "PROMOTE_X_SHADOW",
        )

    def test_deteriorating_champion_rolls_back_to_last_best(self):
        state = x._initial_state(datetime(2026, 9, 1, tzinfo=timezone.utc))
        state["champion_setup_id"] = "X-TREND"
        state["last_best_setup_id"] = "X-BASE"
        captures = []
        for _ in range(x.ROLLBACK_WINDOW):
            captures.append({
                "predictions": {
                    "X-TREND": {"probability_long": .90, "direction": "LONG"},
                    "X-BASE": {"probability_long": .15, "direction": "SHORT"},
                },
                "outcomes": {"4h": {"resolved": {"up": False, "return_bps": -10.0}}},
            })
        state["captures"] = captures
        x._recalibrate(state, datetime(2026, 9, 20, tzinfo=timezone.utc))
        self.assertEqual(state["champion_setup_id"], "X-BASE")
        self.assertEqual(state["calibration_events"][-1]["action"], "ROLLBACK")


if __name__ == "__main__":
    unittest.main()
