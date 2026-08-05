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
import update_ai_outlook_v3 as v3


def source(language: str, area: str, index: int, title: str, summary: str) -> dict:
    return {
        "id": index,
        "language": language,
        "source_language": language,
        "area": area,
        "category": area,
        "title": title,
        "summary": summary,
        "source": f"Source {language.upper()} {index}",
        "url": f"https://example.com/{language}/{area}/{index}",
        "published_at": "2026-08-05T06:30:00+00:00",
        "provenance_id": f"prov_{language}_{area}_{index}",
        "source_quality": 78,
    }


class DailyAiOutlookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.moment = datetime(2026, 8, 5, 9, 0, tzinfo=ZoneInfo("Europe/Warsaw"))
        self.pools = {
            "pl": [
                source("pl", "economy", 1, "NBP komentuje perspektywy inflacji", "Nowe dane pokazują zmianę presji cenowej."),
                source("pl", "economy", 2, "Rynek czeka na kolejną decyzję RPP", "Ekonomiści analizują ścieżkę stóp procentowych."),
                source("pl", "science", 3, "Naukowcy publikują wyniki badań", "Zespół opisał nową metodę pomiaru."),
            ],
            "en": [
                source("en", "geopolitics", 1, "NATO prepares a new security decision", "Officials are discussing the next formal step."),
                source("en", "geopolitics", 2, "Allies review sanctions policy", "A further official update is expected."),
                source("en", "science", 3, "Researchers publish new battery results", "The study reports a measurable efficiency gain."),
            ],
        }

    def test_deterministic_fallback_is_valid_fresh_and_language_isolated(self) -> None:
        payload, audit = daily.build_fallback(
            self.moment,
            pools=self.pools,
            primary_error="provider unavailable",
        )
        v3.validate_payload(payload)
        self.assertEqual(payload["date"], "2026-08-05")
        self.assertEqual(payload["generation_mode"], "deterministic_daily_fallback")
        self.assertEqual(audit["fallback_version"], daily.FALLBACK_VERSION)
        for language in ("pl", "en"):
            edition = payload[language]
            self.assertTrue(edition["forecast_id"].startswith(f"2026-08-05-{language}-"))
            self.assertEqual(edition["source_language"], language)
            self.assertEqual(edition["engine"]["edition_language"], language)
            self.assertEqual(edition["engine"]["selection_mode"], "deterministic_daily_fallback")
            self.assertTrue(edition["sources"])
            self.assertTrue(all(item["source_language"] == language for item in edition["sources"]))

    def test_low_value_lottery_or_death_item_loses_to_substantive_story(self) -> None:
        pool = [
            source("pl", "economy", 1, "Kumulacja Lotto. Do wygrania 100 mln zł", "Wyniki losowania."),
            source("pl", "economy", 2, "Nowe dane o inflacji i stopach procentowych", "NBP i rynek analizują dalszy kierunek."),
        ]
        area, selected, _ = daily._select_sources("pl", self.moment, pool)
        self.assertEqual(area, "economy")
        self.assertEqual(selected[0]["url"], pool[1]["url"])

    def test_fallback_uses_allowed_horizons_and_governance(self) -> None:
        payload, _ = daily.build_fallback(self.moment, pools=self.pools)
        self.assertEqual(payload["pl"]["horizon"], "3–6 miesięcy")
        self.assertEqual(payload["en"]["horizon"], "3–6 months")
        self.assertTrue(payload["pl"]["governance"]["disclaimer_required"])
        self.assertTrue(payload["en"]["governance"]["disclaimer_required"])


if __name__ == "__main__":
    unittest.main()
