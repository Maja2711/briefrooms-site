from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import audit_weekly_model_runtime as runtime

TZ = ZoneInfo("Europe/Warsaw")


class PlannedEntryAuditWindowTests(unittest.TestCase):
    def item(self):
        return {
            "direction": "long",
            "entry_price": None,
            "trade_status": "planned",
            "pending_entry_decision": {
                "decided_at": "2026-08-10T08:58:59+02:00",
                "entry_not_before": "2026-08-10T08:58:59+02:00",
                "decision": {"direction": "long"},
            },
        }

    def test_planned_directional_entry_is_valid_before_deadline(self):
        latest = datetime(2026, 8, 10, 10, 0, tzinfo=TZ)
        now = datetime(2026, 8, 10, 9, 49, tzinfo=TZ)
        self.assertTrue(runtime.planned_entry_is_valid(self.item(), latest, now))

    def test_missing_entry_becomes_invalid_after_deadline(self):
        latest = datetime(2026, 8, 10, 10, 0, tzinfo=TZ)
        now = datetime(2026, 8, 10, 10, 0, 1, tzinfo=TZ)
        self.assertFalse(runtime.planned_entry_is_valid(self.item(), latest, now))

    def test_missing_pending_decision_is_never_exempt(self):
        item = self.item()
        item["pending_entry_decision"] = None
        latest = datetime(2026, 8, 10, 10, 0, tzinfo=TZ)
        now = datetime(2026, 8, 10, 9, 49, tzinfo=TZ)
        self.assertFalse(runtime.planned_entry_is_valid(item, latest, now))


if __name__ == "__main__":
    unittest.main()
