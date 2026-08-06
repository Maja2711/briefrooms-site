from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from ai_outlook_pl_quality import PolishOutlookQualityError, validate_pl_edition


class PolishAiOutlookQualityTests(unittest.TestCase):
    def valid_edition(self) -> dict:
        return {
            "title": "Prezydent podpisze ustawę o Osobistym Koncie Inwestycyjnym",
            "thesis": (
                "Do 30 września 2026 r. Prezydent RP podpisze ustawę wprowadzającą "
                "Osobiste Konto Inwestycyjne."
            ),
            "rationale": (
                "Ustawa została uchwalona i oczekuje na decyzję prezydenta. "
                "Prognoza dotyczy wyłącznie jej podpisania."
            ),
            "confirmation": (
                "Do 2026-09-30 ustawa o Osobistym Koncie Inwestycyjnym zostanie podpisana "
                "przez Prezydenta RP."
            ),
            "invalidation": (
                "Do 2026-09-30 ustawa o Osobistym Koncie Inwestycyjnym nie zostanie podpisana "
                "przez Prezydenta RP."
            ),
            "resolution_summary": (
                "Rozstrzygnięcie 2026-09-30: podpisanie ustawy o Osobistym Koncie "
                "Inwestycyjnym przez Prezydenta RP."
            ),
            "probability": 66,
            "forecast_type": "official_decision",
            "sources": [
                {
                    "name": "TVN24",
                    "url": (
                        "https://tvn24.pl/biznes/z-kraju/osobiste-konto-inwestycyjne-"
                        "ustawa-czeka-na-podpis-prezydenta-st9176647"
                    ),
                    "provenance_id": "prov_oki",
                    "source_language": "pl",
                }
            ],
            "resolution": {
                "metric": "Podpisanie ustawy o Osobistym Koncie Inwestycyjnym przez Prezydenta RP",
                "comparison_operator": ">=",
                "threshold": 1.0,
                "unit": "zdarzenie binarne",
                "baseline_date": "2026-08-06",
                "baseline_value": 0.0,
                "data_source_for_verification": "Oficjalne komunikaty Prezydenta RP",
                "verification_url": "https://www.prezydent.pl",
                "resolution_date": "2026-09-30",
                "status": "open",
            },
            "engine": {
                "methodology_version": "pl-outcome-forecast-v2",
            },
        }

    def test_accepts_real_official_decision(self) -> None:
        validate_pl_edition(self.valid_edition())

    def test_rejects_communication_count_meta_forecast(self) -> None:
        edition = self.valid_edition()
        edition["title"] = "Pojawią się kolejne oficjalne komunikaty"
        edition["thesis"] = "Do 2026-09-30 pojawią się co najmniej dwa komunikaty."
        edition["confirmation"] = "Do 2026-09-30 pojawią się dwa komunikaty o ustawie."
        edition["invalidation"] = "Do 2026-09-30 nie pojawią się dwa komunikaty o ustawie."
        edition["resolution_summary"] = "Do 2026-09-30 liczba komunikatów wyniesie dwa."
        edition["resolution"]["metric"] = "Liczba oficjalnych komunikatów o ustawie"
        edition["resolution"]["unit"] = "komunikaty"
        edition["forecast_type"] = "official_indicator"
        with self.assertRaisesRegex(PolishOutlookQualityError, "media|publication"):
            validate_pl_edition(edition)

    def test_rejects_unofficial_verification_target(self) -> None:
        edition = self.valid_edition()
        edition["resolution"]["verification_url"] = "https://www.bankier.pl"
        with self.assertRaisesRegex(PolishOutlookQualityError, "official"):
            validate_pl_edition(edition)

    def test_rejects_wrong_methodology_version(self) -> None:
        edition = self.valid_edition()
        edition["engine"]["methodology_version"] = "pl-semantic-quality-v1"
        with self.assertRaisesRegex(PolishOutlookQualityError, "current outcome methodology"):
            validate_pl_edition(edition)

    def test_rejects_unrelated_source(self) -> None:
        edition = self.valid_edition()
        edition["sources"][0]["url"] = (
            "https://www.bankier.pl/wiadomosc/ceny-paliw-spadek-decyzja-rzadu.html"
        )
        with self.assertRaisesRegex(PolishOutlookQualityError, "unrelated"):
            validate_pl_edition(edition)

    def test_rejects_duplicated_provenance(self) -> None:
        edition = self.valid_edition()
        second = copy.deepcopy(edition["sources"][0])
        second["url"] += "?copy=1"
        edition["sources"].append(second)
        with self.assertRaisesRegex(PolishOutlookQualityError, "duplicated provenance"):
            validate_pl_edition(edition)


if __name__ == "__main__":
    unittest.main()
