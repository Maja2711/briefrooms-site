import json
import tempfile
import unittest
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from scripts import stock_trading_component_champion as champion
from scripts import stock_trading_component_monitor as monitor
from scripts import stock_trading_portfolio as portfolio
from scripts import stock_trading_v2_production_bridge as bridge
from scripts import stock_trading_v2_production_replay as replay
from tests import test_stock_trading_portfolio_evaluation as fixtures


class ComponentEconomicMonitorTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PortfolioEvaluationTests()
        self.fixture.setUp()
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.now = self.fixture.now
        self.prior_policy = self.fixture.policy
        self.policy = deepcopy(self.prior_policy)
        self.policy["markets"]["US"]["minimum_cash_edge_points"] = 4.0
        self.prior_manifest = champion.bootstrap_manifest(created_at=self.now.isoformat())
        self.prior_manifest["components"]["ranking"]["version"] = "unchanged-ranking-v9"
        self.manifest = champion.build_promoted_manifest(self.prior_manifest, component="entry", version="cash-4", deployment_id="cash-deployment-4",
            challenger_id="cash-challenger-4", evidence_sha256="evidence", promotion_id="promotion", promoted_at=self.now.isoformat())
        self.manifest_path = self.root / "manifest.json"
        self.policy_path = self.root / "policy.json"
        champion.save_manifest(self.manifest, self.manifest_path)
        portfolio._atomic(self.policy_path, self.policy)
        self.portfolio_path = self.root / "canonical_portfolio_do_not_write.json"
        self.portfolio_bytes = json.dumps(self.fixture.state, sort_keys=True).encode("utf-8")
        self.portfolio_path.write_bytes(self.portfolio_bytes)
        self.config = bridge.load_config()
        self.registered = monitor.register_activation(previous_policy=self.prior_policy, previous_manifest=self.prior_manifest,
            activated_policy=self.policy, activated_manifest=self.manifest, initial_state=self.fixture.state,
            bridge_config=self.config, activated_at=self.now + timedelta(seconds=1), root=self.root / "monitor")
        self.activation = self.registered["activation"]
        self.end = replay._time(self.activation["monitoring_contract"]["window_end_at"])

    def run_monitor(self, as_of=None, **kwargs):
        return monitor.run(root=self.root / "monitor", frames_root=self.root / "frames", manifest_path=self.manifest_path,
            policy_path=self.policy_path, history_dir=self.root / "history", audit_path=self.root / "audit.jsonl",
            as_of=as_of or self.end, **kwargs)

    def capture_loss_tape(self):
        decision = self.now + timedelta(seconds=10)
        quote = {**self.fixture.quote(), **{key: decision.isoformat() for key in ("observed_at", "received_at", "decision_at")}}
        replay.capture_frame(self.root / "frames", kind="ADMISSION", observed_at=decision, recorded_at=decision, policy=self.policy,
            initial_state=self.fixture.state, bridge_config=self.config, runtime={"phase": "FULL"}, opportunities={"US": self.fixture.opportunity(["AAA"], utility=54.5)},
            quotes={"US": {"AAA": quote}}, benchmark={"US": {"price": 100, "provider": "SPY native receipt", "observed_at": decision.isoformat()}})
        later = self.now + timedelta(minutes=5)
        observation = self.fixture.raw_observation(later, [self.fixture.bar(later, later, low=96.9, close=98)])
        replay.capture_frame(self.root / "frames", kind="portfolio_lifecycle", observed_at=later, recorded_at=later, policy=self.policy,
            initial_state=self.fixture.state, observations={"US": {"AAA": observation}},
            benchmark={"US": {"price": 100, "provider": "SPY native receipt", "observed_at": later.isoformat()}})
        terminal = self.end - timedelta(hours=1)
        replay.capture_frame(self.root / "frames", kind="SESSION_CLOSE", observed_at=terminal, recorded_at=terminal, policy=self.policy,
            initial_state=self.fixture.state, benchmark={"US": {"price": 100, "provider": "SPY native receipt", "observed_at": terminal.isoformat()}})

    def test_full_native_future_window_rolls_back_only_entry_with_new_revision(self):
        self.capture_loss_tape()
        with patch.object(portfolio, "_chart", side_effect=AssertionError("monitor must not fetch data")):
            early = self.run_monitor(as_of=self.end - timedelta(days=2))
            self.assertEqual("MONITORING", early["results"][0]["status"])
            self.assertFalse(early["production_mutated"])
            final = self.run_monitor()
        self.assertEqual("ROLLED_BACK", final["results"][0]["status"], final)
        self.assertTrue(final["production_mutated"])
        restored = champion.load_manifest(self.manifest_path)
        self.assertEqual(3, restored["revision"])
        self.assertEqual(2, restored["previous_revision"])
        self.assertEqual(self.prior_manifest["components"]["entry"], restored["components"]["entry"])
        for name in champion.COMPONENTS:
            if name != "entry":
                self.assertEqual(self.manifest["components"][name], restored["components"][name])
        self.assertEqual(self.prior_policy, json.loads(self.policy_path.read_text(encoding="utf-8")))
        self.assertEqual(self.portfolio_bytes, self.portfolio_path.read_bytes())
        self.assertTrue((self.root / "history" / "champion-r000002.json").exists())
        audit = self.root / "audit.jsonl"
        self.assertEqual("ECONOMIC_COMPONENT_ROLLBACK", json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])["event"])
        saved_audit = audit.read_bytes()
        repeated = self.run_monitor(as_of=self.end + timedelta(days=1))
        self.assertFalse(repeated["production_mutated"])
        self.assertTrue(repeated["results"][0]["already_final"])
        self.assertEqual(saved_audit, audit.read_bytes())

    def test_missing_control_receipts_stay_pending_without_mutation(self):
        before = self.manifest_path.read_bytes(), self.policy_path.read_bytes()
        result = self.run_monitor()
        self.assertEqual("PENDING", result["results"][0]["status"])
        self.assertEqual("paired_actual_control_replay_incomplete", result["results"][0]["reason"])
        self.assertFalse(result["production_mutated"])
        self.assertEqual(before, (self.manifest_path.read_bytes(), self.policy_path.read_bytes()))

    def test_changed_policy_revision_or_runtime_cannot_trigger_stale_rollback(self):
        self.capture_loss_tape()
        changed = deepcopy(self.manifest)
        changed["revision"] += 1
        champion.save_manifest(changed, self.manifest_path)
        result = self.run_monitor()
        self.assertEqual("active_manifest_or_policy_changed", result["results"][0]["reason"])
        champion.save_manifest(self.manifest, self.manifest_path)
        with patch.object(replay, "production_code_sha256", return_value="new-code"):
            drift = self.run_monitor()
        self.assertEqual("production_code_changed", drift["results"][0]["reason"])
        self.assertFalse(drift["production_mutated"])

    def test_activation_and_observations_are_immutable_and_dry_run_does_not_write(self):
        self.capture_loss_tape()
        before = self.policy_path.read_bytes(), self.manifest_path.read_bytes()
        dry = self.run_monitor(dry_run=True)
        self.assertEqual("DRY_RUN_ROLLBACK_READY", dry["results"][0]["status"])
        self.assertEqual(before, (self.policy_path.read_bytes(), self.manifest_path.read_bytes()))
        self.assertFalse((self.root / "monitor" / "observations").exists())
        self.run_monitor(as_of=self.end - timedelta(days=2))
        receipt = next((self.root / "monitor" / "observations").rglob("*.json"))
        altered = json.loads(receipt.read_text(encoding="utf-8"))
        altered["net_nav"] = 999999
        receipt.write_text(json.dumps(altered), encoding="utf-8")
        with self.assertRaisesRegex(monitor.MonitorError, "immutable economic monitoring receipt conflict"):
            self.run_monitor(as_of=self.end - timedelta(days=1))
        deployment = Path(self.registered["path"])
        body = json.loads(deployment.read_text(encoding="utf-8"))
        body["monitoring_contract"]["rollback_min_net_delta_percent"] = 100
        deployment.write_text(json.dumps(body), encoding="utf-8")
        with self.assertRaisesRegex(monitor.MonitorError, "activation receipt invalid"):
            self.run_monitor()

    def test_activation_rejects_additional_policy_mutation(self):
        modified = deepcopy(self.policy)
        modified["markets"]["US"]["maximum_risk_percent"] = .09
        with self.assertRaisesRegex(monitor.MonitorError, "cannot absorb other policy changes"):
            monitor.register_activation(previous_policy=self.prior_policy, previous_manifest=self.prior_manifest,
                activated_policy=modified, activated_manifest=self.manifest, initial_state=self.fixture.state,
                bridge_config=self.config, activated_at=self.now, root=self.root / "bad-monitor")


if __name__ == "__main__":
    unittest.main()
