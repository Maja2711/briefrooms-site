from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import investments_wes_lifecycle as lifecycle


class ExpiredNoEntryLifecycleTests(unittest.TestCase):
    def run_case(self, row):
        with tempfile.TemporaryDirectory() as tmp:
            weekly_dir = Path(tmp)
            week = {
                "week_id": "2026-W37",
                "market_window": {"exit_target_local": "2026-09-11T22:00:00+02:00"},
                "instruments": [row],
            }
            path = weekly_dir / "2026-W37.json"
            path.write_text(json.dumps(week), encoding="utf-8")
            with patch.object(lifecycle, "WEEKLY_DIR", weekly_dir):
                changed = lifecycle.settle_due_positions(
                    datetime.fromisoformat("2026-09-14T13:00:00+02:00")
                )
            saved = json.loads(path.read_text(encoding="utf-8"))["instruments"][0]
            return changed, saved

    def test_directional_decision_without_entry_expires_after_deadline(self):
        changed, row = self.run_case({
            "instrument_id": "sp500_futures",
            "symbol": "ES=F",
            "direction": "short",
            "entry_price": None,
            "exit_price": None,
            "trade_status": "planned",
            "pending_entry_decision": {"decision": {"direction": "short"}},
            "continuous_exposure_active": True,
            "continuous_exposure_status": "open",
            "next_entry_status": "pending",
        })
        self.assertTrue(changed)
        self.assertEqual("expired_no_entry", row["trade_status"])
        self.assertEqual("no_entry", row["execution_outcome"])
        self.assertEqual("short", row["expired_entry_direction"])
        self.assertIsNone(row["pending_entry_decision"])
        self.assertFalse(row["continuous_exposure_active"])
        self.assertEqual("closed", row["continuous_exposure_status"])
        self.assertIsNone(row["entry_price"])
        self.assertIsNone(row["exit_price"])
        self.assertNotEqual("no_trade", row.get("result"))

    def test_neutral_forecast_with_expired_wes_authorization_is_terminalized(self):
        changed, row = self.run_case({
            "instrument_id": "sp500_futures",
            "symbol": "ES=F",
            "direction": "neutral",
            "entry_price": None,
            "exit_price": None,
            "trade_status": "planned",
            "result": "no_trade",
            "result_value": 0.0,
            "result_percent": 0.0,
            "pending_entry_decision": None,
            "wes_entry_authorization": {
                "authorized_at": "2026-09-08T06:39:37+02:00",
                "expires_at": "2026-09-08T06:59:37+02:00",
                "candidate": {"direction": "long", "entry_class": "midweek_trigger"},
            },
            "continuous_exposure_active": True,
            "continuous_exposure_status": "open",
            "next_entry_status": "pending",
        })
        self.assertTrue(changed)
        self.assertEqual("expired_no_entry", row["trade_status"])
        self.assertEqual("no_entry", row["execution_outcome"])
        self.assertEqual("long", row["expired_entry_direction"])
        self.assertEqual("no_trade", row["result"])
        self.assertEqual(0.0, row["result_value"])
        self.assertEqual(0.0, row["result_percent"])
        self.assertIsNone(row["entry_price"])
        self.assertIsNone(row["exit_price"])
        self.assertFalse(row["continuous_exposure_active"])
        self.assertEqual("closed", row["continuous_exposure_status"])

    def test_plain_neutral_no_trade_closes_without_fake_directional_expiry(self):
        changed, row = self.run_case({
            "instrument_id": "sp500_futures",
            "symbol": "ES=F",
            "direction": "neutral",
            "entry_price": None,
            "exit_price": None,
            "trade_status": "planned",
            "result": "no_trade",
            "result_value": 0.0,
            "result_percent": 0.0,
            "pending_entry_decision": None,
            "continuous_exposure_active": False,
            "continuous_exposure_status": "closed",
        })
        self.assertTrue(changed)
        self.assertEqual("no_trade", row["trade_status"])
        self.assertIsNone(row.get("execution_outcome"))
        self.assertIsNone(row.get("expired_entry_direction"))
        self.assertIsNone(row["entry_price"])
        self.assertIsNone(row["exit_price"])


if __name__ == "__main__":
    unittest.main()
