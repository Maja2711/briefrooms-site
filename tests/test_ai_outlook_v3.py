import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "update_ai_outlook_v3.py"
SPEC = importlib.util.spec_from_file_location("update_ai_outlook_v3", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class AiOutlookV3Tests(unittest.TestCase):
    def edition(self, language: str, title: str, probability: int) -> dict:
        horizon = "6–12 miesięcy" if language == "pl" else "6–12 months"
        disclaimer = "Prognoza testowa." if language == "pl" else "Test forecast."
        return {
            "category": "Ekonomia" if language == "pl" else "Economy",
            "title": title,
            "thesis": "Testowa mierzalna prognoza." if language == "pl" else "A measurable test forecast.",
            "horizon": horizon,
            "rationale": "Uzasadnienie." if language == "pl" else "Rationale.",
            "confirmation": "Potwierdzenie." if language == "pl" else "Confirmation.",
            "invalidation": "Obalenie." if language == "pl" else "Invalidation.",
            "resolution_summary": "Kryterium rozstrzygnięcia." if language == "pl" else "Resolution criterion.",
            "resolution_criteria": "Kryterium rozstrzygnięcia." if language == "pl" else "Resolution criterion.",
            "date_label": "3 sierpnia 2026" if language == "pl" else "August 3, 2026",
            "forecast_id": f"2026-08-03-{language}-cand_01",
            "probability": probability,
            "source_language": language,
            "source_policy": MODULE.SOURCE_POLICY[language],
            "sources": [
                {
                    "name": "Źródło" if language == "pl" else "Source",
                    "url": f"https://example.com/{language}/article",
                    "source_language": language,
                    "provenance_id": f"prov-{language}",
                }
            ],
            "resolution": {
                "schema_version": MODULE.RESOLUTION_SCHEMA_VERSION,
                "metric": "metric",
                "comparison_operator": ">=",
                "threshold": 1.0,
                "unit": "count",
                "baseline_date": "2026-08-03",
                "baseline_value": None,
                "data_source_for_verification": "Public source",
                "verification_url": "https://example.com/verify",
                "resolution_date": "2027-08-03",
                "geography": "Global",
                "status": "open",
            },
            "governance": {
                "schema_version": MODULE.GOVERNANCE_SCHEMA_VERSION,
                "risk_class": "general_forecast",
                "disclaimer_required": True,
                "disclaimers": {
                    "pl": "Prognoza testowa.",
                    "en": "Test forecast.",
                },
            },
            "engine": {
                "version": MODULE.ENGINE_VERSION,
                "edition_language": language,
                "weights_snapshot": MODULE.weights_snapshot(),
                "selected_area": "economy",
            },
            "disclaimer": disclaimer,
        }

    def test_english_source_pool_uses_only_english_feed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            news = temp_root / "data" / "news"
            news.mkdir(parents=True)
            (news / "en.json").write_text(
                json.dumps(
                    {
                        "schema_version": "news-live-v2",
                        "language": "en",
                        "sections": {
                            "business": [
                                {
                                    "title": "English economy signal",
                                    "summary": "English evidence about inflation and markets.",
                                    "source": "English Source",
                                    "link": "https://example.com/en/economy",
                                    "published_at": "2026-08-03T06:00:00Z",
                                }
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            (news / "pl.json").write_text(
                json.dumps(
                    {
                        "schema_version": "news-live-v2",
                        "language": "pl",
                        "sections": {
                            "ekonomia": [
                                {
                                    "title": "Polski sygnał gospodarczy",
                                    "summary": "Polski tekst o inflacji.",
                                    "source": "Polskie źródło",
                                    "link": "https://example.com/pl/gospodarka",
                                    "published_at": "2026-08-03T06:00:00Z",
                                }
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            original_root = MODULE.ROOT
            MODULE.ROOT = temp_root
            try:
                items = MODULE.source_items("en")
            finally:
                MODULE.ROOT = original_root
        self.assertEqual(1, len(items))
        self.assertEqual("en", items[0]["source_language"])
        self.assertIn("/en/", items[0]["url"])

    def test_english_edition_rejects_polish_source(self) -> None:
        edition = self.edition("en", "English forecast", 66)
        edition["sources"][0]["source_language"] = "pl"
        with self.assertRaises(MODULE.OutlookV3ValidationError):
            MODULE.validate_edition("en", edition)

    def test_pl_and_en_editions_are_independently_valid(self) -> None:
        payload = {
            "schema_version": 2,
            "date": "2026-08-03",
            "generated_at": "2026-08-03T07:15:00+02:00",
            "edition_policy": "independent-per-language",
            "source_policy": dict(MODULE.SOURCE_POLICY),
            "pl": self.edition("pl", "Polska prognoza", 64),
            "en": self.edition("en", "Different English forecast", 72),
        }
        MODULE.validate_payload(payload)
        self.assertNotEqual(payload["pl"]["title"], payload["en"]["title"])
        self.assertNotEqual(payload["pl"]["probability"], payload["en"]["probability"])


if __name__ == "__main__":
    unittest.main()
