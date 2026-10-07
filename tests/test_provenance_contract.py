from __future__ import annotations

import copy
import unittest

from scripts import provenance_compat as compat
from scripts import provenance_contract as pc


NOW = "2026-10-07T20:00:00Z"


class ProvenanceContractTests(unittest.TestCase):
    def test_contract_hash_is_deterministic_and_attachment_is_non_economic(self):
        payload = {
            "decision": "SHORT",
            "score": -0.31,
            "execution_provenance": {"source": "legacy", "recorded_at": NOW},
        }
        original = copy.deepcopy(payload)
        envelope = pc.build_envelope(
            artifact_id="daily:test:1",
            artifact_type="decision",
            engine_id="daily_eurusd",
            engine_version="v1.9",
            created_at=NOW,
            source_payload=payload,
            evidence_ids=["e2", "e1", "e1"],
            belief_ids=["b1"],
            decision_id="daily:test:1",
            authority="decision",
            prospective=True,
        )
        self.assertEqual(envelope["evidence_ids"], ["e1", "e2"])
        self.assertEqual(envelope["payload_hash"], pc.payload_hash(payload))

        attached = pc.attach_provenance(payload, envelope)
        self.assertEqual(payload, original)
        self.assertEqual(pc.economic_payload(attached), original)
        self.assertEqual(attached["execution_provenance"], original["execution_provenance"])
        pc.verify_attached(attached)

    def test_hash_mismatch_fails_closed(self):
        payload = {"x": 1}
        envelope = pc.build_envelope(
            artifact_id="x:1",
            artifact_type="test",
            engine_id="test",
            created_at=NOW,
            source_payload=payload,
            authority="test",
        )
        with self.assertRaises(pc.ProvenanceContractError):
            pc.validate_envelope(envelope, source_payload={"x": 2})

    def test_all_compatibility_adapters_emit_same_v1_envelope_without_mutation(self):
        samples = {
            "belief_l3a": (
                {
                    "schema_version": "briefrooms-l3a-lineage-v1",
                    "attribution_id": "l3attr-1",
                    "intent_id": "intent-1",
                    "question_id": "question-1",
                    "belief_id": "belief.eurusd",
                    "evidence_ids": ["ev-2", "ev-1"],
                    "forecast_id": "forecast-1",
                },
                {"created_at": NOW},
            ),
            "wes": (
                {
                    "leg_id": "leg-1",
                    "instrument_id": "eurusd",
                    "entry_price": 1.10,
                    "exit_price": 1.11,
                    "position_leg_seal": {
                        "artifact_id": "position_leg:2026-W41:eurusd:leg-1",
                        "payload_hash": "legacy-seal-hash",
                    },
                    "decision_id": "wes-decision-1",
                },
                {"week_id": "2026-W41", "created_at": NOW, "engine_version": "5"},
            ),
            "daily": (
                {
                    "schema_version": "daily-eurusd-belief-decision-v1",
                    "decision_source": "NATIVE_BELIEF_FIRST",
                    "epistemic_source": "BELIEF_CORE_CF07",
                    "direction": "SHORT",
                    "used_beliefs": [
                        {
                            "belief_id": "belief.eurusd.trend",
                            "representative_evidence_ids": ["ev-daily-1"],
                        }
                    ],
                },
                {"created_at": NOW},
            ),
            "brace": (
                {
                    "schema_version": 1,
                    "report_version": "1.0",
                    "generated_at": NOW,
                    "activation_policy": {
                        "candidate_source": "data/portfolio10k/analysis.json:candidates"
                    },
                    "entities": {
                        "active": [{"entity_id": "aapl"}],
                        "dormant": [],
                    },
                    "materialized_belief_definitions": [
                        {"belief_id": "entity.aapl.earnings_quality"}
                    ],
                    "anti_hindsight": {"historical_backfill": False},
                },
                {},
            ),
            "stock_trading": (
                {
                    "action": "open",
                    "position_id": "pos-1",
                    "source_engine": "v2",
                    "opened_at": NOW,
                    "entry_decision_at": NOW,
                    "execution_provenance": {
                        "source": "stock_trading_v2_production_bridge",
                        "quote_observed_at": "2026-10-07T19:59:00Z",
                        "quote_received_at": "2026-10-07T19:59:01Z",
                        "recorded_at": NOW,
                    },
                },
                {},
            ),
            "shadow": (
                {
                    "id": "deepbook",
                    "name": "DeepBook Predict Shadow",
                    "last_run_id": 123,
                    "last_run_at": NOW,
                    "source": "data/investments/deepbook_predict_shadow.json",
                    "workflow": "deepbook-predict-shadow.yml",
                    "status": "RUNNING",
                },
                {},
            ),
        }

        expected_fields = set(pc.REQUIRED_FIELDS)
        for family, (payload, context) in samples.items():
            with self.subTest(family=family):
                original = copy.deepcopy(payload)
                envelope = compat.adapt(family, payload, **context)
                self.assertEqual(payload, original)
                self.assertEqual(envelope["schema_version"], pc.SCHEMA_VERSION)
                self.assertEqual(set(envelope), expected_fields)
                self.assertEqual(envelope["payload_hash"], pc.payload_hash(payload))
                self.assertEqual(
                    envelope["domain_provenance"]["compatibility_mode"],
                    "read_only_sidecar",
                )
                pc.validate_envelope(envelope, source_payload=payload)

    def test_wes_adapter_does_not_replace_or_recompute_existing_seal(self):
        payload = {
            "leg_id": "leg-7",
            "instrument_id": "btcusd",
            "entry_price": 100.0,
            "exit_price": 104.0,
            "position_leg_seal": {
                "artifact_id": "position_leg:2026-W41:btcusd:leg-7",
                "payload_hash": "existing-wes-seal",
                "manifest_record_hash": "existing-chain-record",
            },
        }
        before = copy.deepcopy(payload)
        envelope = compat.adapt(
            "wes",
            payload,
            week_id="2026-W41",
            created_at=NOW,
            engine_version="5",
        )
        self.assertEqual(payload, before)
        self.assertEqual(
            envelope["artifact_id"],
            "position_leg:2026-W41:btcusd:leg-7",
        )
        self.assertEqual(
            envelope["domain_provenance"]["existing_seal_payload_hash"],
            "existing-wes-seal",
        )
        self.assertFalse(envelope["domain_provenance"]["reseal_required"])

    def test_missing_point_in_time_timestamp_is_not_fabricated(self):
        with self.assertRaises(pc.ProvenanceContractError):
            compat.adapt("daily", {"direction": "FLAT"})


if __name__ == "__main__":
    unittest.main()
