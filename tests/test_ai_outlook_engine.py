import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ai_outlook_engine import (  # noqa: E402
    ENGINE_VERSION,
    WEIGHTS_VERSION,
    annotate_sources,
    governance_for,
    normalize_resolution,
    probability_from_score,
    score_candidate,
    select_candidate,
    weights_snapshot,
)


class AiOutlookEngineTests(unittest.TestCase):
    def setUp(self):
        self.items = annotate_sources([
            {
                "title": "Inflacja i stopy procentowe zmieniają rynek kredytowy",
                "summary": "Bank centralny analizuje inflację i koszt kredytu.",
                "category": "Ekonomia",
                "source": "Reuters",
                "url": "https://www.reuters.com/economy/example?utm_source=x",
                "published_at": "2026-08-02T00:00:00Z",
                "origin_organization": "Reuters",
                "provenance_id": "wire_reuters_macro_1",
            },
            {
                "title": "Portal powtarza depeszę o stopach procentowych",
                "summary": "Według Reuters ten sam materiał dotyczy inflacji i kredytu.",
                "category": "Ekonomia",
                "source": "Portal",
                "url": "https://portal.example/reuters-copy",
                "published_at": "2026-08-02T00:10:00Z",
                "origin_organization": "Reuters",
                "provenance_id": "wire_reuters_macro_1",
            },
            {
                "title": "Nowe sankcje zmieniają handel między Rosją a UE",
                "summary": "Konflikt i sankcje wpływają na bezpieczeństwo oraz handel.",
                "category": "Geopolityka",
                "source": "BBC",
                "url": "https://www.bbc.com/news/example",
                "published_at": "2026-08-02T00:00:00Z",
            },
            {
                "title": "Badanie kliniczne nowego leczenia choroby serca",
                "summary": "Wyniki badania klinicznego opisują leczenie pacjentów.",
                "category": "Zdrowie",
                "source": "NIH",
                "url": "https://www.nih.gov/health/example",
                "published_at": "2026-08-02T00:00:00Z",
            },
            {
                "title": "Naukowcy opisują nowe odkrycie w fizyce",
                "summary": "Badanie może wpłynąć na rozwój technologii.",
                "category": "Nauka",
                "source": "Nature",
                "url": "https://www.nature.com/articles/example",
                "published_at": "2026-08-02T00:00:00Z",
            },
        ])

    def candidate(self, area="economy", source_ids=None, score=80, category="macro"):
        return {
            "candidate_id": "cand_test",
            "area": area,
            "content_category": category,
            "source_ids": source_ids or [1],
            "title": f"Prognoza {area}",
            "thesis": "Mierzalna prognoza przyszłego rozwoju.",
            "horizon_pl": "6–12 miesięcy",
            "horizon_en": "6–12 months",
            "selection_reason": "Wyraźny mechanizm i dostępne dane.",
            "resolution": {
                "metric": "Publiczny wskaźnik",
                "comparison_operator": ">=",
                "threshold": 10,
                "unit": "percent",
                "baseline_date": "2026-08-02",
                "data_source_for_verification": "Urząd statystyczny",
                "verification_url": "https://example.com/data",
                "resolution_date": "2027-08-02",
                "geography": "Polska",
            },
            "evidence_quality": score,
            "measurability": score,
            "causal_strength": score,
            "verifiability": score,
            "novelty": score,
            "speculation_risk": 20,
        }

    def test_engine_and_weights_are_versioned_and_frozen_by_snapshot(self):
        self.assertEqual(ENGINE_VERSION, "ai-outlook-engine-v1.1")
        self.assertEqual(WEIGHTS_VERSION, "weights-2026-08-v1")
        self.assertAlmostEqual(sum(weights_snapshot().values()), 1.0)

    def test_duplicate_provenance_is_one_independent_confirmation(self):
        candidate = self.candidate(source_ids=[1, 2])
        scored = score_candidate(candidate, self.items, [], "2026-08-02")
        self.assertEqual(scored["score_breakdown"]["source_count"], 2)
        self.assertEqual(scored["score_breakdown"]["independent_provenance_count"], 1)
        self.assertEqual(scored["score_breakdown"]["independent_source_bonus"], 0.0)

    def test_structured_resolution_is_required(self):
        broken = self.candidate()
        del broken["resolution"]
        with self.assertRaisesRegex(ValueError, "resolution"):
            score_candidate(broken, self.items, [], "2026-08-02")

    def test_resolution_date_must_be_future(self):
        broken = self.candidate()
        broken["resolution"]["resolution_date"] = "2026-08-02"
        with self.assertRaisesRegex(ValueError, "after publication"):
            normalize_resolution(broken, "2026-08-02")

    def test_market_and_health_disclaimers_are_data_governed(self):
        market = governance_for(self.candidate(category="market_investment"))
        self.assertEqual(market["risk_class"], "regulated_financial_content")
        self.assertIn("porady inwestycyjnej", market["disclaimers"]["pl"])
        health_candidate = self.candidate(area="health", source_ids=[4], category="clinical_health")
        health = governance_for(health_candidate)
        self.assertEqual(health["risk_class"], "medical_information")
        self.assertIn("medycznego", health["disclaimers"]["pl"])

    def test_health_authoritative_source_passes(self):
        health = self.candidate(area="health", source_ids=[4], category="clinical_health", score=85)
        scored = score_candidate(health, self.items, [], "2026-08-02")
        self.assertTrue(scored["score_breakdown"]["safety_gate"])
        self.assertTrue(scored["score_breakdown"]["authoritative"])

    def test_economy_priority_and_rejection_log(self):
        economy = self.candidate(area="economy", source_ids=[1], category="macro", score=78)
        geopolitics = self.candidate(area="geopolitics", source_ids=[3], category="geopolitics", score=94)
        geopolitics["candidate_id"] = "cand_geo"
        winner, ranked, log = select_candidate([geopolitics, economy], self.items, [], "2026-08-02")
        self.assertEqual(winner["area"], "economy")
        rejected = next(row for row in log if row["candidate_id"] == "cand_geo")
        self.assertEqual(rejected["decision"], "rejected")
        self.assertIn("LOWER_PRIORITY_AREA", {reason["code"] for reason in rejected["rejection_reasons"]})
        self.assertEqual(len(ranked), 2)

    def test_invalid_candidate_is_logged(self):
        invalid = self.candidate()
        invalid["candidate_id"] = "bad"
        del invalid["resolution"]
        valid = self.candidate()
        valid["candidate_id"] = "good"
        winner, _, log = select_candidate([invalid, valid], self.items, [], "2026-08-02")
        self.assertEqual(winner["candidate_id"], "good")
        bad = next(row for row in log if row["candidate_id"] == "bad")
        self.assertEqual(bad["rejection_reasons"][0]["code"], "INVALID_CANDIDATE")

    def test_probability_mapping_is_conservative_and_bounded(self):
        self.assertEqual(probability_from_score(0), 55)
        self.assertLessEqual(probability_from_score(100), 80)


if __name__ == "__main__":
    unittest.main()
