import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import investments_weekly_v5 as v5


class GovernedWeeklyModelTests(unittest.TestCase):
    def method(self, enabled=True):
        return {"instruments": [{"id": "x", "enabled_for_new_positions": enabled,
                                  "validation_gate_reason": "failed_validation"}]}

    def test_common_gate_blocks_new_entry_for_every_layer(self):
        item = {"instrument_id": "x", "direction": "long", "trade_status": "planned"}
        allowed, changed = v5.gate(item, self.method(False), "x")
        self.assertFalse(allowed)
        self.assertTrue(changed)
        self.assertEqual(item["direction"], "neutral")
        self.assertEqual(item["trade_status"], "no_trade")

    def test_common_gate_preserves_existing_open_position(self):
        item = {"instrument_id": "x", "direction": "long", "entry_price": 100.0,
                "exit_price": None, "trade_status": "open"}
        allowed, _ = v5.gate(item, self.method(False), "x")
        self.assertFalse(allowed)
        self.assertEqual(item["direction"], "long")
        self.assertEqual(item["entry_price"], 100.0)
        self.assertEqual(item["validation_gate"], "grandfathered_existing_position_no_new_entries")

    def test_entry_must_not_precede_decision(self):
        decided = datetime(2026, 7, 20, 8, 34, tzinfo=v5.legacy.TZ)
        pending = {"entry_not_before": decided.isoformat()}
        with patch.object(v5.v2, "first_bar_at_or_after", return_value={
            "price": 1.0, "timestamp": (decided - timedelta(minutes=5)).isoformat(), "source": "test"
        }):
            self.assertIsNone(v5.entry_point("X", pending))
        with patch.object(v5.v2, "first_bar_at_or_after", return_value={
            "price": 1.0, "timestamp": (decided + timedelta(minutes=5)).isoformat(), "source": "test"
        }):
            self.assertIsNotNone(v5.entry_point("X", pending))

    def test_thesis_exit_blocks_same_week_reentry(self):
        now = datetime(2026, 7, 21, 10, 0, tzinfo=v5.legacy.TZ)
        item = {"direction": "long", "entry_price": 100, "exit_price": 95,
                "exit_reason": "daily_model_directional_invalidation"}
        week = {"market_window": {"exit_target_local": "2026-07-24T22:00:00+02:00"}}
        blocked, changed = v5.lock_reentry(item, week, now)
        self.assertTrue(blocked)
        self.assertTrue(changed)
        self.assertTrue(item["reentry_lock"]["active"])

    def test_strategy_switch_does_not_create_thesis_lock(self):
        now = datetime(2026, 7, 21, 10, 0, tzinfo=v5.legacy.TZ)
        item = {"direction": "long", "entry_price": 100, "exit_price": 99,
                "exit_reason": "v4_daily_strategy_direction_switch"}
        blocked, _ = v5.lock_reentry(item, {"market_window": {}}, now)
        self.assertFalse(blocked)

    def test_no_trade_is_first_class_decision(self):
        policy = {"no_trade": {"enabled": True, "minimum_directional_raw_score": 35,
                                "minimum_directional_utility": 6, "conflict_no_trade_below_raw_score": 45}}
        decision = {"strategy_id": "base_v2", "direction": "long", "raw_score": 18,
                    "utility": 4, "candidates": {}}
        result = v5.no_trade(decision, {"score": 18, "data_quality": "passed"},
                             {"score": 12, "data_quality": "passed"}, policy)
        self.assertEqual(result["strategy_id"], "no_trade")
        self.assertEqual(result["direction"], "neutral")

    def test_strong_aligned_signal_remains_directional(self):
        policy = {"no_trade": {"enabled": True, "minimum_directional_raw_score": 35,
                                "minimum_directional_utility": 6, "conflict_no_trade_below_raw_score": 45}}
        decision = {"strategy_id": "weekly_trend", "direction": "long", "raw_score": 65,
                    "utility": 11, "candidates": {}}
        result = v5.no_trade(decision, {"score": 55, "data_quality": "passed"},
                             {"score": 65, "data_quality": "passed"}, policy)
        self.assertEqual(result["strategy_id"], "weekly_trend")
        self.assertEqual(result["direction"], "long")


if __name__ == "__main__":
    unittest.main()
