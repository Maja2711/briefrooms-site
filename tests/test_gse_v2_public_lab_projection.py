from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path

from scripts.gse_v2_public_lab_projection import build_projection, event_threat_projection, improvement_pct


class GSEV2PublicLabProjectionTests(unittest.TestCase):
    def test_improvement_pct(self):
        self.assertAlmostEqual(improvement_pct(0.25, 0.20), 20.0)
        self.assertIsNone(improvement_pct(None, 0.20))

    def test_projection_is_safe_marks_best_horizon_and_exposes_truthful_activity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "gse_state.json").write_text(json.dumps({
                "mode":"shadow","last_run_at":"2026-01-04T12:17:00Z"
            }))
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
            ledger_rows = [
                {"recorded_at":"2026-01-03T09:00:00Z","candidates_added":1,"verifications_added":0,"record_hash":"old"},
                {"recorded_at":"2026-01-03T11:00:00Z","candidates_added":4,"verifications_added":2,"record_hash":"new"},
                {"recorded_at":"2026-01-03T10:00:00Z","candidates_added":2,"verifications_added":1,"record_hash":"mid"},
            ]
            (root / "gse_v2_learning_ledger.jsonl").write_text("\n".join(json.dumps(row) for row in ledger_rows)+"\n")
            (root / "gse_verifications.jsonl").write_text(json.dumps({"forecast_id":"f1","verified_at":"2026-01-04T10:00:00Z"})+"\n")
            (root / "gse_v2_regime_verifications.jsonl").write_text(json.dumps({"candidate_id":"c1","verified_at":"2026-01-04T11:00:00Z"})+"\n")
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps({"events":[{"event_id":"e1","event_cluster_id":"c1","event_at":"2024-01-01T00:00:00Z","label":"Event","scenario_types":["sanctions_escalation"],"source":"Primary","source_ref":"https://example.com","source_reliability":0.9}]}))
            out = build_projection(root, catalog)
            self.assertEqual(out["schema_version"], "gse-v2-public-lab-v3")
            self.assertEqual(out["engine"]["full_name"], "Geopolitical Scenario Engine")
            self.assertEqual(out["summary"]["verified_clusters"], 12)
            self.assertEqual(out["best_horizon"]["label"], "30d")
            self.assertEqual(out["activity"]["last_scan_at"], "2026-01-04T12:17:00Z")
            self.assertEqual(out["activity"]["last_learning_at"], "2026-01-03T11:00:00Z")
            self.assertEqual(out["activity"]["last_verification_at"], "2026-01-04T11:00:00Z")
            self.assertEqual(out["activity"]["last_base_verification_at"], "2026-01-04T10:00:00Z")
            self.assertEqual(out["activity"]["last_v2_verification_at"], "2026-01-04T11:00:00Z")
            self.assertEqual(out["learning_timeline"][0]["record_hash"], "new")
            self.assertEqual(out["learning_timeline"][1]["record_hash"], "mid")
            self.assertEqual(out["learning_timeline"][2]["record_hash"], "old")
            self.assertFalse(out["engine"]["decision_influence"])
            self.assertFalse(out["public_boundary"]["raw_evidence_exposed"])
            self.assertNotIn("evidence", out)


    def test_event_threat_projection_is_sanitized_and_exposes_brier(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "gse_event_threat_state.json").write_text(json.dumps({
                "schema_version": "gse-event-threat-v1",
                "mode": "shadow",
                "generated_at": "2026-09-29T12:17:00Z",
                "model_status": "prospective_uncalibrated_seed",
                "probability_semantics": "research only",
                "current_estimates": [{
                    "event_type": "russia_attack_poland",
                    "label": "Russia armed attack on Poland",
                    "target": "Poland",
                    "horizon_hours": 720,
                    "prior_probability": 0.012,
                    "predicted_probability": 0.02,
                    "confidence": 0.4,
                    "signal_score": 0.5,
                    "evidence_24h": 2,
                    "evidence_7d": 3,
                    "evidence_30d": 4,
                    "independent_sources_7d": 2,
                    "primary_sources_30d": 0,
                    "precursor_categories": ["force_posture"],
                    "supporting_evidence_ids": ["private-e1"],
                    "calibration_status": "uncalibrated_seed",
                }],
            }))
            (root / "gse_event_probability_calibration.json").write_text(json.dumps({
                "overall": {
                    "count": 12,
                    "positive_count": 1,
                    "status": "insufficient_sample",
                    "mean_brier": 0.03,
                    "mean_prior_brier": 0.031,
                    "delta_brier_vs_prior": -0.001,
                    "bias": 0.01,
                }
            }))
            out = event_threat_projection(root)
            self.assertEqual(out["model_status"], "prospective_uncalibrated_seed")
            self.assertEqual(out["calibration"]["count"], 12)
            self.assertEqual(out["estimates"][0]["target"], "Poland")
            self.assertEqual(out["estimates"][0]["delta_vs_prior"], 0.008)
            self.assertNotIn("supporting_evidence_ids", json.dumps(out))
            self.assertNotIn("private-e1", json.dumps(out))
            self.assertFalse(out["raw_evidence_exposed"])


    def test_featured_thesis_uses_latest_frozen_candidate_and_stays_sanitized(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = {
                "candidate_id": "private-candidate-id",
                "baseline_forecast_id": "private-forecast-id",
                "asset": "GOLD",
                "symbol": "GC=F",
                "forecast_at": "2026-01-04T12:00:00Z",
                "target_at": "2026-02-03T12:00:00Z",
                "horizon_hours": 720,
                "direction": 1,
                "baseline_v1_probability": 0.61,
                "v2_regime_candidate_probability": 0.66,
                "epistemic_confidence": 0.72,
                "effective_cluster_n": 11,
                "scenario_diagnostics": [
                    {"scenario_type": "middle_east_energy_escalation", "neighbours": [{"event_id": "private-event"}]}
                ],
            }
            (root / "gse_v2_regime_forecasts.jsonl").write_text(json.dumps(candidate) + "\n")
            config = root / "featured.json"
            config.write_text(json.dumps({
                "enabled": True,
                "thesis_id": "gold-up-30d",
                "question_pl": "Czy ryzyko geopolityczne wesprze złoto?",
                "question_en": "Will geopolitical risk support gold?",
                "asset": "GOLD",
                "horizon_hours": 720,
                "expected_direction": 1,
                "max_candidate_age_hours": 12,
            }))
            from scripts.gse_v2_public_lab_projection import featured_thesis_projection
            out = featured_thesis_projection(root, config, generated_at="2026-01-04T13:00:00Z")
            self.assertEqual(out["thesis_id"], "gold-up-30d")
            self.assertEqual(out["probability"], 0.66)
            self.assertEqual(out["baseline_v1_probability"], 0.61)
            self.assertEqual(out["freshness"], "fresh")
            self.assertEqual(out["scenario_types"], ["middle_east_energy_escalation"])
            self.assertTrue(out["research_only"])
            self.assertFalse(out["decision_influence"])
            self.assertNotIn("candidate_id", out)
            self.assertNotIn("baseline_forecast_id", out)
            self.assertNotIn("neighbours", json.dumps(out))



if __name__=="__main__":
    unittest.main()
