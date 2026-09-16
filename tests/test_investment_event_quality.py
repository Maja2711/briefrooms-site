import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import investment_event_quality as quality


class InvestmentEventQualityTests(unittest.TestCase):
    def test_senator_does_not_match_nato(self):
        self.assertEqual((0.58, "reported_actor"), quality.exact_authority("Senator introduces sanctions bill"))

    def test_labor_strike_is_rejected(self):
        row = {
            "title": "President comments as port workers begin strike over wages",
            "event_type": "escalation",
        }
        self.assertEqual("labor_strike_not_military_strike", quality.rejection_reason(row))

    def test_real_military_strike_is_not_rejected(self):
        row = {
            "title": "President ordered missile strikes on military targets",
            "event_type": "escalation",
        }
        self.assertIsNone(quality.rejection_reason(row))

    def test_contextual_truce_is_rejected(self):
        row = {
            "title": "Accident occurs during truce in border region",
            "event_type": "deescalation",
        }
        self.assertEqual("contextual_deescalation_without_new_action", quality.rejection_reason(row))

    def test_operational_ceasefire_is_accepted(self):
        row = {
            "title": "Prime minister announced and signed ceasefire agreement",
            "event_type": "deescalation",
        }
        self.assertIsNone(quality.rejection_reason(row))


if __name__ == "__main__":
    unittest.main()
