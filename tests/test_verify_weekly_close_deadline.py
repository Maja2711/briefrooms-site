import importlib.util
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "verify_weekly_close_deadline.py"
SPEC = importlib.util.spec_from_file_location("verify_weekly_close_deadline", MODULE_PATH)
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)

TZ = ZoneInfo("Europe/Warsaw")


def row(**updates):
    value = {
        "instrument_id": "eurusd",
        "direction": "short",
        "entry_price": 1.15,
        "exit_price": None,
        "exit_captured_at": None,
        "trade_status": "open",
        "continuous_exposure_active": True,
        "continuous_exposure_status": "open",
        "risk_status": "open_multi_instrument_continuous_exposure",
        "pending_entry_decision": None,
    }
    value.update(updates)
    return value


def week(item, week_id="2026-W32"):
    return {
        "week_id": week_id,
        "market_window": {
            "exit_target_local": "2026-08-07T22:00:00+02:00",
        },
        "instruments": [item],
    }


class WeeklyCloseDeadlineTests(unittest.TestCase):
    def test_open_position_is_allowed_before_deadline(self):
        now = datetime(2026, 8, 7, 21, 59, tzinfo=TZ)
        self.assertEqual([], guard.lifecycle_errors(week(row()), now))

    def test_open_position_is_rejected_after_deadline(self):
        now = datetime(2026, 8, 7, 22, 1, tzinfo=TZ)
        errors = guard.lifecycle_errors(week(row()), now)
        self.assertTrue(any("has no numeric exit" in error for error in errors))
        self.assertTrue(any("continuous_exposure_active remains true" in error for error in errors))

    def test_placeholder_exit_is_not_treated_as_a_close(self):
        now = datetime(2026, 8, 7, 22, 1, tzinfo=TZ)
        errors = guard.lifecycle_errors(week(row(exit_price="week_in_progress")), now)
        self.assertTrue(any("has no numeric exit" in error for error in errors))

    def test_closed_position_requires_consistent_closed_metadata(self):
        now = datetime(2026, 8, 7, 22, 1, tzinfo=TZ)
        closed = row(
            exit_price=1.16,
            exit_captured_at="2026-08-07T22:00:00+02:00",
            trade_status="closed",
            continuous_exposure_active=False,
            continuous_exposure_status="closed",
            risk_status="closed",
        )
        self.assertEqual([], guard.lifecycle_errors(week(closed), now))

    def test_stale_exposure_metadata_is_rejected_even_with_exit_price(self):
        now = datetime(2026, 8, 7, 22, 1, tzinfo=TZ)
        stale = row(
            exit_price=1.16,
            exit_captured_at="2026-08-07T22:00:00+02:00",
            trade_status="closed",
        )
        errors = guard.lifecycle_errors(week(stale), now)
        self.assertTrue(any("continuous_exposure_active remains true" in error for error in errors))
        self.assertTrue(any("continuous_exposure_status remains" in error for error in errors))
        self.assertTrue(any("risk_status remains" in error for error in errors))

    def test_pending_entry_is_rejected_after_deadline(self):
        now = datetime(2026, 8, 7, 22, 1, tzinfo=TZ)
        pending = row(
            entry_price=None,
            trade_status="planned",
            continuous_exposure_active=False,
            continuous_exposure_status="pending",
            risk_status="closed",
            pending_entry_decision={"decision": {"direction": "short"}},
        )
        errors = guard.lifecycle_errors(week(pending), now)
        self.assertTrue(any("pending directional entry" in error for error in errors))

    def test_missing_current_ledger_is_rejected_after_deadline(self):
        now = datetime(2026, 8, 7, 22, 1, tzinfo=TZ)
        with tempfile.TemporaryDirectory() as temp_dir:
            errors = guard.verify_directory(Path(temp_dir), now)
        self.assertTrue(any("current weekly ledger is missing" in error for error in errors))

    def test_directory_scan_accepts_a_closed_current_week(self):
        now = datetime(2026, 8, 7, 22, 1, tzinfo=TZ)
        closed = row(
            exit_price=1.16,
            exit_captured_at="2026-08-07T22:00:00+02:00",
            trade_status="closed",
            continuous_exposure_active=False,
            continuous_exposure_status="closed",
            risk_status="closed",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "2026-W32.json"
            path.write_text(json.dumps(week(closed)), encoding="utf-8")
            errors = guard.verify_directory(Path(temp_dir), now)
        self.assertEqual([], errors)


if __name__ == "__main__":
    unittest.main()
