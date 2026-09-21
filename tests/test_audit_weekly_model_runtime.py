import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import audit_weekly_model_runtime as runtime


class WeeklyRuntimeIntegrityTests(unittest.TestCase):
    def test_forecast_only_planned_state_is_valid_before_week_deadline(self):
        item = {
            "instrument_id": "eurusd",
            "direction": "short",
            "trade_status": "planned",
            "entry_price": None,
        }
        now = datetime(2026, 9, 21, 9, 0, tzinfo=runtime.base.TZ)
        deadline = datetime(2026, 9, 25, 22, 0, tzinfo=runtime.base.TZ)
        self.assertTrue(runtime.planned_entry_is_valid(item, deadline, now))

    def test_pending_state_requires_frozen_execution_decision(self):
        item = {
            "instrument_id": "btcusd",
            "direction": "long",
            "trade_status": "pending",
            "entry_price": None,
        }
        now = datetime(2026, 9, 21, 9, 0, tzinfo=runtime.base.TZ)
        deadline = datetime(2026, 9, 25, 22, 0, tzinfo=runtime.base.TZ)
        self.assertFalse(runtime.planned_entry_is_valid(item, deadline, now))

    def test_valid_pending_state_is_not_a_missing_entry(self):
        item = {
            "instrument_id": "btcusd",
            "direction": "neutral",
            "trade_status": "pending",
            "entry_price": None,
            "pending_entry_decision": {
                "decided_at": "2026-09-21T09:00:00+02:00",
                "entry_not_before": "2026-09-21T09:00:00+02:00",
                "decision": {"direction": "long"},
            },
        }
        now = datetime(2026, 9, 21, 9, 1, tzinfo=runtime.base.TZ)
        deadline = datetime(2026, 9, 25, 22, 0, tzinfo=runtime.base.TZ)
        self.assertTrue(runtime.planned_entry_is_valid(item, deadline, now))
        self.assertEqual(runtime.base.item_violations(item), [])

    def test_unresolved_planned_state_is_not_valid_after_week_deadline(self):
        item = {
            "instrument_id": "eurusd",
            "direction": "short",
            "trade_status": "planned",
            "entry_price": None,
        }
        now = datetime(2026, 9, 25, 22, 1, tzinfo=runtime.base.TZ)
        deadline = datetime(2026, 9, 25, 22, 0, tzinfo=runtime.base.TZ)
        self.assertFalse(runtime.planned_entry_is_valid(item, deadline, now))


if __name__ == "__main__":
    unittest.main()
