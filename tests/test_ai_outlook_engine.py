import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ai_outlook_engine import (  # noqa: E402
    annotate_sources,
    probability_from_score,
    score_candidate,
    select_candidate,
)


class AiOutlookEngineTests(unittest.TestCase):
    def setUp(self):
        self.items = annotate_sources(
            [
                {
                    "title": "Inflacja i stopy procentowe zmieniają rynek kredytowy",
                    "summary": "Bank centralny analizuje inflację i koszt kredytu.",
                    "category": "Ekonomia",
                    "source": "Reuters",
                    "url": "https://www.reuters.com/economy/example",
                },
                {
                    "title": "Nowe sankcje zmieniają handel między Rosją a UE",
                    "summary": "Konflikt i sankcje wpływają na bezpieczeństwo oraz handel.",
                    "category": "Geopolityka",
                    "source": "BBC",
                    "url": "https://www.bbc.com/news/example",
                },
                {
                    "title": "Badanie kliniczne nowego leczenia choroby serca",
                    "summary": "Wyniki badania klinicznego opisują leczenie pacjentów.",
                    "category": "Zdrowie",
                    "source": "NIH",
                    "url": "https://www.nih.gov/health/example",
                },
                {
                    "title": "Naukowcy opisują nowe odkrycie w fizyce",
                    "summary": "Badanie może wpłynąć na rozwój technologii.",
                    "category": "Nauka",
                    "source": "Nature",
                    "url": "https://www.nature.com/articles/example",
                },
            ]
        )

    def candidate(self, area, source_id, score, risk=20):
        return {
            "area": area,
            "source_ids": [source_id],
            "title": f"Prognoza {area}",
            "thesis": "Mierzalna prognoza przyszłego rozwoju.",
            "horizon_pl": "6–12 miesięcy",
            "horizon_en": "6–12 months",
            "resolution_criteria": "Do 31 grudnia 2027 publiczne dane pokażą mierzalną zmianę co najmniej jednego wskazanego wskaźnika.",
            "selection_reason": "Wyraźny mechanizm i dostępne dane.",
            "evidence_quality": score,
            "measurability": score,
            "causal_strength": score,
            "verifiability": score,
            "novelty": score,
            "speculation_risk": risk,
        }

    def test_economy_wins_priority_when_it_clears_threshold(self):
        economy = self.candidate("economy", 1, 76)
        science = self.candidate("science", 4, 96)
        winner, ranked = select_candidate([science, economy], self.items, [])
        self.assertEqual(winner["area"], "economy")
        self.assertEqual(winner["selection_mode"], "priority_area_threshold")
        self.assertEqual(len(ranked), 2)

    def test_geopolitics_wins_when_economy_is_too_weak(self):
        economy = self.candidate("economy", 1, 45, risk=80)
        geopolitics = self.candidate("geopolitics", 2, 84)
        winner, _ = select_candidate([economy, geopolitics], self.items, [])
        self.assertEqual(winner["area"], "geopolitics")

    def test_health_authoritative_source_passes_safety_gate(self):
        health = self.candidate("health", 3, 82)
        scored = score_candidate(health, self.items, [])
        self.assertTrue(scored["score_breakdown"]["safety_gate"])
        self.assertTrue(scored["score_breakdown"]["authoritative"])

    def test_health_weak_single_source_is_capped_below_threshold(self):
        weak_items = annotate_sources(
            [
                {
                    "title": "Zdrowie i nowa terapia",
                    "summary": "Opis leczenia pacjentów.",
                    "category": "Zdrowie",
                    "source": "Small Blog",
                    "url": "https://example.com/health",
                }
            ]
        )
        health = self.candidate("health", 1, 95)
        scored = score_candidate(health, weak_items, [])
        self.assertFalse(scored["score_breakdown"]["safety_gate"])
        self.assertLess(scored["engine_score"], scored["score_breakdown"]["threshold"])

    def test_probability_mapping_is_conservative_and_bounded(self):
        self.assertEqual(probability_from_score(0), 55)
        self.assertGreaterEqual(probability_from_score(70), 55)
        self.assertLessEqual(probability_from_score(100), 80)


if __name__ == "__main__":
    unittest.main()
