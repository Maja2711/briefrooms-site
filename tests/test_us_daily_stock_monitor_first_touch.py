from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from scripts import us_daily_stock_position_monitor as monitor

NY = ZoneInfo("America/New_York")


class UsDailyFirstTouchTests(unittest.TestCase):
    def setUp(self):
        self.position = {"entry": 100.0, "stop": 95.0, "target": 109.0}

    def row(self, minute: int, *, high: float, low: float, close: float = 100.0):
        return {
            "at": datetime(2026, 9, 2, 10, minute, tzinfo=NY),
            "open": 100.0,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000,
        }

    def test_target_first_wins_even_if_stop_is_hit_later(self):
        path = [
            self.row(1, high=109.2, low=99.0, close=108.5),
            self.row(2, high=101.0, low=94.5, close=95.0),
        ]
        touch = monitor.first_touch(self.position, path)
        self.assertEqual(touch["reason"], "target")
        self.assertFalse(touch["same_bar"])
        self.assertEqual(touch["bar"]["at"].minute, 1)

    def test_stop_first_wins_even_if_target_is_hit_later(self):
        path = [
            self.row(1, high=101.0, low=94.8, close=95.0),
            self.row(2, high=109.5, low=99.0, close=109.0),
        ]
        touch = monitor.first_touch(self.position, path)
        self.assertEqual(touch["reason"], "stop")
        self.assertFalse(touch["same_bar"])

    def test_same_minute_tp_sl_is_conservative_stop(self):
        touch = monitor.first_touch(
            self.position,
            [self.row(1, high=110.0, low=94.0, close=103.0)],
        )
        self.assertEqual(touch["reason"], "stop")
        self.assertTrue(touch["same_bar"])


if __name__ == "__main__":
    unittest.main()
