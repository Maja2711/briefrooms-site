import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ai_outlook_metrics import public_calibration_summary  # noqa: E402


class AiOutlookMetricsTests(unittest.TestCase):
    def record(self, probability, status):
        return {"probability": probability, "resolution": {"status": status}}

    def test_brier_hidden_below_30_resolved(self):
        records = [self.record(70, "resolved_true") for _ in range(29)]
        summary = public_calibration_summary(records)
        self.assertFalse(summary["public_brier_available"])
        self.assertIsNone(summary["brier_score"])

    def test_brier_published_at_30_resolved(self):
        records = [self.record(70, "resolved_true") for _ in range(30)]
        summary = public_calibration_summary(records)
        self.assertTrue(summary["public_brier_available"])
        self.assertAlmostEqual(summary["brier_score"], 0.09)

    def test_open_forecasts_are_raw_counters_only(self):
        records = [self.record(60, "open") for _ in range(12)]
        summary = public_calibration_summary(records)
        self.assertEqual(summary["published"], 12)
        self.assertEqual(summary["in_progress"], 12)
        self.assertEqual(summary["resolved"], 0)


if __name__ == "__main__":
    unittest.main()
