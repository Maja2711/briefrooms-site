import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import investments_wes_daily_carry_review as carry_review
import investments_wes_guarded_lifecycle as guarded

TZ = ZoneInfo("Europe/Warsaw")


def authorized_item(actual_direction="long", authorized_direction="long"):
    return {
        "instrument_id": "EURUSD",
        "symbol": "EURUSD=X",
        "direction": actual_direction,
        "entry_price": 1.10,
        "entry_captured_at": "2026-08-14T14:25:00+02:00",
        "exit_price": None,
        "wes_entry_authorization": {
            "authorization_type": "early_close_reentry",
            "expires_at": "2026-08-14T15:00:00+02:00",
            "candidate": {"direction": authorized_direction},
        },
    }


def qualified_carry(deadline="2026-08-21T14:25:00+02:00"):
    item = authorized_item()
    item.update({
        "wes_early_reentry_source_exit_at": "2026-08-11T12:00:00+02:00",
        "wes_early_reentry_qualified": True,
        "wes_weekend_carry_allowed": True,
        "wes_holding_policy": "early_close_reentry_7_calendar_days",
        "wes_holding_deadline_local": deadline,
    })
    return item


class WesLifecycleHardeningTests(unittest.TestCase):
    def test_matching_authorized_direction_passes(self):
        now = datetime(2026, 8, 14, 14, 30, tzinfo=TZ)
        guarded.validate_open_reentry_authorization(authorized_item(), now)

    def test_direction_mismatch_fails_closed(self):
        now = datetime(2026, 8, 14, 14, 30, tzinfo=TZ)
        with self.assertRaises(RuntimeError):
            guarded.validate_open_reentry_authorization(authorized_item("short", "long"), now)

    def test_missing_authorized_direction_fails_closed(self):
        now = datetime(2026, 8, 14, 14, 30, tzinfo=TZ)
        with self.assertRaises(RuntimeError):
            guarded.validate_open_reentry_authorization(authorized_item("long", ""), now)

    def test_expired_authorization_fails_closed(self):
        now = datetime(2026, 8, 14, 15, 1, tzinfo=TZ)
        with self.assertRaises(RuntimeError):
            guarded.validate_open_reentry_authorization(authorized_item(), now)

    def test_non_early_authorization_is_not_subject_to_reentry_guard(self):
        item = authorized_item("short", "long")
        item["wes_entry_authorization"]["authorization_type"] = "normal_weekly_entry"
        guarded.validate_open_reentry_authorization(item, datetime(2026, 8, 14, 14, 30, tzinfo=TZ))

    def test_qualified_prior_week_carry_is_active_before_rolling_deadline(self):
        now = datetime(2026, 8, 17, 10, 0, tzinfo=TZ)
        self.assertTrue(carry_review.active_carry_item(qualified_carry(), now))

    def test_qualified_prior_week_carry_is_inactive_after_rolling_deadline(self):
        now = datetime(2026, 8, 21, 14, 26, tzinfo=TZ)
        self.assertFalse(carry_review.active_carry_item(qualified_carry(), now))

    def test_forged_weekend_carry_is_rejected(self):
        item = qualified_carry()
        item["wes_early_reentry_qualified"] = False
        now = datetime(2026, 8, 17, 10, 0, tzinfo=TZ)
        self.assertFalse(carry_review.active_carry_item(item, now))

    def test_prior_week_ledger_is_discovered_for_daily_review(self):
        now = datetime(2026, 8, 17, 23, 10, tzinfo=TZ)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "2026-W33.json"
            path.write_text(json.dumps({"week_id": "2026-W33", "instruments": [qualified_carry()]}), encoding="utf-8")
            found = carry_review.carry_paths(now, Path(tmp))
            self.assertEqual([path], found)


if __name__ == "__main__":
    unittest.main()
