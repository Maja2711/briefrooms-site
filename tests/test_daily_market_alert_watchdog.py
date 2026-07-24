from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from scripts.check_daily_market_alert import expected_mode, slot_complete

UTC = timezone.utc
MOMENT = datetime(2026, 7, 27, 20, 30, tzinfo=UTC)
TARGETS = (
    datetime(2026, 7, 27, 14, 0, tzinfo=UTC),
    datetime(2026, 7, 27, 19, 30, tzinfo=UTC),
)


def payload(edition="open", updated_at="2026-07-27T16:00:00+02:00"):
    return {
        "schema_version": "2.0",
        "session_date": "2026-07-27",
        "edition": edition,
        "updated_at": updated_at,
        "instruments": [{"id": key} for key in ("sp500", "brent", "us10y")],
        "preclose_check": None,
    }


class DailyMarketAlertWatchdogTests(unittest.TestCase):
    @patch("scripts.check_daily_market_alert.session_targets", return_value=TARGETS)
    def test_auto_selects_due_slots_after_grace(self, _targets):
        self.assertEqual(expected_mode(datetime(2026, 7, 27, 15, 0, tzinfo=UTC), "auto", 45), "open")
        self.assertEqual(expected_mode(MOMENT, "auto", 45), "preclose")

    @patch("scripts.check_daily_market_alert.session_targets", return_value=TARGETS)
    def test_open_requires_current_session_and_fresh_timestamp(self, _targets):
        self.assertTrue(slot_complete(payload(), "open", MOMENT)[0])
        stale = payload(updated_at="2026-07-24T16:00:00+02:00")
        self.assertFalse(slot_complete(stale, "open", MOMENT)[0])
        wrong_day = payload()
        wrong_day["session_date"] = "2026-07-24"
        self.assertFalse(slot_complete(wrong_day, "open", MOMENT)[0])

    @patch("scripts.check_daily_market_alert.session_targets", return_value=TARGETS)
    def test_preclose_accepts_material_edition_or_no_change_check(self, _targets):
        material = payload("preclose", "2026-07-27T21:30:00+02:00")
        self.assertTrue(slot_complete(material, "preclose", MOMENT)[0])
        checked = payload()
        checked["preclose_check"] = {"checked_at": "2026-07-27T21:31:00+02:00"}
        self.assertTrue(slot_complete(checked, "preclose", MOMENT)[0])

    @patch("scripts.check_daily_market_alert.session_targets", return_value=TARGETS)
    def test_preclose_rejects_missing_check(self, _targets):
        self.assertFalse(slot_complete(payload(), "preclose", MOMENT)[0])


if __name__ == "__main__":
    unittest.main()
