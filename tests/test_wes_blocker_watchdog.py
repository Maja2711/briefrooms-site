import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import wes_blocker_watchdog as watcher

TZ = ZoneInfo("Europe/Warsaw")
NOW = datetime(2026, 11, 11, 13, 0, tzinfo=TZ)


def week(candidate=None):
    return {"week_id": "2026-W46", "instruments": [
        {"instrument_id": iid, "trade_status": "no_trade",
         "wes_trigger_candidate": candidate or {"direction": "neutral",
             "raw_score": 0, "confirmations": 0,
             "directional_admission": {"passed": False}},
         "reentry_lock": {"active": True, "scope": "wes_no_trade_monitoring"}}
        for iid in watcher.INSTRUMENTS]}


def prices():
    return {"prices": {iid: {"price": 1.0, "timestamp": NOW.isoformat()}
                       for iid in watcher.INSTRUMENTS}}


class RecursiveWesBlockerTests(unittest.TestCase):
    def test_zero_trades_diagnosed_without_forced_entries_or_veto_release(self):
        result = watcher.evaluate(week(), prices(), {}, [], now=NOW)
        self.assertEqual("NO_TRADE_REVIEWED", result["status"])
        self.assertEqual(3, result["no_trade_instruments"])
        for item in result["instruments"].values():
            self.assertIn("MONITORING_LOCK_NOT_A_HARD_VETO", item["blocker_reasons"])
            self.assertIn("FEWER_THAN_TWO_INDEPENDENT_CONFIRMATIONS", item["blocker_reasons"])
            self.assertFalse(item["learning"]["automatic_unlock_allowed"])

    def test_active_pending_entry_with_stale_quote_requests_recovery(self):
        w = week()
        w["instruments"][0]["pending_entry_decision"] = {"decision": {"direction": "long"}}
        data = prices()
        data["prices"]["eurusd"]["timestamp"] = (NOW-timedelta(hours=1)).isoformat()
        result = watcher.evaluate(w, data, {}, [], now=NOW)
        self.assertEqual("RECOVERY_REQUIRED", result["status"])
        self.assertIn("eurusd:PENDING_ENTRY_LIVE_QUOTE_STALE", result["recovery_reasons"])

    def test_old_ecb_guard_diagnosed_for_recovery_not_auto_traded(self):
        result = watcher.evaluate(week(), prices(), {
            "eurusd_central_bank_event_risk": {
                "entry_guard_enforced": True,
                "diagnostics": {"minutes_until": -2900},
            }}, [], now=NOW)
        self.assertEqual("RECOVERY_REQUIRED", result["status"])
        self.assertIn("eurusd:OBSOLETE_CB_EVENT_STILL_VETOING", result["recovery_reasons"])
        self.assertTrue(result["auto_trade_forbidden"])

    def test_history_counts_only_real_entries_not_missed_profit(self):
        historical = [
            {"instruments": [{"instrument_id": "btcusd",
                              "trade_status": "no_trade",
                              "no_trade_reason": "insufficient_directional_confirmations"}]},
            {"instruments": [{"instrument_id": "btcusd",
                              "entry_price": 70000,
                              "entry_captured_at": "2026-10-01T11:00:00+02:00",
                              "trade_status": "closed"}]},
        ]
        result = watcher.evaluate(week(), prices(), {}, historical, now=NOW)
        past = result["instruments"]["btcusd"]["past"]
        self.assertEqual(1, past["no_trade_weeks"])
        self.assertEqual(1, past["executed_weeks"])
        self.assertEqual(0, past["verified_blocker_benefit_samples"])

    def test_carried_position_is_not_classified_as_missing_trade(self):
        result = watcher.evaluate(week(), prices(), {
            "instruments": {"btcusd": {"next_entry_status": "position_carried_from_prior_week"}}
        }, [], now=NOW)
        self.assertEqual("TRADE_OR_CARRY", result["instruments"]["btcusd"]["status"])
        self.assertEqual(2, result["no_trade_instruments"])

    def test_friday_observation_marks_weekly_zero_trade_audit(self):
        friday = datetime(2026, 11, 13, 20, 0, tzinfo=TZ)
        result = watcher.evaluate(week(), prices(), {}, [], now=friday)
        self.assertTrue(result["weekly_no_trade_review_required"])


if __name__ == "__main__":
    unittest.main()
