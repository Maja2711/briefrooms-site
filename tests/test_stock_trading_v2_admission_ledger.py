from __future__ import annotations

import unittest
import json
import os
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

from scripts import stock_trading_v2_admission_ledger as admission
from scripts import stock_trading_v2_contracts as contracts
from scripts import stock_trading_v2_experience_store as experience


POLICY = {
    "policy_version": "test-policy",
    "forced_trade_allowed": False,
    "markets": {
        "GPW": {"max_open_positions": 3, "minimum_entry_score": 72, "minimum_reward_risk": 1.5, "maximum_risk_percent": 0.07},
        "US": {"max_open_positions": 3, "minimum_entry_score": 72, "minimum_reward_risk": 1.5, "maximum_risk_percent": 0.07},
    },
}
for _market, _currency in (("GPW", "PLN"), ("US", "USD")):
    POLICY["markets"][_market].update({
        "target_position_notional": 5000, "position_currency": _currency,
        "fractional_quantity_allowed": True, "sizing_policy_version": "FIXED_NOTIONAL_V1",
    })


def event(*, selected: bool, conservative_ev: float = 0.1, score: float = 80.0, blocker=None):
    state = {
        "identity": {"name": "Test", "ticker": "TST", "sector": "test"},
        "decision_path": {
            "producer_decision": "TRANSAKCJA" if selected else "BRAK_TRANSAKCJI",
            "selection_mode": "NORMAL",
            "first_blocking_gate": blocker,
        },
        "score_state": {
            "score": score,
            "expected_value": {"conservative_ev_r": conservative_ev},
        },
        "market_state": {
            "historical_data_gate": {"accepted": True},
            "execution_data_gate": {"status": "accepted"},
        },
        "risk_plan": {
            "reference_price": 100.0,
            "stop": 95.0,
            "target": 110.0,
            "risk_percent": 0.05,
            "reward_risk": 2.0,
        },
    }
    return contracts.make_experience_event(
        market="GPW",
        symbol="TST.WA",
        decision_at="2026-09-16T10:00:00+02:00",
        session_date="2026-09-16",
        selected=selected,
        source_engine="test",
        source_schema_version="test-v1",
        source_policy_version="test-policy",
        source_payload_sha256="abc123",
        candidate_state=state,
        recorded_at="2026-09-16T08:00:01Z",
    )


def formal_event(*, selected=True, utility=56, decision_at="2026-10-23T10:00:00Z", recorded_at="2026-10-23T10:00:01Z"):
    policy = deepcopy(POLICY)
    policy["schema_version"] = "stock-trading-policy-v1"
    for market in policy["markets"].values():
        market["minimum_cash_edge_points"] = 5
    snapshots = {"policy": policy, "opportunity_config": {"cash_utility": 50}, "portfolio": {"markets": {"GPW": {"open_positions": []}}}, "opportunity": {"market": "GPW", "generated_at": decision_at}}
    context = {"schema_version": "stock-trading-v2-decision-context-v1", "market": "GPW", "decision_at": decision_at, "evidence_snapshot": {}}
    for name, snapshot in snapshots.items():
        context[name + "_snapshot"] = snapshot
        context[name + "_sha256"] = contracts.payload_sha256(snapshot)
    return contracts.make_experience_event(market="GPW", symbol="TST.WA", decision_at=decision_at, session_date=decision_at[:10], selected=selected,
        source_engine="stock-trading-v2", source_schema_version="v2", source_policy_version=policy["policy_version"], source_payload_sha256=context["opportunity_sha256"],
        candidate_state={"decision_context": context, "score_state": {"utility": utility, "portfolio_utility": utility},
            "decision_path": {"eligible": True, "already_held": False, "available_slots": 3, "minimum_cash_edge_points": 5, "cash_utility": 50, "cash_margin_only_blocker": not selected}}, recorded_at=recorded_at)


class StockTradingV2AdmissionLedgerTests(unittest.TestCase):
    def test_epoch_cli_runs_before_discovery_and_does_not_reset_or_read_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root, store = base / "admission", base / "experience"
            policy = deepcopy(POLICY)
            policy["schema_version"] = "stock-trading-policy-v1"
            policy_path = base / "policy.json"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            args = ["admission", "--root", str(root), "--policy", str(policy_path), "--activate-epoch"]
            with patch("sys.argv", args), patch.object(contracts, "iso_utc", return_value="2026-10-23T09:00:00Z"), patch.object(admission, "iter_events", side_effect=AssertionError("activation must not read source events")):
                self.assertEqual(admission.main(), 0)
            epoch_path = base / "admission_epochs/prospective-v2.json"
            original = epoch_path.read_bytes()
            with patch("sys.argv", args), patch.object(contracts, "iso_utc", return_value="2026-10-24T09:00:00Z"):
                self.assertEqual(admission.main(), 0)
            self.assertEqual(epoch_path.read_bytes(), original)
            store.mkdir()
            (store / "source.json").write_text(json.dumps(formal_event()), encoding="utf-8")
            result = admission.freeze_all(experience_root=store, admission_root=root, policy_path=policy_path, now="2026-10-23T10:01:00Z")
            self.assertEqual(result["observations_written"], 1)
            self.assertEqual(result["retrospective_excluded"], 0)
            self.assertEqual(admission.verify(root)["formal_count"], 1)

    def test_real_1059_experiences_and_2078_admissions_keep_original_bytes(self):
        fixture_data = Path(os.environ.get("STOCK_TRADING_LEGACY_FIXTURE_ROOT", experience.DEFAULT_STORE_ROOT.parent))
        original_store = fixture_data / experience.DEFAULT_STORE_ROOT.name
        original_admission_root = fixture_data / admission.DEFAULT_ROOT.name
        original_events = list(experience.iter_event_files(original_store))
        original_admissions = list(original_admission_root.rglob("*.json"))
        if len(original_events) < 1059 or len(original_admissions) < 2078:
            self.skipTest("Full legacy 1059-experience/2078-admission snapshot unavailable; set STOCK_TRADING_LEGACY_FIXTURE_ROOT to the read-only research data/investments snapshot")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            store, root = base / "experience", base / "admission"
            shutil.copytree(original_store, store)
            shutil.copytree(original_admission_root, root)
            # The live fixture now contains prospective v2 admissions too.
            # Their original epoch is part of the immutable source contract;
            # omitting it would manufacture a different activation in this copy.
            original_epochs = fixture_data / "stock_trading_v2_admission_epochs"
            epochs = base / "admission_epochs"
            if original_epochs.exists():
                shutil.copytree(original_epochs, epochs)
            expected_formal = sum(admission.is_formal_observation(json.loads(path.read_text(encoding="utf-8")))
                                  for path in original_admissions)
            before = {path: path.read_bytes() for directory in (store, root, epochs) for path in directory.rglob("*.json")}
            policy = deepcopy(POLICY)
            policy["schema_version"] = "stock-trading-policy-v1"
            policy_path = base / "policy.json"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            first = admission.freeze_all(experience_root=store, admission_root=root, policy_path=policy_path, now="2026-10-26T09:00:00Z")
            policy["policy_version"] = "changed-current-policy-must-not-reinterpret-history"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            second = admission.freeze_all(experience_root=store, admission_root=root, policy_path=policy_path, now="2026-10-26T09:01:00Z")
            self.assertEqual(first["observations_written"], 0)
            self.assertEqual(second["observations_written"], 0)
            self.assertEqual({path: path.read_bytes() for path in before}, before)
            self.assertEqual(experience.verify_store(store)["event_count"], len(original_events))
            self.assertEqual(admission.verify(root)["count"], len(original_admissions))
            self.assertEqual(admission.verify(root)["formal_count"], expected_formal)

    def test_actual_v2_opportunity_adapter_produces_admissible_frozen_context(self):
        from scripts import stock_trading_v2_opportunity_engine as opportunity
        from scripts import stock_trading_v2_opportunity_experience as freeze
        from tests.test_stock_trading_v2_opportunity_experience import evidence_snapshot

        policy = deepcopy(POLICY)
        for market in policy["markets"].values():
            market["minimum_cash_edge_points"] = 5
        evidence = evidence_snapshot()
        source = opportunity.compare_opportunities(evidence, {"markets": {"US": {"open_positions": []}}}, opportunity.load_config(),
            generated_at=datetime(2026, 9, 16, 15, tzinfo=timezone.utc), policy_snapshot=policy)
        events = freeze.events_from_snapshots(source, evidence, candidates_per_cycle=2, recorded_at="2026-09-16T15:00:01Z")
        with tempfile.TemporaryDirectory() as tmp:
            epoch = admission.ensure_epoch(Path(tmp) / "admissions", policy, now="2026-09-16T14:00:00Z")
            rows = [admission.make_observation(item, policy=policy, epoch=epoch, recorded_at="2026-09-16T15:00:02Z") for item in events]
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(admission.is_formal_observation(row) for row in rows))
            self.assertEqual(sum(row["champion"]["action"] == "LONG" for row in rows), 1)
            self.assertTrue(all(row["champion"]["policy_sha256"] == contracts.payload_sha256(policy) for row in rows))

    def test_formal_baseline_uses_source_policy_and_is_frozen_once(self):
        source = formal_event()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "admission"
            epoch = admission.ensure_epoch(root, POLICY, now="2026-10-23T09:00:00Z")
            changed_current = deepcopy(POLICY)
            changed_current["policy_version"] = "changed-after-source"
            observation = admission.make_observation(source, policy=changed_current, recorded_at="2026-10-23T10:01:00Z", epoch=epoch)
            self.assertTrue(admission.is_formal_observation(observation))
            self.assertEqual(observation["champion"]["policy_version"], "test-policy")
            self.assertEqual(observation["champion"]["action"], "LONG")
            self.assertEqual(observation["admission_contract"]["minimum_cash_edge_points"], 5)
            self.assertTrue(admission.persist(root, observation))
            path = admission.observation_path(root, observation)
            frozen_bytes = path.read_bytes()
            later = admission.make_observation(source, policy=changed_current, recorded_at="2026-10-23T10:02:00Z", epoch=epoch)
            self.assertEqual(later["observation_id"], observation["observation_id"])
            self.assertFalse(admission.persist(root, later))
            self.assertEqual(path.read_bytes(), frozen_bytes)
            self.assertEqual(admission.verify(root)["formal_count"], 1)

    def test_new_epoch_does_not_admit_history_and_old_rows_are_unchanged(self):
        old = event(selected=True)
        legacy = admission.make_observation(old, policy=POLICY, recorded_at="2026-09-16T08:01:00Z")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            store, root = base / "experience", base / "admission"
            store.mkdir()
            (store / "old.json").write_text(json.dumps(old), encoding="utf-8")
            admission.persist(root, legacy)
            old_path = admission.observation_path(root, legacy)
            old_bytes = old_path.read_bytes()
            late = formal_event(decision_at="2026-09-17T10:00:00Z", recorded_at="2026-09-17T10:00:01Z")
            (store / "late.json").write_text(json.dumps(late), encoding="utf-8")
            policy = deepcopy(POLICY)
            policy["schema_version"] = "stock-trading-policy-v1"
            policy_path = base / "policy.json"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            result = admission.freeze_all(experience_root=store, admission_root=root, policy_path=policy_path, now="2026-10-23T09:00:00Z")
            self.assertEqual(result["observations_written"], 0)
            self.assertEqual(result["observations_existing"], 1)
            self.assertEqual(result["retrospective_excluded"], 1)
            self.assertEqual(old_path.read_bytes(), old_bytes)
            self.assertEqual(list(admission.iter_observations(root, formal_only=True)), [])
            retrospective_root = base / "admission_retrospective"
            retrospective = list(admission.iter_observations(retrospective_root))
            self.assertEqual(len(retrospective), 1)
            self.assertFalse(admission.is_formal_observation(retrospective[0]))
            self.assertFalse(retrospective[0]["governance"]["prospective_before_outcome"])
            epoch_path = base / "admission_epochs/prospective-v2.json"
            epoch_bytes = epoch_path.read_bytes()
            policy["policy_version"] = "another-policy"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            admission.freeze_all(experience_root=store, admission_root=root, policy_path=policy_path, now="2026-10-24T09:00:00Z")
            self.assertEqual(epoch_path.read_bytes(), epoch_bytes)
            self.assertEqual(old_path.read_bytes(), old_bytes)

    def test_formal_source_hash_and_actual_cash_decision_are_verified(self):
        source = formal_event(selected=False, utility=54.5)
        with tempfile.TemporaryDirectory() as tmp:
            epoch = admission.ensure_epoch(Path(tmp) / "admissions", POLICY, now="2026-10-23T09:00:00Z")
            row = admission.make_observation(source, policy=POLICY, epoch=epoch, recorded_at="2026-10-23T10:01:00Z")
            self.assertEqual(row["champion"]["action"], "CASH")
            self.assertEqual(row["champion"]["reason"], "cash_admission_margin")
            for key, value in (("policy_sha256", "forged"), ("action", "LONG")):
                tampered = deepcopy(row)
                tampered["champion"][key] = value
                tampered.pop("observation_sha256")
                tampered["observation_sha256"] = contracts.payload_sha256(tampered)
                with self.assertRaises(contracts.ContractError):
                    admission.validate_observation(tampered)
            stale = "2026-10-23T10:16:00Z"
            with self.assertRaisesRegex(contracts.ContractError, "prospectively"):
                admission.make_observation(source, policy=POLICY, epoch=epoch, recorded_at=stale)
            future = formal_event(recorded_at="2026-10-23T11:00:00Z")
            with self.assertRaisesRegex(contracts.ContractError, "prospectively"):
                admission.make_observation(future, policy=POLICY, epoch=epoch, recorded_at="2026-10-23T10:01:00Z")

    def test_positive_selected_candidate_is_long(self):
        action, reason, diagnostics = admission.champion_admission(event(selected=True), POLICY)
        self.assertEqual(action, "LONG")
        self.assertEqual(reason, "qualified_high_expectancy_candidate")
        self.assertTrue(diagnostics["portfolio_qualification_ran"])

    def test_non_positive_conservative_ev_is_frozen_as_cash_reason(self):
        action, reason, _ = admission.champion_admission(event(selected=True, conservative_ev=-0.01), POLICY)
        self.assertEqual(action, "CASH")
        self.assertEqual(reason, "non_positive_conservative_expected_value")

    def test_producer_rejection_is_cash_with_original_blocker(self):
        action, reason, diagnostics = admission.champion_admission(event(selected=False, blocker="liquidity"), POLICY)
        self.assertEqual(action, "CASH")
        self.assertEqual(reason, "producer_rejected:liquidity")
        self.assertFalse(diagnostics["portfolio_qualification_ran"])

    def test_observation_is_hashed_and_shadow_only(self):
        payload = admission.make_observation(event(selected=True), policy=POLICY, recorded_at="2026-09-16T08:01:00Z")
        self.assertEqual(payload["champion"]["action"], "LONG")
        self.assertFalse(payload["governance"]["production_decision_influence"])
        admission.validate_observation(payload)
        payload["champion"]["reason"] = "tampered"
        with self.assertRaises(Exception):
            admission.validate_observation(payload)


if __name__ == "__main__":
    unittest.main()
