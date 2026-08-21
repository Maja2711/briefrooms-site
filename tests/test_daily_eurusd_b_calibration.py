from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from scripts import daily_eurusd_experiment_v13 as v13

UTC = timezone.utc
OBSERVED = datetime(2026, 8, 21, 14, 0, tzinfo=UTC)


def payload(probability: float, confidence: float) -> dict:
    return {
        "schema_version": 2,
        "beliefs": [
            {
                "belief_id": belief_id,
                "probability": probability,
                "confidence": confidence,
                "last_updated": (OBSERVED - timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
            }
            for belief_id in v13.BELIEF_WEIGHTS
        ],
    }


class DailyEURUSDBCalibrationTests(unittest.TestCase):
    def test_positive_belief_support_can_trigger_long(self):
        calibrated = v13._calibrated_belief_snapshot(payload(.55, .50), OBSERVED)
        self.assertTrue(calibrated["available"])
        self.assertEqual(calibrated["direction"], "LONG")
        self.assertGreaterEqual(calibrated["score"], 60.0)
        self.assertLess(calibrated["raw_score"], calibrated["score"])
        self.assertEqual(calibrated["decision_calibration"]["method"], "support_scale_v1")
        self.assertFalse(calibrated["decision_calibration"]["canonical_belief_state_modified"])

    def test_negative_belief_support_can_trigger_short(self):
        calibrated = v13._calibrated_belief_snapshot(payload(.45, .50), OBSERVED)
        self.assertEqual(calibrated["direction"], "SHORT")
        self.assertLessEqual(calibrated["score"], 40.0)

    def test_tiny_support_remains_flat(self):
        calibrated = v13._calibrated_belief_snapshot(payload(.502, .20), OBSERVED)
        self.assertEqual(calibrated["direction"], "FLAT")

    def test_signed_score_remains_raw_for_hybrid_context(self):
        calibrated = v13._calibrated_belief_snapshot(payload(.55, .50), OBSERVED)
        self.assertAlmostEqual(calibrated["signed_score"], calibrated["raw_signed_score"])
        self.assertTrue(calibrated["decision_calibration"]["hybrid_context_uses_raw_support"])


if __name__ == "__main__":
    unittest.main()
