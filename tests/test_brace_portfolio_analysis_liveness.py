from datetime import datetime, timezone
import unittest

from scripts.brace_portfolio_analysis_liveness import (
    assess_analysis_liveness,
    latest_required_slot,
)


class BraceAnalysisLivenessTests(unittest.TestCase):
    def test_weekday_analysis_before_latest_required_slot_is_overdue(self):
        now = datetime(2026, 9, 18, 17, 37, tzinfo=timezone.utc)
        result = assess_analysis_liveness(
            {"generated_at": "2026-09-17T00:42:50+00:00"},
            now,
        )
        self.assertTrue(result["overdue"])
        self.assertEqual("ANALYSIS_OVERDUE", result["status"])
        self.assertEqual("2026-09-17T22:30:00+00:00", result["latest_required_slot"])

    def test_fresh_recovery_analysis_clears_overdue(self):
        now = datetime(2026, 9, 18, 17, 37, tzinfo=timezone.utc)
        result = assess_analysis_liveness(
            {"generated_at": "2026-09-18T17:30:00+00:00"},
            now,
        )
        self.assertFalse(result["overdue"])
        self.assertEqual("CURRENT", result["status"])

    def test_weekend_uses_friday_as_latest_required_slot(self):
        now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)  # Sunday
        slot = latest_required_slot(now)
        self.assertEqual("2026-09-18T22:30:00+00:00", slot.isoformat(timespec="seconds"))

    def test_monday_before_daily_slot_still_requires_friday(self):
        now = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)  # Monday
        slot = latest_required_slot(now)
        self.assertEqual("2026-09-18T22:30:00+00:00", slot.isoformat(timespec="seconds"))

    def test_grace_window_does_not_mark_new_slot_due_too_early(self):
        now = datetime(2026, 9, 18, 23, 15, tzinfo=timezone.utc)
        slot = latest_required_slot(now)
        self.assertEqual("2026-09-17T22:30:00+00:00", slot.isoformat(timespec="seconds"))


if __name__ == "__main__":
    unittest.main()
