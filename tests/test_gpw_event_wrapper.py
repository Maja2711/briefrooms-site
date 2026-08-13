from __future__ import annotations

import unittest

from scripts import gpw_event_driven_loop as wrapper


class GpwEventWrapperTests(unittest.TestCase):
    def test_final_context_uses_second_review_approved_source(self):
        context = {
            "primary_type": "contract",
            "primary_label": "kontrakt / zamówienie",
            "primary_source_id": "first",
            "primary_source_kind": "issuer_report",
            "primary_channel": "ESPI_EBI_PAP",
            "materiality": 4,
            "official_report_used": True,
            "selected_event_source_ids": ["first", "second"],
            "market_reaction": {},
            "event_learning": {"catalyst_adjustment": 0.0},
        }
        approved = [{
            "id": "second",
            "source_kind": "news",
            "channel": "NEWS",
            "event_type": "regulatory",
            "event_label": "regulacja / spór prawny",
            "materiality": 5,
            "age_hours": 1,
        }]
        final = wrapper._context_from_final_sources(context, approved)
        self.assertEqual(final["primary_source_id"], "second")
        self.assertEqual(final["primary_type"], "regulatory")
        self.assertFalse(final["official_report_used"])


if __name__ == "__main__":
    unittest.main()
