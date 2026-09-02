import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from belief_aris_shadow import (
    ARISBeliefShadow,
    CONTRACT_VERSION,
    MODE,
    REPRESENTATION_NAMESPACE,
    SHADOW_AUTHORITY_POLICY,
    build_shadow_report,
    validate_shadow_authority,
    validate_shadow_report,
)


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
        self.assertFalse(state.production_decision_influence)
        self.assertFalse(state.belief_core_writeback_enabled)
        self.assertFalse(state.source_writeback_enabled)
        self.assertFalse(state.consumer_contract_export_enabled)
        self.assertFalse(state.trade_execution_enabled)
        self.assertFalse(state.automatic_promotion_enabled)
        self.assertFalse(state.automatic_tuning_enabled)
        self.assertEqual(state.representation_namespace, REPRESENTATION_NAMESPACE)
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

    def test_report_is_shadow_only_and_physically_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "belief-core"
            output_dir = root / "aris-shadow"
            state_dir.mkdir()
            state_path = state_dir / "state.json"
            original = json.dumps(self.payload(), sort_keys=True) + "\n"
            state_path.write_text(original, encoding="utf-8")

            report = build_shadow_report(state_dir, output_dir)

            self.assertEqual(report["contract_version"], CONTRACT_VERSION)
            self.assertEqual(report["mode"], MODE)
            self.assertEqual(report["representation_namespace"], REPRESENTATION_NAMESPACE)
            self.assertEqual(report["authority"], SHADOW_AUTHORITY_POLICY)
            self.assertEqual(state_path.read_text(encoding="utf-8"), original)
            self.assertFalse((state_dir / "aris_belief_shadow.json").exists())
            self.assertTrue((output_dir / "aris_belief_shadow.json").exists())
            validate_shadow_report(report)

    def test_same_directory_output_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            (state_dir / "state.json").write_text(json.dumps(self.payload()), encoding="utf-8")
            with self.assertRaises(ValueError):
                build_shadow_report(state_dir, state_dir)

    def test_authority_policy_fails_closed_when_any_guard_is_relaxed(self):
        for key, expected in SHADOW_AUTHORITY_POLICY.items():
            authority = dict(SHADOW_AUTHORITY_POLICY)
            authority[key] = not expected
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    validate_shadow_authority(authority)

    def test_report_validation_rejects_consumer_or_production_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "belief-core"
            output_dir = root / "aris-shadow"
            state_dir.mkdir()
            (state_dir / "state.json").write_text(json.dumps(self.payload()), encoding="utf-8")
            report = build_shadow_report(state_dir, output_dir)

            report["authority"]["consumer_contract_export_enabled"] = True
            with self.assertRaises(ValueError):
                validate_shadow_report(report)

            report["authority"] = dict(SHADOW_AUTHORITY_POLICY)
            report["beliefs"]["b1"]["production_decision_influence"] = True
            with self.assertRaises(ValueError):
                validate_shadow_report(report)


if __name__ == "__main__":
    unittest.main()
