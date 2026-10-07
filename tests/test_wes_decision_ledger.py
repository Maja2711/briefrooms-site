import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts import wes_decision_ledger as ledger


class WesDecisionLedgerTests(unittest.TestCase):
    def payload(self, decided_at="2026-10-07T10:00:00+02:00"):
        return {
            "decided_at": decided_at,
            "entry_not_before": decided_at,
            "decision": {
                "strategy_id": "daily_weekly_blend",
                "direction": "short",
                "raw_score": -72.0,
                "confidence": 0.73,
            },
            "fresh_signal": {"score": -72.0, "signals": {"last_close": 1.12}},
            "weekly_signal": {"score": -44.0},
            "macro_context": {"direction": "short"},
            "entry_price_plan": {
                "execution_mode": "limit_pullback",
                "target_price": 1.124,
                "reference_price": 1.12,
                "expires_at": "2026-10-07T11:00:00+02:00",
            },
            "authorization_basis": {
                "strategy_id": "daily_weekly_blend",
                "direction": "short",
                "directional_admission_passed": True,
            },
            "rule": "execute_only_when_frozen_entry_target_is_touched_after_decision",
        }

    def test_freeze_is_idempotent_and_full_payload_is_hashed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ledger.json"
            first = ledger.append_frozen_decision(
                self.payload(), week_id="2026-W41", instrument_id="eurusd", path=path
            )
            second = ledger.append_frozen_decision(
                self.payload(), week_id="2026-W41", instrument_id="eurusd", path=path
            )
            self.assertEqual(first["decision_id"], second["decision_id"])
            self.assertEqual(first["payload_hash"], second["payload_hash"])
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(1, len(stored["records"]))
            self.assertEqual(
                ledger.sha256(stored["records"][0]["payload"]),
                stored["records"][0]["payload_hash"],
            )
            ledger.verify_ledger(path)

    def test_mutating_any_frozen_payload_field_fails_integrity(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ledger.json"
            frozen = ledger.append_frozen_decision(
                self.payload(), week_id="2026-W41", instrument_id="eurusd", path=path
            )
            tampered = copy.deepcopy(frozen)
            tampered["entry_price_plan"]["target_price"] = 1.13
            with self.assertRaises(ledger.WesDecisionLedgerError):
                ledger.assert_pending_integrity(tampered)

    def test_successor_gets_new_decision_id_and_predecessor_is_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ledger.json"
            first = ledger.append_frozen_decision(
                self.payload(), week_id="2026-W41", instrument_id="eurusd", path=path
            )
            updated = ledger.payload_from_pending(first)
            updated["decided_at"] = "2026-10-07T10:20:00+02:00"
            updated["entry_price_plan"]["execution_mode"] = "market_now"
            updated["entry_price_plan"]["order_type"] = "market"
            second = ledger.successor_decision(
                first,
                updated,
                week_id="2026-W41",
                instrument_id="eurusd",
                decision_kind="LIMIT_TO_MARKET_PROMOTION",
                path=path,
            )
            self.assertNotEqual(first["decision_id"], second["decision_id"])
            self.assertEqual(first["decision_id"], second["predecessor_decision_id"])
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(2, len(stored["records"]))
            self.assertEqual("limit_pullback", stored["records"][0]["payload"]["entry_price_plan"]["execution_mode"])
            self.assertEqual("market_now", stored["records"][1]["payload"]["entry_price_plan"]["execution_mode"])
            ledger.verify_ledger(path)

    def test_ledger_detects_historical_payload_rewrite(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ledger.json"
            ledger.append_frozen_decision(
                self.payload(), week_id="2026-W41", instrument_id="eurusd", path=path
            )
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["records"][0]["payload"]["decision"]["direction"] = "long"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ledger.WesDecisionLedgerError):
                ledger.verify_ledger(path)


if __name__ == "__main__":
    unittest.main()
