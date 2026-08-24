from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from scripts.belief_market_data_adapter import Bar
from scripts import daily_eurusd_spot_v14 as v14

UTC = timezone.utc
OPENED = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)


def position(direction: str = "LONG") -> dict:
    if direction == "LONG":
        entry, stop, target = 1.10000, 1.09000, 1.11800
    else:
        entry, stop, target = 1.10000, 1.11000, 1.08200
    return {
        "schema_version": "eurusd-daily-position-v1",
        "trade_id": f"test-{direction.lower()}",
        "status": "OPEN",
        "direction": direction,
        "opened_at": OPENED.isoformat().replace("+00:00", "Z"),
        "expires_at": (OPENED + timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
        "entry": entry,
        "stop": stop,
        "target": target,
        "entry_score": 65.0 if direction == "LONG" else 35.0,
        "entry_confidence": 0.3,
        "entry_components": {},
        "entry_weights": {},
        "engine_version": "test",
    }


def bar(hours: float, close: float, *, high: float | None = None, low: float | None = None) -> Bar:
    return Bar(
        timestamp=OPENED + timedelta(hours=hours),
        open=close,
        high=close if high is None else high,
        low=close if low is None else low,
        close=close,
    )


class DailyEURUSDDynamicExitTests(unittest.TestCase):
    def test_existing_24h_position_is_upgraded_to_soft_24h_hard_27h(self):
        upgraded = v14.normalize_position(position())
        self.assertEqual(upgraded["soft_expires_at"], "2026-08-25T09:00:00Z")
        self.assertEqual(upgraded["expires_at"], "2026-08-25T12:00:00Z")
        self.assertEqual(upgraded["dynamic_exit_policy"], "R_PACE_V1")

    def test_profit_is_protected_after_meaningful_giveback_and_adverse_momentum(self):
        bars = [
            bar(0, 1.1000),
            bar(3, 1.1070, high=1.1080),
            bar(5, 1.1060),
            bar(6, 1.1035),
        ]
        trade = v14.evaluate_position(position("LONG"), bars, bars[-1].timestamp)
        self.assertIsNotNone(trade)
        self.assertEqual(trade["exit_reason"], "DYNAMIC_PROFIT_EXIT")
        self.assertGreater(trade["r_multiple"], 0.0)
        self.assertGreaterEqual(trade["monitor"]["dynamic_exit"]["giveback_r"], 0.25)

    def test_losing_long_is_cut_before_stop_when_pace_deteriorates(self):
        bars = [
            bar(0, 1.1000),
            bar(3, 1.0990),
            bar(5, 1.0980),
            bar(6, 1.0960),
        ]
        trade = v14.evaluate_position(position("LONG"), bars, bars[-1].timestamp)
        self.assertIsNotNone(trade)
        self.assertEqual(trade["exit_reason"], "DYNAMIC_RISK_EXIT")
        self.assertGreater(trade["r_multiple"], -1.0)

    def test_losing_short_is_cut_symmetrically_before_stop(self):
        bars = [
            bar(0, 1.1000),
            bar(3, 1.1010),
            bar(5, 1.1020),
            bar(6, 1.1040),
        ]
        trade = v14.evaluate_position(position("SHORT"), bars, bars[-1].timestamp)
        self.assertIsNotNone(trade)
        self.assertEqual(trade["exit_reason"], "DYNAMIC_RISK_EXIT")
        self.assertGreater(trade["r_multiple"], -1.0)

    def test_soft_24h_horizon_extends_when_hold_economics_remain_positive(self):
        bars = [
            bar(0, 1.1000),
            bar(21.0, 1.1060),
            bar(23.0, 1.1085),
            bar(24.1, 1.1120),
        ]
        trade = v14.evaluate_position(position("LONG"), bars, bars[-1].timestamp)
        self.assertIsNone(trade)
        diag = v14.dynamic_exit_diagnostics(v14.normalize_position(position("LONG")), bars, bars[-1].timestamp)
        self.assertTrue(diag["soft_horizon_reached"])
        self.assertGreaterEqual(diag["hold_score"], v14.SOFT_EXTENSION_MIN_HOLD_SCORE)
        self.assertGreaterEqual(diag["tp_feasibility_ratio"], v14.SOFT_EXTENSION_MIN_FEASIBILITY)

    def test_soft_24h_horizon_closes_when_extension_is_not_justified(self):
        bars = [
            bar(0, 1.1000),
            bar(21.0, 1.1030),
            bar(23.0, 1.1020),
            bar(24.1, 1.1005),
        ]
        trade = v14.evaluate_position(position("LONG"), bars, bars[-1].timestamp)
        self.assertIsNotNone(trade)
        self.assertEqual(trade["exit_reason"], "SOFT_HORIZON_EXIT")

    def test_hard_27h_horizon_always_closes(self):
        bars = [
            bar(0, 1.1000),
            bar(24.0, 1.1020),
            bar(26.0, 1.1040),
            bar(27.0, 1.1050),
        ]
        trade = v14.evaluate_position(position("LONG"), bars, bars[-1].timestamp)
        self.assertIsNotNone(trade)
        self.assertEqual(trade["exit_reason"], "TIME_EXIT_27H")


if __name__ == "__main__":
    unittest.main()
