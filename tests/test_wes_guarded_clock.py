"""Regression: guarded WES must use the available canonical Warsaw clock."""
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import investments_wes_guarded_lifecycle as guarded


class GuardedWesClockTests(unittest.TestCase):
    def test_empty_week_still_runs_post_entry_validation(self):
        now = datetime(2026, 11, 19, 12, 0, tzinfo=ZoneInfo("Europe/Warsaw"))
        path = Path("/tmp/wes-clock-test.json")
        with patch.object(guarded.base.legacy, "now_local", return_value=now) as clock, \
             patch.object(guarded.base.v4, "read", return_value={"instruments": []}), \
             patch.object(guarded, "_BASE_APPLY_OPEN_REENTRY_METADATA", return_value=False) as apply:
            self.assertFalse(guarded.apply_open_reentry_metadata(path))
        clock.assert_called_once_with()
        apply.assert_called_once_with(path)

    def test_early_reentry_validation_uses_canonical_clock(self):
        now = datetime(2026, 11, 19, 12, 0, tzinfo=ZoneInfo("Europe/Warsaw"))
        item = {
            "instrument_id": "eurusd", "direction": "short",
            "wes_entry_authorization": {
                "authorization_type": guarded.base.AUTHORIZATION_TYPE,
                "candidate": {"direction": "short"},
            },
        }
        with patch.object(guarded.base.v5, "open_position", return_value=True), \
             patch.object(guarded.base.legacy, "now_local", return_value=now), \
             patch.object(guarded.base, "early_reentry_authorized", return_value=True) as authorized:
            guarded.validate_open_reentry_authorization(item)
        authorized.assert_called_once_with(item, now)


if __name__ == "__main__":
    unittest.main()
