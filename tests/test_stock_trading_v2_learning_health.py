from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scripts import stock_trading_v2_learning_health as health
from scripts import stock_trading_v2_contracts as contracts
from scripts import stock_trading_v2_outcome_replay as outcomes
from scripts import stock_trading_v2_regret_engine as regret
from scripts import stock_trading_v2_challenger_eval as evaluation
from tests.test_stock_trading_v2_admission_ledger import event


def regret_report(*, groups=(), generated_at="2026-09-17T12:00:00Z"):
    row = {"schema_version": regret.SCHEMA_VERSION, "generated_at": generated_at,
        "horizons": {"1": {"comparisons": [{"source_group": group} for group in groups]}},
        "governance": {"production_decision_influence": False, "automatic_policy_writeback": False}}
    row["report_sha256"] = contracts.payload_sha256(row)
    return row


def evaluation_report(*, sources=(), generated_at="2026-09-17T12:00:00Z"):
    row = {"schema_version": evaluation.SCHEMA_VERSION, "generated_at": generated_at, "challenger_id": "test-cash-margin",
        "state": "COLLECTING_FRESH_HOLDOUT", "formal_samples": [{"source_event_id": source, "decision_at": "2026-09-16T08:00:00Z"} for source in sources],
        "governance": {"production_decision_influence": False, "production_promotion_enabled": False}}
    row["evaluation_sha256"] = contracts.payload_sha256(row)
    return row


class StockTradingV2LearningHealthTests(unittest.TestCase):
    def test_continuous_windows_have_distinct_stable_progress_identities(self):
        payload = evaluation_report()
        payload["formal_samples"] = [{"source_event_id": "anchor-1", "block_id": f"anchor-1:{index:04d}",
            "execution_semantics": "continuous_production_lifecycle_v2", "decision_at": "2026-09-16T08:00:00Z"}
            for index in (1, 2)]
        payload.pop("evaluation_sha256")
        payload["evaluation_sha256"] = contracts.payload_sha256(payload)
        first = health.build_report([], [], None, [payload], horizons=[1], now="2026-10-26T10:00:00Z")
        self.assertEqual(first["stages"]["evaluations"]["progress_count"], 2)
        changed = deepcopy(payload)
        changed["generated_at"] = "2026-10-27T10:00:00Z"
        changed["formal_samples"][0]["settled_at"] = changed["generated_at"]
        changed.pop("evaluation_sha256")
        changed["evaluation_sha256"] = contracts.payload_sha256(changed)
        repeated = health.build_report([], [], None, [changed], horizons=[1], previous=first, now="2026-10-27T10:00:00Z")
        self.assertEqual(repeated["stages"]["evaluations"]["new_progress_items"], 0)

    def test_preterminal_continuous_coverage_is_separate_from_evaluated_samples(self):
        payload = evaluation_report()
        payload["formal_proof"] = {"protocol": {"schema_version": "stock-trading-cash-edge-protocol-v2", "fixed_regular_sessions": 60},
            "cohort": {"members": [{"source_event_id": "anchor-1"}]}}
        payload["metrics"] = {"coverage": {"status": "PENDING", "reason": "waiting_for_all_predeclared_session_closes",
            "expected_sessions": 60, "observed_closes": 4, "regular_end": "2027-01-26T21:00:00Z", "calendar_sha256": "calendar-1"}}
        payload.pop("evaluation_sha256")
        payload["evaluation_sha256"] = contracts.payload_sha256(payload)
        first = health.build_report([], [], None, [payload], horizons=[1], now="2026-10-26T10:00:00Z")
        later = health.build_report([], [], None, [payload], horizons=[1], previous=first, now="2026-10-30T10:00:00Z")
        stage = later["stages"]["evaluations"]
        self.assertEqual(stage["state"], "COLLECTING_PROSPECTIVE_TAPE")
        self.assertEqual(stage["progress_count"], 0)
        self.assertEqual(stage["new_progress_items"], 0)
        self.assertIsNone(stage["last_progress_observed_at"])
        collection = stage["prospective_collection"][0]
        self.assertEqual(collection["coverage"]["observed_closes"], 4)
        self.assertEqual(collection["frozen_anchors"], 1)
        self.assertFalse(collection["formal_evaluation_progress_credited"])
        self.assertFalse(later["attention_required"])
        broken = deepcopy(payload)
        broken["metrics"]["coverage"] = {"status": "PENDING", "reason": "preregistered_session_calendar_missing_or_invalid"}
        broken.pop("evaluation_sha256")
        broken["evaluation_sha256"] = contracts.payload_sha256(broken)
        unavailable = health.build_report([], [], None, [broken], horizons=[1], previous=first, now="2026-10-30T10:00:00Z")
        self.assertEqual(unavailable["stages"]["evaluations"]["state"], "EMPTY_STALLED")
        self.assertTrue(unavailable["attention_required"])

    def test_cohort_enrollment_files_are_not_evaluation_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".cohorts").mkdir()
            (root / ".cohorts" / "trial.json").write_text('{"schema_version":"cohort-ledger"}', encoding="utf-8")
            payload = evaluation_report(sources=["event-1"])
            (root / "evaluation.json").write_text(json.dumps(payload), encoding="utf-8")
            rows = health._evaluation_rows(root)
            self.assertEqual(rows, [payload])
            report = health.build_report([], [], None, rows, horizons=[1])
            self.assertEqual(report["stages"]["evaluations"]["progress_count"], 1)

    def test_failure_keeps_last_valid_watermarks_and_first_recovery_is_only_baseline(self):
        valid = health.build_report([], [], None, [evaluation_report(sources=["event-1"])],
            horizons=[1], now="2026-10-26T10:00:00Z")
        failed = health.build_failure_report(contracts.ContractError("changed source binding"),
            previous=valid, now="2026-10-27T10:00:00Z", workflow_status="success")
        self.assertEqual(failed["workflow_status"], "failure")
        self.assertEqual(failed["source_integrity"]["status"], "FAILED")
        self.assertTrue(failed["attention_required"])
        for name in valid["stages"]:
            for key in ("progress_count", "progress_keys", "seen_progress_keys", "baseline_observed_at", "last_progress_observed_at"):
                self.assertEqual(failed["stages"][name][key], valid["stages"][name][key])
            self.assertEqual(failed["stages"][name]["state"], "UNAVAILABLE")
            self.assertEqual(failed["stages"][name]["new_progress_items"], 0)
        first_failure = health.build_failure_report(ValueError("unreadable"), now="2026-10-26T10:00:00Z")
        recovered = health.build_report([], [], None, [evaluation_report(sources=["event-1"])],
            horizons=[1], previous=first_failure, now="2026-10-27T10:00:00Z")
        self.assertEqual(recovered["stages"]["evaluations"]["state"], "BASELINE_OBSERVED")
        self.assertEqual(recovered["stages"]["evaluations"]["new_progress_items"], 0)
        self.assertIsNone(recovered["stages"]["evaluations"]["last_progress_observed_at"])

    def test_cli_persists_failure_report_without_changing_invalid_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "experience"
            store.mkdir()
            source = store / "bad.json"
            source.write_bytes(b'{"invalid')
            config = root / "config.json"
            config.write_text(json.dumps({"schema_version": "stock-trading-v2-learning-config-v1", "horizons_sessions": [1], "governance": {"production_decision_influence": False}}), encoding="utf-8")
            output = root / "health.json"
            args = ["health", "--experience-root", str(store), "--outcome-root", str(root / "outcomes"),
                "--regret", str(root / "regret.json"), "--evaluations", str(root / "evaluations"),
                "--config", str(config), "--output", str(output)]
            before = source.read_bytes()
            with patch("sys.argv", args):
                self.assertEqual(health.main(), 1)
            self.assertEqual(source.read_bytes(), before)
            report = json.loads(output.read_text(encoding="utf-8"))
            health.validate_report(report)
            self.assertEqual(report["source_integrity"]["error_type"], "JSONDecodeError")
            self.assertEqual(report["workflow_status"], "failure")
            self.assertTrue(all(stage["state"] == "UNAVAILABLE" for stage in report["stages"].values()))

    def test_invalid_source_bindings_or_duplicate_rows_cannot_create_progress(self):
        source = event(selected=True)
        outcome = outcomes.make_outcome(source, {"status": "SETTLED", "horizon_sessions": 1, "net_r": 0.5}, replay_version="test")
        with self.assertRaisesRegex(contracts.ContractError, "missing source"):
            health.build_report([], [outcome], None, [], horizons=[1])
        forged = deepcopy(outcome)
        forged["source_event_sha256"] = "forged"
        forged.pop("outcome_sha256")
        forged["outcome_sha256"] = contracts.payload_sha256(forged)
        with self.assertRaisesRegex(contracts.ContractError, "source binding"):
            health.build_report([source], [forged], None, [], horizons=[1])
        with self.assertRaisesRegex(contracts.ContractError, "duplicate.*source/horizon"):
            health.build_report([source], [outcome, outcome], None, [], horizons=[1])
        with self.assertRaisesRegex(contracts.ContractError, "duplicate.*source event"):
            health.build_report([source, source], [outcome], None, [], horizons=[1])

    def test_first_read_of_old_evidence_is_a_baseline_not_new_learning_progress(self):
        source = event(selected=True)
        outcome = outcomes.make_outcome(source, {"status": "SETTLED", "horizon_sessions": 1, "net_r": 0.5}, replay_version="test")
        report = health.build_report([source], [outcome], None, [], horizons=[1], now="2026-10-26T10:00:00Z")
        stage = report["stages"]["outcomes"]
        self.assertEqual(stage["state"], "BASELINE_OBSERVED")
        self.assertEqual(stage["new_progress_items"], 0)
        self.assertIsNone(stage["last_progress_observed_at"])

    def test_empty_expected_outcome_queue_alerts_after_stalling(self):
        source = event(selected=True)
        first = health.build_report([source], [], None, [], horizons=[1], now="2026-10-22T10:00:00Z")
        later = health.build_report([source], [], None, [], horizons=[1], previous=first, now="2026-10-26T10:00:00Z")
        self.assertEqual(later["stages"]["outcomes"]["state"], "EMPTY_STALLED")
        self.assertTrue(later["attention_required"])
        self.assertEqual(later["stages"]["evaluations"]["state"], "EMPTY")

    def test_timestamp_rewrites_do_not_fake_progress_and_stages_advance_separately(self):
        source = event(selected=True)
        first_outcome = outcomes.make_outcome(source, {"status": "SETTLED", "horizon_sessions": 1, "net_r": 0.5}, replay_version="test", settled_at="2026-09-17T12:00:00Z")
        first = health.build_report([source], [first_outcome], regret_report(groups=["abc123"]), [evaluation_report(sources=[source["event_id"]])], horizons=[1, 2], now="2026-09-18T12:00:00Z")
        later_outcome = outcomes.make_outcome(source, {"status": "SETTLED", "horizon_sessions": 1, "net_r": 0.5}, replay_version="test", settled_at="2026-09-23T12:00:00Z")
        repeated = health.build_report([source], [later_outcome], regret_report(groups=["abc123"], generated_at="2026-09-23T12:00:00Z"),
            [evaluation_report(sources=[source["event_id"]], generated_at="2026-09-23T12:00:00Z")], horizons=[1, 2], previous=first, now="2026-09-23T12:00:00Z")
        for name in ("outcomes", "regret", "evaluations"):
            self.assertEqual(repeated["stages"][name]["state"], "STALLED")
            self.assertEqual(repeated["stages"][name]["new_progress_items"], 0)
            self.assertIsNone(repeated["stages"][name]["last_progress_observed_at"])
            self.assertEqual(repeated["stages"][name]["baseline_observed_at"], "2026-09-18T12:00:00Z")
        second_outcome = outcomes.make_outcome(source, {"status": "SETTLED", "horizon_sessions": 2, "net_r": 0.7}, replay_version="test", settled_at="2026-09-23T12:00:00Z")
        progressed = health.build_report([source], [later_outcome, second_outcome], regret_report(groups=["abc123"]),
            [evaluation_report(sources=[source["event_id"]])], horizons=[1, 2], previous=repeated, now="2026-09-24T12:00:00Z")
        self.assertEqual(progressed["stages"]["outcomes"]["state"], "ADVANCING")
        self.assertEqual(progressed["stages"]["regret"]["state"], "STALLED")
        self.assertEqual(progressed["stages"]["evaluations"]["state"], "STALLED")

    def test_mature_unresolved_is_distinct_from_pending_and_no_calendar_is_invented(self):
        source = event(selected=False)
        report = health.build_report([source], [], None, [], horizons=[1, 2, 5], now="2026-09-21T12:00:00Z",
            sessions_by_market={"GPW": ["2026-09-17", "2026-09-18"], "US": []}, workflow_status="failure")
        stage = report["stages"]["outcomes"]
        self.assertEqual(stage["mature_unresolved"], 2)
        self.assertEqual(stage["pending_or_maturity_unknown"], 1)
        self.assertEqual(report["workflow_status"], "failure")
        self.assertTrue(report["attention_required"])
        unknown = health.build_report([source], [], None, [], horizons=[1, 2, 5], now="2026-10-21T12:00:00Z")
        self.assertEqual(unknown["stages"]["outcomes"]["mature_unresolved"], 0)
        self.assertEqual(unknown["stages"]["outcomes"]["maturity_basis"], "observed_completed_sessions_lower_bound")

    def test_removal_or_pass_state_rewrite_is_not_progress(self):
        initial_eval = evaluation_report(sources=["event-1", "event-2"])
        initial = health.build_report([], [], None, [initial_eval], horizons=[1], now="2026-09-18T12:00:00Z")
        changed = evaluation_report(sources=["event-2"])
        changed["state"] = "RESEARCH_PASS_AWAITING_MANUAL_ENABLEMENT"
        changed.pop("evaluation_sha256")
        changed["evaluation_sha256"] = contracts.payload_sha256(changed)
        later = health.build_report([], [], None, [changed], horizons=[1], previous=initial, now="2026-09-19T12:00:00Z")
        self.assertEqual(later["stages"]["evaluations"]["new_progress_items"], 0)
        self.assertIsNone(later["stages"]["evaluations"]["last_progress_observed_at"])
        self.assertEqual(later["stages"]["evaluations"]["baseline_observed_at"], "2026-09-18T12:00:00Z")

    def test_cli_only_writes_isolated_report_and_preserves_every_input_byte(self):
        source = event(selected=True)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            store = base / "experience"
            store.mkdir()
            source_path = store / "event.json"
            source_path.write_text(json.dumps(source), encoding="utf-8")
            config_path = base / "config.json"
            config_path.write_text(json.dumps({"schema_version": "stock-trading-v2-learning-config-v1", "horizons_sessions": [1], "governance": {"production_decision_influence": False}}), encoding="utf-8")
            report_path = base / "health.json"
            before = {path: path.read_bytes() for path in base.rglob("*") if path.is_file()}
            args = ["health", "--experience-root", str(store), "--outcome-root", str(base / "outcomes"), "--regret", str(base / "regret.json"), "--evaluations", str(base / "eval"), "--config", str(config_path), "--report", str(report_path), "--workflow-status", "failure"]
            with patch("sys.argv", args):
                self.assertEqual(health.main(), 0)
            self.assertEqual({path: path.read_bytes() for path in before}, before)
            health.validate_report(json.loads(report_path.read_text(encoding="utf-8")))
            args[args.index(str(report_path))] = str(source_path)
            with patch("sys.argv", args), self.assertRaisesRegex(contracts.ContractError, "overlaps"):
                health.main()


if __name__ == "__main__":
    unittest.main()
