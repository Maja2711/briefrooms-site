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


    def test_production_admission_blocks_verified_h1_veto(self):
        # Import the same module instance as the production unittest suite.
        # A second import as scripts.daily_eurusd_spot_v19 re-runs _install(),
        # rebinding the v18/base hooks and creating two different manifests.
        import daily_eurusd_spot_v19 as v19
        from unittest.mock import patch
        from types import SimpleNamespace
        from datetime import timedelta
        now = h1.START + timedelta(hours=40)
        candidate = v19.DailyEngineOutput(
            instrument=v19.base.EURUSD, timestamp=now.isoformat(), direction="LONG",
            score=0.8, confidence=0.8, entry=1.12, stop=1.11, target=1.14,
            horizon="intraday_to_27h", engine_version=v19.ENGINE_VERSION,
            status="OPEN", decision_mode="WITH", metadata={
            "bollinger_h1": {"available": True, "allowed": False, "reason": "h1_trend_not_confirmed"},
            "final_decision": {},
        }).validate()
        with patch.object(v19.belief_decision, "calendar_safety", return_value={"blocked": False}), \
             patch.object(v19.v16, "_apply_same_thesis_guard", return_value=candidate), \
             patch.object(v19.production_policy, "reflection_context", return_value={}), \
             patch.object(v19.production_policy, "reentry_context", return_value={"criteria": {}, "version_id": "test"}), \
             patch.object(v19.production_policy, "reentry_contenders", return_value={}), \
             patch.object(v19.reentry_safety, "evaluate", return_value={"required": False, "allowed": True}), \
             patch.object(v19, "_policy_state", return_value={}):
            result = v19._execution_admission(candidate, None, observed_at=now)
        self.assertFalse(result["allowed"])
        self.assertIn("bollinger_h1_h1_trend_not_confirmed", result["reasons"])
        self.assertEqual(candidate.direction, "LONG")

    def test_future_h1_is_not_admissible(self):
        from datetime import timedelta
        now = h1.START + timedelta(hours=40)
        result = h1.bb.evaluate(h1.bars([1.12 + 0.0001 * i for i in range(40)]), "LONG", now - timedelta(minutes=5))
        self.assertTrue(result["available"])
        self.assertEqual(result["last_closed_h1"], (h1.START + timedelta(hours=39)).isoformat())
        self.assertEqual(result["age_minutes"], 55.0)

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
