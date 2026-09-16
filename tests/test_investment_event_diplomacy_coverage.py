import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import investment_event_intelligence as event
import investment_event_diplomacy_coverage as diplomacy
import investment_event_quality as quality


class DiplomacyCoverageTests(unittest.TestCase):
    def setUp(self):
        diplomacy.install()

    def test_reuters_style_revive_talks_headline_is_deescalation(self):
        title = "China's top diplomat urges Iran, US to show restraint, revive talks"
        pressure, severity, event_type = event.event_pressure(title)
        strength, action_type = event.action_strength(title)
        authority, role = quality.exact_authority(title)

        self.assertEqual(-1.0, pressure)
        self.assertEqual("deescalation", event_type)
        self.assertGreaterEqual(severity, 0.70)
        self.assertEqual("rhetoric_or_warning", action_type)
        self.assertLess(strength, 0.70)
        self.assertEqual("senior_cabinet", role)
        self.assertGreaterEqual(authority, 0.90)

    def test_return_to_talks_is_detected(self):
        pressure, severity, event_type = event.event_pressure(
            "Iran says it is ready to return to talks with the United States"
        )
        self.assertEqual(-1.0, pressure)
        self.assertEqual("deescalation", event_type)
        self.assertGreaterEqual(severity, 0.70)

    def test_reopen_dialogue_is_detected(self):
        pressure, _, event_type = event.event_pressure(
            "Foreign minister calls for reopening dialogue with Washington"
        )
        self.assertEqual(-1.0, pressure)
        self.assertEqual("deescalation", event_type)

    def test_diplomacy_queries_cover_iran_us_talks(self):
        joined = " ".join(event.QUERIES).casefold()
        self.assertIn("iran", joined)
        self.assertIn("revive talks", joined)
        self.assertIn("negotiations", joined)
        self.assertIn("dialogue", joined)

    def test_quality_guard_accepts_new_diplomacy_action(self):
        row = {
            "title": "China's top diplomat urges Iran, US to show restraint, revive talks",
            "event_type": "deescalation",
        }
        self.assertIsNone(quality.rejection_reason(row))

    def test_contextual_dialogue_without_action_is_not_promoted(self):
        row = {
            "title": "Markets react during diplomatic dialogue between Iran and China",
            "event_type": "deescalation",
        }
        # "dialogue" alone is background context; there is no operative diplomacy action.
        self.assertEqual(
            "contextual_deescalation_without_new_action",
            quality.rejection_reason(row),
        )


if __name__ == "__main__":
    unittest.main()
