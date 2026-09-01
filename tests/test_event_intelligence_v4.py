from __future__ import annotations

import unittest

from scripts.news_event_intelligence_v4 import (
    EVENT_INTELLIGENCE_VERSION,
    canonical_event_id,
    cluster_events,
    corroboration,
    evidence_root,
    same_event,
)


class EventIntelligenceV4Tests(unittest.TestCase):
    def story(self, source, title, summary="", origin=None, role="publisher_unverified", confidence=0.0):
        return {
            "source": source,
            "publisher_source": source,
            "title": title,
            "summary": summary,
            "published_at": "2026-09-01T12:00:00+00:00",
            "image": "https://example.com/image.jpg",
            "origin_source": origin,
            "origin_confidence": confidence,
            "provenance_role": role,
        }

    def test_reuters_republications_are_one_evidence_lineage(self):
        rows = [
            self.story("TVN24", "Fed cuts rates by 25 basis points", origin="Reuters", role="republication", confidence=0.93),
            self.story("RMF24", "Fed cuts interest rates by 25 basis points", origin="Reuters", role="republication", confidence=0.93),
            self.story("Reuters", "Fed cuts rates by 25 basis points", origin="Reuters", role="original", confidence=1.0),
        ]
        result = corroboration(rows)
        self.assertEqual(result["independent_evidence_paths"], 1)
        self.assertEqual(result["evidence_roots"][0]["root"], "Reuters")
        self.assertEqual(result["status"], "single_lineage")

    def test_independent_ap_and_primary_source_increase_corroboration(self):
        rows = [
            self.story("Reuters", "Fed cuts rates by 25 basis points", origin="Reuters", role="original", confidence=1.0),
            self.story("Associated Press", "Federal Reserve cuts rates by a quarter point", origin="Associated Press", role="original", confidence=1.0),
            self.story("Federal Reserve", "Federal Reserve lowers target range by 25 basis points", origin="Federal Reserve", role="original", confidence=1.0),
        ]
        result = corroboration(rows)
        self.assertEqual(result["independent_evidence_paths"], 3)
        self.assertEqual(result["status"], "strong")
        self.assertGreaterEqual(result["score"], 65)

    def test_same_event_clusters_independent_coverage(self):
        first = self.story("Reuters", "ECB cuts rates by 25 basis points after inflation falls", origin="Reuters", role="original", confidence=1.0)
        second = self.story("BBC News", "ECB cuts interest rates by 25 basis points as inflation eases")
        self.assertTrue(same_event(first, second))

    def test_different_events_do_not_cluster_just_because_same_source(self):
        first = self.story("Reuters", "ECB cuts rates by 25 basis points", origin="Reuters", role="original", confidence=1.0)
        second = self.story("Reuters", "Apple launches new iPhone in California", origin="Reuters", role="original", confidence=1.0)
        self.assertFalse(same_event(first, second))

    def test_cluster_emits_one_canonical_event_and_evidence_metadata(self):
        rows = [
            self.story("Reuters", "ECB cuts rates by 25 basis points after inflation falls", origin="Reuters", role="original", confidence=1.0),
            self.story("BBC News", "ECB cuts interest rates by 25 basis points as inflation eases"),
        ]
        events, diag = cluster_events(rows)
        self.assertEqual(len(events), 1)
        self.assertEqual(diag["event_duplicates_suppressed"], 1)
        self.assertEqual(events[0]["event_intelligence_version"], EVENT_INTELLIGENCE_VERSION)
        self.assertEqual(events[0]["independent_evidence_paths"], 2)
        self.assertIn(events[0]["corroboration_status"], {"corroborated", "strong"})

    def test_event_id_is_deterministic(self):
        rows = [self.story("Reuters", "ECB cuts rates by 25 basis points", origin="Reuters", role="original", confidence=1.0)]
        self.assertEqual(canonical_event_id(rows), canonical_event_id(rows))

    def test_unverified_publishers_are_independent_roots(self):
        a = self.story("BBC News", "Major earthquake hits region")
        b = self.story("The Guardian", "Major earthquake hits region")
        self.assertNotEqual(evidence_root(a), evidence_root(b))


if __name__ == "__main__":
    unittest.main()
