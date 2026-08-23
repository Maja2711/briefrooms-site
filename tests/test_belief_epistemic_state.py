import json
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from belief_epistemic_state import (
    AggregateAuthority,
    DrilldownPolicy,
    EpistemicStateBuilder,
    build_runtime_snapshot,
    persist_runtime_snapshot,
)


class EpistemicStateTests(unittest.TestCase):
    def fixture(self):
        state = {
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
                    "support_evidence_ids": ["e1"],
                    "opposing_evidence_ids": ["e2"],
                    "representative_evidence_ids": ["e1", "e2"],
                    "independent_clusters": 2,
                    "source_diversity": 2,
                    "contradiction_score": 0.31,
                    "cluster_conflict_score": 0.0,
                    "freshness_score": 0.91,
                    "last_updated": "2026-08-23T20:00:00Z",
                    "entity": "US",
                    "domain": "macro",
                    "alternative_group": None,
                    "tags": [],
                    "horizon_hours": 72,
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
                    "source_type": "primary",
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
                    "source_type": "secondary",
                    "metadata": {"observation_id": "o2"},
                },
            ],
        }
        observations = [
            {"observation_id": "o1", "metric": "pmi", "entity": "US", "value": 54.2, "unit": "index", "observed_at": "2026-08-23T19:00:00Z", "source": "official", "source_ref": "source://one"},
            {"observation_id": "o2", "metric": "credit", "entity": "US", "value": -0.2, "unit": "score", "observed_at": "2026-08-23T18:00:00Z", "source": "secondary", "source_ref": "source://two"},
        ]
        return state, observations

    def test_authority_forbids_llm_override(self):
        authority = AggregateAuthority()
        self.assertTrue(authority.llm_may_inspect)
        self.assertTrue(authority.llm_may_challenge)
        self.assertFalse(authority.llm_may_override_probability)
        self.assertFalse(authority.llm_may_ignore_aggregate)
        self.assertFalse(authority.belief_core_writeback_enabled)

    def test_state_is_reversible_to_source_with_bounded_depth(self):
        state, observations = self.fixture()
        builder = EpistemicStateBuilder(state, observations)
        epistemic = builder.build_for_belief("macro.growth.up")
        self.assertEqual(epistemic.member_belief_ids, ("macro.growth.up",))
        self.assertAlmostEqual(epistemic.delta_probability, 0.14)
        self.assertTrue(epistemic.contributions)
        self.assertEqual(epistemic.provenance_root["path"], "state->belief->evidence->observation->source")

        drill = builder.drilldown(epistemic, depth=4)
        self.assertEqual(drill["beliefs"][0]["belief_id"], "macro.growth.up")
        self.assertTrue(any(row["evidence_id"] == "e1" for row in drill["evidence"]))
        self.assertTrue(any(row["observation_id"] == "o1" for row in drill["observations"]))
        self.assertTrue(any(row["source_ref"] == "source://one" for row in drill["sources"]))
        self.assertEqual(drill["stop_reason"], "bounded_drilldown_max_depth_reached")

    def test_drilldown_cannot_exceed_hard_limit(self):
        state, observations = self.fixture()
        builder = EpistemicStateBuilder(state, observations)
        epistemic = builder.build_for_belief("macro.growth.up")
        with self.assertRaises(ValueError):
            builder.drilldown(epistemic, depth=epistemic.drilldown_policy.max_depth + 1)

    def test_triggers_are_deterministic(self):
        state, observations = self.fixture()
        state["beliefs"][0]["confidence"] = 0.3
        builder = EpistemicStateBuilder(state, observations)
        epistemic = builder.build_for_belief("macro.growth.up", policy=DrilldownPolicy())
        self.assertTrue(epistemic.drilldown_required)
        self.assertIn("low_confidence", epistemic.drilldown_reasons)

    def test_leave_one_out_contributions_preserve_sign(self):
        state, observations = self.fixture()
        builder = EpistemicStateBuilder(state, observations)
        epistemic = builder.build_for_belief("macro.growth.up")
        by_id = {row.contributor_id: row for row in epistemic.contributions}
        self.assertGreater(by_id["e1"].signed_probability_delta, 0)
        self.assertLess(by_id["e2"].signed_probability_delta, 0)

    def test_runtime_persistence_tracks_delta_without_writeback(self):
        state, observations = self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "state.json").write_text(json.dumps(state), encoding="utf-8")
            with (root / "observations.jsonl").open("w", encoding="utf-8") as handle:
                for row in observations:
                    handle.write(json.dumps(row) + "\n")
            first = build_runtime_snapshot(root)
            persist_runtime_snapshot(root, first)
            state["beliefs"][0]["probability"] = 0.72
            state["beliefs"][0]["previous_probability"] = 0.68
            (root / "state.json").write_text(json.dumps(state), encoding="utf-8")
            second = build_runtime_snapshot(root)
            self.assertAlmostEqual(second["states"]["macro.growth.up"]["delta_probability"], 0.04)
            self.assertFalse(second["controls"]["belief_core_writeback_enabled"])
            self.assertFalse(second["controls"]["llm_override_enabled"])


if __name__ == "__main__":
    unittest.main()
