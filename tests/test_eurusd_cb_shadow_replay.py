"""Synthetic scenarios exercise replay mechanics, never performance claims."""
import copy
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from eurusd_central_bank_event_risk import DEFAULT_POLICY
from eurusd_central_bank_wire import digest, iso
from eurusd_cb_shadow_replay import freeze_manifest, evaluate, persist_immutable, validate_manifest
from tests.test_eurusd_central_bank_wire import NOW, CALENDAR, collected, Client, feed, URL, DOC
import eurusd_central_bank_wire as wire


def descriptor(identity="fixture:episode", event_id="fixture:fomc", now=NOW, split="TEST", scope="EURUSD_WEEKLY", bank="FED"):
    return {"episode_id": identity, "event_id": event_id, "bank": bank, "scope": scope, "decision_at": iso(now),
            "horizon_at": iso(now + timedelta(minutes=120)), "split": split}


def episode():
    calendar = copy.deepcopy(CALENDAR)
    calendar[0]["time_precision"] = "exact"  # Explicitly sourced synthetic timestamp.
    snapshot = collected(calendar=calendar)
    quote = {"observed_at": iso(NOW), "ingested_at": iso(NOW), "quote_id": "fixture:decision-quote",
             "bid": 1.11367, "ask": 1.11369, "cost_pips": .2, "verified": True,
             "source": "SYNTHETIC_EXECUTABLE_FIXTURE", "baseline_spread_pips": .2}
    inputs = {"position": {"instrument_id": "eurusd", "position_id": "fixture:position", "direction": "SHORT",
                           "entry_price": 1.11869, "initial_stop_loss": 1.12487, "stop_loss": 1.12487,
                           "take_profit": 1.11000, "opened_at": iso(NOW - timedelta(days=1))},
              "quote": quote, "wire": snapshot, "calendar_events": calendar}
    def tick(offset, bid, ask):
        return {**quote, "quote_id": f"fixture:quote:{offset}", "observed_at": iso(NOW + timedelta(seconds=offset)),
                "ingested_at": iso(NOW + timedelta(seconds=offset + 1)), "bid": bid, "ask": ask}
    return {"episode_id": "fixture:episode", "position_id": "fixture:position", "decision_input": inputs,
            "capture_receipt": {"captured_at": iso(NOW), "input_sha256": digest(inputs), "immutable_id": "fixture:receipt"},
            "outcome_quotes": [tick(1, 1.11367, 1.11369), tick(60, 1.11600, 1.11602), tick(7198, 1.1180, 1.11802)],
            "regime": {"label": "SYNTHETIC_VOLATILE", "source": "fixture:regime", "observed_at": iso(NOW)},
            "baseline_lifecycle_parity": "NOT_CERTIFIED"}


class ShadowReplayTests(unittest.TestCase):
    def manifest(self, descriptors=None):
        return freeze_manifest(descriptors or [descriptor()], DEFAULT_POLICY, frozen_at=NOW - timedelta(hours=2))

    def test_preregister_before_decision_and_reject_outcome_selection(self):
        self.assertEqual(self.manifest()["minimum_independent_test_events"], 40)
        with self.assertRaises(ValueError):
            freeze_manifest([descriptor()], DEFAULT_POLICY, frozen_at=NOW + timedelta(seconds=1))
        with self.assertRaises(ValueError):
            self.manifest([{**descriptor(), "future_net_R": 5.}])
        invalid = self.manifest()
        invalid["minimum_independent_test_events"] = 1
        invalid["manifest_sha256"] = digest({key: value for key, value in invalid.items() if key != "manifest_sha256"})
        with self.assertRaises(ValueError):
            validate_manifest(invalid)

    def test_asof_shadow_close_preserves_baseline_and_explicit_costs(self):
        source = episode()
        before = copy.deepcopy(source)
        report = evaluate(self.manifest(), [source], evaluated_at=NOW + timedelta(hours=3))
        self.assertEqual(source, before)
        self.assertEqual(report["status"], "INSUFFICIENT_EVIDENCE")
        result = report["results"][0]
        self.assertEqual(result["recommended_action"], "CLOSE_BEFORE_EVENT")
        self.assertEqual(result["shadow_status"], "COUNTERFACTUAL_CLOSE_NOT_EXECUTED")
        self.assertEqual(result["close_latency_seconds"], 2.)
        baseline = result["stress_results"][0]["baseline_original"]["net_R"]
        event = result["stress_results"][0]["event_risk_policy"]["net_R"]
        self.assertGreater(event, baseline)
        self.assertAlmostEqual(event, (50. - .2) / 61.8, places=6)
        self.assertLess(result["stress_results"][-1]["event_risk_policy"]["net_R"], event)
        self.assertFalse(report["activation_allowed"])

    def test_future_outcome_changes_performance_but_never_decision(self):
        one, two = episode(), episode()
        two["outcome_quotes"][-1].update(bid=1.111, ask=1.11102)
        left = evaluate(self.manifest(), [one], evaluated_at=NOW + timedelta(hours=3))["results"][0]
        right = evaluate(self.manifest(), [two], evaluated_at=NOW + timedelta(hours=3))["results"][0]
        self.assertEqual(left["decision_id"], right["decision_id"])
        self.assertEqual(left["recommended_action"], right["recommended_action"])
        self.assertNotEqual(left["stress_results"][0]["baseline_original"], right["stress_results"][0]["baseline_original"])
        self.assertEqual(left["stress_results"][0]["event_risk_policy"]["net_R"], right["stress_results"][0]["event_risk_policy"]["net_R"])

    def test_gap_stop_uses_observed_ask_not_assumed_stop_fill(self):
        source = episode()
        source["outcome_quotes"][1].update(bid=1.12700, ask=1.12750)
        report = evaluate(self.manifest(), [source], evaluated_at=NOW + timedelta(hours=3))
        result = report["results"][0]
        self.assertEqual(result["baseline_exit_reason"], "FROZEN_SL_OBSERVED_QUOTE")
        self.assertLess(result["stress_results"][0]["baseline_original"]["net_R"], -1.)

    def test_missing_future_or_stale_end_quotes_do_not_create_results(self):
        report = evaluate(self.manifest(), [], evaluated_at=NOW + timedelta(hours=3))
        self.assertEqual(report["status"], "PENDING_INCOMPLETE_FROZEN_SAMPLE")
        self.assertFalse(report["sample_closed"])
        source = episode()
        source["outcome_quotes"] = source["outcome_quotes"][:-1]
        report = evaluate(self.manifest(), [source], evaluated_at=NOW + timedelta(hours=3))
        self.assertEqual(report["status"], "INVALID_EVIDENCE")
        self.assertIn("stale", report["rejected_episodes"][0]["reason"])

    def test_post_decision_input_or_wrong_event_is_rejected(self):
        for mutate in (lambda e: e["decision_input"]["quote"].update(ingested_at=iso(NOW + timedelta(seconds=1))),
                       lambda e: e.update(position_id="fixture:another-position")):
            source = episode()
            mutate(source)
            source["capture_receipt"]["input_sha256"] = digest(source["decision_input"])
            report = evaluate(self.manifest(), [source], evaluated_at=NOW + timedelta(hours=3))
            self.assertEqual(report["status"], "INVALID_EVIDENCE")
        report = evaluate(self.manifest([descriptor(event_id="wrong-event")]), [episode()], evaluated_at=NOW + timedelta(hours=3))
        self.assertEqual(report["status"], "INVALID_EVIDENCE")

    def test_future_knowledge_or_missing_actual_bid_ask_is_rejected(self):
        for mutate in (lambda e: e["decision_input"]["wire"].update(as_of=iso(NOW + timedelta(minutes=1))),
                       lambda e: e["outcome_quotes"][0].pop("ask"),
                       lambda e: e["outcome_quotes"][0].update(verified=False)):
            source = episode()
            mutate(source)
            source["capture_receipt"]["input_sha256"] = digest(source["decision_input"])
            report = evaluate(self.manifest(), [source], evaluated_at=NOW + timedelta(hours=3))
            self.assertEqual(report["status"], "INVALID_EVIDENCE")

    def test_exact_frozen_membership_and_independent_event_clusters(self):
        with self.assertRaises(ValueError):
            evaluate(self.manifest(), [{**episode(), "episode_id": "new-winner"}], evaluated_at=NOW + timedelta(hours=3))
        with self.assertRaises(ValueError):
            self.manifest([descriptor(split="TRAIN"), descriptor(identity="same-event-test", split="TEST")])
        with self.assertRaises(ValueError):
            self.manifest([descriptor(), descriptor(identity="overlap", event_id="second-event", now=NOW + timedelta(minutes=1))])
        other = descriptor(identity="daily-same-event", scope="EURUSD_DAILY")
        second_episode = {**episode(), "episode_id": other["episode_id"]}
        report = evaluate(self.manifest([descriptor(), other]), [episode(), second_episode], evaluated_at=NOW + timedelta(hours=3))
        self.assertEqual(report["coverage"]["independent_test_events"], 1)

    def test_training_precedes_test_and_cannot_change_final_test_membership(self):
        training = descriptor(identity="train", event_id="earlier", now=NOW - timedelta(days=1), split="TRAIN")
        with self.assertRaises(ValueError):
            self.manifest([training, descriptor()])  # Freeze is after training: not prospective.
        manifest = freeze_manifest([training, descriptor()], DEFAULT_POLICY, frozen_at=NOW - timedelta(days=2))
        self.assertEqual(manifest["episodes"][0]["split"], "TRAIN")

    def test_closed_artifact_cannot_change_after_later_outcomes(self):
        manifest = self.manifest()
        report = evaluate(manifest, [episode()], evaluated_at=NOW + timedelta(hours=3))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            self.assertTrue(persist_immutable(path, report))
            self.assertFalse(persist_immutable(path, report))
            changed = copy.deepcopy(report)
            changed["results"].clear()
            with self.assertRaises(ValueError):
                persist_immutable(path, changed)

    def test_protect_recommendation_never_invents_a_stop_upgrade(self):
        source = episode()
        source["decision_input"]["position"]["take_profit"] = 1.09000
        source["capture_receipt"]["input_sha256"] = digest(source["decision_input"])
        report = evaluate(self.manifest(), [source], evaluated_at=NOW + timedelta(hours=3))
        result = report["results"][0]
        self.assertEqual(result["recommended_action"], "PROTECT_PROFIT")
        self.assertEqual(result["shadow_status"], "UNSUPPORTED_STOP_UPGRADE_BASELINE_RETAINED")
        self.assertEqual(result["baseline_exit_quote_id"], result["policy_exit_quote_id"])
        self.assertEqual(result["stress_results"][0]["delta_net_R"], 0.)

    def test_stale_ingestion_cannot_claim_an_earlier_executable_close(self):
        source = episode()
        source["outcome_quotes"][0]["ingested_at"] = iso(NOW + timedelta(seconds=90))
        report = evaluate(self.manifest(), [source], evaluated_at=NOW + timedelta(hours=3))
        self.assertEqual(report["status"], "INVALID_EVIDENCE")
        self.assertFalse(report["activation_allowed"])

    def test_actual_publication_windows_are_descriptive_and_require_coverage(self):
        source = episode()
        source["outcome_wire"] = collected(Client({wire.OFFICIAL_RSS["fed_monetary"]: feed(published="Wed, 28 Oct 2026 18:02:00 GMT"), URL: DOC}), now=NOW + timedelta(minutes=2))
        report = evaluate(self.manifest(), [source], evaluated_at=NOW + timedelta(hours=3))
        windows = report["results"][0]["publication_windows"]
        self.assertEqual(windows["status"], "DESCRIPTIVE_NOT_CALIBRATED")
        self.assertEqual([item["minutes"] for item in windows["windows"]], [1, 5, 15, 30, 60, 120])
        self.assertEqual(windows["windows"][0]["status"], "OBSERVED_QUOTE_PATH_ONLY")
        self.assertEqual(windows["windows"][1]["status"], "INSUFFICIENT_QUOTE_COVERAGE")


if __name__ == "__main__":
    unittest.main()
