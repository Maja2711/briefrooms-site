from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.belief_adapter_contract import Observation
from scripts.brace_entity_evidence_interpretation import (
    CONTRACT_VERSION,
    REPORT_FILENAME,
    STATE_FILENAME,
    capabilities,
    promotion_evidence_standard,
    run,
    safety_controls,
)


def fact(
    *,
    entity: str = "alpha",
    metric: str,
    value: float,
    unit: str = "USD",
    observed_at: str,
    accession: str,
    fiscal_year: int,
    fiscal_period: str,
    period_start: str,
    period_end: str,
    tag: str | None = None,
    taxonomy: str = "us-gaap",
) -> dict[str, Any]:
    local_tag = tag or {
        "revenue": "RevenueFromContractWithCustomerExcludingAssessedTax",
        "diluted_eps": "EarningsPerShareDiluted",
        "net_income": "NetIncomeLoss",
        "operating_income": "OperatingIncomeLoss",
        "operating_cash_flow": "NetCashProvidedByUsedInOperatingActivities",
        "capex": "PaymentsToAcquirePropertyPlantAndEquipment",
        "net_interest_income": "InterestIncomeExpenseNet",
    }.get(metric, metric)
    observation = Observation.make(
        adapter="entity_primary_source_sec",
        metric=f"entity_primary_fact.{metric}",
        entity=entity,
        observed_at=observed_at,
        value=value,
        unit=unit,
        source="SEC EDGAR XBRL Company Facts",
        source_type="primary",
        source_ref=f"https://data.sec.gov/api/xbrl/companyfacts/{entity}#{accession}#{local_tag}#{period_end}",
        reliability=.995,
        independence_cluster=f"issuer-filing:{entity}:{accession}",
        tags=("entity", "primary_source", "xbrl_fact", metric),
        metadata={
            "ticker": entity.upper(),
            "form": "10-Q" if fiscal_period != "FY" else "10-K",
            "form_base": "10-Q" if fiscal_period != "FY" else "10-K",
            "accession_number": accession,
            "taxonomy": taxonomy,
            "tag": local_tag,
            "period_start": period_start,
            "period_end": period_end,
            "fiscal_year": fiscal_year,
            "fiscal_period": fiscal_period,
            "frame": f"CY{fiscal_year}{fiscal_period}",
            "dimension_candidates": [],
            "belief_polarity": "uninterpreted_primary_fact",
            "historical_comparison_performed": False,
            "forecast_eligible": False,
        },
    )
    payload = {
        "observation_id": observation.observation_id,
        "adapter": observation.adapter,
        "metric": observation.metric,
        "entity": observation.entity,
        "observed_at": observation.observed_at,
        "value": observation.value,
        "unit": observation.unit,
        "source": observation.source,
        "source_type": observation.source_type,
        "source_ref": observation.source_ref,
        "reliability": observation.reliability,
        "independence_cluster": observation.independence_cluster,
        "status": observation.status,
        "tags": list(observation.tags),
        "metadata": dict(observation.metadata),
    }
    return payload


def primary_state(
    observations: list[dict[str, Any]],
    *,
    window: str = "2026-01-01T00:00:00Z",
    status: str = "active",
    sector: str = "Information Technology",
    last_updated_at: str = "2027-08-21T00:00:00Z",
) -> dict[str, Any]:
    return {
        "schema_version": "brace-entity-primary-source-evidence-v1",
        "mode": "research_shadow",
        "last_updated_at": last_updated_at,
        "entities": {
            "alpha": {
                "entity_id": "alpha",
                "current_status": status,
                "current_window_opened_at": window if status == "active" else None,
                "sector": sector,
                "reporting_regime": "domestic_sec_periodic_reporting",
            }
        },
        "observations": observations,
    }


class BraceEntityEvidenceInterpretationTests(unittest.TestCase):
    def _run(self, root: Path, payload: dict[str, Any], when: datetime):
        primary = root / "primary.json"
        primary.write_text(json.dumps(payload), encoding="utf-8")
        state_dir = root / "state"
        return run(state_dir, primary_state_path=primary, as_of=when)

    def _state(self, root: Path) -> dict[str, Any]:
        return json.loads((root / "state" / STATE_FILENAME).read_text(encoding="utf-8"))

    def _decision(self, state: dict[str, Any], dimension: str) -> dict[str, Any]:
        matches = [row for row in state["interpretations"] if row["dimension"] == dimension]
        self.assertTrue(matches, dimension)
        return matches[-1]

    def test_first_run_is_activation_only_and_seeds_no_historical_evidence(self) -> None:
        baseline = fact(
            metric="revenue", value=100.0, observed_at="2026-08-20T21:00:00Z",
            accession="a-2026-q2", fiscal_year=2026, fiscal_period="Q2",
            period_start="2026-04-01", period_end="2026-06-30",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = self._run(root, primary_state([baseline]), datetime(2026, 8, 21, tzinfo=timezone.utc))
            state = self._state(root)
            self.assertTrue(report["sample"]["activation_only_this_run"])
            self.assertEqual(report["sample"]["new_evidence_this_run"], 0)
            self.assertEqual(state["interpretations"], [])
            self.assertEqual(state["evidence"], [])
            self.assertIn(baseline["observation_id"], state["seen_primary_observation_ids"])
            self.assertTrue(state["entities"]["alpha"]["baselines"])

    def test_revenue_yoy_growth_supports_revenue_durability(self) -> None:
        baseline = fact(
            metric="revenue", value=100.0, observed_at="2026-08-20T21:00:00Z",
            accession="a-2026-q2", fiscal_year=2026, fiscal_period="Q2",
            period_start="2026-04-01", period_end="2026-06-30",
        )
        current = fact(
            metric="revenue", value=108.0, observed_at="2027-08-20T21:00:00Z",
            accession="a-2027-q2", fiscal_year=2027, fiscal_period="Q2",
            period_start="2027-04-01", period_end="2027-06-30",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, primary_state([baseline]), datetime(2026, 8, 21, tzinfo=timezone.utc))
            report = self._run(root, primary_state([baseline, current]), datetime(2027, 8, 21, tzinfo=timezone.utc))
            state = self._state(root)
            decision = self._decision(state, "revenue_durability")
            self.assertEqual(decision["status"], "support")
            self.assertEqual(decision["direction"], 1)
            self.assertAlmostEqual(decision["delta"], .08)
            self.assertEqual(report["sample"]["new_evidence_this_run"], 1)
            evidence = state["evidence"][-1]
            self.assertEqual(evidence["belief_id"], "entity.alpha.revenue_durability")
            self.assertEqual(evidence["direction"], 1)
            self.assertEqual(evidence["source_type"], "derived")
            self.assertEqual(evidence["observed_at"], "2027-08-21T00:00:00Z")

    def test_revenue_yoy_decline_opposes_revenue_durability(self) -> None:
        baseline = fact(
            metric="revenue", value=100.0, observed_at="2026-08-20T21:00:00Z",
            accession="a-2026-q2", fiscal_year=2026, fiscal_period="Q2",
            period_start="2026-04-01", period_end="2026-06-30",
        )
        current = fact(
            metric="revenue", value=90.0, observed_at="2027-08-20T21:00:00Z",
            accession="a-2027-q2", fiscal_year=2027, fiscal_period="Q2",
            period_start="2027-04-01", period_end="2027-06-30",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, primary_state([baseline]), datetime(2026, 8, 21, tzinfo=timezone.utc))
            self._run(root, primary_state([baseline, current]), datetime(2027, 8, 21, tzinfo=timezone.utc))
            decision = self._decision(self._state(root), "revenue_durability")
            self.assertEqual(decision["status"], "oppose")
            self.assertEqual(decision["direction"], -1)

    def test_small_change_is_neutral_and_creates_no_evidence(self) -> None:
        baseline = fact(
            metric="revenue", value=100.0, observed_at="2026-08-20T21:00:00Z",
            accession="a-2026-q2", fiscal_year=2026, fiscal_period="Q2",
            period_start="2026-04-01", period_end="2026-06-30",
        )
        current = fact(
            metric="revenue", value=101.0, observed_at="2027-08-20T21:00:00Z",
            accession="a-2027-q2", fiscal_year=2027, fiscal_period="Q2",
            period_start="2027-04-01", period_end="2027-06-30",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, primary_state([baseline]), datetime(2026, 8, 21, tzinfo=timezone.utc))
            report = self._run(root, primary_state([baseline, current]), datetime(2027, 8, 21, tzinfo=timezone.utc))
            decision = self._decision(self._state(root), "revenue_durability")
            self.assertEqual(decision["status"], "neutral")
            self.assertIsNone(decision["direction"])
            self.assertEqual(report["sample"]["new_evidence_this_run"], 0)

    def test_earnings_momentum_prefers_eps_over_net_income(self) -> None:
        baseline_eps = fact(
            metric="diluted_eps", value=2.0, unit="USD/shares", observed_at="2026-08-20T21:00:00Z",
            accession="a-2026-q2", fiscal_year=2026, fiscal_period="Q2",
            period_start="2026-04-01", period_end="2026-06-30",
        )
        baseline_ni = fact(
            metric="net_income", value=100.0, observed_at="2026-08-20T21:00:00Z",
            accession="a-2026-q2", fiscal_year=2026, fiscal_period="Q2",
            period_start="2026-04-01", period_end="2026-06-30",
        )
        current_eps = fact(
            metric="diluted_eps", value=2.3, unit="USD/shares", observed_at="2027-08-20T21:00:00Z",
            accession="a-2027-q2", fiscal_year=2027, fiscal_period="Q2",
            period_start="2027-04-01", period_end="2027-06-30",
        )
        current_ni = fact(
            metric="net_income", value=70.0, observed_at="2027-08-20T21:00:00Z",
            accession="a-2027-q2", fiscal_year=2027, fiscal_period="Q2",
            period_start="2027-04-01", period_end="2027-06-30",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, primary_state([baseline_eps, baseline_ni]), datetime(2026, 8, 21, tzinfo=timezone.utc))
            self._run(root, primary_state([baseline_eps, baseline_ni, current_eps, current_ni]), datetime(2027, 8, 21, tzinfo=timezone.utc))
            decision = self._decision(self._state(root), "earnings_momentum")
            self.assertEqual(decision["status"], "support")
            self.assertEqual(decision["current_primary_observation_ids"], [current_eps["observation_id"]])

    def test_margin_contract_uses_margin_not_naive_operating_income_growth(self) -> None:
        baseline_revenue = fact(
            metric="revenue", value=100.0, observed_at="2026-08-20T21:00:00Z",
            accession="a-2026-q2", fiscal_year=2026, fiscal_period="Q2",
            period_start="2026-04-01", period_end="2026-06-30",
        )
        baseline_op = fact(
            metric="operating_income", value=20.0, observed_at="2026-08-20T21:00:00Z",
            accession="a-2026-q2", fiscal_year=2026, fiscal_period="Q2",
            period_start="2026-04-01", period_end="2026-06-30",
        )
        current_revenue = fact(
            metric="revenue", value=140.0, observed_at="2027-08-20T21:00:00Z",
            accession="a-2027-q2", fiscal_year=2027, fiscal_period="Q2",
            period_start="2027-04-01", period_end="2027-06-30",
        )
        current_op = fact(
            metric="operating_income", value=21.0, observed_at="2027-08-20T21:00:00Z",
            accession="a-2027-q2", fiscal_year=2027, fiscal_period="Q2",
            period_start="2027-04-01", period_end="2027-06-30",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, primary_state([baseline_revenue, baseline_op]), datetime(2026, 8, 21, tzinfo=timezone.utc))
            self._run(root, primary_state([baseline_revenue, baseline_op, current_revenue, current_op]), datetime(2027, 8, 21, tzinfo=timezone.utc))
            decision = self._decision(self._state(root), "margin_trajectory")
            self.assertEqual(decision["status"], "oppose")
            self.assertLess(decision["delta"], 0)
            self.assertAlmostEqual(decision["baseline_value"], .20)
            self.assertAlmostEqual(decision["current_value"], .15)

    def test_mismatched_fiscal_period_does_not_create_yoy_evidence(self) -> None:
        baseline = fact(
            metric="revenue", value=100.0, observed_at="2026-08-20T21:00:00Z",
            accession="a-2026-q2", fiscal_year=2026, fiscal_period="Q2",
            period_start="2026-04-01", period_end="2026-06-30",
        )
        current = fact(
            metric="revenue", value=130.0, observed_at="2027-11-20T21:00:00Z",
            accession="a-2027-q3", fiscal_year=2027, fiscal_period="Q3",
            period_start="2027-07-01", period_end="2027-09-30",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, primary_state([baseline]), datetime(2026, 8, 21, tzinfo=timezone.utc))
            report = self._run(root, primary_state([baseline, current]), datetime(2027, 11, 21, tzinfo=timezone.utc))
            decision = self._decision(self._state(root), "revenue_durability")
            self.assertEqual(decision["status"], "baseline_only")
            self.assertEqual(report["sample"]["new_evidence_this_run"], 0)

    def test_same_period_amendment_refreshes_baseline_without_momentum_signal(self) -> None:
        baseline = fact(
            metric="revenue", value=100.0, observed_at="2026-08-20T21:00:00Z",
            accession="a-2026-q2", fiscal_year=2026, fiscal_period="Q2",
            period_start="2026-04-01", period_end="2026-06-30",
        )
        amended = fact(
            metric="revenue", value=103.0, observed_at="2026-09-01T21:00:00Z",
            accession="a-2026-q2-amend", fiscal_year=2026, fiscal_period="Q2",
            period_start="2026-04-01", period_end="2026-06-30",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, primary_state([baseline]), datetime(2026, 8, 21, tzinfo=timezone.utc))
            report = self._run(root, primary_state([baseline, amended]), datetime(2026, 9, 2, tzinfo=timezone.utc))
            decision = self._decision(self._state(root), "revenue_durability")
            self.assertEqual(decision["status"], "amendment_baseline_refresh")
            self.assertEqual(report["sample"]["new_evidence_this_run"], 0)

    def test_source_window_reset_prevents_cross_dormancy_comparison(self) -> None:
        baseline = fact(
            metric="revenue", value=100.0, observed_at="2026-08-20T21:00:00Z",
            accession="a-2026-q2", fiscal_year=2026, fiscal_period="Q2",
            period_start="2026-04-01", period_end="2026-06-30",
        )
        current = fact(
            metric="revenue", value=130.0, observed_at="2027-08-20T21:00:00Z",
            accession="a-2027-q2", fiscal_year=2027, fiscal_period="Q2",
            period_start="2027-04-01", period_end="2027-06-30",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, primary_state([baseline], window="2026-01-01T00:00:00Z"), datetime(2026, 8, 21, tzinfo=timezone.utc))
            report = self._run(
                root,
                primary_state([baseline, current], window="2027-08-01T00:00:00Z"),
                datetime(2027, 8, 21, tzinfo=timezone.utc),
            )
            decision = self._decision(self._state(root), "revenue_durability")
            self.assertEqual(decision["status"], "baseline_only")
            self.assertEqual(report["sample"]["new_evidence_this_run"], 0)

    def test_capex_is_not_polarized_by_more_equals_better_rule(self) -> None:
        capex = fact(
            metric="capex", value=500.0, observed_at="2027-08-20T21:00:00Z",
            accession="a-2027-q2", fiscal_year=2027, fiscal_period="Q2",
            period_start="2027-01-01", period_end="2027-06-30",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, primary_state([]), datetime(2026, 8, 21, tzinfo=timezone.utc))
            self._run(root, primary_state([capex]), datetime(2027, 8, 21, tzinfo=timezone.utc))
            state = self._state(root)
            self.assertFalse(any(row["belief_id"].endswith(".capex_returns") for row in state["evidence"]))
            self.assertFalse(any(row["dimension"] == "capex_returns" for row in state["interpretations"]))

    def test_financials_nii_contract_is_enabled_only_for_financials(self) -> None:
        baseline = fact(
            metric="net_interest_income", value=100.0, observed_at="2026-08-20T21:00:00Z",
            accession="a-2026-q2", fiscal_year=2026, fiscal_period="Q2",
            period_start="2026-04-01", period_end="2026-06-30",
        )
        current = fact(
            metric="net_interest_income", value=110.0, observed_at="2027-08-20T21:00:00Z",
            accession="a-2027-q2", fiscal_year=2027, fiscal_period="Q2",
            period_start="2027-04-01", period_end="2027-06-30",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, primary_state([baseline], sector="Financials"), datetime(2026, 8, 21, tzinfo=timezone.utc))
            self._run(root, primary_state([baseline, current], sector="Financials"), datetime(2027, 8, 21, tzinfo=timezone.utc))
            decision = self._decision(self._state(root), "net_interest_income_durability")
            self.assertEqual(decision["status"], "support")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, primary_state([baseline], sector="Information Technology"), datetime(2026, 8, 21, tzinfo=timezone.utc))
            self._run(root, primary_state([baseline, current], sector="Information Technology"), datetime(2027, 8, 21, tzinfo=timezone.utc))
            state = self._state(root)
            self.assertFalse(any(row["dimension"] == "net_interest_income_durability" for row in state["interpretations"]))

    def test_rerun_is_idempotent(self) -> None:
        baseline = fact(
            metric="revenue", value=100.0, observed_at="2026-08-20T21:00:00Z",
            accession="a-2026-q2", fiscal_year=2026, fiscal_period="Q2",
            period_start="2026-04-01", period_end="2026-06-30",
        )
        current = fact(
            metric="revenue", value=108.0, observed_at="2027-08-20T21:00:00Z",
            accession="a-2027-q2", fiscal_year=2027, fiscal_period="Q2",
            period_start="2027-04-01", period_end="2027-06-30",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, primary_state([baseline]), datetime(2026, 8, 21, tzinfo=timezone.utc))
            first = self._run(root, primary_state([baseline, current]), datetime(2027, 8, 21, tzinfo=timezone.utc))
            state1 = self._state(root)
            second = self._run(root, primary_state([baseline, current]), datetime(2027, 8, 21, tzinfo=timezone.utc))
            state2 = self._state(root)
            self.assertGreater(first["sample"]["new_interpretations_this_run"], 0)
            self.assertEqual(second["sample"]["new_interpretations_this_run"], 0)
            self.assertEqual(second["sample"]["new_evidence_this_run"], 0)
            self.assertEqual(state1["interpretations"], state2["interpretations"])
            self.assertEqual(state1["evidence"], state2["evidence"])

    def test_zero_influence_and_promotion_governance(self) -> None:
        self.assertTrue(all(value is False for value in safety_controls().values()))
        caps = capabilities()
        self.assertTrue(caps["deterministic_entity_interpretation_enabled"])
        self.assertTrue(caps["support_oppose_neutral_classification_enabled"])
        self.assertTrue(caps["belief_compatible_evidence_materialization_enabled"])
        self.assertFalse(caps["llm_interpretation_enabled"])
        self.assertFalse(caps["belief_core_state_update_enabled"])
        self.assertFalse(caps["entity_forecast_capture_enabled"])
        self.assertFalse(caps["with_without_bridge_enabled"])
        gate = promotion_evidence_standard()
        self.assertTrue(gate["with_without_required"])
        self.assertTrue(gate["effective_n_required"])
        self.assertFalse(gate["automatic_promotion"])
        self.assertEqual(CONTRACT_VERSION, "entity-interpretation-contracts-v1")

    def test_report_is_written_and_states_evidence_not_applied_to_belief_core(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = self._run(root, primary_state([]), datetime(2026, 8, 21, tzinfo=timezone.utc))
            self.assertTrue((root / "state" / REPORT_FILENAME).exists())
            self.assertTrue(report["interpretation_boundary"]["evidence_materialized_but_not_applied_to_belief_core"])
            self.assertFalse(report["interpretation_boundary"]["entity_forecasts_created"])
            self.assertFalse(report["contracts"]["enabled"][0]["materiality_band"] < 0)


if __name__ == "__main__":
    unittest.main()
