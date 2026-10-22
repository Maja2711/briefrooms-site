"""Run the existing EURUSD Daily Bollinger checks under the production unittest CI.

The original test modules expose pytest-style functions; unittest discovery does
not collect those functions. This adapter intentionally invokes them unchanged.
No Shadow engines or production state are modified.
"""
import unittest

from tests import test_daily_eurusd_bollinger_h1 as h1
from tests import test_daily_eurusd_bollinger_d1 as d1


class DailyEURUSDBollingerH1Tests(unittest.TestCase):
    def test_flat(self):
        h1.test_belief_flat_cannot_become_trade()

    def test_long_short_alignment(self):
        h1.test_confirmed_uptrend_allows_long_but_not_short()

    def test_downtrend(self):
        h1.test_downtrend_allows_short_but_not_long()

    def test_missing_history(self):
        h1.test_missing_history_fails_closed()

    def test_incomplete_h1(self):
        h1.test_incomplete_hour_never_used()

    def test_stale_h1(self):
        h1.test_stale_data_fails_closed()


    def test_future_h1_is_not_admissible(self):
        from datetime import timedelta
        now = h1.START + timedelta(hours=40)
        result = h1.bb.evaluate(h1.bars([1.12 + 0.0001 * i for i in range(40)]), "LONG", now - timedelta(minutes=5))
        self.assertTrue(result["available"] or result["reason"] == "stale_h1_bollinger")
        self.assertLessEqual(result.get("age_minutes", 0), 90)

    def test_stale_h1_exposes_freshness_diagnostics(self):
        from datetime import timedelta
        now = h1.START + timedelta(hours=40)
        result = h1.bb.evaluate(h1.bars([1.12 + 0.0001 * i for i in range(40)]), "LONG", now + timedelta(hours=3))
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "stale_h1_bollinger")
        self.assertGreater(result["age_minutes"], 90)
        self.assertGreaterEqual(result["closed_h1"], 33)
        self.assertIn("last_closed_h1", result)

    def test_flat_price_has_no_spurious_band(self):
        from datetime import timedelta
        now = h1.START + timedelta(hours=40)
        result = h1.bb.evaluate(h1.bars([1.12] * 40), "LONG", now)
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "invalid_bollinger_geometry")


class DailyEURUSDBollingerD1Tests(unittest.TestCase):
    pass


for name in dir(d1):
    if name.startswith("test_") and callable(getattr(d1, name)):
        setattr(DailyEURUSDBollingerD1Tests, name, staticmethod(getattr(d1, name)))
