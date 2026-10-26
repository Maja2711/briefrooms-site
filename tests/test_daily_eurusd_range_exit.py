"""Deterministic tests for prospective EUR/USD Daily H1 range exits."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from scripts.belief_market_data_adapter import Bar
from scripts import daily_eurusd_range_exit as range_exit
from scripts import daily_eurusd_lifecycle as lifecycle
from scripts import daily_eurusd_spot_v14 as v14

UTC = timezone.utc
START = datetime(2026, 10, 22, 0, tzinfo=UTC)
OPENED = START + timedelta(hours=24)


def bar(stamp, close, *, low=None, high=None):
    return Bar(timestamp=stamp, open=close, close=close,
               high=close if high is None else high,
               low=close if low is None else low)


def history(*, last=1.1004, trend=False, complete=True):
    rows = []
    for hour in range(24):
        price = (1.1000 + 0.00035 * hour
                 if trend else 1.1000 + (0.00025 if hour % 2 else -0.00025))
        for minute in range(60 if complete else 10):
            rows.append(bar(START + timedelta(hours=hour, minutes=minute), price))
    for minute in range(31):
        rows.append(bar(OPENED + timedelta(minutes=minute), last))
    return rows


def position(direction="LONG", *, entry=None):
    if direction == "LONG":
        entry = 1.0995 if entry is None else entry
        stop, target = entry - 0.00318, entry + 0.00400
    else:
        entry = 1.1009 if entry is None else entry
        stop, target = entry + 0.00318, entry - 0.00400
    return {
        "status": "OPEN", "direction": direction, "trade_id": "range-test-" + direction,
        "opened_at": OPENED.isoformat().replace("+00:00", "Z"),
        "expires_at": (OPENED + timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
        "entry": entry, "stop": stop, "target": target,
        "entry_score": 70.0, "entry_confidence": 0.5,
    }


class RangeAwareProductionExitTests(unittest.TestCase):
    def assess(self, pos, rows, when=None):
        return range_exit.assess(
            pos, rows, when or rows[-1].timestamp,
            exit_price_at_mid=lifecycle.execution_risk_price,
        )

    def test_profitable_long_exits_near_upper_band_in_range(self):
        rows = history()
        signal = self.assess(position(), rows)
        self.assertTrue(signal["close"], signal)
        self.assertEqual(signal["regime"], "RANGE")
        self.assertGreaterEqual(signal["profit_pips"], 7)
        exit_trade = v14.evaluate_position(position(), rows, rows[-1].timestamp)
        self.assertIsNotNone(exit_trade)
        self.assertEqual(exit_trade["exit_reason"], "DYNAMIC_RANGE_PROFIT_EXIT")
        self.assertGreater(exit_trade["r_multiple"], 0)
        self.assertEqual(exit_trade["monitor"]["dynamic_exit"]["policy"], "H1_RANGE_AWARE_V1")

    def test_profitable_short_exits_near_lower_band_in_range(self):
        rows = history(last=1.1000)
        signal = self.assess(position("SHORT"), rows)
        self.assertTrue(signal["close"], signal)
        trade = v14.evaluate_position(position("SHORT"), rows, rows[-1].timestamp)
        self.assertEqual(trade["exit_reason"], "DYNAMIC_RANGE_PROFIT_EXIT")
        self.assertGreater(trade["r_multiple"], 0)

    def test_trending_h1_stays_with_original_exit_logic(self):
        rows = history(last=1.1080, trend=True)
        pos = position(entry=1.1071)
        signal = self.assess(pos, rows)
        self.assertFalse(signal["close"], signal)
        self.assertEqual(signal["reason"], "trend_or_unreliable_range")

    def test_incomplete_h1_falls_back_to_legacy(self):
        rows = history(complete=False)
        signal = self.assess(position(), rows)
        self.assertFalse(signal["close"])
        self.assertEqual(signal["reason"], "insufficient_complete_h1")

    def test_insufficient_profit_does_not_force_exit(self):
        rows = history(last=1.1004)
        signal = self.assess(position(entry=1.1002), rows)
        self.assertFalse(signal["close"])
        self.assertEqual(signal["reason"], "not_profitable_near_outer_band")

    def test_future_bars_not_used(self):
        rows = history()
        asof = rows[-1].timestamp
        expected = self.assess(position(), rows, asof)
        rows.append(bar(asof + timedelta(hours=1), 1.1400))
        observed = self.assess(position(), rows, asof)
        self.assertEqual(expected, observed)

    def test_hard_stop_loss_priority_over_range_profit(self):
        rows = history()
        last = rows[-1]
        rows[-1] = bar(last.timestamp, 1.1004, low=1.0960)
        trade = v14.evaluate_position(position(), rows, last.timestamp)
        self.assertEqual(trade["exit_reason"], "STOP_LOSS")

    def test_closed_h1_age_must_be_recent(self):
        rows = history()[:-30]
        now = rows[-1].timestamp + timedelta(hours=3)
        signal = self.assess(position(), rows, now)
        self.assertFalse(signal["close"])

    def test_existing_dynamic_exit_remains_for_sparse_history(self):
        rows = [
            bar(OPENED, 1.1000),
            bar(OPENED + timedelta(minutes=20), 1.0970),
            bar(OPENED + timedelta(minutes=30), 1.0969),
        ]
        trade = v14.evaluate_position(position(), rows, rows[-1].timestamp)
        self.assertIsNotNone(trade)
        self.assertIn(trade["exit_reason"], {"DYNAMIC_RISK_EXIT", "STOP_LOSS"})


if __name__ == "__main__":
    unittest.main()
