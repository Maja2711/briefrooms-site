from __future__ import annotations

import unittest

from scripts import daily_stock_bilingual_localization as loc


class DailyStockBilingualLocalizationTests(unittest.TestCase):
    def gpw_selection(self):
        return {
            "symbol": "JSW.WA",
            "ticker": "JSW",
            "name": "JSW",
            "thesis": "JSW ma najwyższy ranking po połączeniu sygnałów.",
            "why_now": "Wybór łączy momentum, ryzyko i bieżącą sesję.",
            "activation": "Nie gonić ceny powyżej strefy wejścia.",
            "sources": [{
                "id": "market:JSW",
                "publisher": "Yahoo",
                "title": "Yahoo — bieżące kwotowanie JSW.WA",
                "source_kind": "market_quote",
            }],
        }

    def us_selection(self):
        return {
            "symbol": "CRM",
            "ticker": "CRM",
            "name": "Salesforce",
            "thesis": "Salesforce has the highest final quantitative ranking.",
            "why_now": "Selection uses relative momentum, liquidity and risk geometry.",
            "activation": "Enter only inside the stated zone.",
            "sources": [{
                "id": "market:CRM",
                "publisher": "Yahoo",
                "title": "Yahoo current-session quote — CRM",
                "source_kind": "market_quote",
            }],
        }

    def test_gpw_fallback_is_english_and_complete(self):
        selection, mode = loc.localize_selection(self.gpw_selection(), "gpw", allow_ai=False)
        localized = selection["localized"]["en"]
        self.assertEqual(mode, "fallback")
        self.assertEqual(localized["language"], "en")
        self.assertEqual(localized["source_language"], "pl")
        self.assertIn("active GPW Daily Trade", localized["thesis"])
        self.assertIn("Current market quote", localized["source_summaries"]["market:JSW"])
        self.assertNotIn("najwyższy", localized["thesis"].lower())

    def test_us_fallback_is_polish_and_complete(self):
        selection, mode = loc.localize_selection(self.us_selection(), "us", allow_ai=False)
        localized = selection["localized"]["pl"]
        self.assertEqual(mode, "fallback")
        self.assertEqual(localized["language"], "pl")
        self.assertEqual(localized["source_language"], "en")
        self.assertIn("aktywnym wyborem US Daily Stock", localized["thesis"])
        self.assertIn("Bieżące kwotowanie", localized["source_summaries"]["market:CRM"])
        self.assertNotIn("highest final", localized["thesis"].lower())

    def test_existing_complete_translation_is_preserved(self):
        selection = self.gpw_selection()
        selection["localized"] = {
            "en": {
                "language": "en",
                "source_language": "pl",
                "thesis": "Existing English thesis.",
                "why_now": "Existing English rationale.",
                "activation": "Existing English activation.",
                "source_summaries": {"market:JSW": "Existing English source summary."},
            }
        }
        updated, mode = loc.localize_selection(selection, "gpw", allow_ai=False)
        self.assertEqual(mode, "existing")
        self.assertEqual(updated["localized"]["en"]["thesis"], "Existing English thesis.")

    def test_non_trade_payload_is_not_modified(self):
        payload = {"decision": "NO_TRADE", "selection": None}
        updated, mode = loc.localize_payload(payload, "us", allow_ai=False)
        self.assertEqual(mode, "not_trade")
        self.assertEqual(updated, payload)


if __name__ == "__main__":
    unittest.main()
