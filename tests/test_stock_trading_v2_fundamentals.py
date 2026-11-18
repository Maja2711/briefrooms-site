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
        facts = example_facts()
        # 6% revenue growth vs. 60% EPS growth crosses the anomaly threshold.
        facts["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"]["units"]["USD"][1]["val"] = 106
        result = f.analyze_companyfacts(facts, as_of=NOW, sector="Technology",
                                        reference_price=32.0, price_session="2026-11-18")
        self.assertEqual(result["status"], "COMPLETE")
        self.assertAlmostEqual(result["metrics"]["revenue_growth_yoy"], .06)
        self.assertAlmostEqual(result["metrics"]["eps_growth_yoy"], .60)
        self.assertAlmostEqual(result["metrics"]["fcf_margin"], 13 / 106)
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

    def test_direct_10q_quarters_without_ytd_or_future_facts(self):
        facts = example_facts()["facts"]["us-gaap"]
        revenue = facts["RevenueFromContractWithCustomerExcludingAssessedTax"]["units"]["USD"]
        eps = facts["EarningsPerShareDiluted"]["units"]["USD/shares"]
        for start, end, filed, value, eps_value in (
            ("2025-07-01", "2025-09-30", "2025-10-22", 21, 0.20),
            ("2026-07-01", "2026-09-30", "2026-10-22", 28, 0.30),
        ):
            revenue.append(record(value, start, end, filed, form="10-Q"))
            eps.append(record(eps_value, start, end, filed, form="10-Q"))
        revenue.append(record(500, "2026-01-01", "2026-09-30", "2026-10-22", form="10-Q"))
        eps.append(record(9, "2026-07-01", "2026-09-30", "2026-11-18", form="10-Q"))
        result = f.analyze_companyfacts({"facts": {"us-gaap": facts}}, as_of=NOW)
        q = result["quarterly_metrics"]
        self.assertEqual(result["latest_quarter_end"], "2026-09-30")
        self.assertEqual(result["prior_year_quarter_end"], "2025-09-30")
        self.assertEqual(q["revenue_quarter_usd"], 28)
        self.assertAlmostEqual(q["revenue_growth_quarter_yoy"], 1/3)
        self.assertAlmostEqual(q["eps_diluted_quarter_usd"], .30)
        self.assertAlmostEqual(q["eps_growth_quarter_yoy"], .50)
        self.assertEqual(result["source_facts"]["eps_diluted_quarter"]["form"], "10-Q")
        self.assertEqual(result["source_facts"]["eps_diluted_quarter"]["start"], "2026-07-01")

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

    def test_shadow_fundamental_context_does_not_change_opportunity_utility(self):
        from scripts import stock_trading_v2_contracts as contracts
        from scripts import stock_trading_v2_deep_evidence as deep
        from scripts import stock_trading_v2_opportunity_engine as opportunities

        frontier = {
            "market": "US", "generated_at": "2026-11-18T18:00:00Z",
            "frontier_sha256": "c" * 64,
            "candidates": [{
                "symbol": "TEST", "opportunity_score": 85,
                "market_data_symbol": "TEST", "sector": "Technology",
                "features": {"last_close": 100.0, "atr_fraction": .02,
                             "latest_session": "2026-11-18", "returns": {}},
                "liquidity": {"production_reference_pass": True, "median_turnover": 30000000},
            }],
        }
        fundamental_row = {
            "symbol": "TEST", "market": "US", "status": "COMPLETE",
            "fundamental_score": 95.0, "production_decision_influence": False,
        }
        fundamental_snapshot = {
            "schema_version": f.SCHEMA, "market": "US",
            "generated_at": "2026-11-18T18:00:00Z",
            "source_frontier_sha256": frontier["frontier_sha256"],
            "candidate_count": 1, "candidates": [fundamental_row],
            "governance": {"production_decision_influence": False,
                           "automatic_portfolio_admission": False,
                           "llm_calls": 0, "score_mode": "shadow_only"},
        }
        fundamental_snapshot["fundamentals_sha256"] = contracts.payload_sha256(fundamental_snapshot)
        evidence_by_symbol = {"TEST": ([], {"primary": {"ok": True}, "secondary": {"ok": True}})}
        deep_config = deep.load_config()
        with mock.patch.object(deep.discovery, "validate_frontier"):
            baseline = deep.build_deep_evidence(frontier, evidence_by_symbol, deep_config, generated_at=NOW)
            with_fundamentals = deep.build_deep_evidence(
                frontier, evidence_by_symbol, deep_config, generated_at=NOW,
                fundamental_snapshot=fundamental_snapshot,
            )
        self.assertEqual(baseline["candidates"][0]["deep_opportunity_score"],
                         with_fundamentals["candidates"][0]["deep_opportunity_score"])
        self.assertEqual(with_fundamentals["candidates"][0]["fundamental_analysis"]["fundamental_score"], 95.0)
        self.assertFalse(with_fundamentals["fundamental_score_influence"])
        self.assertEqual(with_fundamentals["source_fundamentals_sha256"],
                         fundamental_snapshot["fundamentals_sha256"])
        op_config = opportunities.load_config()
        baseline_op = opportunities.compare_opportunities(baseline, None, op_config, generated_at=NOW)
        extra_op = opportunities.compare_opportunities(with_fundamentals, None, op_config, generated_at=NOW)
        self.assertEqual(baseline_op["evaluated_candidates"][0]["utility"],
                         extra_op["evaluated_candidates"][0]["utility"])
        self.assertEqual(baseline_op["decision"]["action"], extra_op["decision"]["action"])
        self.assertEqual(extra_op["evaluated_candidates"][0]["fundamental_analysis"]["fundamental_score"], 95.0)

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
