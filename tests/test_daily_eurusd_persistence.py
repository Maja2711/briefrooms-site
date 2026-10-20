"""Publication regressions across conflict retries and the exit-only writer."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from scripts import validate_daily_eurusd_persistence as gate


def payload():
    return {
        "schema_version": "daily-engine-output-v1", "instrument": "EUR/USD",
        "timestamp": "2026-10-20T10:00:00Z", "direction": "FLAT", "score": 50,
        "confidence": 0.3, "entry": None, "stop": None, "target": None,
        "horizon": "intraday_to_27h", "engine_version": gate.ENGINE_VERSION,
        "status": "NO_TRADE", "decision_mode": "WITH",
        "metadata": {
            "dynamic_exit": {"enabled": True}, "same_thesis_reentry_guard": {"enabled": True},
            "contextual_entry_policy": {"direction_mutation_allowed": False, "decision_influence": False},
            "belief_pipeline": {"architecture": "BELIEF_FIRST_V1", "final_decision_after_belief": True},
            "direction_authority": {
                "owner": "NATIVE_DAILY_EURUSD_BELIEF_FIRST_DECISION_ENGINE",
                "source": "NATIVE_BELIEF_FIRST", "epistemic_input": "BELIEF_CORE_CF07",
            },
            "final_decision": {
                "decision_source": "NATIVE_BELIEF_FIRST", "epistemic_source": "BELIEF_CORE_CF07",
                "legacy_raw_score_direction_authority": False,
                "epistemic_consumer": {"available": True},
            },
            "shadow_boundary": {name: {"production_direction_authority": False, "production_timing_authority": False} for name in ("A_B_C", "FSE", "EURUSD_X")},
        },
    }


def history(*rows):
    return {"schema_version": "eurusd-daily-history-v1", "trades": list(rows)}


def position():
    return {"trade_id": "eurusd:frozen", "status": "OPEN", "direction": "LONG", "opened_at": "2026-10-20T09:00:00Z", "entry": 1.12, "stop": 1.11, "target": 1.14, "decision_manifest": {"policy_version_id": "frozen-v1"}}


class DailyEURUSDPersistenceTests(unittest.TestCase):
    def test_qualified_flat_is_publishable(self):
        self.assertEqual([], gate.validate_payload(payload()))

    def test_missing_epistemic_cannot_publish_after_conflict_recompute(self):
        output = payload()
        output["metadata"]["final_decision"]["epistemic_consumer"] = {"available": False, "reason": "stale"}
        self.assertTrue(any("EPISTEMIC_UNAVAILABLE_NO_PUBLICATION" in e for e in gate.validate_payload(output)))
        output["status"] = "CLOSED_DYNAMIC"
        self.assertEqual([], gate.validate_payload(output))

    def test_open_risk_management_is_not_blocked_by_upstream_unavailable(self):
        output = payload()
        output["metadata"]["position"] = position()
        output["metadata"]["final_decision"]["epistemic_consumer"] = {"available": False}
        self.assertEqual([], gate.validate_payload(output))

    def test_recompute_cannot_restore_legacy_authority_or_shadow_influence(self):
        for field in ("production_direction_authority", "production_timing_authority"):
            with self.subTest(field=field):
                output = payload()
                output["metadata"]["shadow_boundary"]["FSE"][field] = True
                self.assertTrue(gate.validate_payload(output))
        output = payload()
        output["metadata"]["candidate"] = {"gate_reasons": ["daily_entry_limit_reached"]}
        self.assertTrue(any("legacy admission" in e for e in gate.validate_payload(output)))

    def test_legacy_output_only_allowed_on_exit_only_path(self):
        output = payload()
        output["engine_version"] = "eurusd-daily-spot-v1.9.0"
        self.assertTrue(gate.validate_payload(output))
        self.assertEqual([], gate.validate_payload(output, lifecycle_only=True))

    def test_closed_history_is_append_only_and_never_relabelled(self):
        closed = {"trade_id": "old", "r_multiple": -1, "engine_version": "legacy"}
        before = history(closed)
        self.assertEqual([], gate.validate_history(history(closed, {"trade_id": "new"}), before))
        self.assertTrue(gate.validate_history(history(), before))
        self.assertTrue(gate.validate_history(history(dict(closed, r_multiple=1)), before))
        self.assertTrue(gate.validate_history(history(closed, closed), before))
        self.assertTrue(gate.validate_history(history({"r_multiple": 1}), before))

    def test_policy_change_cannot_rewrite_open_entry_or_frozen_manifest(self):
        before = payload()
        before["metadata"]["position"] = position()
        after = copy.deepcopy(before)
        after["metadata"]["position"]["mark_price"] = 1.121
        self.assertEqual([], gate.validate_frozen_position(after, history(), before))
        after["metadata"]["position"]["decision_manifest"]["policy_version_id"] = "new-v2"
        self.assertTrue(gate.validate_frozen_position(after, history(), before))

    def test_settlement_preserves_entry_and_manifest(self):
        before = payload()
        before["metadata"]["position"] = position()
        after = payload()
        row = dict(position(), status="CLOSED", closed_at="2026-10-20T10:00:00Z")
        self.assertEqual([], gate.validate_frozen_position(after, history(row), before))
        row["entry"] = 1.119
        self.assertTrue(gate.validate_frozen_position(after, history(row), before))
        self.assertTrue(gate.validate_frozen_position(after, history(), before))

    def test_new_open_notification_is_required_on_recompute_too(self):
        output = payload()
        output["metadata"]["position"] = position()
        self.assertTrue(gate.validate_notification_coverage(output, {"events": []}))
        event_id = hashlib.sha256(b"daily|OPEN|eurusd:frozen").hexdigest()[:24]
        self.assertEqual([], gate.validate_notification_coverage(output, {"events": [{"event_id": event_id}]}))

    def _files(self, root, output, trades, **extra):
        values = {gate.SPOT: output, gate.HISTORY: trades, gate.EVENTS: {"events": []}, **extra}
        for name, value in values.items():
            path = Path(root) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value), encoding="utf-8")

    def test_shared_state_gate_audits_provenance_on_every_writer(self):
        for lifecycle_only in (False, True):
            with self.subTest(lifecycle_only=lifecycle_only), tempfile.TemporaryDirectory() as tmp:
                self._files(tmp, payload(), history())
                baseline = {gate.SPOT: payload(), gate.HISTORY: history()}
                with mock.patch.object(gate, "_baseline", side_effect=lambda root, ref, path, errors: baseline.get(path)), mock.patch.object(gate.audit_provenance, "audit_paths", return_value={"errors": ["bad provenance"]}):
                    errors = gate.validate_state(Path(tmp), "HEAD", lifecycle_only)
                self.assertTrue(any("native provenance" in e for e in errors))

    def test_exit_only_gate_forbids_feedback_or_unrelated_trade_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._files(tmp, payload(), history({"trade_id": "unrelated"}), **{gate.POLICY: {"changed": True}})
            baseline = {gate.SPOT: payload(), gate.HISTORY: history(), gate.POLICY: {"changed": False}}
            with mock.patch.object(gate, "_baseline", side_effect=lambda root, ref, path, errors: baseline.get(path)), mock.patch.object(gate.audit_provenance, "audit_paths", return_value={"errors": []}), mock.patch.object(gate, "validate_production_feedback", return_value=[]):
                errors = gate.validate_state(Path(tmp), "HEAD", True)
            self.assertTrue(any("mutated production policy" in e for e in errors))
            self.assertTrue(any("settled an unrelated position" in e for e in errors))

    def test_workflow_retries_use_same_gate_and_watcher_precedes_web_push(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/daily-eurusd-monitor.yml").read_text(encoding="utf-8")
        retry = workflow.split("Canonical Daily state changed remotely; recomputing", 1)[1]
        self.assertLess(retry.index("validate_daily_eurusd_persistence.py --baseline-ref HEAD"), retry.index('stage_daily_state'))
        self.assertIn("validate_daily_eurusd_persistence.py --baseline-ref HEAD^", workflow)
        self.assertLess(workflow.index("name: Ensure realtime lifecycle watcher"), workflow.index("name: Dispatch persisted Daily transition to Web Push"))
        for path in (gate.POLICY, gate.DECISIONS):
            self.assertIn(path, workflow)
        watcher = (root / "scripts/daily_eurusd_realtime_watch.sh").read_text(encoding="utf-8")
        self.assertLess(watcher.index("validate_daily_eurusd_persistence.py --baseline-ref HEAD --lifecycle-only"), watcher.index("git commit"))

    def _real_sample(self, root):
        from tests.test_daily_eurusd_production_record import ProductionDecisionRecordTests
        helper = ProductionDecisionRecordTests()
        now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
        opened = helper._runtime(root, now)
        before_policy = json.loads((root / "eurusd_production_policy_state.json").read_text())
        before_ledger = json.loads((root / "eurusd_production_decisions.json").read_text())
        closed = helper._runtime(root, now + timedelta(minutes=2), stop=True)
        state = json.loads((root / "eurusd_production_policy_state.json").read_text())
        ledger = json.loads((root / "eurusd_production_decisions.json").read_text())
        trades = json.loads((root / "eurusd_daily_history.json").read_text())
        return opened.to_dict(), closed.to_dict(), trades, state, ledger, before_policy, before_ledger

    def test_real_runtime_feedback_and_all_crossfile_bindings(self):
        with tempfile.TemporaryDirectory() as tmp:
            opened, closed, trades, state, ledger, before_policy, before_ledger = self._real_sample(Path(tmp))
            self.assertEqual([], gate.validate_production_feedback(closed, trades, state, ledger, before_policy, before_ledger))
            self.assertTrue(any("actual history" in e for e in gate.validate_production_feedback(closed, history(), state, ledger)))
            empty = dict(ledger, decisions=[])
            self.assertTrue(any("case has no identical" in e for e in gate.validate_production_feedback(closed, trades, state, empty)))
            from scripts import daily_eurusd_decision_record as records
            unknown = copy.deepcopy(ledger)
            manifest = unknown["decisions"][0]
            manifest["policy_version_id"] = manifest["policy"]["version_id"] = "unknown-version"
            body = {k: v for k, v in manifest.items() if k not in {"decision_id", "manifest_sha256"}}
            manifest["decision_id"] = "eurusd-production-decision:" + records.digest(body)[:24]
            manifest["manifest_sha256"] = records.digest({k: v for k, v in manifest.items() if k != "manifest_sha256"})
            self.assertTrue(any("policy version missing" in e for e in gate.validate_production_feedback(closed, trades, state, unknown)))
            rewritten = copy.deepcopy(trades)
            rewritten["trades"][0]["entry"] += .001
            self.assertTrue(any("manifested closed trade" in e for e in gate.validate_production_feedback(closed, rewritten, state, ledger)))

    def test_degraded_runtime_close_publishes_with_unchanged_corrupt_governance(self):
        from tests.test_daily_eurusd_production_record import ProductionDecisionRecordTests
        now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
        for missing in (False, True):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                live = root / "runtime"
                live.mkdir()
                helper = ProductionDecisionRecordTests()
                opened = helper._runtime(live, now).to_dict()
                policy_path = live / "eurusd_production_policy_state.json"
                ledger_path = live / "eurusd_production_decisions.json"
                if missing:
                    policy_path.unlink()
                    ledger_path.unlink()
                else:
                    policy_path.write_bytes(b"{invalid")
                raw_before = {gate.POLICY: policy_path.read_bytes() if policy_path.exists() else None,
                              gate.DECISIONS: ledger_path.read_bytes() if ledger_path.exists() else None}
                closed = helper._runtime(live, now + timedelta(minutes=2), stop=True).to_dict()
                trades = json.loads((live / "eurusd_daily_history.json").read_text())
                self._files(root, closed, trades)
                for name, raw in raw_before.items():
                    if raw is not None:
                        (root / name).write_bytes(raw)
                baseline = {gate.SPOT: opened, gate.HISTORY: history()}
                with mock.patch.object(gate, "_baseline", side_effect=lambda root, ref, path, errors: baseline.get(path)), mock.patch.object(gate, "_baseline_raw", side_effect=lambda root, ref, path: raw_before.get(path)):
                    self.assertEqual([], gate.validate_state(root, "HEAD"))
                self.assertEqual(raw_before[gate.POLICY], gate._raw(root, gate.POLICY))
                # The same degraded flag cannot authorize an unrelated/new trade.
                baseline[gate.SPOT] = payload()
                with mock.patch.object(gate, "_baseline", side_effect=lambda root, ref, path, errors: baseline.get(path)), mock.patch.object(gate, "_baseline_raw", side_effect=lambda root, ref, path: raw_before.get(path)):
                    self.assertTrue(gate.validate_state(root, "HEAD"))

    def test_runtime_code_change_after_rebase_blocks_old_computation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            opened, closed, trades, state, ledger, _, _ = self._real_sample(root)
            self._files(root, closed, trades, **{gate.POLICY: state, gate.DECISIONS: ledger})
            with mock.patch.object(gate, "_baseline", return_value=None), mock.patch.object(gate._module("daily_eurusd_decision_record"), "runtime_code_hash", return_value="different-code"):
                self.assertTrue(any("runtime source changed" in e for e in gate.validate_state(root, "HEAD")))


if __name__ == "__main__":
    unittest.main()
