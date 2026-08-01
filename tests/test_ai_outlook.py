import importlib.util
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "update_ai_outlook.py"
SPEC = importlib.util.spec_from_file_location("update_ai_outlook", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class AiOutlookTests(unittest.TestCase):
    def setUp(self):
        self.items = [
            {
                "source": "Example Source",
                "url": "https://example.com/article",
                "title": "Example",
                "summary": "Example summary",
                "category": "Economy",
                "language": "en",
                "published_at": "2026-08-02T00:00:00Z",
            }
        ]
        self.raw = {
            "probability": 67,
            "source_ids": [1],
            "pl": {
                "category": "Gospodarka",
                "title": "Testowa prognoza",
                "thesis": "To jest testowa, mierzalna prognoza oparta na źródle.",
                "horizon": "6–12 miesięcy",
                "rationale": "To jest uzasadnienie prognozy.",
                "confirmation": "Ten sygnał potwierdzi prognozę.",
                "invalidation": "Ten sygnał obali prognozę.",
            },
            "en": {
                "category": "Economy",
                "title": "Test forecast",
                "thesis": "This is a testable forecast grounded in the source.",
                "horizon": "6–12 months",
                "rationale": "This is the forecast rationale.",
                "confirmation": "This signal would confirm it.",
                "invalidation": "This signal would invalidate it.",
            },
        }

    def test_validate_generated_maps_only_known_sources(self):
        moment = datetime(2026, 8, 2, 8, 0, tzinfo=ZoneInfo("Europe/Warsaw"))
        payload = MODULE.validate_generated(self.raw, self.items, moment)
        self.assertEqual(payload["date"], "2026-08-02")
        self.assertEqual(payload["pl"]["probability"], 67)
        self.assertEqual(payload["en"]["sources"][0]["url"], "https://example.com/article")
        MODULE.validate_file(payload)

    def test_unknown_source_is_rejected(self):
        broken = json.loads(json.dumps(self.raw))
        broken["source_ids"] = [2]
        moment = datetime(2026, 8, 2, 8, 0, tzinfo=ZoneInfo("Europe/Warsaw"))
        with self.assertRaises(MODULE.OutlookValidationError):
            MODULE.validate_generated(broken, self.items, moment)

    def test_probability_outside_contract_is_rejected(self):
        broken = json.loads(json.dumps(self.raw))
        broken["probability"] = 91
        moment = datetime(2026, 8, 2, 8, 0, tzinfo=ZoneInfo("Europe/Warsaw"))
        with self.assertRaises(MODULE.OutlookValidationError):
            MODULE.validate_generated(broken, self.items, moment)


if __name__ == "__main__":
    unittest.main()
