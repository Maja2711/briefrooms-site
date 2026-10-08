"""P2.1 active-session watchdog, frozen-source lineage and alert policy tests."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from hypothesis_shadow_watchdog import assess, public_view
from hypothesis_challenger_engine import run, SCHEMA
from tests.test_hypothesis_challenger_engine import (
    candidate, registry, utility, forecast, verification, stamp
)

# 2026-10-08 is a Thursday. New York is still on summer time (EDT).
THURSDAY = "2026-10-08T14:58:00Z"  # 10:58 NY, after first slot grace
FRIDAY = "2026-10-09T11:00:00Z"    # 07:00 NY, outside US session
SATURDAY = "2026-10-10T15:00:00Z"


def state_with(c, when=THURSDAY):
    return {"schema_version": SCHEMA, "generated_at": when,
            "candidates": {c["candidate_id"]: c},
            "authority": {"production_writeback": False, "frozen_forecast_mutation": False,
                          "automatic_production_promotion": False, "trade_execution": False}}


def scheduler(now=THURSDAY, slots=None):
    return {"last_run_at": now, "last_status": {"observations_collected": 30},
            "completed_slots": slots or {}, "gaps": []}


def live_at(time_str, ident="btc.volatility.benign", slot="1000", hour=14, minutes=30, target_hour=20):
    return {
        "forecast_id": "live-forecast-" + slot,
        "belief_id": ident,
        "forecast_at": "2026-10-08T%02d:%02d:00Z" % (hour, minutes),
        "target_at": "2026-10-08T%02d:00:00Z" % target_hour,
        "predicted_probability": .7,
        "outcome_rule": "vix_below_dynamic_cap",
        "metadata": {
            "hypothesis_version": "1", "calibration_horizon_bucket": "1S_US_SESSION",
            "slot_key": "wes-assets:2026-10-08:" + slot,
            "outcome_spec": {"kind": "value_below", "symbol": "^VIX", "threshold": 20},
        },
    }


class P21WatchdogTests(unittest.TestCase):
    def _candidate(self, created_at="2026-10-08T11:29:00Z"):
        c = candidate()
        c["created_at"] = c["activation_boundary"] = created_at
        c["hypothesis_id"] = "btc.volatility.benign"
        c["horizon_bucket"] = "__ALL_HORIZONS__"
        return c

    def test_premarket_no_false_market_alert(self):
        c = self._candidate()
        when = "2026-10-08T12:00:00Z"
        result = assess({"forecasts": [], "verifications": []},
                        state_with(c, when), scheduler(now=when), now=when)
        self.assertEqual("PASS", result["status"])
        self.assertEqual("WAITING_FIRST_SHADOW_FREEZE", result["readiness"])
        self.assertFalse(result["session"]["market_window_active"])

    def test_weekend_no_false_collector_stale_alert(self):
        c = self._candidate()
        result = assess({"forecasts": [], "verifications": []},
                        state_with(c, "2026-10-09T16:00:00Z"),
                        scheduler(now="2026-10-09T16:00:00Z"), now=SATURDAY)
        self.assertNotIn("COLLECTOR_STALE_DURING_MARKET", result["alert_codes"])
        self.assertNotIn("BASELINE_FORECAST_MISSING_AFTER_SLOT", result["alert_codes"])

    def test_confirmed_slot_without_any_forecast_fails_even_without_challenger(self):
        # Source liveness is independent of P2 candidate discovery/activation.
        no_candidate_p2 = {"schema_version": SCHEMA, "generated_at": THURSDAY,
                           "candidates": {}}
        done = {"wes-assets:2026-10-08:1000": "2026-10-08T14:33:00Z"}
        r = assess({"forecasts": [], "verifications": []}, no_candidate_p2,
                   scheduler(slots=done), now=THURSDAY)
        self.assertEqual("FAIL", r["status"])
        self.assertEqual(1, r["summary"]["confirmed_slots_without_any_forecast"])
        self.assertIn("SOURCE_FORECASTS_MISSING_AFTER_CONFIRMED_SLOT", r["alert_codes"])

    def test_confirmed_slot_with_forecast_does_not_raise_global_source_alarm(self):
        no_candidate_p2 = {"schema_version": SCHEMA, "generated_at": THURSDAY,
                           "candidates": {}}
        done = {"wes-assets:2026-10-08:1000": "2026-10-08T14:33:00Z"}
        r = assess({"forecasts": [live_at(THURSDAY)], "verifications": []},
                   no_candidate_p2, scheduler(slots=done), now=THURSDAY)
        self.assertEqual(0, r["summary"]["confirmed_slots_without_any_forecast"])
        self.assertNotIn("SOURCE_FORECASTS_MISSING_AFTER_CONFIRMED_SLOT", r["alert_codes"])
        self.assertEqual("PASS", r["status"])

    def test_us_close_slot_is_audited_after_cash_session_ends(self):
        # Final 16:00 NY slot matures at 16:55, later than collection close.
        now = "2026-10-08T20:58:00Z"  # 16:58 NY, market-window inactive
        no_candidate_p2 = {"schema_version": SCHEMA, "generated_at": now,
                           "candidates": {}}
        done = {"wes-assets:2026-10-08:1600": "2026-10-08T20:09:00Z"}
        r = assess({"forecasts": [], "verifications": []}, no_candidate_p2,
                   scheduler(now=now, slots=done), now=now)
        self.assertFalse(r["session"]["market_window_active"])
        self.assertIn("SOURCE_FORECASTS_MISSING_AFTER_CONFIRMED_SLOT", r["alert_codes"])
        self.assertEqual("FAIL", r["status"])

    def test_market_snapshot_missing_escalates_after_sla_even_with_fresh_heartbeat(self):
        c = self._candidate()
        r = assess({"forecasts": [], "verifications": []}, state_with(c),
                   scheduler(now=THURSDAY), now=THURSDAY)
        self.assertEqual("FAIL", r["status"])
        self.assertIn("MARKET_SNAPSHOT_SLA_BREACHED", r["alert_codes"])
        self.assertEqual(1, r["summary"]["market_snapshot_sla_breaches"])
        self.assertEqual("BLOCKED", r["production_e2e_status"])

    def test_market_snapshot_before_55_minute_grace_not_critical(self):
        c = self._candidate()
        now = "2026-10-08T14:54:00Z"  # 10:54 NY
        r = assess({"forecasts": [], "verifications": []},
                   state_with(c, now), scheduler(now=now), now=now)
        self.assertNotIn("MARKET_SNAPSHOT_SLA_BREACHED", r["alert_codes"])
        self.assertEqual(0, r["summary"]["market_snapshot_sla_breaches"])
        self.assertEqual("PASS", r["status"])

    def test_confirmed_snapshot_avoids_sla_alert(self):
        c = self._candidate()
        done = {"wes-assets:2026-10-08:1000": "2026-10-08T14:33:00Z"}
        r = assess({"forecasts": [live_at(THURSDAY)], "verifications": []},
                   state_with(c), scheduler(slots=done), now=THURSDAY)
        self.assertNotIn("MARKET_SNAPSHOT_SLA_BREACHED", r["alert_codes"])

    def test_receipt_outside_slot_phase_does_not_prove_snapshot(self):
        c = self._candidate()
        for when in ("2026-10-08T13:00:00Z",  # 09:00 NY, before slot
                     "2026-10-08T18:30:00Z",  # 14:30 NY, after next slot
                     "2026-10-09T14:32:00Z"):  # next day, retrospective
            with self.subTest(receipt=when):
                done = {"wes-assets:2026-10-08:1000": when}
                result = assess(
                    {"forecasts": [live_at(THURSDAY)], "verifications": []},
                    state_with(c), scheduler(slots=done), now=THURSDAY)
                self.assertIn("MARKET_SNAPSHOT_SLA_BREACHED", result["alert_codes"])
                self.assertEqual("FAIL", result["status"])

    def test_late_receipt_is_not_accepted_after_the_collection_phase(self):
        now = "2026-10-08T20:58:00Z"  # 16:58 NY
        c = self._candidate()
        done = {"wes-assets:2026-10-08:1000": "2026-10-08T18:30:00Z",
                "wes-assets:2026-10-08:1300": "2026-10-08T17:31:00Z",
                "wes-assets:2026-10-08:1600": "2026-10-08T20:11:00Z"}
        result = assess({"forecasts": [], "verifications": []},
                        state_with(c, now), scheduler(now=now, slots=done),
                        now=now)
        self.assertIn("MARKET_SNAPSHOT_SLA_BREACHED", result["alert_codes"])
        self.assertEqual(["wes-assets:2026-10-08:1000"],
                         result["source_sla"]["unresolved_missing_slot_keys"])

    def test_breached_slot_carries_across_weekend_and_holiday(self):
        c = self._candidate()
        # Wednesday closing slot is still an incident after Thanksgiving,
        # the shortened Friday session and the weekend, unless proved.
        now = "2026-11-30T12:00:00Z"  # Monday 07:00 NY, before new slots
        checkpoint = {"source_sla": {
            "unresolved_missing_slot_keys": ["wes-assets:2026-11-25:1600"]}}
        result = assess({"forecasts": [], "verifications": []},
                        state_with(c, now), scheduler(now=now), now=now,
                        previous=checkpoint)
        self.assertFalse(result["session"]["market_window_active"])
        self.assertEqual([], result["session"]["due_slots"])
        self.assertEqual("FAIL", result["status"])
        self.assertIn("MARKET_SNAPSHOT_SLA_BREACHED", result["alert_codes"])
        self.assertEqual(1, result["source_sla"]["market_snapshot_sla_breaches"])

        # A real timestamp from the original collection window resolves it;
        # merely running a new collector does not.
        done = {"wes-assets:2026-11-25:1600": "2026-11-25T21:12:00Z"}
        resolved = assess({"forecasts": [], "verifications": []},
                          state_with(c, now), scheduler(now=now, slots=done),
                          now=now, previous=checkpoint)
        self.assertNotIn("MARKET_SNAPSHOT_SLA_BREACHED", resolved["alert_codes"])
        self.assertEqual([], resolved["source_sla"]["unresolved_missing_slot_keys"])

    def test_independent_checkpoint_is_loaded_by_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = "2026-11-30T12:00:00Z"  # NYSE premarket; prior SLA must persist
            (root / "state.json").write_text(
                json.dumps({"forecasts": [], "verifications": []}), encoding="utf-8")
            (root / "HYPOTHESIS_CHALLENGERS_STATE.json").write_text(
                json.dumps({"schema_version": SCHEMA, "generated_at": now,
                            "candidates": {}}), encoding="utf-8")
            (root / "scheduler.json").write_text(
                json.dumps(scheduler(now=now)), encoding="utf-8")
            checkpoint = root / "prior.json"
            checkpoint.write_text(json.dumps({
                "schema_version": "briefrooms-p2-snapshot-sla-checkpoint-v1",
                "generated_at": "2026-11-25T22:00:00Z",
                "unresolved_missing_slot_keys": ["wes-assets:2026-11-25:1600"],
            }), encoding="utf-8")
            output = root / "monitor.json"
            cmd = [sys.executable,
                   str(Path(__file__).resolve().parents[1] /
                       "scripts/hypothesis_shadow_watchdog.py"),
                   "--monitor", "--state-dir", str(root), "--output", str(output),
                   "--sla-checkpoint", str(checkpoint), "--now", now]
            run_result = subprocess.run(cmd, text=True, capture_output=True)
            self.assertEqual(0, run_result.returncode, run_result.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("FAIL", report["status"])
            self.assertEqual(["wes-assets:2026-11-25:1600"],
                             report["source_sla"]["unresolved_missing_slot_keys"])

    def test_official_nyse_holiday_has_no_false_snapshot_sla(self):
        c = self._candidate()
        when = "2026-11-26T15:58:00Z"  # Thanksgiving, 10:58 NY
        r = assess({"forecasts": [], "verifications": []},
                   state_with(c, when), scheduler(now=when), now=when)
        self.assertEqual("VERIFIED", r["session"]["calendar_status"])
        self.assertTrue(r["session"]["holiday_closed"])
        self.assertEqual([], r["session"]["due_slots"])
        self.assertNotIn("MARKET_SNAPSHOT_SLA_BREACHED", r["alert_codes"])
        self.assertEqual("PASS", r["status"])

    def test_early_close_skips_afternoon_source_slots(self):
        c = self._candidate()
        when = "2026-11-27T20:58:00Z"  # 15:58 NY, day after Thanksgiving
        r = assess({"forecasts": [], "verifications": []},
                   state_with(c, when), scheduler(now=when), now=when)
        self.assertTrue(r["session"]["early_close"])
        self.assertEqual("13:00", r["session"]["nyse_close_time"])
        self.assertEqual(["1000"], r["session"]["due_slots"])
        self.assertEqual(1, r["summary"]["market_snapshot_sla_breaches"])

    def test_close_auction_missing_snapshot_stays_critical_after_close(self):
        c = self._candidate()
        when = "2026-10-08T20:58:00Z"  # 16:58 NY
        done = {"wes-assets:2026-10-08:1000": "2026-10-08T14:31:00Z",
                "wes-assets:2026-10-08:1300": "2026-10-08T17:31:00Z"}
        r = assess({"forecasts": [], "verifications": []},
                   state_with(c, when), scheduler(now=when, slots=done), now=when)
        self.assertFalse(r["session"]["market_window_active"])
        self.assertIn("MARKET_SNAPSHOT_SLA_BREACHED", r["alert_codes"])
        self.assertEqual(1, r["summary"]["market_snapshot_sla_breaches"])

    def test_unknown_future_calendar_fails_closed(self):
        when = "2029-10-08T15:58:00Z"
        c = self._candidate()
        r = assess({"forecasts": [], "verifications": []},
                   state_with(c, when), scheduler(now=when), now=when)
        self.assertIn("NYSE_CALENDAR_COVERAGE_UNKNOWN", r["alert_codes"])
        self.assertEqual([], r["session"]["due_slots"])

    def test_successful_slot_without_candidate_base_forecast_is_critical(self):
        c = self._candidate()
        done = {"wes-assets:2026-10-08:1000": "2026-10-08T14:33:00Z"}
        r = assess({"forecasts": [], "verifications": []}, state_with(c),
                   scheduler(slots=done), now=THURSDAY)
        self.assertEqual("FAIL", r["status"])
        self.assertIn("BASELINE_FORECAST_MISSING_AFTER_SLOT", r["alert_codes"])
        self.assertEqual(0, r["summary"]["frozen_shadow_forecasts"])

    def test_open_source_forecast_missing_shadow_freeze_is_recoverable_alarm(self):
        c = self._candidate()
        f = live_at(THURSDAY)
        r = assess({"forecasts": [f], "verifications": []}, state_with(c),
                   scheduler(slots={"wes-assets:2026-10-08:1000": "2026-10-08T14:34:00Z"}),
                   now=THURSDAY)
        self.assertEqual("FAIL", r["status"])
        self.assertIn("SHADOW_FREEZE_GAP_OPEN", r["alert_codes"])
        self.assertEqual(1, r["summary"]["recoverable_freeze_gaps"])
        self.assertEqual("rerun_p2_bridge", next(x["recovery"] for x in r["alerts"]
                          if x["code"] == "SHADOW_FREEZE_GAP_OPEN"))

    def test_late_source_forecast_irrecoverable_never_backfills(self):
        c = self._candidate()
        f = live_at(THURSDAY, target_hour=16)
        r = assess({"forecasts": [f], "verifications": []},
                   state_with(c, "2026-10-08T19:00:00Z"),
                   scheduler(now="2026-10-08T19:00:00Z"),
                   now="2026-10-08T19:00:00Z")
        self.assertIn("SHADOW_FREEZE_MISSED_IRRECOVERABLE", r["alert_codes"])
        self.assertEqual(1, r["summary"]["irrecoverable_missed_freezes"])

    def test_collector_stale_after_due_slot(self):
        c = self._candidate()
        r = assess({"forecasts": [], "verifications": []}, state_with(c),
                   scheduler(now="2026-10-08T12:00:00Z"), now=THURSDAY)
        self.assertIn("COLLECTOR_STALE_DURING_MARKET", r["alert_codes"])

    def test_full_prospective_path_from_bridge_then_real_verification(self):
        c = self._candidate()
        f = live_at(THURSDAY, target_hour=17)
        state = {"forecasts": [f], "verifications": []}
        raw = run(state, utility(), state_with(c, THURSDAY), now=THURSDAY,
                  discover=False)
        self.assertEqual(1, raw["summary"]["frozen_shadow_forecasts"])
        pre = assess(state, raw, scheduler(now=THURSDAY), now=THURSDAY)
        self.assertEqual("WAITING_REAL_SETTLEMENT", pre["readiness"])
        self.assertEqual(0, pre["summary"]["real_settled_events"])
        first = pre["candidates"][0]["first_shadow_freeze_proof"]
        self.assertEqual(f["forecast_id"], first["forecast_id"])
        self.assertTrue(first["shadow_forecast_id"].startswith("hcf-"))
        self.assertTrue(first["source_snapshot_sha256"])
        self.assertLess(first["frozen_at"], first["target_at"])
        # No forecast rewrite / no false outcome. Later append a Verification
        # as the *actual* canonical record from the source pipeline would.
        outcome_at = "2026-10-08T17:20:00Z"
        state["verifications"].append({
            "verification_id": "real-verification-1",
            "forecast_id": f["forecast_id"], "belief_id": f["belief_id"],
            "target_at": f["target_at"], "verified_at": outcome_at,
            "outcome": False, "calibration_eligible": True,
            "outcome_source": "Yahoo Finance chart",
            "outcome_ref": "yahoo:^VIX:target=" + f["target_at"]
        })
        backlog = assess(state, raw, scheduler(now=outcome_at), now=outcome_at)
        self.assertIn("SETTLEMENT_BACKLOG", backlog["alert_codes"])
        advanced = run(state, utility(), raw, now=outcome_at, discover=False)
        complete = assess(state, advanced, scheduler(now=outcome_at, slots={
            "wes-assets:2026-10-08:1000": "2026-10-08T14:35:00Z"
        }), now=outcome_at)
        self.assertEqual("REAL_SETTLEMENT_VERIFIED", complete["readiness"])
        self.assertEqual(1, complete["summary"]["real_settled_events"])
        self.assertEqual(0, complete["summary"]["real_settlement_backlog"])
        self.assertEqual("PASS", complete["status"])
        self.assertEqual("PASS", complete["production_e2e_status"])
        evidence = complete["production_e2e_proof"]
        self.assertEqual(first["shadow_forecast_id"], evidence["shadow_forecast_id"])
        self.assertEqual("real-verification-1", evidence["source_verification_id"])
        self.assertEqual(first["source_snapshot_sha256"], evidence["source_snapshot_sha256"])
        self.assertEqual(complete["candidates"][0]["first_shadow_freeze_proof"], first)
        self.assertLess(evidence["frozen_at"], evidence["target_at"])
        self.assertLessEqual(evidence["target_at"], evidence["verified_at"])
        self.assertFalse(complete["authority"]["retrospective_oos_backfill"])
        public = public_view(complete)
        self.assertNotIn("shadow_forecasts", public)
        self.assertEqual("REAL_SETTLEMENT_VERIFIED", public["readiness"])

    def test_collector_bridge_is_idempotent_and_does_not_discover(self):
        c = self._candidate()
        f = live_at(THURSDAY)
        state = {"forecasts": [f], "verifications": []}
        first = run(state, utility("CHALLENGER"), state_with(c, THURSDAY),
                    now=THURSDAY, discover=False)
        second = run(state, utility("CHALLENGER"), first, now=THURSDAY,
                     discover=False)
        self.assertEqual(1, second["summary"]["frozen_shadow_forecasts"])
        self.assertEqual(1, second["summary"]["candidates_total"])
        self.assertEqual([], second["events_this_run"])
        self.assertEqual(0, second["summary"]["new_candidate_count"])

    def test_source_fingerprint_conflict_is_red(self):
        c = self._candidate()
        f = live_at(THURSDAY)
        state = {"forecasts": [f], "verifications": []}
        frozen = run(state, utility(), state_with(c, THURSDAY), now=THURSDAY,
                     discover=False)
        state["forecasts"][0]["predicted_probability"] = .9
        r = assess(state, frozen, scheduler(now="2026-10-08T20:20:00Z"),
                   now="2026-10-08T20:20:00Z")
        self.assertIn("SHADOW_SETTLEMENT_INTEGRITY_FAILURE", r["alert_codes"])

    def test_append_only_checkpoint_detects_rewritten_shadow_prediction(self):
        c = self._candidate()
        f = live_at(THURSDAY)
        state = {"forecasts": [f], "verifications": []}
        frozen = run(state, utility(), state_with(c, THURSDAY),
                     now=THURSDAY, discover=False)
        original = assess(state, frozen, scheduler(now=THURSDAY), now=THURSDAY)
        altered = copy.deepcopy(frozen)
        commit = next(iter(altered["candidates"][c["candidate_id"]]["shadow_forecasts"].values()))
        commit["challenger_probability"] = .999
        later = assess(state, altered, scheduler(now=THURSDAY), now=THURSDAY,
                       previous=original)
        self.assertIn("SHADOW_APPEND_ONLY_INTEGRITY_FAILURE", later["alert_codes"])

    def test_real_canonical_verification_does_not_fake_p2_oos_evidence(self):
        c = self._candidate()
        f = live_at(THURSDAY)
        real = {
            "verification_id": "from-belief-core",
            "forecast_id": f["forecast_id"], "belief_id": f["belief_id"],
            "outcome": False, "calibration_eligible": True,
            "verified_at": "2026-10-08T20:15:00Z",
            "outcome_source": "Yahoo Finance chart",
            "outcome_ref": "yahoo:^VIX:target=" + f["target_at"],
        }
        observed = assess({"forecasts": [f], "verifications": [real]},
                          state_with(c, "2026-10-08T20:20:00Z"),
                          scheduler(now="2026-10-08T20:20:00Z"),
                          now="2026-10-08T20:20:00Z")
        self.assertEqual(1, observed["source"]["canonical_market_verification_count"])
        self.assertEqual(0, observed["summary"]["real_settled_events"])
        self.assertEqual("WAITING_FIRST_SHADOW_FREEZE", observed["readiness"])
        pub = public_view(observed)
        self.assertTrue(pub["canonical_market_verification_probe"]["verified"])
        self.assertTrue(pub["canonical_market_verification_probe"]["not_p2_oos_proof"])

    def test_yahoo_label_with_wrong_market_symbol_is_not_real_settlement(self):
        c = self._candidate()
        f = live_at(THURSDAY)
        state = {"forecasts": [f], "verifications": []}
        frozen = run(state, utility(), state_with(c, THURSDAY),
                     now=THURSDAY, discover=False)
        outcome_at = "2026-10-08T20:20:00Z"
        state["verifications"] = [{
            "verification_id": "ref-mismatch", "forecast_id": f["forecast_id"],
            "belief_id": f["belief_id"], "outcome": False,
            "verified_at": outcome_at, "calibration_eligible": True,
            "outcome_source": "Yahoo Finance chart",
            "outcome_ref": "yahoo:BTC-USD:target=" + f["target_at"],
        }]
        out = run(state, utility(), frozen, now=outcome_at, discover=False)
        self.assertEqual(0, out["summary"]["settled_oos_events"])
        r = assess(state, out, scheduler(now=outcome_at), now=outcome_at)
        self.assertEqual(0, r["source"]["canonical_market_verification_count"])
        self.assertEqual(0, r["summary"]["real_settled_events"])

    def test_frozen_market_outcome_rule_mutation_invalidates_shadow(self):
        c = self._candidate()
        f = live_at(THURSDAY)
        state = {"forecasts": [f], "verifications": []}
        frozen = run(state, utility(), state_with(c, THURSDAY),
                     now=THURSDAY, discover=False)
        state["forecasts"][0]["metadata"]["outcome_spec"]["symbol"] = "BTC-USD"
        r = assess(state, frozen, scheduler(now=THURSDAY), now=THURSDAY)
        self.assertIn("SHADOW_FROZEN_SOURCE_INTEGRITY_FAILURE", r["alert_codes"])

    def test_manual_verified_outcome_cannot_settle_p2(self):
        c = self._candidate()
        f = live_at(THURSDAY)
        state = {"forecasts": [f], "verifications": []}
        frozen = run(state, utility(), state_with(c, THURSDAY),
                     now=THURSDAY, discover=False)
        state["verifications"].append({
            "verification_id": "manual-test", "forecast_id": f["forecast_id"],
            "belief_id": f["belief_id"], "outcome": False,
            "verified_at": "2026-10-08T20:05:00Z",
            "calibration_eligible": True,
            "outcome_source": "manual",
        })
        out = run(state, utility(), frozen, now="2026-10-08T20:10:00Z",
                  discover=False)
        self.assertEqual(0, out["summary"]["settled_oos_events"])

    def test_timestamp_corruption_is_critical(self):
        c = self._candidate()
        r = assess({"forecasts": [], "verifications": []},
                   state_with(c, "2026-10-08T19:00:00Z"),
                   scheduler(now=THURSDAY), now=THURSDAY)
        self.assertIn("P2_REPORT_TIMESTAMP_INVALID", r["alert_codes"])


if __name__ == "__main__":
    unittest.main()
