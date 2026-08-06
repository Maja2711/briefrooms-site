from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from ai_outlook_pl_methodology import (
    METHODOLOGY_VERSION,
    candidate_rejection_reason,
    filter_candidates,
)


class PolishAiOutlookMethodologyTests(unittest.TestCase):
    publication_date = "2026-08-06"

    def items(self) -> list[dict]:
        return [
            {
                "id": 1,
                "area": "economy",
                "title": "Osobiste Konto Inwestycyjne. Ustawa czeka na podpis prezydenta",
                "summary": (
                    "Nowy instrument finansowy może wejść w życie 1 stycznia 2027 roku. "
                    "Ustawa oczekuje na podpis Prezydenta RP."
                ),
                "source": "TVN24",
                "url": (
                    "https://tvn24.pl/biznes/z-kraju/osobiste-konto-inwestycyjne-"
                    "ustawa-czeka-na-podpis-prezydenta-st9176647"
                ),
                "source_quality": 78,
                "provenance_id": "prov_oki",
            }
        ]

    def valid_candidate(self) -> dict:
        return {
            "candidate_id": "pl_01",
            "area": "economy",
            "content_category": "regulatory",
            "forecast_type": "official_decision",
            "source_ids": [1],
            "title": "Prezydent podpisze ustawę o Osobistym Koncie Inwestycyjnym",
            "forecast_statement": (
                "Do 30 września 2026 r. Prezydent RP podpisze ustawę o Osobistym "
                "Koncie Inwestycyjnym."
            ),
            "why_now": "Ustawa została uchwalona i oczekuje na decyzję prezydenta.",
            "causal_chain": "Zakończenie prac parlamentarnych przeniosło decyzję do prezydenta.",
            "horizon_pl": "3–6 miesięcy",
            "horizon_en": "3–6 months",
            "selection_reason": "Jedno oczekujące i oficjalnie weryfikowalne zdarzenie.",
            "resolution": {
                "metric": "Podpisanie ustawy o Osobistym Koncie Inwestycyjnym przez Prezydenta RP",
                "comparison_operator": ">=",
                "threshold": 1,
                "unit": "zdarzenie binarne",
                "baseline_date": "2026-08-06",
                "baseline_value": 0,
                "data_source_for_verification": "Oficjalne komunikaty Prezydenta RP",
                "verification_url": "https://www.prezydent.pl",
                "resolution_date": "2026-09-30",
                "geography": "Polska",
            },
            "evidence_quality": 82,
            "measurability": 95,
            "causal_strength": 80,
            "verifiability": 95,
            "novelty": 75,
            "speculation_risk": 20,
        }

    def test_accepts_real_world_binary_decision(self) -> None:
        self.assertIsNone(
            candidate_rejection_reason(
                self.valid_candidate(), self.items(), self.publication_date
            )
        )

    def test_rejects_meta_forecast_about_communications(self) -> None:
        candidate = self.valid_candidate()
        candidate["title"] = "Pojawią się kolejne oficjalne komunikaty"
        candidate["forecast_statement"] = "Do końca roku pojawią się dwa komunikaty."
        candidate["resolution"]["metric"] = "Liczba oficjalnych komunikatów"
        candidate["resolution"]["unit"] = "komunikaty"
        candidate["forecast_type"] = "official_indicator"
        self.assertEqual(
            candidate_rejection_reason(candidate, self.items(), self.publication_date),
            "meta_forecast",
        )

    def test_rejects_unofficial_verification_url(self) -> None:
        candidate = self.valid_candidate()
        candidate["resolution"]["verification_url"] = "https://www.bankier.pl"
        self.assertEqual(
            candidate_rejection_reason(candidate, self.items(), self.publication_date),
            "verification_not_official",
        )

    def test_rejects_missing_baseline(self) -> None:
        candidate = self.valid_candidate()
        candidate["resolution"]["baseline_value"] = None
        self.assertEqual(
            candidate_rejection_reason(candidate, self.items(), self.publication_date),
            "missing_numeric_baseline_or_threshold",
        )

    def test_filter_records_rejections_and_marks_methodology(self) -> None:
        valid = self.valid_candidate()
        invalid = copy.deepcopy(valid)
        invalid["candidate_id"] = "pl_02"
        invalid["resolution"]["metric"] = "Liczba artykułów o OKI"
        accepted, rejected = filter_candidates(
            [valid, invalid], self.items(), self.publication_date
        )
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["methodology_version"], METHODOLOGY_VERSION)
        self.assertEqual(rejected[0]["reason"], "meta_forecast")


if __name__ == "__main__":
    unittest.main()
