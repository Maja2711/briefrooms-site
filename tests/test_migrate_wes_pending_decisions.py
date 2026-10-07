import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts import migrate_wes_pending_decisions as migrate
from scripts import wes_decision_ledger as ledger


class WesPendingDecisionMigrationTests(unittest.TestCase):
    def test_current_pending_is_sealed_without_economic_change_and_retry_is_idempotent(self):
        tz = ZoneInfo("Europe/Warsaw")
        now = datetime(2026, 10, 7, 20, 0, tzinfo=tz)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            weekly = root / "weekly"
            weekly.mkdir()
            ledger_path = root / "wes_decision_ledger.json"
            ledger_path.write_text(json.dumps({
                "schema_version": ledger.SCHEMA_VERSION,
                "records": [],
                "head_record_hash": None,
            }), encoding="utf-8")
            week_path = weekly / "2026-W41.json"
            pending = {
                "decided_at": "2026-10-07T18:00:00+02:00",
                "entry_not_before": "2026-10-07T18:00:00+02:00",
                "decision": {"strategy_id": "weekly_trend", "direction": "long", "raw_score": 62.0},
                "fresh_signal": {"score": 62.0},
                "weekly_signal": {"score": 55.0},
                "macro_context": None,
                "entry_price_plan": {
                    "instrument_id": "sp500_futures",
                    "direction": "long",
                    "execution_mode": "limit_pullback",
                    "target_price": 7820.80737707,
                    "expires_at": "2026-10-07T21:00:00+02:00",
                },
                "authorization_basis": {
                    "strategy_id": "weekly_trend",
                    "direction": "long",
                    "directional_admission_passed": True,
                },
                "rule": "execute_only_when_frozen_entry_target_is_touched_after_decision",
            }
            week_path.write_text(json.dumps({
                "week_id": "2026-W41",
                "instruments": [{
                    "instrument_id": "sp500_futures",
                    "trade_status": "pending",
                    "pending_entry_decision": pending,
                }],
            }), encoding="utf-8")

            before_hash = ledger.sha256(pending)
            with patch.object(migrate, "WEEKLY", weekly), patch.object(ledger, "LEDGER_PATH", ledger_path):
                first = migrate.seal_current_pending(now=now, ledger_path=ledger_path)
                self.assertEqual(first["status"], "SEALED")
                self.assertEqual(first["sealed"][0]["target_price"], 7820.80737707)
                after = json.loads(week_path.read_text(encoding="utf-8"))
                sealed = after["instruments"][0]["pending_entry_decision"]
                self.assertEqual(sealed["entry_price_plan"]["target_price"], 7820.80737707)
                self.assertEqual(before_hash, sealed["payload_hash"])
                self.assertTrue(str(sealed["decision_id"]).startswith("wes-dec-"))
                ledger.assert_pending_integrity(sealed)

                second = migrate.seal_current_pending(now=now, ledger_path=ledger_path)
                self.assertEqual(second["status"], "NOTHING_TO_SEAL")
                stored = json.loads(ledger_path.read_text(encoding="utf-8"))
                self.assertEqual(len(stored["records"]), 1)
                ledger.verify_ledger(ledger_path)


if __name__ == "__main__":
    unittest.main()
