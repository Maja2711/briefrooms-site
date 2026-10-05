from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from scripts.belief_market_data_adapter import Bar
from scripts.daily_eurusd_fast_lifecycle import evaluate_open_position, close_output


def payload(direction="SHORT", opened=None):
    opened = opened or datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)
    stop = 1.1280 if direction == "SHORT" else 1.1220
    target = 1.1190 if direction == "SHORT" else 1.1310
    return {
        "schema_version": "daily-engine-output-v1",
        "instrument": "EUR/USD",
        "timestamp": opened.isoformat().replace("+00:00", "Z"),
        "direction": direction,
        "score": 35.0 if direction == "SHORT" else 65.0,
        "confidence": 0.4,
        "entry": 1.1250,
        "stop": stop,
        "target": target,
        "horizon": "intraday_to_27h",
        "engine_version": "eurusd-daily-spot-v1.9.0",
        "status": "OPEN",
        "decision_mode": "WITH",
        "metadata": {
            "position": {
                "trade_id": "eurusd:test",
                "status": "OPEN",
                "direction": direction,
                "opened_at": opened.isoformat().replace("+00:00", "Z"),
                "expires_at": (opened + timedelta(hours=27)).isoformat().replace("+00:00", "Z"),
                "entry": 1.1250,
                "stop": stop,
                "target": target,
                "entry_score": 35.0 if direction == "SHORT" else 65.0,
                "entry_confidence": 0.4,
                "engine_version": "eurusd-daily-spot-v1.9.0",
            },
            "runtime_projection": {"engine_version": "eurusd-daily-spot-v1.9.0"},
        },
    }


class DailyEurusdFastLifecycleTests(unittest.TestCase):
    def test_short_stop_is_detected_without_entry_authority(self):
        p = payload("SHORT")
        opened = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)
        bars = [
            Bar(timestamp=opened + timedelta(minutes=1), open=1.1250, high=1.1281, low=1.1240, close=1.1275),
        ]
        trade = evaluate_open_position(p, bars, bars[-1].timestamp)
        self.assertIsNotNone(trade)
        self.assertEqual(trade["exit_reason"], "STOP_LOSS")
        self.assertEqual(trade["trade_id"], "eurusd:test")

    def test_hard_27h_exit_uses_first_tradable_bar_after_expiry(self):
        opened = datetime(2026, 10, 2, 16, 16, 29, tzinfo=timezone.utc)
        p = payload("SHORT", opened)
        after = opened + timedelta(hours=54, minutes=44)
        bars = [
            Bar(timestamp=opened + timedelta(hours=1), open=1.1250, high=1.1260, low=1.1240, close=1.1255),
            Bar(timestamp=after, open=1.1262, high=1.1263, low=1.1261, close=1.12625),
        ]
        trade = evaluate_open_position(p, bars, bars[-1].timestamp)
        self.assertIsNotNone(trade)
        self.assertEqual(trade["exit_reason"], "TIME_EXIT_27H")
        self.assertEqual(trade["closed_at"], after.isoformat().replace("+00:00", "Z"))

    def test_no_trigger_keeps_position_open(self):
        p = payload("SHORT")
        opened = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)
        bars = [
            Bar(timestamp=opened + timedelta(minutes=1), open=1.1250, high=1.1260, low=1.1245, close=1.1252),
        ]
        self.assertIsNone(evaluate_open_position(p, bars, bars[-1].timestamp))

    def test_close_projection_removes_directional_geometry(self):
        p = payload("SHORT")
        trade = {
            "trade_id": "eurusd:test",
            "direction": "SHORT",
            "opened_at": "2026-10-01T10:00:00Z",
            "closed_at": "2026-10-01T11:00:00Z",
            "entry": 1.125,
            "stop": 1.128,
            "target": 1.119,
            "exit_price": 1.128,
            "exit_reason": "STOP_LOSS",
            "r_multiple": -1.0,
        }
        out = close_output(p, trade, {"trades": [trade]}, datetime(2026, 10, 1, 11, 0, tzinfo=timezone.utc))
        self.assertEqual(out["direction"], "FLAT")
        self.assertEqual(out["status"], "CLOSED_SL")
        self.assertIsNone(out["entry"])
        self.assertIsNone(out["stop"])
        self.assertIsNone(out["target"])
        self.assertFalse(out["metadata"]["fast_lifecycle"]["entry_authority"])


if __name__ == "__main__":
    unittest.main()
