from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from scripts.brace_entity_belief_state_forecast import (
    CONTRACT_VERSION,
    DIMENSION_CONFIG,
    FORECAST_HORIZON_DAYS,
    REPORT_FILENAME,
    STATE_FILENAME,
    capabilities,
    promotion_evidence_standard,
    run,
    safety_controls,
)
from scripts.brace_entity_evidence_interpretation import (
    CONTRACT_VERSION as PR14_CONTRACT_VERSION,
    SCHEMA_VERSION as PR14_SCHEMA_VERSION,
)


def z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def evidence_row(
    *,
    entity: str,
    dimension: str,
    observed_at: datetime,
    direction: int,
    suffix: str,
    strength: float = 0.35,
    cluster: str | None = None,
    contract_version: str = PR14_CONTRACT_VERSION,
    contract_id: str | None = None,
) -> dict[str, Any]:
    bid = f"entity.{entity}.{dimension}"
    cid = contract_id or str(DIMENSION_CONFIG[dimension]["contract_id"])
    return {
        "evidence_id": f"ev-{entity}-{dimension}-{suffix}",
        "belief_id": bid,
        "source": "PR14 deterministic interpretation of SEC primary facts",
        "observed_at": z(observed_at),
        "direction": direction,
        "strength": strength,
        "reliability": 0.995,
        "independence_cluster": cluster or f"issuer-filing:{entity}:{suffix}",
        "source_type": "derived",
        "source_ref": f"pr14://{entity}/{suffix}/{cid}",
        "derived_from": [f"obs-derived-{entity}-{dimension}-{suffix}"],
        "evidence_type": "entity_fundamental_yoy",
        "note": "fixture",
        "metadata": {
            "contract_id": cid,
            "contract_version": contract_version,
            "pnl_tuned": False,
            "promotion_authority": False,
            "current_primary_observation_ids": [f"primary-{suffix}"],
            "baseline_primary_observation_ids": [f"primary-base-{suffix}"],
        },
    }


def interpretation_row(
    *,
    entity: str,
    dimension: str,
    computed_at: datetime,
    status: str,
    suffix: str,
) -> dict[str, Any]:
    direction = 1 if status == "support" else -1 if status == "oppose" else None
    return {
        "interpretation_id": f"int-{entity}-{dimension}-{suffix}",
        "computed_at": z(computed_at),
        "entity_id": entity,
        "belief_id": f"entity.{entity}.{dimension}",
        "dimension": dimension,
        "contract_id": str(DIMENSION_CONFIG[dimension]["contract_id"]),
        "status": status,
        "direction": direction,
        "strength": 0.35 if direction else 0.0,
        "accession_number": suffix,
        "pnl_tuned": False,
    }


def source_state(
    *,
    last_run_at: datetime,
    evidence: list[Mapping[str, Any]] | None = None,
    interpretations: list[Mapping[str, Any]] | None = None,
    alpha_status: str = "active",
    bank_status: str = "active",
    alpha_window: datetime | None = None,
    bank_window: datetime | None = None,
) -> dict[str, Any]:
    alpha_window = alpha_window or datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)
    bank_window = bank_window or alpha_window
    return {
        "schema_version": PR14_SCHEMA_VERSION,
        "mode": "research_shadow",
        "contract_version": PR14_CONTRACT_VERSION,
        "first_run_at": z(alpha_window),
        "last_run_at": z(last_run_at),
        "entities": {
            "alpha": {
                "entity_id": "alpha",
                "current_status": alpha_status,
                "sector": "Information Technology",
                "reporting_regime": "domestic_sec_periodic_reporting",
                "source_window_opened_at": z(alpha_window) if alpha_status == "active" else None,
            },
            "bank": {
                "entity_id": "bank",
                "current_status": bank_status,
                "sector": "Financials",
                "reporting_regime": "domestic_sec_periodic_reporting",
                "source_window_opened_at": z(bank_window) if bank_status == "active" else None,
            },
        },
        "interpretations": [dict(x) for x in (interpretations or [])],
        "derived_observations": [],
        "evidence": [dict(x) for x in (evidence or [])],
        "seen_primary_observation_ids": [],
    }


class BraceEntityBeliefStateForecastTests(unittest.TestCase):
    def _write(self, root: Path, payload: Mapping[str, Any]) -> Path:
        path = root / "pr14.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _belief(self, report: Mapping[str, Any], belief_id: str) -> Mapping[str, Any]:
        return next(row for row in report["belief_states"] if row["belief_id"] == belief_id)

    def test_first_run_is_activation_only_and_does_not_backfill_existing_pr14_evidence(self) -> None:
        t0 = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        old = evidence_row(
            entity="alpha", dimension="revenue_durability",
            observed_at=t0 - timedelta(hours=1), direction=1, suffix="old",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = run(
                root / "state",
                interpretation_state_path=self._write(root, source_state(last_run_at=t0, evidence=[old])),
                as_of=t0,
            )
            self.assertTrue(report["sample"]["activation_only_this_run"])
            self.assertEqual(report["sample"]["ingested_evidence_total"], 0)
            self.assertEqual(report["sample"]["forecasts_total"], 0)
            belief = self._belief(report, "entity.alpha.revenue_durability")
            self.assertEqual(belief["probability"], 0.5)
            runtime = json.loads((root / "state" / STATE_FILENAME).read_text())
            self.assertIn(old["evidence_id"], runtime["seen_pr14_evidence_ids"])
            self.assertTrue((root / "state" / REPORT_FILENAME).exists())

    def test_new_support_evidence_updates_belief_and_freezes_one_prospective_forecast(self) -> None:
        t0 = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(days=1)
        ev = evidence_row(entity="alpha", dimension="revenue_durability", observed_at=t1, direction=1, suffix="q1")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._write(root, source_state(last_run_at=t0))
            state_dir = root / "state"
            run(state_dir, interpretation_state_path=source, as_of=t0)
            source.write_text(json.dumps(source_state(last_run_at=t1, evidence=[ev])))
            report = run(state_dir, interpretation_state_path=source, as_of=t1)
            belief = self._belief(report, "entity.alpha.revenue_durability")
            self.assertGreater(belief["probability"], 0.5)
            self.assertEqual(report["sample"]["new_evidence_this_run"], 1)
            self.assertEqual(report["sample"]["new_forecasts_this_run"], 1)
            forecast = report["forecasts"][0]
            self.assertEqual(forecast["belief_id"], "entity.alpha.revenue_durability")
            self.assertAlmostEqual(forecast["horizon_hours"], FORECAST_HORIZON_DAYS * 24)
            self.assertFalse(forecast["metadata"]["engine_influence"])
            self.assertFalse(forecast["metadata"]["pnl_tuned"])

    def test_new_oppose_evidence_moves_probability_below_prior(self) -> None:
        t0 = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(days=1)
        ev = evidence_row(entity="alpha", dimension="earnings_momentum", observed_at=t1, direction=-1, suffix="q1")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._write(root, source_state(last_run_at=t0))
            state_dir = root / "state"
            run(state_dir, interpretation_state_path=source, as_of=t0)
            source.write_text(json.dumps(source_state(last_run_at=t1, evidence=[ev])))
            report = run(state_dir, interpretation_state_path=source, as_of=t1)
            belief = self._belief(report, "entity.alpha.earnings_momentum")
            self.assertLess(belief["probability"], 0.5)

    def test_second_update_does_not_create_overlapping_live_forecast_for_same_belief(self) -> None:
        t0 = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(days=1)
        t2 = t1 + timedelta(days=10)
        ev1 = evidence_row(entity="alpha", dimension="revenue_durability", observed_at=t1, direction=1, suffix="q1")
        ev2 = evidence_row(entity="alpha", dimension="revenue_durability", observed_at=t2, direction=-1, suffix="q2")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state_dir = root / "state"
            source = self._write(root, source_state(last_run_at=t0))
            run(state_dir, interpretation_state_path=source, as_of=t0)
            source.write_text(json.dumps(source_state(last_run_at=t1, evidence=[ev1])))
            first = run(state_dir, interpretation_state_path=source, as_of=t1)
            self.assertEqual(first["sample"]["forecasts_total"], 1)
            source.write_text(json.dumps(source_state(last_run_at=t2, evidence=[ev1, ev2])))
            second = run(state_dir, interpretation_state_path=source, as_of=t2)
            self.assertEqual(second["sample"]["new_evidence_this_run"], 1)
            self.assertEqual(second["sample"]["new_forecasts_this_run"], 0)
            self.assertEqual(second["sample"]["forecasts_total"], 1)

    def test_dormant_entity_evidence_can_be_preserved_but_never_opens_new_forecast(self) -> None:
        t0 = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(days=1)
        ev = evidence_row(entity="alpha", dimension="margin_trajectory", observed_at=t1, direction=1, suffix="q1")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state_dir = root / "state"
            source = self._write(root, source_state(last_run_at=t0))
            run(state_dir, interpretation_state_path=source, as_of=t0)
            source.write_text(json.dumps(source_state(last_run_at=t1, evidence=[ev], alpha_status="dormant")))
            report = run(state_dir, interpretation_state_path=source, as_of=t1)
            self.assertEqual(report["sample"]["new_evidence_this_run"], 1)
            self.assertEqual(report["sample"]["new_forecasts_this_run"], 0)
            self.assertEqual(report["sample"]["dormant_entities"], 1)

    def test_nii_dimension_is_registered_and_ingested_only_for_financials(self) -> None:
        t0 = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(days=1)
        alpha_nii = evidence_row(entity="alpha", dimension="net_interest_income_durability", observed_at=t1, direction=1, suffix="a")
        bank_nii = evidence_row(entity="bank", dimension="net_interest_income_durability", observed_at=t1, direction=1, suffix="b")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state_dir = root / "state"
            source = self._write(root, source_state(last_run_at=t0))
            run(state_dir, interpretation_state_path=source, as_of=t0)
            source.write_text(json.dumps(source_state(last_run_at=t1, evidence=[alpha_nii, bank_nii])))
            report = run(state_dir, interpretation_state_path=source, as_of=t1)
            belief_ids = {row["belief_id"] for row in report["belief_states"]}
            self.assertNotIn("entity.alpha.net_interest_income_durability", belief_ids)
            self.assertIn("entity.bank.net_interest_income_durability", belief_ids)
            self.assertEqual(report["sample"]["new_evidence_this_run"], 1)
            self.assertTrue(any(x.get("reason") == "dimension_not_enabled_for_entity" for x in report["source_issues"]))

    def test_due_forecast_is_verified_from_first_future_support_interpretation_inside_window(self) -> None:
        t0 = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(days=1)
        outcome_time = t1 + timedelta(days=80)
        due_run = t1 + timedelta(days=121)
        ev1 = evidence_row(entity="alpha", dimension="revenue_durability", observed_at=t1, direction=1, suffix="q1")
        ev2 = evidence_row(entity="alpha", dimension="revenue_durability", observed_at=outcome_time, direction=1, suffix="q2")
        outcome = interpretation_row(entity="alpha", dimension="revenue_durability", computed_at=outcome_time, status="support", suffix="q2")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state_dir = root / "state"
            source = self._write(root, source_state(last_run_at=t0))
            run(state_dir, interpretation_state_path=source, as_of=t0)
            source.write_text(json.dumps(source_state(last_run_at=t1, evidence=[ev1])))
            created = run(state_dir, interpretation_state_path=source, as_of=t1)
            forecast_id = created["forecasts"][0]["forecast_id"]
            source.write_text(json.dumps(source_state(last_run_at=outcome_time, evidence=[ev1, ev2], interpretations=[outcome])))
            mid = run(state_dir, interpretation_state_path=source, as_of=outcome_time)
            self.assertEqual(mid["sample"]["verifications_total"], 0)
            final = run(state_dir, interpretation_state_path=source, as_of=due_run)
            self.assertEqual(final["sample"]["new_verifications_this_run"], 1)
            verification = final["verifications"][0]
            self.assertEqual(verification["forecast_id"], forecast_id)
            self.assertTrue(verification["outcome"])
            self.assertTrue(verification["calibration_eligible"])
            self.assertEqual(verification["outcome_ref"], outcome["interpretation_id"])

    def test_neutral_next_report_is_censored_not_forced_to_false(self) -> None:
        t0 = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(days=1)
        outcome_time = t1 + timedelta(days=80)
        due_run = t1 + timedelta(days=121)
        ev1 = evidence_row(entity="alpha", dimension="margin_trajectory", observed_at=t1, direction=1, suffix="q1")
        neutral = interpretation_row(entity="alpha", dimension="margin_trajectory", computed_at=outcome_time, status="neutral", suffix="q2")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state_dir = root / "state"
            source = self._write(root, source_state(last_run_at=t0))
            run(state_dir, interpretation_state_path=source, as_of=t0)
            source.write_text(json.dumps(source_state(last_run_at=t1, evidence=[ev1])))
            run(state_dir, interpretation_state_path=source, as_of=t1)
            source.write_text(json.dumps(source_state(last_run_at=outcome_time, evidence=[ev1], interpretations=[neutral])))
            run(state_dir, interpretation_state_path=source, as_of=outcome_time)
            final = run(state_dir, interpretation_state_path=source, as_of=due_run)
            self.assertEqual(final["sample"]["verifications_total"], 0)
            self.assertEqual(final["sample"]["new_neutral_censors_this_run"], 1)
            self.assertEqual(final["forecast_closure_status_counts"].get("censored_neutral"), 1)

    def test_interpretation_after_target_cannot_resolve_forecast(self) -> None:
        t0 = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(days=1)
        after_target = t1 + timedelta(days=121)
        ev1 = evidence_row(entity="alpha", dimension="earnings_momentum", observed_at=t1, direction=1, suffix="q1")
        late = interpretation_row(entity="alpha", dimension="earnings_momentum", computed_at=after_target, status="support", suffix="late")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state_dir = root / "state"
            source = self._write(root, source_state(last_run_at=t0))
            run(state_dir, interpretation_state_path=source, as_of=t0)
            source.write_text(json.dumps(source_state(last_run_at=t1, evidence=[ev1])))
            run(state_dir, interpretation_state_path=source, as_of=t1)
            source.write_text(json.dumps(source_state(last_run_at=after_target, evidence=[ev1], interpretations=[late])))
            final = run(state_dir, interpretation_state_path=source, as_of=after_target)
            self.assertEqual(final["sample"]["new_verifications_this_run"], 0)
            self.assertEqual(final["sample"]["new_no_outcome_closures_this_run"], 1)
            self.assertEqual(final["forecast_closure_status_counts"].get("no_comparable_outcome_by_target"), 1)

    def test_future_dated_and_wrong_contract_evidence_fail_closed(self) -> None:
        t0 = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(days=1)
        future = evidence_row(entity="alpha", dimension="revenue_durability", observed_at=t1 + timedelta(days=1), direction=1, suffix="future")
        wrong = evidence_row(
            entity="alpha", dimension="revenue_durability", observed_at=t1, direction=1,
            suffix="wrong", contract_version="wrong-version",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state_dir = root / "state"
            source = self._write(root, source_state(last_run_at=t0))
            run(state_dir, interpretation_state_path=source, as_of=t0)
            source.write_text(json.dumps(source_state(last_run_at=t1, evidence=[future, wrong])))
            report = run(state_dir, interpretation_state_path=source, as_of=t1)
            self.assertEqual(report["sample"]["new_evidence_this_run"], 0)
            codes = {row["code"] for row in report["source_issues"]}
            self.assertIn("future_dated_pr14_evidence", codes)
            self.assertIn("pr14_evidence_rejected", codes)

    def test_rerun_is_idempotent(self) -> None:
        t0 = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(days=1)
        ev = evidence_row(entity="alpha", dimension="revenue_durability", observed_at=t1, direction=1, suffix="q1")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state_dir = root / "state"
            source = self._write(root, source_state(last_run_at=t0))
            run(state_dir, interpretation_state_path=source, as_of=t0)
            source.write_text(json.dumps(source_state(last_run_at=t1, evidence=[ev])))
            first = run(state_dir, interpretation_state_path=source, as_of=t1)
            second = run(state_dir, interpretation_state_path=source, as_of=t1)
            self.assertEqual(first["sample"]["forecasts_total"], second["sample"]["forecasts_total"])
            self.assertEqual(second["sample"]["new_evidence_this_run"], 0)
            self.assertEqual(second["sample"]["new_forecasts_this_run"], 0)

    def test_zero_brace_influence_and_promotion_governance(self) -> None:
        self.assertTrue(all(value is False for value in safety_controls().values()))
        caps = capabilities()
        self.assertTrue(caps["entity_belief_core_state_update_enabled"])
        self.assertTrue(caps["prospective_entity_forecast_capture_enabled"])
        self.assertTrue(caps["deterministic_forecast_outcome_resolution_enabled"])
        self.assertFalse(caps["brace_entity_bridge_enabled"])
        self.assertFalse(caps["with_without_bridge_enabled"])
        gate = promotion_evidence_standard()
        self.assertTrue(gate["with_without_required"])
        self.assertTrue(gate["effective_n_required"])
        self.assertFalse(gate["effective_n_threshold_defined_here"])
        self.assertFalse(gate["automatic_promotion"])
        self.assertEqual(CONTRACT_VERSION, "entity-belief-forecast-contract-v1")


if __name__ == "__main__":
    unittest.main()
