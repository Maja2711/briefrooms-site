from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path

from scripts.gse_v2_public_lab_projection import build_projection, improvement_pct


class GSEV2PublicLabProjectionTests(unittest.TestCase):
    def test_improvement_pct(self):
        self.assertAlmostEqual(improvement_pct(0.25, 0.20), 20.0)
        self.assertIsNone(improvement_pct(None, 0.20))

    def test_projection_is_safe_and_marks_best_horizon(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "gse_v2_learning_state.json").write_text(json.dumps({
                "mode":"shadow","readiness":{"status":"shadow_learning","reasons":["prospective_paired_n_below_30"]},"prospective":{"paired_n":4}
            }))
            (root / "gse_v2_historical_walkforward.json").write_text(json.dumps({
                "evaluable_predictions":60,
                "overall":{"regime_aware":{"n":60,"brier":0.24,"log_loss":0.68},"unweighted_analogue":{"n":60,"brier":0.26,"log_loss":0.72}},
                "by_horizon":{
                    "24":{"regime_aware":{"n":20,"brier":0.27},"unweighted_analogue":{"n":20,"brier":0.28}},
                    "168":{"regime_aware":{"n":20,"brier":0.25},"unweighted_analogue":{"n":20,"brier":0.27}},
                    "720":{"regime_aware":{"n":20,"brier":0.21},"unweighted_analogue":{"n":20,"brier":0.25}}
                },
                "by_scenario":{}
            }))
            (root / "gse_v2_policy_proposal.json").write_text(json.dumps({"status":"eligible_for_human_shadow_review","candidate":{"similarity_temperature":0.5}}))
            (root / "gse_v2_regime_calibration.json").write_text(json.dumps({"overall":{"paired_n":4,"mean_brier_v1":0.31,"mean_brier_v2_regime":0.30}}))
            (root / "gse_v2_enriched_library.json").write_text(json.dumps({"coverage":{"response_rows":90}}))
            (root / "gse_historical_discovery_state.json").write_text(json.dumps({"effective_verified_cluster_n":12,"target_verified_clusters":100,"target_met":False}))
            (root / "gse_v2_learning_ledger.jsonl").write_text(json.dumps({"recorded_at":"2026-01-01T00:00:00Z","candidates_added":4,"verifications_added":2,"record_hash":"abc"})+"\n")
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps({"events":[{"event_id":"e1","event_cluster_id":"c1","event_at":"2024-01-01T00:00:00Z","label":"Event","scenario_types":["sanctions_escalation"],"source":"Primary","source_ref":"https://example.com","source_reliability":0.9}]}))
            out = build_projection(root, catalog)
            self.assertEqual(out["engine"]["full_name"], "Geopolitical Scenario Engine")
            self.assertEqual(out["summary"]["verified_clusters"], 12)
            self.assertEqual(out["best_horizon"]["label"], "30d")
            self.assertFalse(out["engine"]["decision_influence"])
            self.assertFalse(out["public_boundary"]["raw_evidence_exposed"])
            self.assertNotIn("evidence", out)


if __name__ == "__main__":
    unittest.main()
