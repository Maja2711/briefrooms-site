from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import daily_eurusd_belief_decision as decision
import daily_eurusd_decision_record as records
import daily_eurusd_fast_lifecycle as fast
import daily_eurusd_lifecycle as lifecycle
import daily_eurusd_spot_v19 as v19
import execution_price_engine as epe
from belief_epistemic_state import EpistemicStateBuilder
from belief_market_data_adapter import Bar

UTC = timezone.utc


def epistemic(snapshot_at, evidence_at, *, release=None, probability=.8):
    definitions, beliefs, evidence = [], [], []
    for index, bid in enumerate(decision.BELIEF_WEIGHTS):
        eid = f"source-{index}"
        definitions.append({"belief_id": bid, "claim": bid, "half_life_hours": decision.BELIEF_MAX_AGE_HOURS[bid]})
        beliefs.append({"belief_id": bid, "probability": probability, "confidence": .8,
            "freshness_score": .99, "representative_evidence_ids": [eid],
            "last_updated": snapshot_at.isoformat(), "audit_status": "clean"})
        evidence.append({"evidence_id": eid, "observed_at": evidence_at.isoformat(),
            "direction": 1, "strength": .8, "reliability": .9,
            "source_type": "derived", "source_ref": "https://www.bls.gov/",
            "metadata": dict(release or {}) if "macro_surprise" in bid else {}})
    states = EpistemicStateBuilder({"definitions": definitions, "beliefs": beliefs, "evidence": evidence}).build_all()
    return {"contract_version": "belief-epistemic-state-v1", "created_at": snapshot_at.isoformat(),
        "authority": {"llm_may_ignore_aggregate": False, "llm_may_override_probability": False},
        "controls": {"belief_core_writeback_enabled": False},
        "states": {bid: state.to_dict() for bid, state in states.items()}}


def calendar_rows(now, events=()):
    rows = [{"adapter": "macro_event_calendar", "metric": "calendar_coverage",
        "observed_at": now.isoformat(), "metadata": {"complete": True}}]
    rows.extend({"adapter": "macro_event_calendar", "metric": "scheduled_macro_event",
        "source": "BLS", "source_ref": "https://www.bls.gov/", "metadata": {
            "importance": "high", "title": uid, "uid": uid, "event_at": at.isoformat()}}
        for uid, at in events)
    return rows


class Client:
    def __init__(self, now, *, stop=False):
        self.now, self.stop = now, stop

    def bars(self, symbol, period, interval):
        count = 120 if interval == "1m" else 220
        step = timedelta(minutes=1 if interval == "1m" else 30)
        result = [Bar(timestamp=self.now - step * (count - 1 - index), open=1.12,
            high=1.1201, low=1.1199, close=1.12) for index in range(count)]
        if self.stop and interval == "1m":
            result[-1] = Bar(timestamp=self.now, open=1.12, high=1.1201, low=1.11, close=1.111)
        return result


class ProductionDecisionRecordTests(unittest.TestCase):
    def test_snapshot_delay_never_refreshes_original_evidence(self):
        source = datetime(2026, 10, 20, 12, tzinfo=UTC)
        original = source - timedelta(minutes=1)
        payload = epistemic(source, original)
        state, meta = decision._epistemic_projection_to_belief_state(payload, observed_at=source + timedelta(minutes=10))
        result = decision.synthesize(state, observed_at=source + timedelta(minutes=10), authoritative_consumer=meta)
        self.assertTrue(result["used_beliefs"])
        self.assertTrue(all(row["observed_at"] == original.isoformat().replace("+00:00", "Z")
                            for row in result["used_beliefs"]))

    def test_old_projection_without_lineage_uses_source_snapshot_clock(self):
        source = datetime(2026, 10, 20, 12, tzinfo=UTC)
        payload = epistemic(source, source - timedelta(minutes=1))
        for bid, row in payload["states"].items():
            row["provenance_root"].pop("evidence_lineage")
            row["freshness"] = .5 ** ((1 / 60) / decision.BELIEF_MAX_AGE_HOURS[bid])
        state, _ = decision._epistemic_projection_to_belief_state(payload, observed_at=source + timedelta(minutes=10))
        self.assertEqual(state["evidence"][0]["observed_at"], "2026-10-20T11:59:00Z")
        self.assertEqual(state["evidence"][0]["source_evidence"], [])

    def test_release_requires_original_matching_official_actuals(self):
        event = datetime(2026, 10, 20, 12, 1, tzinfo=UTC)
        now = event + timedelta(minutes=9)
        release = {"adapter": "daily_eurusd_macro_fast", "phase": "POST_RELEASE",
            "event_uid": "nfp", "event_at": event.isoformat(), "actual_count": 2}
        def check(evidence_at, metadata, events):
            payload = epistemic(now - timedelta(minutes=1), evidence_at, release=metadata)
            state, meta = decision._epistemic_projection_to_belief_state(payload, observed_at=now)
            result = decision.synthesize(state, observed_at=now, authoritative_consumer=meta)
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / "observations.jsonl").write_text("\n".join(map(json.dumps, calendar_rows(now, events))), encoding="utf-8")
                return decision.calendar_safety(observed_at=now, state_path=root / "state.json", decision=result)
        self.assertTrue(check(event - timedelta(minutes=2), release, [("nfp", event)])["blocked"])
        self.assertFalse(check(event + timedelta(minutes=1), release, [("nfp", event)])["blocked"])
        wrong = {**release, "event_uid": "different-release"}
        self.assertTrue(check(event + timedelta(minutes=1), wrong, [("nfp", event)])["blocked"])
        self.assertTrue(check(event + timedelta(minutes=1), release,
            [("nfp", event), ("cpi", event + timedelta(minutes=1))])["blocked"])

    def _runtime(self, root, now, *, stop=False, probability=.8):
        state_path, ep_path = root / "belief.json", root / "epistemic.json"
        state_path.write_text("{}", encoding="utf-8")
        ep_path.write_text(json.dumps(epistemic(now - timedelta(seconds=30), now - timedelta(minutes=1), probability=probability)), encoding="utf-8")
        (root / "observations.jsonl").write_text("\n".join(map(json.dumps, calendar_rows(now))), encoding="utf-8")
        fill = epe.verify_daily_live_quotes("LONG", [epe.Quote(1.12, now, "Stooq:EURUSD"),
            epe.Quote(1.12002, now, "Yahoo Finance:EURUSD")], now=now)
        with patch.dict(os.environ, {"BELIEF_CORE_STATE": str(state_path), "BELIEF_EPISTEMIC_STATE": str(ep_path),
            "DAILY_EURUSD_REQUIRE_EPISTEMIC": "1"}), patch.object(epe, "daily_eurusd_market_fill", return_value=fill):
            return v19.run_cycle(root / "eurusd_daily_spot.json", root / "eurusd_daily_history.json", Client(now, stop=stop))

    def test_new_admission_and_canonical_close_bind_same_manifest(self):
        now = datetime(2026, 10, 20, 12, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            opened = self._runtime(root, now)
            self.assertEqual(opened.status, "OPEN")
            position = opened.metadata["position"]
            self.assertEqual(position["engine_version"], v19.ENGINE_VERSION)
            manifest = copy.deepcopy(position["decision_manifest"])
            records.validate_manifest(manifest)
            self.assertEqual(manifest["execution_price_engine"]["execution_cost_contract"], lifecycle.EXECUTION_COST_CONTRACT)
            closed = self._runtime(root, now + timedelta(minutes=2), stop=True)
            self.assertIsNone(closed.metadata.get("position"))
            trade = closed.metadata["last_trade"]
            self.assertEqual(trade["decision_manifest"], manifest)
            self.assertEqual(trade["engine_version"], v19.ENGINE_VERSION)
            history = json.loads((root / "eurusd_daily_history.json").read_text(encoding="utf-8"))
            self.assertEqual(history["trades"][0]["decision_manifest"], manifest)
            state = json.loads((root / "eurusd_production_policy_state.json").read_text(encoding="utf-8"))
            case = next(iter(state["cases"].values()))
            self.assertEqual(case["outcome"]["source"], "CANONICAL_ACTUAL")
            ledger = records.load_ledger(root / "eurusd_production_decisions.json")
            self.assertEqual(len(ledger["decisions"]), 2)
            before = {**ledger, "decisions": ledger["decisions"][:1]}
            records.validate_ledger_transition(before, ledger)
            changed = copy.deepcopy(ledger)
            changed["decisions"][0]["final_decision"]["score"] += 1
            with self.assertRaises(ValueError):
                records.validate_ledger_transition(before, changed)

    def test_fast_exit_preserves_manifest_without_policy_mutation(self):
        now = datetime(2026, 10, 20, 12, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            opened = self._runtime(root, now)
            policy_path = root / "eurusd_production_policy_state.json"
            policy_before = policy_path.read_bytes()
            bars = Client(now + timedelta(minutes=2), stop=True).bars("EURUSD=X", "5d", "1m")
            trade = fast.evaluate_open_position(opened.to_dict(), bars, bars[-1].timestamp)
            self.assertIsNotNone(trade)
            self.assertEqual(trade["decision_manifest"], opened.metadata["position"]["decision_manifest"])
            self.assertEqual(policy_path.read_bytes(), policy_before)

    def test_corrupt_policy_blocks_new_entries_but_preserves_risk_exit(self):
        now = datetime(2026, 10, 20, 12, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            opened = self._runtime(root, now)
            policy_path = root / "eurusd_production_policy_state.json"
            ledger_path = root / "eurusd_production_decisions.json"
            ledger_before = ledger_path.read_bytes()
            policy_path.write_text("{invalid", encoding="utf-8")
            closed = self._runtime(root, now + timedelta(minutes=2), stop=True)
            self.assertEqual(closed.metadata["production_policy_loop"]["status"], "DEGRADED_EXIT_ONLY")
            self.assertEqual(closed.metadata["last_trade"]["decision_manifest"], opened.metadata["position"]["decision_manifest"])
            self.assertEqual(policy_path.read_text(encoding="utf-8"), "{invalid")
            self.assertEqual(ledger_path.read_bytes(), ledger_before)
            with self.assertRaises(ValueError):
                self._runtime(root, now + timedelta(minutes=3))

    def test_missing_epoch_never_bootstraps_over_existing_open_admission(self):
        now = datetime(2026, 10, 20, 12, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            opened = self._runtime(root, now)
            policy_path = root / "eurusd_production_policy_state.json"
            ledger_path = root / "eurusd_production_decisions.json"
            policy_path.unlink()
            ledger_path.unlink()
            closed = self._runtime(root, now + timedelta(minutes=2), stop=True)
            self.assertEqual(closed.metadata["production_policy_loop"]["status"], "DEGRADED_EXIT_ONLY")
            self.assertEqual(closed.metadata["last_trade"]["decision_manifest"], opened.metadata["position"]["decision_manifest"])
            self.assertFalse(policy_path.exists())
            self.assertFalse(ledger_path.exists())
            with self.assertRaises(ValueError):
                self._runtime(root, now + timedelta(minutes=3))

    def test_declared_actual_policy_outcome_requires_canonical_trade(self):
        now = datetime(2026, 10, 20, 12, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._runtime(root, now)
            self._runtime(root, now + timedelta(minutes=2), stop=True)
            history_path = root / "eurusd_daily_history.json"
            history = json.loads(history_path.read_text(encoding="utf-8"))
            history["trades"] = []
            history_path.write_text(json.dumps(history), encoding="utf-8")
            before = (root / "eurusd_production_policy_state.json").read_bytes()
            with self.assertRaisesRegex(ValueError, "absent from actual history"):
                self._runtime(root, now + timedelta(minutes=3))
            self.assertEqual((root / "eurusd_production_policy_state.json").read_bytes(), before)

    def test_measurement_recomputes_net_result_from_frozen_geometry(self):
        now = datetime(2026, 10, 20, 12, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._runtime(root, now)
            closed = self._runtime(root, now + timedelta(minutes=2), stop=True)
            trade = copy.deepcopy(closed.metadata["last_trade"])
            measured = records.validate_trade_binding(trade)
            trade["r_multiple"] = 1000
            report = records.production_measurements({"trades": [trade, {"trade_id": "legacy"}]})
            self.assertEqual(report["by_policy_version"][trade["decision_manifest"]["policy_version_id"]]["sum_net_r"], measured)
            self.assertEqual(report["legacy_without_manifest"], 1)
            trade["stop"] -= .001
            with self.assertRaises(ValueError):
                records.production_measurements({"trades": [trade]})

    def test_invalid_existing_history_is_never_replaced_with_empty_history(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "history.json"
            for invalid in ("{broken", "[]", '{"schema_version":"wrong","trades":[]}'):
                path.write_text(invalid, encoding="utf-8")
                with self.assertRaises(ValueError):
                    lifecycle.load_history(path)
                self.assertEqual(path.read_text(encoding="utf-8"), invalid)

    def test_reference_exit_proved_before_a_later_feed_outage_is_settled(self):
        now = datetime(2026, 10, 20, 12, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            opened = self._runtime(root, now)
            position = opened.metadata["position"]
            rows = Client(now + timedelta(minutes=2), stop=True).bars("EURUSD=X", "5d", "1m")
            evaluator = v19._counterfactual_evaluator(rows, {}, now + timedelta(minutes=10))
            result = evaluator(position, {})
            self.assertTrue(result["coverage_complete"])
            self.assertIsNotNone(result["closed_trade"])
            # Without a proven close the same outage stays missing evidence.
            rows = Client(now + timedelta(minutes=2)).bars("EURUSD=X", "5d", "1m")
            result = v19._counterfactual_evaluator(rows, {}, now + timedelta(minutes=10))(position, {})
            self.assertFalse(result["coverage_complete"])

    def test_full_monitor_promotes_filters_and_rolls_back_before_next_admission(self):
        from tests.test_daily_eurusd_production_policy_integration import ProductionPolicyRuntimeIntegrationTests
        policy = v19.production_policy
        start = datetime(2026, 7, 1, 12, tzinfo=UTC)
        state, ledger, history = policy.default_state(start), records.empty_ledger(), lifecycle.empty_history()
        helper = ProductionPolicyRuntimeIntegrationTests()
        # Seed genuine prospective admissions through the production hooks,
        # not declared policy metrics. External verified quote acquisition is
        # the only substituted dependency; lifecycle and proof binding are real.
        for index in range(policy.PRIMARY_N):
            at = start + timedelta(days=2 * index + 1)
            candidate = helper.candidate(at)
            context = {"policy_state": state, "decision_at": at}
            with patch.object(v19, "_cycle_context", context), patch.object(v19, "_epe_prepare", return_value=candidate):
                prepared = v19.prepare_entry_candidate(candidate, [], at)
                position = v19._create_position_v19(prepared.to_dict())
            bars = [Bar(at + timedelta(minutes=1), 1.09, high=1.1000, low=1.0890)]
            trade = v19.v18._evaluate_position(position, bars, bars[-1].timestamp)
            self.assertIsNotNone(trade)
            history = lifecycle.append_trade(history, trade)
            state = policy.bind_actual_history(context["policy_state"], history, at + timedelta(minutes=2))
            ledger = records.append_manifest(ledger, prepared.metadata["decision_manifest"])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            policy.save_state(root / "eurusd_production_policy_state.json", state)
            records.save_ledger(root / "eurusd_production_decisions.json", ledger)
            lifecycle.save_history(root / "eurusd_daily_history.json", history, at + timedelta(minutes=2))
            now = at + timedelta(days=3)
            filtered = self._runtime(root, now, probability=.54375)
            self.assertEqual(filtered.direction, "FLAT")
            self.assertEqual(filtered.metadata["final_decision"]["direction"], "LONG", filtered.metadata["final_decision"])
            self.assertEqual(filtered.metadata["production_policy"]["minimum_score_distance"], 4)
            # The reference path starts after a missing hour. Three unique
            # failed observations followed by the next cycle must roll back;
            # missing outcomes must never be filled with artificial zeroes.
            for minute in (180, 181, 182):
                held = self._runtime(root, now + timedelta(minutes=minute), probability=.54375)
                self.assertEqual(held.direction, "FLAT")
            resumed = self._runtime(root, now + timedelta(minutes=183), probability=.54375)
            self.assertEqual(resumed.status, "OPEN")
            self.assertEqual(resumed.metadata["production_policy"]["minimum_score_distance"], 3)
            self.assertEqual(resumed.metadata["production_policy_loop"]["latest_rollback"]["reason"], "DATA_QUALITY_FAILURE")
            state = policy.load_state(root / "eurusd_production_policy_state.json")
            policy.validate_history_bindings(state, lifecycle.load_history(root / "eurusd_daily_history.json"))
            records.validate_ledger(records.load_ledger(root / "eurusd_production_decisions.json"))


if __name__ == "__main__":
    unittest.main()
