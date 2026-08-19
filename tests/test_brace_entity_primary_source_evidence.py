from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from scripts.brace_entity_primary_source_evidence import (
    REPORT_FILENAME,
    STATE_FILENAME,
    capabilities,
    companyfact_observations,
    filing_records,
    parse_sec_acceptance,
    promotion_evidence_standard,
    reporting_regime,
    run,
    safety_controls,
)


class FakeSecClient:
    def __init__(self) -> None:
        self.index: dict[str, Mapping[str, Any]] = {
            "ALPHA": {"ticker": "ALPHA", "cik": 1001, "title": "Alpha Inc"},
            "BETA": {"ticker": "BETA", "cik": 2002, "title": "Beta PLC"},
        }
        self.submission_payloads: dict[int, Mapping[str, Any]] = {}
        self.companyfact_payloads: dict[int, Mapping[str, Any]] = {}
        self.header_acceptance: dict[tuple[int, str], str] = {}

    def ticker_index(self) -> Mapping[str, Mapping[str, Any]]:
        return self.index

    def submissions(self, cik: int) -> Mapping[str, Any]:
        return self.submission_payloads[int(cik)]

    def companyfacts(self, cik: int) -> Mapping[str, Any]:
        return self.companyfact_payloads.get(int(cik), {"facts": {}})

    def acceptance_from_index_headers(self, cik: int, accession_number: str) -> Optional[str]:
        return self.header_acceptance.get((int(cik), str(accession_number)))


def submissions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    keys = (
        "form",
        "accessionNumber",
        "filingDate",
        "reportDate",
        "acceptanceDateTime",
        "primaryDocument",
        "primaryDocDescription",
    )
    recent = {key: [] for key in keys}
    for row in rows:
        for key in keys:
            recent[key].append(row.get(key, ""))
    return {"filings": {"recent": recent}}


def filing(
    *,
    form: str,
    accession: str,
    acceptance: str,
    filing_date: str,
    report_date: str,
    primary: str = "report.htm",
    description: str = "Periodic report",
) -> dict[str, Any]:
    return {
        "form": form,
        "accessionNumber": accession,
        "filingDate": filing_date,
        "reportDate": report_date,
        "acceptanceDateTime": acceptance,
        "primaryDocument": primary,
        "primaryDocDescription": description,
    }


def companyfacts_for(accession: str, form: str = "10-Q") -> dict[str, Any]:
    return {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue",
                    "description": "Revenue from customers",
                    "units": {
                        "USD": [
                            {
                                "start": "2026-04-01",
                                "end": "2026-06-30",
                                "val": 1250000000,
                                "accn": accession,
                                "fy": 2026,
                                "fp": "Q2",
                                "form": form,
                                "filed": "2026-08-21",
                                "frame": "CY2026Q2",
                            },
                            {
                                "start": "2025-04-01",
                                "end": "2025-06-30",
                                "val": 999000000,
                                "accn": "0000001001-25-000001",
                                "fy": 2025,
                                "fp": "Q2",
                                "form": form,
                                "filed": "2025-08-20",
                                "frame": "CY2025Q2",
                            },
                        ]
                    },
                },
                "NetIncomeLoss": {
                    "label": "Net income",
                    "description": "Net income loss",
                    "units": {
                        "USD": [
                            {
                                "start": "2026-04-01",
                                "end": "2026-06-30",
                                "val": 175000000,
                                "accn": accession,
                                "fy": 2026,
                                "fp": "Q2",
                                "form": form,
                                "filed": "2026-08-21",
                            }
                        ]
                    },
                },
                "OperatingIncomeLoss": {
                    "label": "Operating income",
                    "description": "Operating income",
                    "units": {
                        "USD": [
                            {
                                "start": "2026-04-01",
                                "end": "2026-06-30",
                                "val": 220000000,
                                "accn": accession,
                                "fy": 2026,
                                "fp": "Q2",
                                "form": form,
                                "filed": "2026-08-21",
                            }
                        ]
                    },
                },
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "label": "Capital expenditure",
                    "description": "Payments to acquire PP&E",
                    "units": {
                        "USD": [
                            {
                                "start": "2026-01-01",
                                "end": "2026-06-30",
                                "val": 300000000,
                                "accn": accession,
                                "fy": 2026,
                                "fp": "Q2",
                                "form": form,
                                "filed": "2026-08-21",
                            }
                        ]
                    },
                },
                "UnmappedTag": {
                    "label": "Ignore me",
                    "units": {"USD": [{"end": "2026-06-30", "val": 1, "accn": accession, "form": form}]},
                },
            }
        }
    }


class BraceEntityPrimarySourceEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeSecClient()
        self.universe = {
            "instruments": [
                {
                    "instrument_id": "alpha",
                    "data_symbol": "ALPHA",
                    "asset_type": "STOCK",
                    "sector": "Information Technology",
                    "region": "United States",
                    "exchange": "NASDAQ",
                    "availability": "AVAILABLE",
                    "active": True,
                },
                {
                    "instrument_id": "beta",
                    "data_symbol": "BETA",
                    "asset_type": "STOCK",
                    "sector": "Financials",
                    "region": "Europe",
                    "exchange": "NYSE",
                    "availability": "AVAILABLE",
                    "active": True,
                },
                {
                    "instrument_id": "gamma",
                    "data_symbol": "GAMMA",
                    "asset_type": "STOCK",
                    "sector": "Health Care",
                    "availability": "AVAILABLE",
                    "active": True,
                },
                {
                    "instrument_id": "etf",
                    "data_symbol": "ETF",
                    "asset_type": "ETF",
                    "sector": "Diversified",
                    "availability": "AVAILABLE",
                    "active": True,
                },
            ]
        }
        self.portfolio = {
            "positions": [
                {"id": "alpha", "market_symbol": "ALPHA", "asset_type": "Stock", "status": "active"},
                {"id": "etf", "market_symbol": "ETF", "asset_type": "ETF", "status": "active"},
            ]
        }
        self.analysis = {
            "candidates": [
                {
                    "instrument_id": "beta",
                    "data_symbol": "BETA",
                    "asset_type": "STOCK",
                    "availability": "AVAILABLE",
                    "active": True,
                    "rank": 1,
                }
            ]
        }
        self.client.submission_payloads[1001] = submissions([
            filing(
                form="10-K",
                accession="0000001001-26-000001",
                acceptance="20260819160000",
                filing_date="2026-08-19",
                report_date="2025-12-31",
            )
        ])
        self.client.submission_payloads[2002] = submissions([
            filing(
                form="20-F",
                accession="0000002002-26-000001",
                acceptance="20260430160000",
                filing_date="2026-04-30",
                report_date="2025-12-31",
            ),
            filing(
                form="6-K",
                accession="0000002002-26-000002",
                acceptance="20260819150000",
                filing_date="2026-08-19",
                report_date="2026-08-19",
            ),
        ])

    def _paths(self, root: Path) -> tuple[Path, Path, Path]:
        portfolio = root / "portfolio.json"
        analysis = root / "analysis.json"
        universe = root / "universe.json"
        portfolio.write_text(json.dumps(self.portfolio), encoding="utf-8")
        analysis.write_text(json.dumps(self.analysis), encoding="utf-8")
        universe.write_text(json.dumps(self.universe), encoding="utf-8")
        return portfolio, analysis, universe

    def test_sec_acceptance_timestamp_is_conservative_during_dst(self) -> None:
        parsed = parse_sec_acceptance("20260701160000")
        self.assertIsNotNone(parsed)
        # 16:00 fixed EST => 21:00Z; America/New_York in July => 20:00Z.
        # PR13 deliberately uses the later instant.
        self.assertEqual(parsed.isoformat(), "2026-07-01T21:00:00+00:00")

    def test_reporting_regime_resolves_domestic_foreign_and_conflict(self) -> None:
        self.assertEqual(reporting_regime(["10-K", "10-Q", "8-K"]), "domestic_sec_periodic_reporting")
        self.assertEqual(reporting_regime(["20-F", "6-K"]), "foreign_private_issuer_sec")
        self.assertEqual(reporting_regime(["10-K", "6-K"]), "mixed_or_transition_requires_review")
        self.assertEqual(reporting_regime(["8-K"]), "sec_registered_event_reporting_only_unresolved")

    def test_header_fallback_resolves_missing_acceptance_timestamp(self) -> None:
        payload = submissions([
            filing(
                form="8-K",
                accession="0000001001-26-000010",
                acceptance="",
                filing_date="2026-08-20",
                report_date="2026-08-20",
            )
        ])
        self.client.header_acceptance[(1001, "0000001001-26-000010")] = "20260820173000"
        rows, issues = filing_records(
            entity_id="alpha",
            ticker="ALPHA",
            cik=1001,
            submissions=payload,
            client=self.client,
            boundary_at=datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].accession_number, "0000001001-26-000010")
        self.assertFalse(any(issue.code == "acceptance_timestamp_unresolved" for issue in issues))

    def test_first_run_opens_boundary_and_does_not_backfill_old_filings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            portfolio, analysis, universe = self._paths(root)
            state_dir = root / "state"
            report = run(
                state_dir,
                portfolio_path=portfolio,
                analysis_path=analysis,
                universe_path=universe,
                as_of=datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc),
                sec_client=self.client,
            )
            self.assertEqual(report["sample"]["active_entities"], 2)
            self.assertEqual(report["sample"]["prospective_observations_total"], 0)
            self.assertEqual(report["sample"]["new_observations_this_run"], 0)
            self.assertEqual(report["reporting_regimes"]["domestic_sec_periodic_reporting"], 1)
            self.assertEqual(report["reporting_regimes"]["foreign_private_issuer_sec"], 1)
            self.assertFalse(report["anti_hindsight"]["historical_backfill"])
            self.assertTrue((state_dir / STATE_FILENAME).exists())
            self.assertTrue((state_dir / REPORT_FILENAME).exists())

    def test_new_filing_after_boundary_creates_filing_and_exact_accession_facts_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            portfolio, analysis, universe = self._paths(root)
            state_dir = root / "state"
            run(
                state_dir,
                portfolio_path=portfolio,
                analysis_path=analysis,
                universe_path=universe,
                as_of=datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc),
                sec_client=self.client,
            )

            accession = "0000001001-26-000011"
            self.client.submission_payloads[1001] = submissions([
                filing(
                    form="10-Q",
                    accession=accession,
                    acceptance="20260821160000",
                    filing_date="2026-08-21",
                    report_date="2026-06-30",
                    primary="alpha-20260630.htm",
                ),
                filing(
                    form="10-K",
                    accession="0000001001-26-000001",
                    acceptance="20260819160000",
                    filing_date="2026-08-19",
                    report_date="2025-12-31",
                ),
            ])
            self.client.companyfact_payloads[1001] = companyfacts_for(accession)
            report = run(
                state_dir,
                portfolio_path=portfolio,
                analysis_path=analysis,
                universe_path=universe,
                as_of=datetime(2026, 8, 21, 23, 0, tzinfo=timezone.utc),
                sec_client=self.client,
            )
            self.assertGreaterEqual(report["sample"]["new_observations_this_run"], 5)
            self.assertEqual(report["sample"]["new_filing_observations_this_run"], 1)
            self.assertGreaterEqual(report["sample"]["new_structured_fact_observations_this_run"], 4)

            state = json.loads((state_dir / STATE_FILENAME).read_text(encoding="utf-8"))
            alpha_rows = [row for row in state["observations"] if row["entity"] == "alpha"]
            filing_rows = [row for row in alpha_rows if row["metric"] == "entity_primary_filing"]
            fact_rows = [row for row in alpha_rows if row["metric"].startswith("entity_primary_fact.")]
            self.assertEqual(len(filing_rows), 1)
            self.assertTrue(fact_rows)
            self.assertTrue(all(row["metadata"]["accession_number"] == accession for row in fact_rows))
            self.assertFalse(any(row["metadata"].get("historical_comparison_performed") for row in fact_rows))
            self.assertTrue(all(row["metadata"]["belief_polarity"] == "uninterpreted_primary_fact" for row in fact_rows))
            metrics = {row["metric"] for row in fact_rows}
            self.assertIn("entity_primary_fact.revenue", metrics)
            self.assertIn("entity_primary_fact.net_income", metrics)
            self.assertIn("entity_primary_fact.operating_income", metrics)
            self.assertIn("entity_primary_fact.capex", metrics)

    def test_same_accession_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            portfolio, analysis, universe = self._paths(root)
            state_dir = root / "state"
            run(
                state_dir,
                portfolio_path=portfolio,
                analysis_path=analysis,
                universe_path=universe,
                as_of=datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc),
                sec_client=self.client,
            )
            accession = "0000001001-26-000012"
            self.client.submission_payloads[1001] = submissions([
                filing(
                    form="10-Q",
                    accession=accession,
                    acceptance="20260821150000",
                    filing_date="2026-08-21",
                    report_date="2026-06-30",
                )
            ])
            self.client.companyfact_payloads[1001] = companyfacts_for(accession)
            first = run(
                state_dir,
                portfolio_path=portfolio,
                analysis_path=analysis,
                universe_path=universe,
                as_of=datetime(2026, 8, 21, 23, 0, tzinfo=timezone.utc),
                sec_client=self.client,
            )
            total = first["sample"]["prospective_observations_total"]
            second = run(
                state_dir,
                portfolio_path=portfolio,
                analysis_path=analysis,
                universe_path=universe,
                as_of=datetime(2026, 8, 22, 23, 0, tzinfo=timezone.utc),
                sec_client=self.client,
            )
            self.assertEqual(second["sample"]["prospective_observations_total"], total)
            self.assertEqual(second["sample"]["new_observations_this_run"], 0)

    def test_dormant_period_filings_are_not_backfilled_on_reactivation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            portfolio, analysis, universe = self._paths(root)
            state_dir = root / "state"
            run(
                state_dir,
                portfolio_path=portfolio,
                analysis_path=analysis,
                universe_path=universe,
                as_of=datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc),
                sec_client=self.client,
            )

            # Beta leaves BRACE candidates.
            self.analysis = {"candidates": []}
            analysis.write_text(json.dumps(self.analysis), encoding="utf-8")
            run(
                state_dir,
                portfolio_path=portfolio,
                analysis_path=analysis,
                universe_path=universe,
                as_of=datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc),
                sec_client=self.client,
            )

            dormant_accession = "0000002002-26-000020"
            self.client.submission_payloads[2002] = submissions([
                filing(
                    form="6-K",
                    accession=dormant_accession,
                    acceptance="20260822120000",
                    filing_date="2026-08-22",
                    report_date="2026-08-22",
                )
            ])

            # Reactivation on Aug 23 opens a fresh collection window; Aug 22 filing is cursor-only.
            self.analysis = {
                "candidates": [
                    {"instrument_id": "beta", "data_symbol": "BETA", "asset_type": "STOCK", "availability": "AVAILABLE", "active": True, "rank": 2}
                ]
            }
            analysis.write_text(json.dumps(self.analysis), encoding="utf-8")
            reactivated = run(
                state_dir,
                portfolio_path=portfolio,
                analysis_path=analysis,
                universe_path=universe,
                as_of=datetime(2026, 8, 23, 20, 0, tzinfo=timezone.utc),
                sec_client=self.client,
            )
            state = json.loads((state_dir / STATE_FILENAME).read_text(encoding="utf-8"))
            beta_filings = [
                row for row in state["observations"]
                if row["entity"] == "beta" and row["metric"] == "entity_primary_filing"
            ]
            self.assertEqual(beta_filings, [])
            self.assertTrue(reactivated["anti_hindsight"]["reactivation_opens_new_collection_window"])

            new_accession = "0000002002-26-000021"
            self.client.submission_payloads[2002] = submissions([
                filing(
                    form="6-K",
                    accession=new_accession,
                    acceptance="20260824120000",
                    filing_date="2026-08-24",
                    report_date="2026-08-24",
                ),
                filing(
                    form="6-K",
                    accession=dormant_accession,
                    acceptance="20260822120000",
                    filing_date="2026-08-22",
                    report_date="2026-08-22",
                ),
            ])
            self.client.companyfact_payloads[2002] = {"facts": {}}
            run(
                state_dir,
                portfolio_path=portfolio,
                analysis_path=analysis,
                universe_path=universe,
                as_of=datetime(2026, 8, 24, 23, 0, tzinfo=timezone.utc),
                sec_client=self.client,
            )
            state = json.loads((state_dir / STATE_FILENAME).read_text(encoding="utf-8"))
            beta_filings = [
                row for row in state["observations"]
                if row["entity"] == "beta" and row["metric"] == "entity_primary_filing"
            ]
            self.assertEqual(len(beta_filings), 1)
            self.assertEqual(beta_filings[0]["value"]["accession_number"], new_accession)

    def test_unresolved_ticker_fails_soft_without_synthetic_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            portfolio = root / "portfolio.json"
            analysis = root / "analysis.json"
            universe = root / "universe.json"
            portfolio.write_text(json.dumps({"positions": [{"id": "gamma", "market_symbol": "GAMMA", "asset_type": "Stock", "status": "active"}]}), encoding="utf-8")
            analysis.write_text(json.dumps({"candidates": []}), encoding="utf-8")
            universe.write_text(json.dumps(self.universe), encoding="utf-8")
            report = run(
                root / "state",
                portfolio_path=portfolio,
                analysis_path=analysis,
                universe_path=universe,
                as_of=datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc),
                sec_client=self.client,
            )
            self.assertEqual(report["sample"]["source_resolved_active_entities"], 0)
            self.assertEqual(report["sample"]["prospective_observations_total"], 0)
            self.assertTrue(any(row["code"] == "sec_ticker_unresolved" for row in report["source_issues"]))

    def test_companyfacts_map_to_dimensions_but_not_belief_polarity(self) -> None:
        payload = submissions([
            filing(
                form="10-Q",
                accession="0000001001-26-000099",
                acceptance="20260821160000",
                filing_date="2026-08-21",
                report_date="2026-06-30",
            )
        ])
        rows, _ = filing_records(
            entity_id="alpha",
            ticker="ALPHA",
            cik=1001,
            submissions=payload,
            client=self.client,
            boundary_at=datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc),
        )
        facts = companyfact_observations(rows[0], companyfacts_for("0000001001-26-000099"))
        revenue = next(row for row in facts if row.metric == "entity_primary_fact.revenue")
        self.assertEqual(revenue.metadata["dimension_candidates"], ["revenue_durability"])
        self.assertEqual(revenue.metadata["belief_polarity"], "uninterpreted_primary_fact")
        self.assertFalse(revenue.metadata["forecast_eligible"])

    def test_zero_influence_and_future_with_without_governance(self) -> None:
        self.assertTrue(all(value is False for value in safety_controls().values()))
        caps = capabilities()
        self.assertTrue(caps["primary_source_collection_enabled"])
        self.assertTrue(caps["reporting_regime_resolution_enabled"])
        self.assertFalse(caps["belief_evidence_interpretation_enabled"])
        self.assertFalse(caps["entity_forecast_capture_enabled"])
        self.assertFalse(caps["with_without_bridge_enabled"])
        gate = promotion_evidence_standard()
        self.assertTrue(gate["with_without_required"])
        self.assertTrue(gate["effective_n_required"])
        self.assertFalse(gate["automatic_promotion"])


if __name__ == "__main__":
    unittest.main()
