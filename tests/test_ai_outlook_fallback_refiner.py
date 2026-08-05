from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import publish_ai_outlook_daily as daily
import refine_ai_outlook_fallback as refiner
import update_ai_outlook_v3 as v3


def source(language: str, area: str, index: int, title: str) -> dict:
    return {
        "id": index,
        "language": language,
        "source_language": language,
        "area": area,
        "category": area,
        "title": title,
        "summary": "A current source describes a developing issue with measurable follow-up.",
        "source": f"Source {language.upper()}",
        "url": f"https://example.com/{language}/{index}",
        "published_at": "2026-08-05T06:30:00+00:00",
        "provenance_id": f"prov_{language}_{index}",
        "source_quality": 78,
    }


class AiOutlookFallbackRefinerTests(unittest.TestCase):
    def test_refiner_produces_complete_titles_without_mid_word_truncation(self) -> None:
        moment = datetime(2026, 8, 5, 9, 0, tzinfo=ZoneInfo("Europe/Warsaw"))
        pools = {
            "pl": [
                source("pl", "economy", 1, "Zarząd tonuje słowa prezesa. Akcje spółki spadają po wynikach"),
                source("pl", "economy", 2, "Rynek czeka na decyzję regulatora"),
            ],
            "en": [
                source("en", "economy", 1, "Bank battle: history suggests a fight over a windfall tax"),
                source("en", "economy", 2, "Government reviews a new tax policy"),
            ],
        }
        payload, _ = daily.build_fallback(moment, pools=pools)
        self.assertTrue(refiner.refine(payload))
        v3.validate_payload(payload)
        self.assertTrue(payload["pl"]["title"].startswith("Dalsze decyzje rynkowe"))
        self.assertTrue(payload["en"]["title"].startswith("Further market decisions"))
        self.assertLessEqual(len(payload["pl"]["title"]), 100)
        self.assertLessEqual(len(payload["en"]["title"]), 100)
        self.assertNotRegex(payload["pl"]["title"], r"\bpotwierdzeni$")
        self.assertNotRegex(payload["en"]["title"], r"\blik$")
        self.assertEqual(
            payload["pl"]["engine"]["fallback_copy_version"],
            refiner.REFINER_VERSION,
        )
        self.assertEqual(
            payload["en"]["engine"]["fallback_copy_version"],
            refiner.REFINER_VERSION,
        )

    def test_primary_mode_is_not_rewritten(self) -> None:
        payload = {"generation_mode": "ai_primary"}
        self.assertFalse(refiner.refine(payload))


if __name__ == "__main__":
    unittest.main()
