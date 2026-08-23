import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from belief_aris_shadow import ARISBeliefShadow, CONTRACT_VERSION, MODE, build_shadow_report


class ARISBeliefShadowTests(unittest.TestCase):
    def payload(self):
        return {
            "definitions": [{
                "belief_id": "b1", "claim": "Growth accelerates", "prior_probability": 0.5,
                "half_life_hours": 72.0, "entity": "US", "domain": "macro",
            }],
            "beliefs": [{
                "belief_id": "b1", "claim": "Growth accelerates", "probability": 0.68,
                "confidence": 0.72, "previous_probability": 0.62,
                "representative_evidence_ids": ["e1", "e2", "e3"],
                "last_updated": "2026-08-24T00:00:00Z",
            }],
            "evidence": [
                {
                    "evidence_id": "e1", "belief_id": "b1", "observed_at": "2026-08-23T23:00:00Z",
                    "direction": 1, "strength": 0.9, "reliability": 0.95,
                    "source_type": "primary", "source": "official", "source_ref": "src://1",
                },
                {
                    "evidence_id": "e2", "belief_id": "b1", "observed_at": "2026-08-23T20:00:00Z",
                    "direction": 1, "strength": 0.7, "reliability": 0.80,
                    "source_type": "secondary", "source": "market", "source_ref": "src://2",
                },
                {
                    "evidence_id": "e3", "belief_id": "b1", "observed_at": "2026-08-20T00:00:00Z",
                    "direction": -1, "strength": 0.6, "reliability": 0.55,
                    "source_type": "derived", "source": "derived", "source_ref": "src://3",
                },
            ],
        }

    def test_competing_representations_do_not_replace_authority(self):
        state = ARISBeliefShadow(self.payload()).evaluate_belief("b1")
        self.assertEqual(state.authoritative_probability, 0.68)
        self.assertTrue(state.authority_preserved)
        self.assertFalse(state.decision_influence)
        self.assertFalse(state.belief_core_writeback_enabled)
        self.assertGreaterEqual(len(state.representations), 4)

    def test_model_plus_residual_is_explicit(self):
        state = ARISBeliefShadow(self.payload()).evaluate_belief("b1")
        selected = next(x for x in state.representations if x.name == state.selected_representation)
        self.assertEqual(tuple(state.residual_evidence_ids), tuple(selected.residual_evidence_ids))
        self.assertGreaterEqual(selected.information_retention, 0.0)
        self.assertLessEqual(selected.information_retention, 1.0)

    def test_roi_pruning_can_remove_empty_or_low_value_representation(self):
        payload = self.payload()
        payload["evidence"][0]["source_type"] = "derived"
        payload["evidence"][1]["source_type"] = "derived"
        state = ARISBeliefShadow(payload).evaluate_belief("b1", min_roi=0.95)
        rows = {x.name: x for x in state.representations}
        self.assertTrue(rows["primary_preferred"].pruned)
        self.assertFalse(rows["full_representatives"].pruned)

    def test_report_is_shadow_only(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "state.json").write_text(json.dumps(self.payload()), encoding="utf-8")
            report = build_shadow_report(root)
            self.assertEqual(report["contract_version"], CONTRACT_VERSION)
            self.assertEqual(report["mode"], MODE)
            self.assertFalse(report["authority"]["decision_influence"])
            self.assertFalse(report["authority"]["belief_core_writeback_enabled"])
            self.assertFalse(report["authority"]["automatic_tuning_enabled"])
            self.assertTrue((root / "aris_belief_shadow.json").exists())


if __name__ == "__main__":
    unittest.main()
