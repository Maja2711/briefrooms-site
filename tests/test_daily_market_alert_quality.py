from __future__ import annotations

import unittest
from dataclasses import dataclass
from pathlib import Path

from scripts import daily_market_alert_editorial_v2 as quality


@dataclass
class Snapshot:
    instrument_id: str
    name: str
    price_text: str
    change_text: str
    direction: str
    support_text: str
    resistance_text: str
    next_support_text: str
    next_resistance_text: str


SNAPSHOTS = [
    Snapshot("sp500", "S&P 500", "7 485", "+0,64%", "up", "7 470", "7 520", "7 420", "7 550"),
    Snapshot("brent", "Brent", "90,01 USD", "+1,10%", "up", "89,50 USD", "91,50 USD", "86,50 USD", "93,00 USD"),
    Snapshot("us10y", "US 10Y", "4,74%", "+8 pb", "up", "4,71%", "4,75%", "4,69%", "4,81%"),
]


class DailyMarketAlertQualityTests(unittest.TestCase):
    def test_pl_and_en_prompts_are_independent(self):
        spec = quality.load_spec()
        pl = quality.prompt("pl", spec)
        en = quality.prompt("en", spec)
        self.assertIn("wyłącznie POLSKIEJ", pl)
        self.assertIn("ENGLISH alert only", en)
        self.assertNotEqual(pl, en)

    def test_structured_fallback_passes_quality_gate(self):
        editorial = quality.deterministic_editorial(None, SNAPSHOTS, "open")
        report = editorial["quality_report"]
        self.assertTrue(report["passed"])
        self.assertEqual(report["issues"], [])
        self.assertGreaterEqual(report["score"], 92)
        for item in editorial["instruments"]:
            for lang in ("pl", "en"):
                self.assertEqual(set(item["narrative"][lang]), set(quality.FIELDS))

    def test_generic_phrase_is_blocked(self):
        spec = quality.load_spec()
        editorial = quality.deterministic_editorial(None, SNAPSHOTS, "open")
        editorial["instruments"][0]["narrative"]["pl"]["what_changed"] = (
            "Brak potwierdzonego pojedynczego nowego katalizatora; krótkoterminowy obraz wyznaczają bieżąca zmiana indeksu oraz wskazane poziomy techniczne."
        )
        result = quality.report(editorial, SNAPSHOTS, [], spec)
        self.assertFalse(result["passed"])
        self.assertTrue(any("generic" in issue for issue in result["issues"]))

    def test_missing_exact_levels_is_blocked(self):
        spec = quality.load_spec()
        editorial = quality.deterministic_editorial(None, SNAPSHOTS, "open")
        editorial["instruments"][1]["narrative"]["en"]["base_case"] = (
            "The base case is continued range trading, with a breakout confirming direction and a breakdown invalidating the setup."
        )
        result = quality.report(editorial, SNAPSHOTS, [], spec)
        self.assertFalse(result["passed"])
        self.assertTrue(any("base_case_missing_exact_levels" in issue for issue in result["issues"]))

    def test_public_payload_requires_independent_languages(self):
        editorial = quality.deterministic_editorial(None, SNAPSHOTS, "open")
        payload = {
            "instruments": [
                {"id": item["id"], "narrative": item["narrative"]}
                for item in editorial["instruments"]
            ],
            "editorial_quality": editorial["quality_report"],
            "editorial_contract": {
                "pl_generated_independently": True,
                "en_generated_independently": True,
            },
        }
        quality.validate_published_payload(payload)
        payload["editorial_contract"]["en_generated_independently"] = False
        with self.assertRaisesRegex(ValueError, "independently"):
            quality.validate_published_payload(payload)

    def test_spec_is_versioned(self):
        root = Path(__file__).resolve().parents[1]
        self.assertTrue((root / "data/investments/daily_market_alert_editorial_spec.json").exists())
        self.assertEqual(quality.load_spec()["spec_version"], "3.0")


if __name__ == "__main__":
    unittest.main()
