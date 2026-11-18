"""Offline deterministic tests for SEC XBRL Fundamentals; no network and no LLM."""
import copy
import unittest
from datetime import datetime, timezone
from unittest import mock

from scripts import stock_trading_v2_fundamentals as f


NOW = datetime(2026, 11, 18, 18, 0, tzinfo=timezone.utc)


def record(value, start, end, filed, *, form="10-K", accession="0000000000-26-000001"):
    row = {"val": value, "end": end, "filed": filed, "form": form, "accn": accession}
    if start is not None:
        row["start"] = start
    return row


def example_facts():
    periods = [
        ("2024-01-01", "2024-12-31", "2025-02-20"),
        ("2025-01-01", "2025-12-31", "2026-02-20"),
    ]
    values = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": [100, 110],
        "NetIncomeLoss": [10, 16],
        "OperatingIncomeLoss": [15, 19],
        "EarningsPerShareDiluted": [1.0, 1.6],
        "NetCashProvidedByUsedInOperatingActivities": [12, 18],
        "PaymentsToAcquirePropertyPlantAndEquipment": [4, 5],
        "Assets": [100, 120],
        "Liabilities": [45, 50],
        "StockholdersEquity": [55, 70],
    }
    facts = {}
    for tag, series in values.items():
        unit = "USD/shares" if tag == "EarningsPerShareDiluted" else "USD"
        rows = []
        for i, (start, end, filed) in enumerate(periods):
            rows.append(record(series[i], None if tag in {"Assets", "Liabilities", "StockholdersEquity"} else start, end, filed))
        facts[tag] = {"units": {unit: rows}}
    return {"cik": 320193, "entityName": "Example Inc.", "facts": {"us-gaap": facts}}


class FundamentalTests(unittest.TestCase):
    def test_financial_metrics_and_anomaly(self):
        result = f.analyze_companyfacts(example_facts(), as_of=NOW, sector="Technology",
                                        reference_price=32.0, price_session="2026-11-18")
        self.assertEqual(result["status"], "COMPLETE")
        self.assertAlmostEqual(result["metrics"]["revenue_growth_yoy"], .10)
        self.assertAlmostEqual(result["metrics"]["eps_growth_yoy"], .60)
        self.assertAlmostEqual(result["metrics"]["fcf_margin"], 13 / 110)
        self.assertAlmostEqual(result["metrics"]["indicative_price_to_fy_earnings"], 20.0)
        self.assertIn("eps_growth_outpaces_revenue", result["anomalies"])
        self.assertGreaterEqual(result["fundamental_score"], 0)
        self.assertLessEqual(result["fundamental_score"], 100)
        self.assertEqual(result["source_facts"]["revenue_fy"]["accession"], "0000000000-26-000001")

    def test_no_10q_ytd_as_annual_and_same_day_excluded(self):
        facts = example_facts()
        earnings = facts["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"]
        earnings.append(record(999, "2025-01-01", "2025-09-30", "2025-11-01", form="10-Q"))
        earnings.append(record(999, "2025-01-01", "2025-12-31", "2026-11-18"))
        result = f.analyze_companyfacts(facts, as_of=NOW, sector="Technology")
        self.assertEqual(result["metrics"]["net_income_fy_usd"], 16)
        result_early = f.analyze_companyfacts(facts, as_of=datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(result_early["fiscal_end"], "2024-12-31")
        self.assertIsNone(result_early["metrics"]["revenue_growth_yoy"])

    def test_no_fictitious_zero_or_bad_unit(self):
        facts = example_facts()
        facts["facts"]["us-gaap"].pop("EarningsPerShareDiluted")
        result = f.analyze_companyfacts(facts, as_of=NOW, sector="Technology")
        self.assertIsNone(result["metrics"]["eps_growth_yoy"])
        self.assertIsNone(result["metrics"]["indicative_price_to_fy_earnings"])
        self.assertIsNotNone(result["metrics"]["revenue_growth_yoy"])

    def test_banks_are_not_assessed_using_nonbank_score(self):
        result = f.analyze_companyfacts(example_facts(), as_of=NOW, sector="Financial Services - Banks")
        self.assertEqual(result["status"], "NOT_COMPARABLE")
        self.assertIsNone(result["fundamental_score"])
        self.assertIn("sector_specific_financial_model_required", result["quality_flags"])

    def test_stale_annual_not_used_as_live_score(self):
        result = f.analyze_companyfacts(example_facts(),
                                        as_of=datetime(2028, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(result["status"], "STALE_FILING")
        self.assertIsNone(result["fundamental_score"])

    def test_missing_capex_does_not_invent_free_cashflow(self):
        facts = example_facts()
        del facts["facts"]["us-gaap"]["PaymentsToAcquirePropertyPlantAndEquipment"]
        result = f.analyze_companyfacts(facts, as_of=NOW)
        self.assertIsNone(result["metrics"]["free_cashflow_fy_usd"])
        self.assertIsNone(result["metrics"]["fcf_margin"])

    def test_symbol_mapping_and_no_http_for_gpw(self):
        class NoNetwork:
            def get(self, url):
                raise AssertionError("No SEC request should be made for GPW")

        frontier = {"market": "GPW", "generated_at": "2026-11-18T18:00:00Z",
                    "frontier_sha256": "a" * 64, "candidates": [{"symbol": "CDR"}]}
        with mock.patch.object(f.discovery, "validate_frontier"):
            result = f.build_snapshot(frontier, {"candidates_per_cycle": 10}, client=NoNetwork())
        self.assertEqual(result["candidates"][0]["status"], "UNSUPPORTED_SOURCE")
        self.assertIsNone(result["candidates"][0]["fundamental_score"])
        f.validate_snapshot(result)
        corrupted = copy.deepcopy(result)
        corrupted["candidates"][0]["status"] = "COMPLETE"
        with self.assertRaises(ValueError):
            f.validate_snapshot(corrupted)

    def test_us_snapshot_reads_one_companyfacts_payload(self):
        class FakeClient:
            def __init__(self):
                self.urls = []

            def get(self, url):
                self.urls.append(url)
                if "company_tickers.json" in url:
                    return {"0": {"ticker": "TEST", "cik_str": 320193}}
                return example_facts()

        client = FakeClient()
        frontier = {"market": "US", "generated_at": "2026-11-18T18:00:00Z",
                    "frontier_sha256": "b" * 64,
                    "candidates": [{"symbol": "TEST", "sector": "Technology",
                                    "features": {"last_close": 32, "latest_session": "2026-11-18"}}]}
        with mock.patch.object(f.discovery, "validate_frontier"):
            result = f.build_snapshot(frontier, {"candidates_per_cycle": 10}, client=client)
        self.assertEqual(len(client.urls), 2)
        self.assertEqual(result["candidates"][0]["status"], "COMPLETE")
        self.assertEqual(result["governance"]["llm_calls"], 0)
        self.assertFalse(result["governance"]["production_decision_influence"])
        f.validate_snapshot(result)


if __name__ == "__main__":
    unittest.main()
