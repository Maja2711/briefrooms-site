import json
import sys
import tempfile
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

    def test_close_normalizes_v5_exposure_metadata(self):
        item = {
            "continuous_exposure_active": True,
            "continuous_exposure_status": "open",
            "next_entry_status": "open",
            "pending_entry_decision": {"decision": {"direction": "long"}},
            "risk_status": "open_multi_instrument_continuous_exposure",
            "exit_reason": "scheduled_week_close",
        }
        self.assertTrue(v5.v2.mark_exposure_closed(item))
        self.assertFalse(item["continuous_exposure_active"])
        self.assertEqual(item["continuous_exposure_status"], "closed")
        self.assertEqual(item["next_entry_status"], "closed")
        self.assertIsNone(item["pending_entry_decision"])
        self.assertEqual(item["risk_status"], "closed_scheduled_week_close")

    def test_close_does_not_add_v5_metadata_to_legacy_row(self):
        item = {"trade_status": "closed", "exit_price": 100.0}
        self.assertFalse(v5.v2.mark_exposure_closed(item))
        self.assertNotIn("continuous_exposure_active", item)
        self.assertNotIn("continuous_exposure_status", item)

    def test_due_week_is_closed_at_first_bar_and_exposure_is_cleared(self):
        now = datetime(2026, 8, 7, 22, 15, tzinfo=v5.legacy.TZ)
        payload = {
            "week_id": "2026-W32",
            "market_window": {"exit_target_local": "2026-08-07T22:00:00+02:00"},
            "instruments": [{
                "instrument_id": "eurusd",
                "symbol": "EURUSD=X",
                "direction": "short",
                "entry_price": 1.15,
                "exit_price": None,
                "trade_status": "open",
                "continuous_exposure_active": True,
                "continuous_exposure_status": "open",
                "next_entry_status": "open",
                "pending_entry_decision": {"decision": {"direction": "short"}},
                "risk_status": "open_multi_instrument_continuous_exposure",
                "notional_eur": 10000,
            }],
        }
        point = {
            "price": 1.16,
            "timestamp": "2026-08-07T22:00:00+02:00",
            "source": "test:first_bar_at_or_after_target",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "2026-W32.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(v5.v2, "WEEKLY_DIR", Path(temp_dir)), \
                    patch.object(v5.v2.legacy, "now_local", return_value=now), \
                    patch.object(v5.v2, "first_bar_at_or_after", return_value=point):
                self.assertTrue(v5.v2.close_due_weeks())
            saved = json.loads(path.read_text(encoding="utf-8"))

        item = saved["instruments"][0]
        self.assertEqual(1.16, item["exit_price"])
        self.assertEqual("scheduled_week_close", item["exit_reason"])
        self.assertEqual("closed", item["trade_status"])
        self.assertFalse(item["continuous_exposure_active"])
        self.assertEqual("closed", item["continuous_exposure_status"])
        self.assertIsNone(item["pending_entry_decision"])
        self.assertEqual("closed_scheduled_week_close", item["risk_status"])


if __name__ == "__main__":
    unittest.main()
