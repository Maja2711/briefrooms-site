from __future__ import annotations

import unittest
from datetime import datetime, timezone

from scripts.belief_market_data_adapter import Bar
from scripts.daily_eurusd_lifecycle import (
    BASE_WEIGHTS,
    append_trade,
    empty_history,
    entry_gate,
    evaluate_position,
    learning_state,
    position_from_output,
)


class DailyEurusdLifecycleTests(unittest.TestCase):
    def test_legacy_long_is_closed_at_stop_and_records_percent_and_r(self):
        previous = {
            "direction": "LONG",
            "timestamp": "2026-08-20T10:48:56Z",
            "entry": 1.17041,
            "stop": 1.16749,
            "target": 1.17568,
            "score": 64.02,
            "confidence": 0.28,
            "engine_version": "eurusd-daily-spot-v1.0.0",
            "metadata": {
                "components": {
                    "trend": 0.3775,
                    "broad_usd_environment": 0.2332,
                    "us_rates_pressure_proxy": 0.0724,
                },
                "weights": BASE_WEIGHTS,
            },
        }
        position = position_from_output(previous)
        self.assertIsNotNone(position)
        bar = Bar(
            timestamp=datetime(2026, 8, 20, 10, 49, tzinfo=timezone.utc),
            open=1.17020,
            high=1.17030,
            low=1.16740,
            close=1.16900,
        )
        trade = evaluate_position(position, [bar], bar.timestamp)
        self.assertIsNotNone(trade)
        self.assertEqual(trade["exit_reason"], "STOP_LOSS")
        self.assertAlmostEqual(trade["result_percent"], -0.24949, places=5)
        self.assertAlmostEqual(trade["r_multiple"], -1.0, places=4)

    def test_same_minute_stop_and_target_uses_conservative_stop(self):
        previous = {
            "direction": "LONG",
            "timestamp": "2026-08-20T10:48:56Z",
            "entry": 1.17000,
            "stop": 1.16000,
            "target": 1.18000,
            "score": 70.0,
            "confidence": 0.4,
            "engine_version": "test",
            "metadata": {},
        }
        position = position_from_output(previous)
        bar = Bar(
            timestamp=datetime(2026, 8, 20, 10, 50, tzinfo=timezone.utc),
            high=1.18100,
            low=1.15900,
            close=1.17000,
        )
        trade = evaluate_position(position, [bar], bar.timestamp)
        self.assertEqual(trade["exit_reason"], "STOP_LOSS")
        self.assertTrue(trade["monitor"]["conservative_same_bar"])

    def test_loss_tightens_thresholds_and_reduces_supporting_component_weight(self):
        history = append_trade(empty_history(), {
            "trade_id": "loss-1",
            "direction": "LONG",
            "opened_at": "2026-08-20T10:00:00Z",
            "closed_at": "2026-08-20T11:00:00Z",
            "r_multiple": -1.0,
            "exit_reason": "STOP_LOSS",
            "entry_components": {
                "trend": 0.4,
                "broad_usd_environment": 0.2,
                "us_rates_pressure_proxy": 0.1,
            },
        })
        learning = learning_state(history["trades"])
        self.assertGreater(learning["entry_thresholds"]["long"], 66.0)
        self.assertGreater(learning["entry_thresholds"]["min_confidence"], 0.32)
        self.assertLess(learning["adaptive_weights"]["trend"], BASE_WEIGHTS["trend"])

    def test_old_64_score_28_percent_confidence_would_now_be_rejected(self):
        gate = entry_gate(
            direction="LONG",
            score=64.02,
            confidence=0.28,
            history=empty_history(),
            observed_at=datetime(2026, 8, 20, 10, 48, tzinfo=timezone.utc),
            previous_score=63.0,
            stretch_atr=0.5,
            shock_ratio=1.0,
        )
        self.assertFalse(gate["accepted"])
        self.assertIn("score_below_adaptive_long_threshold", gate["reasons"])
        self.assertIn("confidence_below_minimum", gate["reasons"])


if __name__ == "__main__":
    unittest.main()
