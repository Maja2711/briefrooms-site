from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from canonical_epistemic_state import (
    CONTRACT_VERSION,
    CanonicalEpistemicStateError,
    verify_state,
)
from canonical_epistemic_state_builder import (
    OUTPUT_FILENAME,
    build_canonical_epistemic_state,
    persist_canonical_state,
)


class CanonicalEpistemicStateTests(unittest.TestCase):
    def fixture(self):
        core = {
            "schema_version": 2,
            "definitions": [
                {
                    "belief_id": "macro.growth.up",
                    "claim": "Growth accelerates",
                    "prior_probability": 0.5,
                    "half_life_hours": 72,
                    "entity": "US",
                    "domain": "macro",
                }
            ],
            "beliefs": [
                {
                    "belief_id": "macro.growth.up",
                    "claim": "Growth accelerates",
                    "probability": 0.68,
                    "confidence": 0.74,
                    "previous_probability": 0.54,
                    "representative_evidence_ids": ["e1", "e2"],
                    "contradiction_score": 0.31,
                    "freshness_score": 0.91,
                    "last_updated": "2026-08-23T20:00:00Z",
                    "entity": "US",
                    "domain": "macro",
                    "audit_status": "clean",
                }
            ],
            "evidence": [
                {
                    "evidence_id": "e1",
                    "belief_id": "macro.growth.up",
                    "source": "official",
                    "source_ref": "source://one",
                    "observed_at": "2026-08-23T19:00:00Z",
                    "direction": 1,
                    "strength": 0.8,
                    "reliability": 0.9,
                    "independence_cluster": "c1",
                    "evidence_type": "macro_release",
                    "metadata": {"observation_id": "o1"},
                },
                {
                    "evidence_id": "e2",
                    "belief_id": "macro.growth.up",
                    "source": "secondary",
                    "source_ref": "source://two",
                    "observed_at": "2026-08-23T18:00:00Z",
                    "direction": -1,
                    "strength": 0.25,
                    "reliability": 0.7,
                    "independence_cluster": "c2",
                    "evidence_type": "credit",
                    "metadata": {"observation_id": "o2"},
                },
            ],
        }
        observations = [
            {
                "observation_id": "o1",
                "metric": "pmi",
                "entity": "US",
                "value": 54.2,
                "unit": "index",
                "observed_at": "2026-08-23T19:00:00Z",
                "source": "official",
                "source_ref": "source://one",
            },
            {
                "observation_id": "o2",
                "metric": "credit",
                "entity": "US",
                "value": -0.2,
                "unit": "score",
                "observed_at": "2026-08-23T18:00:00Z",
                "source": "secondary",
                "source_ref": "source://two",
            },
        ]
        projection = {
            "contract_version": "belief-epistemic-state-v1",
            "created_at": "2026-08-23T20:01:00Z",
            "authority": {
                "policy": "aggregate-authority-v1",
                "llm_may_override_probability": False,
                "llm_may_ignore_aggregate": False,
            },
            "drilldown_policy": {"max_depth": 4},
            "states": {
                "macro.growth.up": {
                    "state_id": "estate-upstream-1",
                    "topic": "Growth accelerates",
                    "entity": "US",
                    "domain": "macro",
                    "probability": 0.68,
                    "confidence": 0.74,
                    "previous_probability": 0.54,
                    "delta_probability": 0.14,
                    "contradiction": 0.31,
                    "freshness": 0.91,
                    "audit_status": "clean",
                    "member_belief_ids": ["macro.growth.up"],
                    "contributions": [
                        {
                            "contributor_type": "evidence",
                            "contributor_id": "e1",
                            "signed_probability_delta": 0.10,
                            "direction": 1,
                            "source_ref": "source://one",
                            "observation_id": "o1",
                        },
                        {
                            "contributor_type": "evidence",
                            "contributor_id": "e2",
                            "signed_probability_delta": -0.03,
                            "direction": -1,
                            "source_ref": "source://two",
                            "observation_id": "o2",
                        },
                    ],
                    "dominant_support_evidence_ids": ["e1"],
                    "dominant_opposition_evidence_ids": ["e2"],
                    "provenance_root": {
                        "belief_ids": ["macro.growth.up"],
                        "representative_evidence_ids": ["e1", "e2"],
                        "path": "state->belief->evidence->observation->source",
                    },
                    "drilldown_required": False,
                    "drilldown_reasons": [],
                }
            },
            "controls": {
                "decision_writeback_enabled": False,
                "belief_core_writeback_enabled": False,
                "llm_override_enabled": False,
                "automatic_tuning_enabled": False,
            },
        }
        return projection, core, observations

    def build(self):
        projection, core, observations = self.fixture()
        return build_canonical_epistemic_state(
            source_projection=projection,
            belief_core_state=core,
            observations=observations,
        )

    def test_contract_is_canonical_deterministic_and_read_only(self):
        state = self.build()
        verify_state(state)
        self.assertEqual(state.contract_version, CONTRACT_VERSION)
        self.assertTrue(state.state_id.startswith("eps-"))
        self.assertFalse(state.authority.decision_authority)
        self.assertFalse(state.authority.risk_limit_authority)
        self.assertFalse(state.authority.trade_execution_authority)
        self.assertFalse(state.authority.llm_override_enabled)
        self.assertEqual(
            state.provenance_path,
            "state->belief->evidence->observation->source",
        )

    def test_reordered_source_collections_keep_same_identity(self):
        projection, core, observations = self.fixture()
        first = build_canonical_epistemic_state(
            source_projection=projection,
            belief_core_state=core,
            observations=observations,
        )
        core["evidence"] = list(reversed(core["evidence"]))
        observations = list(reversed(observations))
        second = build_canonical_epistemic_state(
            source_projection=projection,
            belief_core_state=core,
            observations=observations,
        )
        self.assertEqual(first.state_id, second.state_id)
        self.assertEqual(first.state_hash, second.state_hash)

    def test_semantic_change_changes_state_hash_without_recomputing_belief(self):
        projection, core, observations = self.fixture()
        first = build_canonical_epistemic_state(
            source_projection=projection,
            belief_core_state=core,
            observations=observations,
        )
        projection["states"]["macro.growth.up"]["probability"] = 0.69
        second = build_canonical_epistemic_state(
            source_projection=projection,
            belief_core_state=core,
            observations=observations,
        )
        self.assertNotEqual(first.state_hash, second.state_hash)
        self.assertEqual(second.beliefs[0].probability, 0.69)

    def test_future_evidence_is_rejected(self):
        projection, core, observations = self.fixture()
        core["evidence"][0]["observed_at"] = "2026-08-23T20:00:30Z"
        with self.assertRaisesRegex(CanonicalEpistemicStateError, "after belief as_of"):
            build_canonical_epistemic_state(
                source_projection=projection,
                belief_core_state=core,
                observations=observations,
            )

    def test_naive_timestamp_is_rejected(self):
        projection, core, observations = self.fixture()
        projection["created_at"] = "2026-08-23T20:01:00"
        with self.assertRaisesRegex(CanonicalEpistemicStateError, "explicit timezone"):
            build_canonical_epistemic_state(
                source_projection=projection,
                belief_core_state=core,
                observations=observations,
            )

    def test_out_of_range_confidence_is_rejected(self):
        projection, core, observations = self.fixture()
        projection["states"]["macro.growth.up"]["confidence"] = 1.2
        with self.assertRaisesRegex(CanonicalEpistemicStateError, "confidence must be in"):
            build_canonical_epistemic_state(
                source_projection=projection,
                belief_core_state=core,
                observations=observations,
            )

    def test_conflicting_duplicate_evidence_id_is_rejected(self):
        projection, core, observations = self.fixture()
        duplicate = copy.deepcopy(core["evidence"][0])
        duplicate["strength"] = 0.1
        core["evidence"].append(duplicate)
        with self.assertRaisesRegex(
            CanonicalEpistemicStateError,
            "conflicting duplicate evidence_id",
        ):
            build_canonical_epistemic_state(
                source_projection=projection,
                belief_core_state=core,
                observations=observations,
            )

    def test_missing_observation_lineage_is_rejected(self):
        projection, core, observations = self.fixture()
        observations = [
            row for row in observations if row["observation_id"] != "o1"
        ]
        with self.assertRaisesRegex(
            CanonicalEpistemicStateError,
            "missing observation referenced",
        ):
            build_canonical_epistemic_state(
                source_projection=projection,
                belief_core_state=core,
                observations=observations,
            )

    def test_tampering_is_detected(self):
        state = self.build()
        tampered = copy.deepcopy(state)
        object.__setattr__(tampered.beliefs[0], "confidence", 0.1)
        with self.assertRaisesRegex(CanonicalEpistemicStateError, "belief hash mismatch"):
            verify_state(tampered)

    def test_persistence_writes_only_canonical_projection(self):
        state = self.build()
        with tempfile.TemporaryDirectory() as tmp:
            output = persist_canonical_state(Path(tmp), state)
            self.assertEqual(output.name, OUTPUT_FILENAME)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["state_id"], state.state_id)
            self.assertFalse(payload["authority"]["decision_authority"])


if __name__ == "__main__":
    unittest.main()
