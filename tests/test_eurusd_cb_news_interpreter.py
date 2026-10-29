"""All documents below are deliberately synthetic, not market publications."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from eurusd_central_bank_news_interpreter import classify, interpret_document


class InterpreterTests(unittest.TestCase):
    def fed(self, document, **kwargs):
        return interpret_document(bank="FED", headline="Federal Reserve issues FOMC statement",
            source_url="https://www.federalreserve.gov/newsevents/pressreleases/monetary-fixture.htm",
            source_class="OFFICIAL_STATEMENT", document=document, **kwargs)

    def test_fed_explicit_range_and_missing_expectations(self):
        result = self.fed("The Committee decided to maintain the target range for the federal funds rate at 3-1/2 to 3-3/4 percent.")
        self.assertTrue(result["statement_verified"])
        self.assertEqual(result["facts"]["rate_actual"], {"lower": 3.5, "upper": 3.75})
        self.assertIsNone(result["facts"]["rate_previous"])
        self.assertIsNone(result["facts"]["surprise_bps"])
        self.assertEqual(result["facts"]["expectations_status"], "MISSING")
        self.assertEqual(result["interpretation"]["usd_eur_bias"], "UNKNOWN")
        self.assertEqual(result["interpretation"]["authority"], "CONTEXT_ONLY")

    def test_ecb_named_three_rates(self):
        result = interpret_document(bank="ECB", headline="Monetary policy decisions", source_url="https://www.ecb.europa.eu/press/pr/date/fixture.html",
            source_class="OFFICIAL_STATEMENT", document="The Governing Council decided to keep rates unchanged. The interest rates on the deposit facility, the main refinancing operations and the marginal lending facility will remain unchanged at 2.00%, 2.15% and 2.40% respectively.")
        self.assertTrue(result["statement_verified"])
        self.assertEqual(result["facts"]["rate_actual"], {"deposit_facility": 2., "main_refinancing": 2.15, "marginal_lending": 2.4})

    def test_no_document_no_official_actuals(self):
        result = self.fed(None)
        self.assertFalse(result["statement_verified"])
        self.assertEqual(result["content_coverage"], "HEADLINE_ONLY")

    def test_secondary_headline_never_verifies_even_copied_text(self):
        result = interpret_document(bank="FED", headline="Fed Chair says rates unchanged", source_url="https://news.google.com/rss/articles/fixture",
            source_class="SECONDARY_HEADLINE", document="Chair Alex Example. The Committee decided to maintain the target range for the federal funds rate at 3.5 to 3.75 percent.")
        self.assertFalse(result["statement_verified"])
        self.assertFalse(result["speaker_verified"])
        self.assertIsNone(result["facts"]["rate_actual"])

    def test_document_bank_must_match(self):
        result = interpret_document(bank="FED", headline="FOMC statement", source_url="https://www.ecb.europa.eu/fixture.html",
            source_class="OFFICIAL_STATEMENT", document="The Committee decided to maintain the target range for the federal funds rate at 3.5 to 3.75 percent.")
        self.assertFalse(result["statement_verified"])

    def test_speaker_is_source_attribution_not_hardcoded_roster(self):
        for name in ("Alex Example", "Jordan Sample"):
            result = self.fed(f"<p>Chair {name}.</p><p>Prepared remarks.</p>")
            self.assertEqual(result["speaker_name"], name)
            self.assertTrue(result["speaker_verified"])
            self.assertEqual(result["live_speech_status"], "LIVE_SPEECH_UNVERIFIED")
        self.assertFalse(self.fed("A speech was delivered.")["speaker_verified"])

    def test_minutes_and_transcripts_are_not_rate_decisions(self):
        self.assertEqual(classify("Minutes of FOMC meeting", "FED"), "MINUTES")
        self.assertEqual(classify("Monetary policy statement and press conference", "ECB"), "PRESS_CONFERENCE")

    def test_script_text_cannot_supply_rate_fact(self):
        result = self.fed("<script>The Committee decided to maintain the target range for the federal funds rate at 3.5 to 3.75 percent.</script><p>Unrelated document.</p>")
        self.assertFalse(result["statement_verified"])


if __name__ == "__main__":
    unittest.main()
