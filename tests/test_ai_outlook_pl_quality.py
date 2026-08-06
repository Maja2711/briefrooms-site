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
            "title": "Dostęp do gotówki: kolejne oficjalne wyjaśnienia",
            "thesis": (
                "Do 6 lutego 2027 r. co najmniej dwa polskie banki lub instytucje "
                "publiczne opublikują konkretne komunikaty o dostępności wypłat gotówki."
            ),
            "rationale": (
                "Punktem wyjścia jest opisana przez źródło trudność z uzyskaniem większej "
                "kwoty gotówki bez wcześniejszego zgłoszenia."
            ),
            "confirmation": (
                "Do 2027-02-06 zostaną opublikowane co najmniej 2 oficjalne komunikaty "
                "dotyczące dostępności wypłat gotówki."
            ),
            "invalidation": (
                "Do 2027-02-06 nie zostaną opublikowane co najmniej 2 takie komunikaty."
            ),
            "resolution_summary": (
                "Prognoza jest trafna, jeżeli do 2027-02-06 liczba oficjalnych komunikatów "
                "o dostępności wypłat gotówki wyniesie co najmniej 2."
            ),
            "sources": [
                {
                    "name": "Bankier.pl",
                    "url": (
                        "https://www.bankier.pl/wiadomosc/Rzad-radzi-trzymac-gotowke-na-"
                        "czarna-godzine-Banki-robia-wszystko-zeby-bylo-o-nia-trudniej-9178676.html"
                    ),
                    "provenance_id": "prov_cash_access",
                    "source_language": "pl",
                }
            ],
            "resolution": {
                "metric": "Liczba oficjalnych komunikatów dotyczących dostępności wypłat gotówki w Polsce",
                "comparison_operator": ">=",
                "threshold": 2.0,
                "unit": "komunikaty",
                "baseline_date": "2026-08-06",
                "baseline_value": 0.0,
                "data_source_for_verification": "Publiczne komunikaty NBP, ZBP, UOKiK i banków",
                "verification_url": "https://www.nbp.pl",
                "resolution_date": "2027-02-06",
                "status": "open",
            },
        }

    def test_accepts_one_coherent_measurable_forecast(self) -> None:
        validate_pl_edition(self.valid_edition())

    def test_rejects_the_published_mixed_metric(self) -> None:
        edition = self.valid_edition()
        edition["resolution"]["metric"] = (
            "Wskaźnik obrotu gotówkowego lub liczba skarg na ograniczenia wypłat"
        )
        edition["resolution"]["baseline_value"] = None
        with self.assertRaises(PolishOutlookQualityError):
            validate_pl_edition(edition)

    def test_rejects_vague_attention_language(self) -> None:
        edition = self.valid_edition()
        edition["thesis"] = "Dynamika regulacyjna utrzyma się w centrum uwagi rynków."
        with self.assertRaisesRegex(PolishOutlookQualityError, "non-testable phrase"):
            validate_pl_edition(edition)

    def test_rejects_unrelated_or_duplicated_sources(self) -> None:
        edition = self.valid_edition()
        unrelated = copy.deepcopy(edition["sources"][0])
        unrelated["url"] = (
            "https://www.bankier.pl/wiadomosc/Domanski-jesli-ceny-paliw-beda-spadac-"
            "to-nie-bedziemy-podejmowac-decyzji-o-ich-obnizce-9178682.html"
        )
        unrelated["provenance_id"] = "prov_fuel"
        edition["sources"].append(unrelated)
        with self.assertRaisesRegex(PolishOutlookQualityError, "unrelated"):
            validate_pl_edition(edition)

        duplicated = self.valid_edition()
        second = copy.deepcopy(duplicated["sources"][0])
        second["url"] += "?copy=1"
        duplicated["sources"].append(second)
        with self.assertRaisesRegex(PolishOutlookQualityError, "duplicated provenance"):
            validate_pl_edition(duplicated)


if __name__ == "__main__":
    unittest.main()
