from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import belief_epistemic_causal_graph as pr19_base
from scripts.belief_epistemic_causal_graph_semantic import run as run_pr19_semantic
from scripts.brace_company_entity_framework import dimension_registry_for, entity_belief_definitions
from scripts import brace_entity_evidence_interpretation as pr14_base
from scripts.brace_entity_evidence_interpretation_semantic import run as run_pr14_semantic
from scripts import brace_entity_belief_state_forecast as pr15_base
from scripts.brace_entity_belief_state_forecast_semantic import run as run_pr15_semantic
from scripts.entity_semantic_eligibility import (
    ARCHETYPE_BANK,
    ARCHETYPE_FINANCIAL_DATA_RATINGS,
    ARCHETYPE_FINANCIALS_UNRESOLVED,
    ARCHETYPE_PAYMENT_NETWORK,
    STATUS_ELIGIBLE,
    STATUS_RESOLVED_MISMATCH,
    STATUS_UNRESOLVED_FAIL_CLOSED,
    dimension_eligibility,
    is_resolved_semantic_mismatch,
    semantic_profile,
)

UTC = timezone.utc
NII = "net_interest_income_durability"


def z(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


class EntitySemanticEligibilityTests(unittest.TestCase):
    def test_business_archetypes_come_from_exposure_key_not_financials_sector(self) -> None:
        bank = semantic_profile({"sector": "Financials", "exposure_key": "diversified_banking"})
        visa = semantic_profile({"sector": "Financials", "exposure_key": "payments_network"})
        spgi = semantic_profile({"sector": "Financials", "exposure_key": "financial_data_ratings"})
        unresolved = semantic_profile({"sector": "Financials"})

        self.assertEqual(bank["entity_archetype"], ARCHETYPE_BANK)
        self.assertEqual(visa["entity_archetype"], ARCHETYPE_PAYMENT_NETWORK)
        self.assertEqual(spgi["entity_archetype"], ARCHETYPE_FINANCIAL_DATA_RATINGS)
        self.assertEqual(unresolved["entity_archetype"], ARCHETYPE_FINANCIALS_UNRESOLVED)
        self.assertTrue(bank["bank_specific_dimensions_eligible"])
        self.assertFalse(visa["bank_specific_dimensions_eligible"])
        self.assertFalse(spgi["bank_specific_dimensions_eligible"])
        self.assertFalse(unresolved["bank_specific_dimensions_eligible"])

    def test_unresolved_fails_closed_but_is_not_irreversibly_deprecated(self) -> None:
        bank = dimension_eligibility({"sector": "Financials", "exposure_key": "diversified_banking"}, NII)
        visa = dimension_eligibility({"sector": "Financials", "exposure_key": "payments_network"}, NII)
        unresolved = dimension_eligibility({"sector": "Financials"}, NII)
        self.assertEqual(bank["status"], STATUS_ELIGIBLE)
        self.assertEqual(visa["status"], STATUS_RESOLVED_MISMATCH)
        self.assertEqual(unresolved["status"], STATUS_UNRESOLVED_FAIL_CLOSED)
        self.assertTrue(is_resolved_semantic_mismatch({"sector": "Financials", "exposure_key": "payments_network"}, NII))
        self.assertFalse(is_resolved_semantic_mismatch({"sector": "Financials"}, NII))

    def test_pr12_materializes_bank_specific_dimensions_only_for_bank_archetype(self) -> None:
        bank = {"entity_id": "jpm", "asset_type": "Stock", "sector": "Financials", "exposure_key": "diversified_banking"}
        visa = {"entity_id": "visa", "asset_type": "Stock", "sector": "Financials", "exposure_key": "payments_network"}
        spgi = {"entity_id": "spgi", "asset_type": "Stock", "sector": "Financials", "exposure_key": "financial_data_ratings"}

        bank_dims = {row["dimension"] for row in dimension_registry_for(bank)}
        visa_dims = {row["dimension"] for row in dimension_registry_for(visa)}
        spgi_dims = {row["dimension"] for row in dimension_registry_for(spgi)}
        self.assertIn(NII, bank_dims)
        self.assertNotIn(NII, visa_dims)
        self.assertNotIn(NII, spgi_dims)
        self.assertIn("revenue_durability", visa_dims)
        self.assertIn("margin_trajectory", spgi_dims)

        visa_beliefs = {row["belief_id"] for row in entity_belief_definitions(visa)}
        self.assertNotIn("entity.visa.net_interest_income_durability", visa_beliefs)

    def test_pr14_preserves_old_visa_nii_records_and_appends_deprecation(self) -> None:
        now = datetime(2026, 8, 20, 14, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "pr14"
            state_dir.mkdir()
            universe = root / "universe.json"
            universe.write_text(json.dumps({"instruments": [
                {"instrument_id": "visa", "asset_type": "Stock", "sector": "Financials", "exposure_key": "payments_network"}
            ]}), encoding="utf-8")
            primary = root / "primary.json"
            primary.write_text(json.dumps({
                "entities": {
                    "visa": {
                        "entity_id": "visa", "current_status": "active", "sector": "Financials",
                        "current_window_opened_at": z(now - timedelta(days=10)),
                        "reporting_regime": "domestic_sec_periodic_reporting",
                    }
                },
                "observations": [],
                "last_updated_at": z(now),
            }), encoding="utf-8")

            old = pr14_base.empty_state()
            old["first_run_at"] = z(now - timedelta(days=5))
            old["last_run_at"] = z(now - timedelta(days=1))
            old["entities"] = {"visa": {"entity_id": "visa", "sector": "Financials", "current_status": "active", "baselines": {}}}
            old["interpretations"] = [{
                "interpretation_id": "legacy-visa-nii-int", "belief_id": "entity.visa.net_interest_income_durability",
                "entity_id": "visa", "dimension": NII, "status": "support", "computed_at": z(now - timedelta(days=2)),
            }]
            old["evidence"] = [{
                "evidence_id": "legacy-visa-nii-ev", "belief_id": "entity.visa.net_interest_income_durability",
            }]
            (state_dir / pr14_base.STATE_FILENAME).write_text(json.dumps(old), encoding="utf-8")

            first = run_pr14_semantic(state_dir, primary_state_path=primary, universe_path=universe, as_of=now)
            migrated = json.loads((state_dir / pr14_base.STATE_FILENAME).read_text())
            dep = migrated["semantic_deprecations"]["entity.visa.net_interest_income_durability"]
            self.assertEqual(dep["status"], "DEPRECATED_SEMANTIC_MISMATCH")
            self.assertEqual(dep["entity_archetype"], ARCHETYPE_PAYMENT_NETWORK)
            self.assertTrue(dep["historical_records_preserved"])
            self.assertIn("legacy-visa-nii-int", dep["interpretation_ids"])
            self.assertIn("legacy-visa-nii-ev", dep["evidence_ids"])
            self.assertEqual(migrated["entities"]["visa"]["sector"], "Financials")
            self.assertEqual(migrated["entities"]["visa"]["entity_archetype"], ARCHETYPE_PAYMENT_NETWORK)
            self.assertIn("entity.visa.net_interest_income_durability", migrated["current_semantic_ineligible_belief_ids"])
            self.assertEqual(len(first["semantic_deprecations"]), 1)

            frozen = json.dumps(dep, sort_keys=True)
            run_pr14_semantic(state_dir, primary_state_path=primary, universe_path=universe, as_of=now + timedelta(hours=1))
            rerun = json.loads((state_dir / pr14_base.STATE_FILENAME).read_text())
            self.assertEqual(json.dumps(rerun["semantic_deprecations"]["entity.visa.net_interest_income_durability"], sort_keys=True), frozen)
            self.assertTrue(any(row.get("interpretation_id") == "legacy-visa-nii-int" for row in rerun["interpretations"]))
            self.assertTrue(any(row.get("evidence_id") == "legacy-visa-nii-ev" for row in rerun["evidence"]))

    def test_pr14_unresolved_financials_fails_closed_without_permanent_deprecation(self) -> None:
        now = datetime(2026, 8, 20, 14, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state_dir = root / "pr14"; state_dir.mkdir()
            universe = root / "universe.json"
            universe.write_text(json.dumps({"instruments": [{"instrument_id": "mystery", "asset_type": "Stock", "sector": "Financials"}]}), encoding="utf-8")
            primary = root / "primary.json"
            primary.write_text(json.dumps({
                "entities": {"mystery": {"entity_id": "mystery", "current_status": "active", "sector": "Financials", "current_window_opened_at": z(now - timedelta(days=2))}},
                "observations": [], "last_updated_at": z(now),
            }), encoding="utf-8")
            report = run_pr14_semantic(state_dir, primary_state_path=primary, universe_path=universe, as_of=now)
            state = json.loads((state_dir / pr14_base.STATE_FILENAME).read_text())
            self.assertEqual(state["semantic_deprecations"], {})
            self.assertIn("entity.mystery.net_interest_income_durability", state["current_semantic_ineligible_belief_ids"])
            self.assertEqual(report["semantic_eligibility"]["unresolved_financials_fail_closed_without_permanent_deprecation"], True)

    def test_pr15_deprecates_existing_visa_nii_definition_and_closes_open_forecast_without_deleting_it(self) -> None:
        t0 = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)
        now = t0 + timedelta(days=1)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state_dir = root / "pr15"; state_dir.mkdir()
            source = root / "pr14.json"
            source.write_text(json.dumps({
                "schema_version": pr15_base.PR14_SCHEMA_VERSION,
                "contract_version": pr15_base.PR14_CONTRACT_VERSION,
                "mode": "research_shadow",
                "last_run_at": z(now),
                "entities": {
                    "visa": {
                        "entity_id": "visa", "current_status": "active", "sector": "Financials",
                        "exposure_key": "payments_network", "entity_archetype": "payment_network",
                        "semantic_eligibility_contract_version": "entity-semantic-eligibility-v1",
                        "reporting_regime": "domestic_sec_periodic_reporting", "source_window_opened_at": z(t0),
                    }
                },
                "interpretations": [], "evidence": [], "derived_observations": [], "seen_primary_observation_ids": [],
            }), encoding="utf-8")

            runtime = pr15_base.empty_state(); runtime["first_run_at"] = z(t0); runtime["last_run_at"] = z(t0)
            (state_dir / pr15_base.STATE_FILENAME).write_text(json.dumps(runtime), encoding="utf-8")
            core = pr15_base.BeliefCore(state_dir / pr15_base.BELIEF_CORE_DIRNAME)
            definition = pr15_base._definition("visa", {"sector": "Financials"}, NII)
            core.register_beliefs([definition]); core.recompute(t0)
            frozen = core.capture_forecast(
                "entity.visa.net_interest_income_durability",
                as_of=t0,
                target_at=t0 + timedelta(days=120),
                regime=pr15_base.FORECAST_REGIME,
                metadata={"contract_version": pr15_base.CONTRACT_VERSION, "dimension": NII, "sector": "Financials"},
            )

            report = run_pr15_semantic(state_dir, interpretation_state_path=source, as_of=now)
            migrated_runtime = json.loads((state_dir / pr15_base.STATE_FILENAME).read_text())
            core_after = json.loads((state_dir / pr15_base.BELIEF_CORE_DIRNAME / "state.json").read_text())
            belief_id = "entity.visa.net_interest_income_durability"
            self.assertIn(belief_id, migrated_runtime["semantic_deprecations"])
            self.assertEqual(migrated_runtime["forecast_closures"][frozen.forecast_id]["status"], "semantic_deprecated")
            self.assertTrue(any(row.get("forecast_id") == frozen.forecast_id for row in core_after["forecasts"]))
            self.assertTrue(any(row.get("belief_id") == belief_id for row in core_after["definitions"]))
            self.assertEqual(report["semantic_deprecations"][0]["status"], "DEPRECATED_SEMANTIC_MISMATCH")
            self.assertFalse(any(row.get("belief_id") == belief_id for row in report["active_forecasts"]))

    def test_pr19_creates_new_graph_without_deprecated_visa_belief_and_keeps_old_snapshot(self) -> None:
        t0 = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)
        t1 = t0 + timedelta(hours=1)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state_dir = root / "pr19"
            source = root / "pr15.json"
            legacy_report = {
                "contract_version": pr19_base.PR15_CONTRACT_VERSION,
                "mode": "research_shadow",
                "active_decision_influence": False,
                "belief_states": [
                    {"belief_id": "entity.visa.net_interest_income_durability"},
                    {"belief_id": "entity.jpm.net_interest_income_durability"},
                ],
                "forecasts": [],
            }
            source.write_text(json.dumps(legacy_report), encoding="utf-8")
            first = pr19_base.run(state_dir, pr15_report_path=source, as_of=t0)
            old_snapshot_id = first["graph_runtime"]["latest_graph_snapshot_id"]
            old_state = json.loads((state_dir / pr19_base.STATE_FILENAME).read_text())
            self.assertIn("entity.visa.net_interest_income_durability", old_state["graph_snapshots"][old_snapshot_id]["contract_index"])

            migrated_report = dict(legacy_report)
            migrated_report["semantic_eligibility"] = {
                "current_semantic_ineligible_belief_ids": ["entity.visa.net_interest_income_durability"]
            }
            migrated_report["semantic_deprecations"] = [{
                "belief_id": "entity.visa.net_interest_income_durability",
                "entity_id": "visa", "dimension": NII, "entity_archetype": "payment_network",
                "exposure_key": "payments_network", "effective_at": z(t1), "immutable_sha256": "source-dep-sha",
            }]
            source.write_text(json.dumps(migrated_report), encoding="utf-8")
            second = run_pr19_semantic(state_dir, pr15_report_path=source, as_of=t1)
            new_snapshot_id = second["graph_runtime"]["latest_graph_snapshot_id"]
            state = json.loads((state_dir / pr19_base.STATE_FILENAME).read_text())
            self.assertNotEqual(old_snapshot_id, new_snapshot_id)
            self.assertIn(old_snapshot_id, state["graph_snapshots"])
            self.assertIn("entity.visa.net_interest_income_durability", state["graph_snapshots"][old_snapshot_id]["contract_index"])
            self.assertNotIn("entity.visa.net_interest_income_durability", state["graph_snapshots"][new_snapshot_id]["contract_index"])
            self.assertIn("entity.jpm.net_interest_income_durability", state["graph_snapshots"][new_snapshot_id]["contract_index"])
            migration = state["semantic_contract_deprecations"]["entity.visa.net_interest_income_durability"]
            self.assertIn(old_snapshot_id, migration["historical_graph_snapshot_ids"])
            self.assertTrue(migration["historical_graph_snapshots_preserved"])


if __name__ == "__main__":
    unittest.main()
