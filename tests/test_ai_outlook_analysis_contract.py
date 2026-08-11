from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from ai_outlook_analysis_contract import (  # noqa: E402
    OutlookAnalysisError,
    validate_public_analysis,
)


class AiOutlookAnalysisContractTests(unittest.TestCase):
    def edition(self) -> dict:
        return {
            "analysis_contract_version": "ai-outlook-analysis-v1",
            "thesis": "TSUE wyda orzeczenie w sprawie podatku bankowego do końca 2027 roku.",
            "rationale": "NSA skierował pytanie prejudycjalne, co rozpoczęło procedurę przed TSUE.",
            "probability_event": "Wydanie przez TSUE orzeczenia w sprawie podatku bankowego do 2027-12-31.",
            "analysis_summary": (
                "Podany procent ocenia termin wydania wyroku, a nie jego treść; materiał nie "
                "zawiera jeszcze argumentacji pozwalającej oszacować kierunek rozstrzygnięcia."
            ),
            "impact": (
                "Wyrok dla banków może obniżyć ich podstawę podatku, a wynik dla państwa "
                "utrzyma obecne wpływy i preferencję dla krajowych obligacji."
            ),
            "watch_items": (
                "Ocenę zmienią pełna treść pytania, stanowiska stron, opinia rzecznika "
                "generalnego oraz wskazana przez TSUE podstawa prawna."
            ),
            "direction": {
                "status": "insufficient_evidence",
                "perspective": "dla banków i budżetu państwa",
                "explanation": (
                    "Dostępny materiał potwierdza procedurę, lecz nie daje podstaw do "
                    "procentowego podziału kierunku merytorycznego wyroku."
                ),
                "scenarios": [],
            },
            "resolution": {
                "metric": "Wydanie przez TSUE orzeczenia w sprawie podatku bankowego",
                "resolution_date": "2027-12-31",
            },
        }

    def test_accepts_explicit_event_and_honest_direction_limit(self) -> None:
        validate_public_analysis(self.edition(), "pl")

    def test_rejects_probability_without_deadline(self) -> None:
        edition = self.edition()
        edition["probability_event"] = "Wydanie przez TSUE orzeczenia w sprawie podatku bankowego."
        with self.assertRaisesRegex(OutlookAnalysisError, "deadline"):
            validate_public_analysis(edition, "pl")

    def test_rejects_direction_scenarios_that_do_not_sum_to_100(self) -> None:
        edition = self.edition()
        edition["direction"] = {
            "status": "estimated",
            "perspective": "dla banków",
            "explanation": "Pełna treść sprawy pozwala porównać trzy rozłączne kierunki rozstrzygnięcia.",
            "scenarios": [
                {"label": "korzystny", "probability": 60, "meaning": "Banki uzyskują prawo do szerszego wyłączenia obligacji."},
                {"label": "niekorzystny", "probability": 30, "meaning": "Obecne zasady podatku bankowego pozostają bez zmian."},
            ],
        }
        with self.assertRaisesRegex(OutlookAnalysisError, "sum to 100"):
            validate_public_analysis(edition, "pl")

    def test_rejects_article_recap_disguised_as_analysis(self) -> None:
        edition = self.edition()
        edition["analysis_summary"] = edition["rationale"] * 2
        with self.assertRaisesRegex(OutlookAnalysisError, "repeats rationale"):
            validate_public_analysis(edition, "pl")


if __name__ == "__main__":
    unittest.main()
