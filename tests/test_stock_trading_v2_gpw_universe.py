from __future__ import annotations

import unittest

from scripts import stock_trading_v2_gpw_universe as gpw_universe
from scripts import stock_trading_v2_gpw_universe_provider as provider
from scripts import stock_trading_v2_universe as universe


LANDING = """
<html><body>
<h1>Lista spółek 402 Spółek</h1>
<form>
<input name="action" value="GPWCompanySearch">
<input name="index[WIG]" value="on">
<input name="country[POLSKA]" value="POLSKA">
<input name="voivodship[11]" value="11">
<input name="sector[510]" value="510">
</form>
</body></html>
"""

FRAGMENT = """
<table>
<tr><td><a href="spolka?isin=PLAAA0000001"><span>ALFA SPÓŁKA AKCYJNA (AAA)</span></a></td></tr>
<tr><td><a href="/spolka?isin=PLBBB0000002">BETA S.A. (BBB)</a></td></tr>
</table>
"""


class StockTradingV2GPWUniverseTests(unittest.TestCase):
    def test_form_parser_enables_broad_filters(self):
        payload = provider.parse_search_form(LANDING)
        self.assertEqual(payload["action"], "GPWCompanySearch")
        self.assertEqual(payload["index[WIG]"], "on")
        self.assertEqual(payload["country[POLSKA]"], "on")
        self.assertEqual(payload["voivodship[11]"], "on")
        self.assertEqual(payload["sector[510]"], "510")

    def test_company_parser_extracts_ticker_name_and_isin(self):
        rows = provider.parse_companies(FRAGMENT)
        self.assertEqual([row["ticker"] for row in rows], ["AAA", "BBB"])
        self.assertEqual(rows[0]["isin"], "PLAAA0000001")
        self.assertEqual(rows[0]["name"], "ALFA SPÓŁKA AKCYJNA")

    def test_expected_count_parser(self):
        self.assertEqual(provider.expected_company_count(LANDING), 402)

    def test_dynamic_snapshot_is_broad_and_shadow_only(self):
        companies = [
            {"ticker": f"A{i:03d}", "isin": f"PL{i:010d}", "name": f"Company {i}"}
            for i in range(200)
        ]
        snapshot = gpw_universe.build_snapshot(
            companies,
            generated_at="2026-09-16T16:00:00Z",
            provider_meta={"complete": True, "companies_received": 200},
        )
        self.assertEqual(snapshot["instrument_count"], 200)
        self.assertTrue(snapshot["governance"]["dynamic_provider_ready"])
        self.assertFalse(snapshot["governance"]["production_decision_influence"])
        self.assertEqual(snapshot["instruments"][0]["market"], "GPW")
        universe.validate_snapshot(snapshot)


if __name__ == "__main__":
    unittest.main()
