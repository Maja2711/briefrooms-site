from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from scripts.investment_semantics_world_state import (
    BROAD_MARKET_IDS,
    CONTRACT_VERSION,
    REPORT_FILENAME,
    SECTOR_FACTOR_IDS,
    SEMANTIC_CONTRACT_VERSION,
    SOURCE_FIELD_SEMANTICS,
    STATE_FILENAME,
    build_world_state_snapshot,
    capabilities,
    run,
    safety_controls,
    semantic_contract,
    semantic_envelope,
)


def z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def broad_report(generated_at: datetime, *, shift: float = 0.0) -> dict[str, Any]:
    current = {}
    for idx, belief_id in enumerate(BROAD_MARKET_IDS):
        current[belief_id] = {
            "probability": min(0.95, max(0.05, 0.50 + shift + idx * 0.02)),
            "confidence": 0.60 + idx * 0.03,
            "audit_status": "ok",
            "independent_clusters": 2,
            "source_diversity": 2,
            "contradiction_score": 0.1,
        }
    return {
        "schema_version": "brace-broad-market-belief-report-v1",
        "report_name": "BRACE_BROAD_MARKET_BELIEF_REPORT",
        "generated_at": z(generated_at),
        "mode": "research_shadow",
        "active_decision_influence": False,
        "current_beliefs": current,
        "safety_controls": {"active_decision_influence": False},
    }


def sector_report(generated_at: datetime, *, shift: float = 0.0) -> dict[str, Any]:
    rows = []
    for idx, belief_id in enumerate(SECTOR_FACTOR_IDS):
        layer = "factor" if belief_id.startswith("factor.") else "sector"
        rows.append({
            "belief_id": belief_id,
            "layer": layer,
            "label": belief_id,
            "numerator": "NUM",
            "denominator": "DEN",
            "probability": min(0.95, max(0.05, 0.45 + shift + idx * 0.015)),
            "confidence": 0.55 + min(idx, 8) * 0.02,
            "audit_status": "ok",
            "data_available": True,
        })
    return {
        "report_version": "brace-sector-factor-belief-report-v1",
        "schema_version": "brace-sector-factor-belief-v1",
        "generated_at": z(generated_at),
        "mode": "research_shadow",
        "active_decision_influence": False,
        "current_beliefs": rows,
        "safety_controls": {"active_decision_influence": False},
    }


def core_state(forecasts: list[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "mode": "shadow",
        "forecasts": [dict(row) for row in (forecasts or [])],
        "verifications": [],
    }


def forecast(forecast_id: str, forecast_at: datetime) -> dict[str, Any]:
    return {
        "forecast_id": forecast_id,
        "forecast_set_id": f"set-{forecast_id}",
        "belief_id": "entity.alpha.revenue_durability",
        "predicted_probability": 0.67,
        "forecast_confidence": 0.54,
        "forecast_at": z(forecast_at),
        "target_at": z(forecast_at + timedelta(days=120)),
        "horizon_hours": 2880.0,
        "domain": "entity_fundamentals",
        "entity": "alpha",
        "regime": "entity_fundamental_reporting_v1",
        "alternative_group": None,
        "outcome_rule": "fixture",
        "representative_evidence_ids": [],
        "evidence_snapshot": [],
        "metadata": {
            "historical_backfill": False,
            "engine_influence": False,
            "promotion_authority": False,
        },
    }


class InvestmentSemanticsWorldStateTests(unittest.TestCase):
    def _write_inputs(
        self,
        root: Path,
        *,
        broad_at: datetime,
        sector_at: datetime,
        forecasts: list[Mapping[str, Any]] | None = None,
        shift: float = 0.0,
    ) -> tuple[Path, Path, Path]:
        broad_path = root / "broad.json"
        sector_path = root / "sector.json"
        core_path = root / "core.json"
        broad_path.write_text(json.dumps(broad_report(broad_at, shift=shift)), encoding="utf-8")
        sector_path.write_text(json.dumps(sector_report(sector_at, shift=shift)), encoding="utf-8")
        core_path.write_text(json.dumps(core_state(forecasts)), encoding="utf-8")
        return broad_path, sector_path, core_path

    def test_semantic_registry_explicitly_separates_existing_confidence_fields(self) -> None:
        registry = semantic_contract()
        self.assertEqual(registry["contract_version"], SEMANTIC_CONTRACT_VERSION)
        self.assertEqual(
            SOURCE_FIELD_SEMANTICS["investments_weekly_v2.confidence"]["semantic_type"],
            "heuristic_signal_strength",
        )
        self.assertEqual(
            SOURCE_FIELD_SEMANTICS["brace_portfolio.confidence_score"]["semantic_type"],
            "data_quality_confidence",
        )
        self.assertEqual(
            SOURCE_FIELD_SEMANTICS["brace_portfolio.probability_of_reaching_target"]["semantic_type"],
            "model_probability",
        )
        self.assertEqual(
            SOURCE_FIELD_SEMANTICS["belief_core.belief_state.probability"]["semantic_type"],
            "belief_probability",
        )
        self.assertEqual(
            SOURCE_FIELD_SEMANTICS["belief_core.belief_state.confidence"]["semantic_type"],
            "belief_confidence",
        )
        self.assertTrue(registry["rules"]["field_name_confidence_does_not_define_semantics"])

    def test_semantic_envelope_never_allows_uncalibrated_value_to_claim_calibrated_probability(self) -> None:
        now = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            semantic_envelope(
                0.72,
                semantic_type="calibrated_probability",
                calibration_status="uncalibrated",
                source_system="fixture",
                source_field="p",
                as_of=z(now),
            )
        row = semantic_envelope(
            0.72,
            semantic_type="heuristic_signal_strength",
            calibration_status="not_applicable",
            source_system="fixture",
            source_field="confidence",
            as_of=z(now),
        )
        self.assertFalse(row["probability_like"])

    def test_world_state_id_is_deterministic_for_same_source_information(self) -> None:
        t0 = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)
        broad = broad_report(t0)
        sector = sector_report(t0 + timedelta(minutes=10))
        a = build_world_state_snapshot(broad, sector, created_at=t0 + timedelta(hours=1))
        b = build_world_state_snapshot(broad, sector, created_at=t0 + timedelta(hours=2))
        self.assertEqual(a["world_state_id"], b["world_state_id"])
        self.assertEqual(a["context_as_of"], z(t0 + timedelta(minutes=10)))
        self.assertFalse(a["direct_market_observables"]["included"])
        self.assertEqual(len(a["belief_context"]["broad_market"]), 4)
        self.assertEqual(len(a["belief_context"]["sector_factor"]), 11)

    def test_future_dated_source_report_fails_closed(self) -> None:
        now = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            build_world_state_snapshot(
                broad_report(now + timedelta(minutes=1)),
                sector_report(now),
                created_at=now,
            )

    def test_first_run_is_activation_only_for_existing_pr15_forecasts(self) -> None:
        t0 = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        old_forecast = forecast("f-old", t0 - timedelta(hours=1))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            broad_path, sector_path, core_path = self._write_inputs(
                root,
                broad_at=t0 - timedelta(hours=2),
                sector_at=t0 - timedelta(hours=1),
                forecasts=[old_forecast],
            )
            report = run(
                root / "state",
                broad_market_report_path=broad_path,
                sector_factor_report_path=sector_path,
                pr15_core_state_path=core_path,
                as_of=t0,
            )
            self.assertEqual(report["world_state_contract"]["snapshot_count"], 1)
            self.assertEqual(report["forecast_context_contract"]["bindings_total"], 0)
            self.assertEqual(report["forecast_context_contract"]["pre_activation_forecasts_total"], 1)
            state = json.loads((root / "state" / STATE_FILENAME).read_text())
            self.assertIn("f-old", state["pre_activation_pr15_forecast_ids"])
            self.assertNotIn("f-old", state["forecast_context_bindings"])

    def test_new_post_activation_forecast_binds_only_to_preexisting_world_state(self) -> None:
        t0 = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(hours=2)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            broad_path, sector_path, core_path = self._write_inputs(
                root,
                broad_at=t0 - timedelta(hours=1),
                sector_at=t0 - timedelta(minutes=30),
            )
            state_dir = root / "state"
            first = run(
                state_dir,
                broad_market_report_path=broad_path,
                sector_factor_report_path=sector_path,
                pr15_core_state_path=core_path,
                as_of=t0,
            )
            world_state_id = first["world_state_contract"]["latest_world_state_id"]
            core_path.write_text(json.dumps(core_state([forecast("f-new", t1)])), encoding="utf-8")
            second = run(
                state_dir,
                broad_market_report_path=broad_path,
                sector_factor_report_path=sector_path,
                pr15_core_state_path=core_path,
                as_of=t1 + timedelta(minutes=5),
            )
            self.assertEqual(second["forecast_context_contract"]["new_bindings_this_run"], 1)
            binding = second["forecast_context_bindings"][0]
            self.assertEqual(binding["forecast_id"], "f-new")
            self.assertEqual(binding["world_state_id"], world_state_id)
            self.assertFalse(binding["retroactive"])
            self.assertFalse(binding["forecast_mutated"])
            self.assertLessEqual(binding["world_state_created_at"], binding["forecast_at"])
            self.assertLessEqual(binding["world_state_source_cutoff_at"], binding["forecast_at"])

    def test_pre_activation_forecast_discovered_later_is_terminally_unbound(self) -> None:
        t0 = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            broad_path, sector_path, core_path = self._write_inputs(
                root,
                broad_at=t0 - timedelta(hours=1),
                sector_at=t0 - timedelta(minutes=30),
            )
            state_dir = root / "state"
            run(
                state_dir,
                broad_market_report_path=broad_path,
                sector_factor_report_path=sector_path,
                pr15_core_state_path=core_path,
                as_of=t0,
            )
            core_path.write_text(
                json.dumps(core_state([forecast("f-late-old", t0 - timedelta(minutes=5))])),
                encoding="utf-8",
            )
            report = run(
                state_dir,
                broad_market_report_path=broad_path,
                sector_factor_report_path=sector_path,
                pr15_core_state_path=core_path,
                as_of=t0 + timedelta(hours=1),
            )
            self.assertEqual(report["forecast_context_contract"]["new_unbound_this_run"], 1)
            row = report["unbound_forecasts"][0]
            self.assertEqual(row["status"], "forecast_predates_pr16_1_activation")
            self.assertTrue(row["terminal_no_retroactive_binding"])

    def test_binding_is_immutable_when_newer_world_state_arrives(self) -> None:
        t0 = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(hours=2)
        t2 = t1 + timedelta(hours=2)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            broad_path, sector_path, core_path = self._write_inputs(
                root,
                broad_at=t0 - timedelta(hours=1),
                sector_at=t0 - timedelta(minutes=30),
            )
            state_dir = root / "state"
            first = run(
                state_dir,
                broad_market_report_path=broad_path,
                sector_factor_report_path=sector_path,
                pr15_core_state_path=core_path,
                as_of=t0,
            )
            first_world = first["world_state_contract"]["latest_world_state_id"]
            core_path.write_text(json.dumps(core_state([forecast("f-new", t1)])), encoding="utf-8")
            bound = run(
                state_dir,
                broad_market_report_path=broad_path,
                sector_factor_report_path=sector_path,
                pr15_core_state_path=core_path,
                as_of=t1 + timedelta(minutes=5),
            )
            binding_before = dict(bound["forecast_context_bindings"][0])
            broad_path.write_text(json.dumps(broad_report(t2 - timedelta(minutes=15), shift=0.05)), encoding="utf-8")
            sector_path.write_text(json.dumps(sector_report(t2 - timedelta(minutes=10), shift=0.05)), encoding="utf-8")
            later = run(
                state_dir,
                broad_market_report_path=broad_path,
                sector_factor_report_path=sector_path,
                pr15_core_state_path=core_path,
                as_of=t2,
            )
            self.assertNotEqual(later["world_state_contract"]["latest_world_state_id"], first_world)
            self.assertEqual(later["forecast_context_bindings"][0], binding_before)
            self.assertEqual(later["world_state_contract"]["snapshot_count"], 2)

    def test_zero_influence_and_research_boundary_are_hard_off(self) -> None:
        controls = safety_controls()
        caps = capabilities()
        self.assertTrue(controls)
        self.assertTrue(all(value is False for value in controls.values()))
        self.assertTrue(caps["canonical_metric_semantics_registry_enabled"])
        self.assertTrue(caps["canonical_world_state_snapshot_enabled"])
        self.assertTrue(caps["prospective_entity_forecast_context_binding_enabled"])
        self.assertFalse(caps["direct_raw_market_observables_in_world_state_enabled"])
        self.assertFalse(caps["with_without_bridge_enabled"])
        self.assertFalse(caps["promotion_gate_enabled"])

    def test_state_and_report_files_are_materialized(self) -> None:
        t0 = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            broad_path, sector_path, core_path = self._write_inputs(
                root,
                broad_at=t0 - timedelta(hours=1),
                sector_at=t0 - timedelta(minutes=30),
            )
            report = run(
                root / "state",
                broad_market_report_path=broad_path,
                sector_factor_report_path=sector_path,
                pr15_core_state_path=core_path,
                as_of=t0,
            )
            self.assertEqual(report["contract_version"], CONTRACT_VERSION)
            self.assertTrue((root / "state" / STATE_FILENAME).exists())
            self.assertTrue((root / "state" / REPORT_FILENAME).exists())
            self.assertEqual(report["promotion"]["status"], "NOT_ELIGIBLE_FOR_PROMOTION_REVIEW")


if __name__ == "__main__":
    unittest.main()
