import unittest

from belief_v3_candidate_library import V3_CANDIDATE_LIBRARY, V3_GOVERNANCE
from decision_lab_public_projection import build_payload


class BeliefV3CandidateLibraryTests(unittest.TestCase):
    def test_library_has_exactly_eleven_shadow_only_candidates(self):
        self.assertEqual(len(V3_CANDIDATE_LIBRARY), 11)
        self.assertEqual(len({x["belief_id"] for x in V3_CANDIDATE_LIBRARY}), 11)
        self.assertEqual(V3_GOVERNANCE["stage"], "SHADOW")
        self.assertFalse(V3_GOVERNANCE["production_decision_influence"])
        self.assertFalse(V3_GOVERNANCE["production_write_authority"])
        self.assertFalse(V3_GOVERNANCE["automatic_tuning"])
        self.assertFalse(V3_GOVERNANCE["automatic_promotion"])
        self.assertEqual(V3_GOVERNANCE["minimum_sample_for_review"], 50)
        self.assertTrue(V3_GOVERNANCE["incremental_information_required_after_minimum_sample"])

    def test_lab_projection_is_fail_closed_before_sample_and_incremental_gate(self):
        payload = build_payload({"forecasts": [], "verifications": [], "definitions": []}, {"belief_calibration": {}})
        block = payload["belief_core_v3_candidates"]
        self.assertEqual(len(block["candidates"]), 11)
        self.assertTrue(all(x["production_recommendation"] == "NIE OCENIAĆ" for x in block["candidates"]))
        self.assertTrue(all(x["production_write_authority"] is False for x in block["candidates"]))
        self.assertTrue(all(x["automatic_promotion"] is False for x in block["candidates"]))

    def test_candidates_without_real_source_do_not_fake_forecasts(self):
        waiting = [x for x in V3_CANDIDATE_LIBRARY if x["source_status"] == "WAITING_REAL_SOURCE"]
        self.assertTrue(waiting)
        self.assertTrue(all(x["required_evidence"] for x in waiting))


if __name__ == "__main__":
    unittest.main()
