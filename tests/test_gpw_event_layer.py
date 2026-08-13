from __future__ import annotations

import unittest

from scripts import gpw_event_layer as events


class GpwEventLayerTests(unittest.TestCase):
    def test_material_event_classification_is_specific(self):
        earnings = events.classify_event("ABC SA: szacunkowe wyniki EBITDA za II kwartał")
        regulatory = events.classify_event("XYZ SA: decyzja UOKiK i kara pieniężna")
        dividend = events.classify_event("Spółka rekomenduje wypłatę dywidendy")
        self.assertEqual(earnings["event_type"], "earnings")
        self.assertEqual(earnings["materiality"], 5)
        self.assertEqual(regulatory["event_type"], "regulatory")
        self.assertEqual(dividend["event_type"], "dividend")

    def test_event_learning_waits_for_sample_and_is_bounded(self):
        row = {
            "decision": "TRANSAKCJA",
            "selection": {"event_context": {"primary_type": "contract"}},
            "outcome": {"status": "RESOLVED", "activated": True, "r_multiple": 2.0},
        }
        inactive = events.event_history_adjustment([row] * 5, "contract", minimum_sample=6)
        active = events.event_history_adjustment([row] * 12, "contract", minimum_sample=6, max_adjustment=8)
        self.assertFalse(inactive["active"])
        self.assertEqual(inactive["catalyst_adjustment"], 0.0)
        self.assertTrue(active["active"])
        self.assertLessEqual(abs(active["catalyst_adjustment"]), 8.0)
        self.assertGreater(active["catalyst_adjustment"], 0.0)

    def test_event_context_carries_official_report_and_market_reaction(self):
        candidate = {
            "symbol": "ABC.WA",
            "reference_price": 100.0,
            "returns": {"1d": 0.03, "5d": 0.08},
            "relative_5d": 0.025,
            "volume_ratio": 1.9,
            "sources": [
                {"id": "src-report", "title": "ABC SA (12/2026) Istotny kontrakt", "publisher": "PAP MediaRoom", "quality": "pierwotne", "source_kind": "issuer_report", "channel": "ESPI_EBI_PAP", "event_type": "contract", "event_label": "kontrakt / zamówienie", "materiality": 4, "age_hours": 2.0},
                {"id": "src-news", "title": "Rynek komentuje kontrakt ABC", "publisher": "Media", "quality": "wtórne", "source_kind": "news", "channel": "NEWS", "event_type": "contract", "event_label": "kontrakt / zamówienie", "materiality": 4, "age_hours": 1.0},
            ],
        }
        analysis = {"source_ids": ["src-report", "src-news"]}
        context = events.build_event_context(candidate, analysis, [], {"event_layer": {}})
        self.assertEqual(context["primary_type"], "contract")
        self.assertTrue(context["official_report_used"])
        self.assertEqual(context["primary_channel"], "ESPI_EBI_PAP")
        self.assertEqual(context["market_reaction"]["return_1d_percent"], 3.0)
        self.assertEqual(context["market_reaction"]["volume_ratio_20d"], 1.9)

    def test_dedupe_prefers_official_report_over_duplicate_news(self):
        rows = [
            {"id": "news", "title": "ABC publikuje wyniki", "source_kind": "news", "materiality": 5, "age_hours": 1},
            {"id": "official", "title": "ABC publikuje wyniki", "source_kind": "issuer_report", "materiality": 5, "age_hours": 2},
        ]
        result = events._dedupe(rows, limit=5)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "official")


if __name__ == "__main__":
    unittest.main()
