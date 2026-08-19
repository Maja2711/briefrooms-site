from __future__ import annotations

import unittest
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts import us_daily_stock as us

NY = ZoneInfo("America/New_York")


class UsDailyStockTests(unittest.TestCase):
    def test_market_calendar(self):
        cfg = {"non_session_dates": []}
        self.assertTrue(us.is_session_day(date(2026, 8, 19), cfg))
        self.assertFalse(us.is_session_day(date(2026, 7, 3), cfg))  # observed Independence Day
        self.assertFalse(us.is_session_day(date(2026, 11, 26), cfg))  # Thanksgiving

    def test_preopen_is_pending(self):
        cfg = us.load_json(us.CONFIG_PATH)
        now = datetime(2026, 8, 19, 9, 20, tzinfo=NY)
        with patch.object(us, "load_config", return_value=cfg):
            payload = us.generate(now)
        self.assertEqual(payload["decision"], "PENDING")
        self.assertFalse(payload["locked"])

    def test_candidate_uses_completed_session_and_hard_risk(self):
        cfg = us.load_json(us.CONFIG_PATH)
        expected = date(2026, 8, 18)
        bars = []
        start = date(2026, 5, 1)
        cursor = start
        price = 100.0
        while len(bars) < 80:
            if cursor.weekday() < 5:
                price *= 1.001
                bars.append(us.Bar(cursor, price - 0.7, price + 1.0, price - 1.0, price, 2_000_000))
            cursor = cursor.fromordinal(cursor.toordinal() + 1)
        bars[-1] = us.Bar(expected, 108.0, 110.0, 107.0, 109.0, 2_500_000)
        company = {"symbol": "TEST", "name": "Test Corp", "sector": "technology"}
        candidate = us.build_candidate(company, bars, expected, cfg)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["last_session"], "2026-08-18")
        self.assertGreaterEqual(candidate["reward_risk"], cfg["minimum_reward_risk"])

    def test_trade_contract_requires_current_session_snapshot(self):
        cfg = us.load_json(us.CONFIG_PATH)
        now = datetime(2026, 8, 19, 9, 40, tzinfo=NY)
        payload = us.base_payload(now, cfg, "TRADE", "x")
        payload["selection"] = {
            "symbol": "AAPL", "score": 75, "reference_price": 200, "entry_zone": [199, 201],
            "stop": 195, "target": 209, "reward_risk": 1.8, "thesis": "x", "why_now": "x",
            "sources": [{"url": "https://example.com"}], "review": {"approved": True},
            "market_snapshot": {"date": "2026-08-18"},
        }
        with self.assertRaises(us.PublicationError):
            us.validate_payload(payload, require_today=True, now=now)


if __name__ == "__main__":
    unittest.main()
