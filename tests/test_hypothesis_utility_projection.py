"""Integration: HUE -> sanitized public LAB -> read-only L3 learning proposals."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from decision_lab_learning import build as build_learning
from decision_lab_public_projection import build_payload, public_hypothesis_utility


class P1ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.utility = {
            "schema_version": "briefrooms-hypothesis-utility-v1",
            "generated_at": "2026-10-08T12:00:00Z",
            "summary": {"hypotheses": 1},
            "policy": {"min_predictive_events": 30},
            "hypotheses": {
                "spx.volatility.benign": {
                    "hypothesis_id": "spx.volatility.benign",
                    "hypothesis_version": "1",
                    "lifecycle_status": "REVIEW",
                    "recommendation": "REVIEW",
                    "reasons": ["negative_prequential_gain"],
                    "predictive_utility": {
                        "independent_events": 45,
                        "brier_gain_vs_prequential_base_rate": -.04,
                        "secret_forecast_lineage": ["must-not-leak"],
                    },
                    "research_utility": {
                        "research_attempts_observed": 5,
                        "unvalidated_settlement_records": 1,
                        "private_question_text": "must-not-leak",
                    },
                }
            },
        }

    def test_projection_has_safe_aggregates_without_private_lineage(self):
        public = public_hypothesis_utility(self.utility)
        row = public["hypotheses"]["spx.volatility.benign"]
        self.assertEqual(45, row["predictive_utility"]["independent_events"])
        self.assertNotIn("secret_forecast_lineage", row["predictive_utility"])
        self.assertNotIn("private_question_text", row["research_utility"])
        self.assertFalse(public["authority"]["production_writeback"])
        self.assertFalse(public["authority"]["automatic_retirement"])
        payload = build_payload(
            {"definitions": [], "forecasts": [], "verifications": []},
            {"belief_calibration": {}}, hypothesis_utility=self.utility)
        self.assertEqual("SHADOW_READ_ONLY", payload["hypothesis_utility"]["status"])
        self.assertEqual(1, payload["hypothesis_utility"]["summary"]["hypotheses"])

    def test_missing_report_explicitly_not_available(self):
        public = public_hypothesis_utility(None)
        self.assertEqual("NOT_AVAILABLE", public["status"])
        self.assertEqual({}, public["hypotheses"])

    def test_review_generates_governed_learning_proposal_only(self):
        learning = build_learning({"belief_calibration": {}}, self.utility)
        matching = [a for a in learning["actions"] if a.get("source") == "HYPOTHESIS_UTILITY_REPORT.json"]
        self.assertEqual(1, len(matching))
        self.assertEqual("hypothesis_utility_review", matching[0]["kind"])
        self.assertEqual(45, matching[0]["independent_event_n"])
        self.assertFalse(learning["automatic_tuning"])
        self.assertFalse(learning["production_write_authority"])

    def test_no_proposal_from_insufficient_samples(self):
        self.utility["hypotheses"]["spx.volatility.benign"]["predictive_utility"]["independent_events"] = 5
        self.assertEqual([], build_learning({"belief_calibration": {}}, self.utility)["actions"])


if __name__ == "__main__":
    unittest.main()
