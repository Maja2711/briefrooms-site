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

    def test_authorized_candidate_uses_frozen_provenance_not_old_monitor(self):
        w = week()
        eur = w["instruments"][0]
        eur.pop("wes_trigger_candidate")
        eur["wes_entry_authorization"] = {
            "authorized_at": (NOW - timedelta(minutes=2)).isoformat(),
            "expires_at": (NOW + timedelta(minutes=20)).isoformat(),
            "directional_admission_passed": True,
            "candidate": {
                "direction": "short",
                "raw_score": 99,
                "confirmations": 4,
                "directional_admission": {"passed": True},
            },
            "required": {"allowed": True},
        }
        eur["pending_entry_decision"] = {
            "decision_id": "wes-dec-example",
            "decision": {"direction": "short"},
        }
        result = watcher.evaluate(w, prices(), {}, [], now=NOW)
        audit = result["instruments"]["eurusd"]
        self.assertTrue(audit["entry_authorization_active"])
        self.assertTrue(audit["pending_entry"])
        self.assertEqual("short", audit["candidate_direction"])
        self.assertEqual(99, audit["candidate_raw_score"])
        self.assertEqual(4, audit["confirmations"])
        self.assertNotIn("WES_TRIGGER_EVIDENCE_MISSING", audit["blocker_reasons"])
        self.assertNotIn("NO_QUALIFIED_DIRECTION", audit["blocker_reasons"])
        self.assertNotIn("DIRECTIONAL_ADMISSION_NOT_PASSED", audit["blocker_reasons"])
        self.assertNotIn("FEWER_THAN_TWO_INDEPENDENT_CONFIRMATIONS",
                         audit["blocker_reasons"])
        self.assertEqual("NO_TRADE_REVIEWED", result["status"])
        self.assertTrue(result["auto_trade_forbidden"])

    def test_expired_authorization_with_pending_plan_requires_recovery(self):
        w = week()
        eur = w["instruments"][0]
        eur["wes_entry_authorization"] = {
            "authorized_at": (NOW - timedelta(hours=2)).isoformat(),
            "expires_at": (NOW - timedelta(minutes=1)).isoformat(),
            "directional_admission_passed": True,
            "candidate": {
                "direction": "short",
                "raw_score": 99,
                "confirmations": 4,
                "directional_admission": {"passed": True},
            },
        }
        eur["pending_entry_decision"] = {"decision": {"direction": "short"}}
        audit = watcher.evaluate(w, prices(), {}, [], now=NOW)
        self.assertEqual("RECOVERY_REQUIRED", audit["status"])
        self.assertFalse(audit["instruments"]["eurusd"]["entry_authorization_active"])
        self.assertIn("PENDING_ENTRY_WITHOUT_VALID_AUTHORIZATION",
                      audit["instruments"]["eurusd"]["blocker_reasons"])
        self.assertIn("eurusd:PENDING_ENTRY_WITHOUT_VALID_AUTHORIZATION",
                      audit["recovery_reasons"])
        self.assertNotEqual("short", audit["instruments"]["eurusd"]["candidate_direction"])
        self.assertTrue(audit["auto_trade_forbidden"])

    def test_old_monitor_candidate_does_not_override_active_authorization(self):
        w = week()
        eur = w["instruments"][0]
        eur["reentry_lock"] = {"active": False}
        eur["wes_entry_authorization"] = {
            "authorized_at": (NOW - timedelta(minutes=1)).isoformat(),
            "expires_at": (NOW + timedelta(minutes=10)).isoformat(),
            "directional_admission_passed": True,
            "candidate": {
                "direction": "long",
                "raw_score": 76,
                "confirmations": 3,
                "directional_admission": {"passed": True},
            },
            "required": {"allowed": True},
        }
        eur["pending_entry_decision"] = {"decision": {"direction": "long"}}
        result = watcher.evaluate(w, prices(), {}, [], now=NOW)
        self.assertEqual("long", result["instruments"]["eurusd"]["candidate_direction"])
        self.assertEqual([], result["instruments"]["eurusd"]["blocker_reasons"])

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

    def test_expired_eurusd_target_is_never_an_active_executable_order(self):
        w = week()
        item = w["instruments"][0]
        item["pending_entry_decision"] = {
            "decision": {"direction": "short"},
            "entry_price_plan": {"execution_mode": "market_now", "target_price": 1.1,
                                 "entry_not_before": (NOW - timedelta(hours=2)).isoformat(),
                                 "expires_at": (NOW - timedelta(minutes=5)).isoformat()},
        }
        result = watcher.evaluate(w, prices(), {}, [], now=NOW)
        eur = result["instruments"]["eurusd"]
        self.assertIn("PUBLISHED_ENTRY_PLAN_EXPIRED_NOT_EXECUTABLE", eur["warnings"])
        self.assertIn("eurusd:EURUSD_EXPIRED_ENTRY_PLAN_REQUIRES_NEW_DECISION",
                      result["recovery_reasons"])

    def test_market_entry_requires_review_not_automatic_fill(self):
        w = week()
        eur = w["instruments"][0]
        eur["reentry_lock"] = {"active": False}
        eur["wes_entry_authorization"] = {
            "authorized_at": (NOW - timedelta(minutes=12)).isoformat(),
            "expires_at": (NOW + timedelta(minutes=20)).isoformat(),
            "directional_admission_passed": True,
            "candidate": {"direction": "short", "confirmations": 3,
                          "directional_admission": {"passed": True}},
            "required": {"allowed": True},
        }
        eur["pending_entry_decision"] = {
            "decision": {"direction": "short"},
            "entry_price_plan": {"execution_mode": "market_now", "target_price": 1.10029,
                                 "entry_not_before": (NOW - timedelta(minutes=12)).isoformat(),
                                 "expires_at": (NOW + timedelta(minutes=3)).isoformat()},
        }
        data = prices()
        data["prices"]["eurusd"]["price"] = 1.10096
        result = watcher.evaluate(w, data, {}, [], now=NOW)
        self.assertIn("eurusd:EURUSD_MARKET_ENTRY_NOT_SETTLED_REVIEW_EPE",
                      result["recovery_reasons"])
        self.assertTrue(result["auto_trade_forbidden"])

        # A current CB gate is an actual blocker, not an execution mismatch.
        guard = {"eurusd_central_bank_event_risk": {
            "entry_guard_enforced": True, "evaluated_at": NOW.isoformat(),
            "diagnostics": {"minutes_until": -10},
        }}
        blocked = watcher.evaluate(w, data, guard, [], now=NOW)
        self.assertNotIn("eurusd:EURUSD_MARKET_ENTRY_NOT_SETTLED_REVIEW_EPE",
                         blocked["recovery_reasons"])

    def test_market_condition_does_not_force_a_frozen_limit_fill(self):
        w = week()
        eur = w["instruments"][0]
        eur["reentry_lock"] = {"active": False}
        eur["wes_entry_authorization"] = {
            "authorized_at": (NOW - timedelta(minutes=11)).isoformat(),
            "expires_at": (NOW + timedelta(minutes=20)).isoformat(),
            "directional_admission_passed": True,
            "candidate": {"direction": "short", "confirmations": 2,
                          "directional_admission": {"passed": True}},
            "required": {"allowed": True},
        }
        eur["pending_entry_decision"] = {
            "decision": {"direction": "short"},
            "entry_price_plan": {"execution_mode": "limit_pullback", "target_price": 1.1,
                                 "entry_not_before": (NOW - timedelta(minutes=11)).isoformat(),
                                 "expires_at": (NOW + timedelta(minutes=10)).isoformat()},
        }
        data = prices()
        data["prices"]["eurusd"]["price"] = 1.101
        result = watcher.evaluate(w, data, {}, [], now=NOW)
        self.assertIn("EURUSD_MARKETABLE_LIMIT_QUOTE_WITHOUT_EPE_FILL_PROOF",
                      result["instruments"]["eurusd"]["warnings"])
        self.assertIn("eurusd:EURUSD_MARKETABLE_LIMIT_REVIEW_EPE", result["recovery_reasons"])

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
