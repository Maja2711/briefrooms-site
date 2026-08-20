from __future__ import annotations

import unittest
from datetime import datetime, timezone

from scripts.daily_eurusd_lifecycle import append_trade, empty_history
from scripts.daily_eurusd_spot_v12 import (
    ENGINE_VERSION,
    direct_entry_gate,
    direct_learning_state,
)


class DailyEurusdDirectSignalAdmissionTests(unittest.TestCase):
    def _loss_history(self):
        return append_trade(empty_history(), {
            "trade_id": "loss-direct-1",
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

    def test_engine_version_is_v12(self):
        self.assertEqual(ENGINE_VERSION, "eurusd-daily-spot-v1.2.0")

    def test_long_is_admitted_despite_all_legacy_veto_conditions(self):
        history = self._loss_history()
        gate = direct_entry_gate(
            direction="LONG",
            score=60.0,
            confidence=0.0,
            history=history,
            observed_at=datetime(2026, 8, 20, 11, 1, tzinfo=timezone.utc),
            previous_score=10.0,
            stretch_atr=99.0,
            shock_ratio=99.0,
        )
        self.assertTrue(gate["accepted"])
        self.assertEqual(gate["reasons"], [])

    def test_short_is_admitted_despite_all_legacy_veto_conditions(self):
        history = self._loss_history()
        gate = direct_entry_gate(
            direction="SHORT",
            score=40.0,
            confidence=0.0,
            history=history,
            observed_at=datetime(2026, 8, 20, 11, 1, tzinfo=timezone.utc),
            previous_score=90.0,
            stretch_atr=99.0,
            shock_ratio=99.0,
        )
        self.assertTrue(gate["accepted"])
        self.assertEqual(gate["reasons"], [])

    def test_flat_remains_no_trade(self):
        gate = direct_entry_gate(
            direction="FLAT",
            score=50.0,
            confidence=0.0,
            history=empty_history(),
            observed_at=datetime(2026, 8, 20, 11, 1, tzinfo=timezone.utc),
            previous_score=50.0,
            stretch_atr=0.0,
            shock_ratio=0.0,
        )
        self.assertFalse(gate["accepted"])
        self.assertEqual(gate["reasons"], ["raw_score_neutral"])

    def test_learning_keeps_weights_but_has_no_admission_limits(self):
        state = direct_learning_state(self._loss_history()["trades"])
        self.assertEqual(state["entry_thresholds"], {
            "long": 60.0,
            "short": 40.0,
            "min_confidence": 0.0,
        })
        self.assertIsNone(state["cooldown_until"])
        self.assertIsNone(state["policy"]["daily_entry_limit"])
        self.assertEqual(state["policy"]["loss_cooldown_hours"], 0)
        self.assertEqual(state["policy"]["blocking_filters"], [])
        self.assertTrue(state["policy"]["one_open_position_at_a_time"])
        self.assertIn("adaptive_weights", state)


if __name__ == "__main__":
    unittest.main()
