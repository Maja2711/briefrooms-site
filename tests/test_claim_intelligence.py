from __future__ import annotations

import unittest

from scripts.news_claim_intelligence import (
    CLAIM_INTELLIGENCE_VERSION,
    CONTRADICTION_DETECTION_VERSION,
    adjusted_corroboration_score,
    analyze_event_claims,
    extract_story_claims,
)
from scripts.news_event_intelligence_v4 import cluster_events


class ClaimIntelligenceTests(unittest.TestCase):
    def story(
        self,
        source: str,
        title: str,
        summary: str = "",
        *,
        origin: str | None = None,
        confidence: float = 0.0,
        role: str = "publisher_unverified",
        published_at: str = "2026-09-01T12:00:00+00:00",
    ) -> dict:
        return {
            "source": source,
            "publisher_source": source,
            "title": title,
            "summary": summary,
            "published_at": published_at,
            "image": "https://example.com/image.jpg",
            "origin_source": origin,
            "origin_confidence": confidence,
            "provenance_role": role,
        }

    def test_reuters_republications_do_not_create_false_contradiction(self) -> None:
        rows = [
            self.story(
                "TVN24",
                "At least 12 people killed in attack",
                origin="Reuters",
                confidence=0.93,
                role="republication",
            ),
            self.story(
                "RMF24",
                "At least 9 people killed in attack",
                origin="Reuters",
                confidence=0.93,
                role="republication",
            ),
            self.story(
                "Onet",
                "12 people killed after attack",
                origin="Reuters",
                confidence=0.93,
                role="republication",
            ),
        ]
        result = analyze_event_claims(rows)
        self.assertEqual(result["claim_consistency_status"], "insufficient_evidence")
        self.assertEqual(result["disputed_claim_count"], 0)

    def test_independent_death_counts_are_disputed_when_near_simultaneous(self) -> None:
        rows = [
            self.story(
                "Reuters",
                "At least 12 people killed in Kyiv attack",
                origin="Reuters",
                confidence=1.0,
                role="original",
                published_at="2026-09-01T12:00:00+00:00",
            ),
            self.story(
                "Associated Press",
                "At least 9 people killed in Kyiv attack",
                origin="Associated Press",
                confidence=1.0,
                role="original",
                published_at="2026-09-01T12:08:00+00:00",
            ),
        ]
        result = analyze_event_claims(rows)
        self.assertEqual(result["claim_consistency_status"], "disputed")
        self.assertEqual(result["disputed_claim_count"], 1)
        self.assertGreater(result["contradiction_score"], 50)

    def test_monotonic_casualty_growth_is_evolving_update(self) -> None:
        rows = [
            self.story(
                "Reuters",
                "7 people killed in Kyiv attack",
                origin="Reuters",
                confidence=1.0,
                role="original",
                published_at="2026-09-01T10:00:00+00:00",
            ),
            self.story(
                "Associated Press",
                "12 people killed in Kyiv attack",
                origin="Associated Press",
                confidence=1.0,
                role="original",
                published_at="2026-09-01T16:00:00+00:00",
            ),
        ]
        result = analyze_event_claims(rows)
        self.assertEqual(result["claim_consistency_status"], "evolving_update")
        self.assertEqual(result["evolving_claim_count"], 1)
        self.assertEqual(result["contradiction_score"], 0.0)

    def test_same_inflation_value_is_consistent(self) -> None:
        rows = [
            self.story(
                "Reuters",
                "Inflation falls to 3.2% in August",
                origin="Reuters",
                confidence=1.0,
                role="original",
            ),
            self.story(
                "BBC News",
                "August inflation falls to 3.2 percent",
                published_at="2026-09-01T12:12:00+00:00",
            ),
        ]
        result = analyze_event_claims(rows)
        self.assertEqual(result["claim_consistency_status"], "consistent")
        self.assertGreaterEqual(result["consistent_claim_count"], 1)

    def test_conflicting_central_bank_direction_is_disputed(self) -> None:
        rows = [
            self.story(
                "Reuters",
                "ECB cuts interest rates after meeting",
                origin="Reuters",
                confidence=1.0,
                role="original",
            ),
            self.story(
                "Associated Press",
                "ECB raises interest rates after meeting",
                origin="Associated Press",
                confidence=1.0,
                role="original",
                published_at="2026-09-01T12:05:00+00:00",
            ),
        ]
        result = analyze_event_claims(rows)
        self.assertEqual(result["claim_consistency_status"], "disputed")
        group = next(item for item in result["claim_groups"] if item["metric"] == "policy_rate_direction")
        self.assertEqual(group["status"], "disputed")

    def test_year_without_metric_is_not_comparable_numeric_claim(self) -> None:
        claims = extract_story_claims(
            self.story("BBC News", "Election campaign begins in 2026", "Parliament prepares for the vote")
        )
        self.assertFalse(any(item["claim_type"] == "numeric_metric" for item in claims))

    def test_dispute_reduces_effective_corroboration(self) -> None:
        analysis = {
            "claim_consistency_status": "disputed",
            "contradiction_score": 80.0,
            "consistent_claim_count": 0,
        }
        self.assertLess(adjusted_corroboration_score(80.0, analysis), 80.0)

    def test_cluster_event_exports_claim_intelligence_metadata(self) -> None:
        rows = [
            self.story(
                "Reuters",
                "ECB cuts interest rates after meeting",
                origin="Reuters",
                confidence=1.0,
                role="original",
            ),
            self.story(
                "Associated Press",
                "ECB raises interest rates after meeting",
                origin="Associated Press",
                confidence=1.0,
                role="original",
                published_at="2026-09-01T12:05:00+00:00",
            ),
        ]
        events, _ = cluster_events(rows)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["claim_intelligence_version"], CLAIM_INTELLIGENCE_VERSION)
        self.assertEqual(event["contradiction_detection_version"], CONTRADICTION_DETECTION_VERSION)
        self.assertEqual(event["claim_consistency_status"], "disputed")
        self.assertEqual(event["effective_corroboration_status"], "disputed")
        self.assertLess(event["claim_adjusted_corroboration_score"], event["corroboration_score"])


if __name__ == "__main__":
    unittest.main()
