from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import stock_trading_v2_contracts as contracts
from scripts import stock_trading_v2_experience_store as store


def payload_with_freeze() -> dict:
    payload = {
        "schema_version": "gpw-daily-pick-v1",
        "policy_version": "gpw-test-v1",
        "date": "2026-09-16",
        "generated_at": "2026-09-16T12:00:00+02:00",
        "decision": "TRANSAKCJA",
        "reason": "test selection",
        "selection": {
            "symbol": "AAA.WA",
            "ticker": "AAA",
            "name": "AAA SA",
            "sector": "tech",
            "score": 78.0,
            "quant_pre_score": 81.0,
            "reference_price": 100.0,
            "entry_zone": [99.5, 100.5],
            "stop": 96.0,
            "target": 108.0,
            "risk_percent": 0.04,
            "reward_risk": 2.0,
            "scores": {"relative_momentum": 84.0},
            "valid_until": "2026-09-18",
            "time_stop": "legacy two-session rule",
            "expected_value_model": {
                "engine": "gpw-empirical-ev-v1",
                "horizon_sessions": 2,
                "role": "empirical_target_and_bounded_reranking_overlay",
                "expected_net_r": 0.12,
                "conservative_ev_r": 0.03,
                "standard_error_r": 0.1,
            },
        },
        "data_quality": {"expected_session": "2026-09-16"},
    }
    source_sha = store._source_payload_sha(payload)
    rejected = {
        "candidate_id": "gpw:2026-09-16:BBB.WA:LONG",
        "market": "gpw",
        "symbol": "BBB.WA",
        "name": "BBB SA",
        "sector": "banki",
        "action": "LONG",
        "selected": False,
        "decision_at": payload["generated_at"],
        "expected_session": "2026-09-16",
        "first_blocking_gate": {
            "name": "minimum_composite_score",
            "passed": False,
            "hard": False,
            "stage": "scoring",
            "reason": "score:69",
            "observed_value": 69.0,
            "threshold": 72.0,
        },
        "decision_path": {"gates": [], "producer_decision": "TRANSAKCJA"},
        "score_state": {"rank": 2, "quant_pre_score": 76.0, "composite_score": 69.0},
        "market_state": {"reference_price": 50.0},
        "risk_plan": {
            "reference_price": 50.0,
            "entry_zone": [49.8, 50.2],
            "stop": 48.0,
            "target": 54.0,
            "reward_risk": 2.0,
            "risk_percent": 0.04,
        },
        "settlement_eligibility": {"eligible": True, "mode": "risk_plan"},
        "governance": {"decision_influence": False},
    }
    rejected["state_sha256"] = contracts.payload_sha256(rejected)
    freeze = {
        "schema_version": store.FREEZE_SCHEMA,
        "market": "gpw",
        "date": payload["date"],
        "decision_at": payload["generated_at"],
        "frozen_at": "2026-09-16T10:01:00Z",
        "source_payload_sha256": source_sha,
        "status": "frozen",
        "selected_symbol": "AAA.WA",
        "candidate_count": 1,
        "economically_evaluable_count": 1,
        "candidates": [rejected],
        "contract": {"prospective_only": True, "decision_influence": False},
    }
    freeze["freeze_sha256"] = contracts.payload_sha256(freeze)
    payload[store.FREEZE_FIELD] = freeze
    return payload


class StockTradingV2ExperienceStoreTests(unittest.TestCase):
    def test_selected_and_rejected_are_normalised_into_same_contract(self):
        payload = payload_with_freeze()
        events = store.events_from_legacy_payload(
            payload,
            market="GPW",
            recorded_at="2026-09-16T10:02:00Z",
        )
        self.assertEqual(len(events), 2)
        selected = next(event for event in events if event["selected"])
        rejected = next(event for event in events if not event["selected"])
        self.assertEqual(selected["symbol"], "AAA.WA")
        self.assertEqual(selected["decision"], "SELECTED")
        self.assertEqual(rejected["symbol"], "BBB.WA")
        self.assertEqual(rejected["decision"], "REJECTED")
        self.assertEqual(
            rejected["candidate_state"]["first_blocking_gate"]["name"],
            "minimum_composite_score",
        )
        self.assertFalse(selected["governance"]["production_decision_influence"])
        self.assertFalse(rejected["governance"]["automatic_policy_writeback"])
        for event in events:
            contracts.validate_experience_event(event)

    def test_store_is_idempotent_for_same_prospective_decision(self):
        payload = payload_with_freeze()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = store.ingest_payload(
                payload,
                market="GPW",
                root=root,
                recorded_at="2026-09-16T10:02:00Z",
            )
            second = store.ingest_payload(
                payload,
                market="GPW",
                root=root,
                recorded_at="2026-09-16T11:02:00Z",
            )
            self.assertEqual(first["events_written"], 2)
            self.assertEqual(second["events_written"], 0)
            self.assertEqual(second["events_existing"], 2)
            verified = store.verify_store(root)
            self.assertTrue(verified["ok"])
            self.assertEqual(verified["event_count"], 2)
            self.assertEqual(verified["markets"], {"GPW": 2})

    def test_store_refuses_same_event_id_with_changed_semantics(self):
        payload = payload_with_freeze()
        events = store.events_from_legacy_payload(
            payload,
            market="GPW",
            recorded_at="2026-09-16T10:02:00Z",
        )
        selected = next(event for event in events if event["selected"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertTrue(store.persist_event(root, selected))
            changed = contracts.make_experience_event(
                market="GPW",
                symbol=selected["symbol"],
                decision_at=selected["decision_at"],
                session_date=selected["session_date"],
                selected=True,
                source_engine=selected["source"]["engine"],
                source_schema_version=selected["source"]["schema_version"],
                source_policy_version=selected["source"]["policy_version"],
                source_payload_sha256=selected["source"]["payload_sha256"],
                candidate_state={"changed_after_outcome": True},
                recorded_at="2026-09-16T11:00:00Z",
            )
            with self.assertRaises(store.ImmutableStoreConflict):
                store.persist_event(root, changed)

    def test_verify_detects_tampering(self):
        payload = payload_with_freeze()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store.ingest_payload(payload, market="GPW", root=root)
            path = next(root.rglob("*.json"))
            event = json.loads(path.read_text(encoding="utf-8"))
            event["candidate_state"]["risk_plan"] = {"stop": 1.0}
            path.write_text(json.dumps(event), encoding="utf-8")
            with self.assertRaises(contracts.ContractError):
                store.verify_store(root)

    def test_data_error_without_candidate_state_creates_no_fake_experience(self):
        payload = {
            "schema_version": "us-daily-stock-v1",
            "policy_version": "us-test-v1",
            "date": "2026-09-16",
            "generated_at": "2026-09-16T14:00:00-04:00",
            "decision": "DATA_ERROR",
            "selection": None,
            "data_quality": {},
        }
        self.assertEqual(store.events_from_legacy_payload(payload, market="US"), [])

    def test_selected_snapshot_preserves_legacy_horizon_as_data_not_policy(self):
        event = next(
            event
            for event in store.events_from_legacy_payload(payload_with_freeze(), market="GPW")
            if event["selected"]
        )
        plan = event["candidate_state"]["risk_plan"]
        self.assertEqual(plan["time_stop_legacy"], "legacy two-session rule")
        self.assertNotIn("time_stop", event["governance"])
        self.assertFalse(event["governance"]["production_decision_influence"])


if __name__ == "__main__":
    unittest.main()
