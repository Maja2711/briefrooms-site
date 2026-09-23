import unittest

from decision_lab_public_projection import build_payload
from test_belief_aris_pattern import ev, verification


class DecisionLabProjectionTests(unittest.TestCase):
    def test_projection_exposes_sanitized_aris_contract(self):
        rows = []
        pattern_rows = set(range(20)) | {45, 48, 52, 55, 58}
        for i in range(60):
            if i in pattern_rows:
                evidence = [
                    ev("eps_revision", -1),
                    ev("rates_pressure", 1),
                    ev("noise_a", 1),
                ]
                outcome = False if i == 52 else True
            else:
                evidence = [ev("noise_a", 1), ev("noise_b", -1)]
                outcome = i % 2 == 0
            rows.append(verification(i, outcome, evidence))

        payload = build_payload(
            {"forecasts": [], "verifications": rows},
            {"belief_calibration": {}},
        )

        self.assertEqual(
            payload["schema_version"],
            "briefrooms_decision_lab_public_v2",
        )
        self.assertTrue(payload["aris_patterns"])
        self.assertEqual(
            payload["aris_pattern_meta"]["causal_status"],
            "ASSOCIATION_ONLY",
        )
        self.assertFalse(
            payload["aris_pattern_meta"]["authority"]["decision_influence"]
        )
        self.assertNotIn(
            "evidence_snapshot",
            payload["aris_patterns"][0],
        )


if __name__ == "__main__":
    unittest.main()
