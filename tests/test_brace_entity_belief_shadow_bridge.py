from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from scripts.brace_entity_belief_shadow_bridge import (
    CONTRACT_VERSION,
    ECONOMIC_HORIZON_DAYS,
    PRIMARY_MODIFIER_SCORE_POINTS,
    REPORT_FILENAME,
    STATE_FILENAME,
    capabilities,
    governed_with_action,
    local_brace_action,
    modifier_for_forecasts,
    promotion_evidence_standard,
    run,
    safety_controls,
)


def z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def world_snapshot(world_id: str, created: datetime) -> dict[str, Any]:
    return {
        "world_state_id": world_id,
        "contract_version": "investment-world-state-v1",
        "created_at": z(created),
        "context_as_of": z(created - timedelta(minutes=5)),
        "source_cutoff_at": z(created - timedelta(minutes=5)),
        "belief_context": {"broad_market": {}, "sector_factor": {}},
    }


def fixture_world(
    *,
    world_id: str,
    created: datetime,
    forecast_id: str | None = None,
    forecast_at: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    state: dict[str, Any] = {
        "schema_version": "investment-semantics-world-state-v1",
        "mode": "research_shadow",
        "contract_version": "investment-semantics-world-state-contract-v1",
        "activated_at": z(created),
        "snapshots": [world_snapshot(world_id, created)],
        "forecast_context_bindings": {},
        "unbound_forecasts": {},
    }
    if forecast_id and forecast_at:
        state["forecast_context_bindings"][forecast_id] = {
            "contract_version": "entity-forecast-world-state-binding-v1",
            "binding_id": f"bind-{forecast_id}",
            "forecast_id": forecast_id,
            "belief_id": "entity.alpha.revenue_durability",
            "forecast_at": z(forecast_at),
            "world_state_id": world_id,
            "world_state_created_at": z(created),
            "world_state_source_cutoff_at": z(created - timedelta(minutes=5)),
            "prospective": True,
            "retroactive": False,
            "forecast_mutated": False,
            "historical_backfill": False,
        }
    report = {
        "schema_version": "investment-semantics-world-state-v1",
        "report_version": "investment-semantics-world-state-report-v1",
        "contract_version": "investment-semantics-world-state-contract-v1",
        "mode": "research_shadow",
        "active_decision_influence": False,
    }
    return state, report


def fixture_core(
    *,
    forecast_id: str,
    forecast_at: datetime,
    target_at: datetime,
    probability: float = 0.8,
    confidence: float = 1.0,
    dimension: str = "revenue_durability",
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "forecasts": [
            {
                "forecast_id": forecast_id,
                "forecast_set_id": f"set-{forecast_id}",
                "belief_id": f"entity.alpha.{dimension}",
                "predicted_probability": probability,
                "forecast_confidence": confidence,
                "forecast_at": z(forecast_at),
                "target_at": z(target_at),
                "horizon_hours": 2880.0,
                "domain": "entity_fundamentals",
                "entity": "alpha",
                "regime": "entity_fundamental_reporting_v1",
                "outcome_rule": "fixture",
                "representative_evidence_ids": [],
                "evidence_snapshot": [],
                "metadata": {"contract_version": "entity-belief-forecast-contract-v1"},
            }
        ],
        "verifications": [],
    }


def fixture_runtime() -> dict[str, Any]:
    return {
        "schema_version": "brace-entity-belief-state-forecast-v1",
        "mode": "research_shadow",
        "contract_version": "entity-belief-forecast-contract-v1",
    }


def fixture_analysis(
    at: datetime,
    *,
    score: float = 55.0,
    risk_score: float = 60.0,
    confidence: float = 1.0,
    price: float = 100.0,
    market_date: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "generated_at": z(at),
        "methodology_version": "brace-portfolio-v3.0.0",
        "positions": [
            {
                "instrument_id": "alpha",
                "broker_symbol": "ALPHA.US",
                "final_score": score,
                "risk_score": risk_score,
                "confidence_score": confidence,
                "current_weight": 0.10,
                "target_weight": 0.15,
                "current_price": price,
                "market_date": market_date or at.date().isoformat(),
                "current_price_updated_at": z(at),
            }
        ],
    }


def fixture_pending(
    at: datetime,
    *,
    score: float = 55.0,
    action: str = "WATCH",
    price: float = 100.0,
    proposed_weight: float = 0.13,
) -> dict[str, Any]:
    return {
        "schema_version": "1.1.0",
        "generated_at": z(at),
        "methodology_version": "brace-portfolio-v3.0.0",
        "safe_mode": False,
        "recommendations": [
            {
                "instrument": "alpha",
                "broker_symbol": "ALPHA.US",
                "action": action,
                "final_score": score,
                "confidence": 1.0,
                "current_weight": 0.10,
                "proposed_weight": proposed_weight,
                "signal_price": price,
                "signal_fx_to_pln": 4.0,
            }
        ],
        "decisions": [],
    }


def fixture_portfolio() -> dict[str, Any]:
    return {"total_value_pln": 10_000.0, "positions": []}


class BraceEntityBeliefShadowBridgeTests(unittest.TestCase):
    def _write(self, root: Path, name: str, payload: Mapping[str, Any]) -> Path:
        path = root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _paths(
        self,
        root: Path,
        *,
        core: Mapping[str, Any],
        world: Mapping[str, Any],
        world_report: Mapping[str, Any],
        pending: Mapping[str, Any],
        analysis: Mapping[str, Any],
    ) -> dict[str, Path]:
        return {
            "pr15_core_state_path": self._write(root, "core.json", core),
            "pr15_runtime_state_path": self._write(root, "runtime.json", fixture_runtime()),
            "world_state_path": self._write(root, "world.json", world),
            "world_report_path": self._write(root, "world-report.json", world_report),
            "pending_decisions_path": self._write(root, "pending.json", pending),
            "analysis_path": self._write(root, "analysis.json", analysis),
            "portfolio_path": self._write(root, "portfolio.json", fixture_portfolio()),
        }

    def test_first_run_is_activation_only_no_historical_decision_backfill(self) -> None:
        t0 = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        f_at = t0 - timedelta(hours=1)
        world, wr = fixture_world(world_id="world-a", created=f_at - timedelta(minutes=30), forecast_id="f1", forecast_at=f_at)
        core = fixture_core(forecast_id="f1", forecast_at=f_at, target_at=t0 + timedelta(days=100))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = run(root / "state", as_of=t0, **self._paths(root, core=core, world=world, world_report=wr, pending=fixture_pending(t0 - timedelta(minutes=10)), analysis=fixture_analysis(t0 - timedelta(minutes=10))))
            self.assertTrue(report["sample"]["activation_only_this_run"])
            self.assertEqual(report["sample"]["pair_sets_total"], 0)
            self.assertEqual(report["sample"]["pre_activation_decision_sets_total"], 1)
            self.assertTrue(report["anti_hindsight"]["historical_decision_backfill"] is False)

    def test_modifier_is_bounded_and_confidence_scaled(self) -> None:
        rows = [{"predicted_probability": 1.0, "forecast_confidence": 1.0}]
        self.assertEqual(modifier_for_forecasts(rows), PRIMARY_MODIFIER_SCORE_POINTS)
        rows = [{"predicted_probability": 0.0, "forecast_confidence": 1.0}]
        self.assertEqual(modifier_for_forecasts(rows), -PRIMARY_MODIFIER_SCORE_POINTS)
        rows = [{"predicted_probability": 0.75, "forecast_confidence": 0.5}]
        self.assertAlmostEqual(modifier_for_forecasts(rows), 0.5)

    def test_belief_cannot_force_or_veto_exit(self) -> None:
        action, reason = governed_with_action("EXIT", "REDUCE")
        self.assertEqual(action, "REDUCE")
        self.assertEqual(reason, "belief_cannot_force_exit")
        action, reason = governed_with_action("HOLD", "EXIT")
        self.assertEqual(action, "EXIT")
        self.assertEqual(reason, "belief_cannot_veto_existing_exit")

    def test_local_threshold_contract_matches_expected_brace_boundaries(self) -> None:
        common = dict(risk_score=60.0, data_quality_confidence=1.0, current_weight=0.10, target_weight=0.15, safe_mode=False)
        self.assertEqual(local_brace_action(score=42.9, **common), "REDUCE")
        self.assertEqual(local_brace_action(score=55.9, **common), "WATCH")
        self.assertEqual(local_brace_action(score=56.0, **common), "HOLD")
        self.assertEqual(local_brace_action(score=75.0, **common), "ADD")

    def test_new_post_activation_decision_creates_pair_only_with_prospective_bound_forecast(self) -> None:
        t0 = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        f_at = t0 + timedelta(hours=1)
        world_created = t0 + timedelta(minutes=30)
        world, wr = fixture_world(world_id="world-a", created=world_created, forecast_id="f1", forecast_at=f_at)
        core = fixture_core(forecast_id="f1", forecast_at=f_at, target_at=t0 + timedelta(days=100), probability=0.8, confidence=1.0)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state = root / "state"
            paths = self._paths(root, core=core, world=world, world_report=wr, pending=fixture_pending(t0), analysis=fixture_analysis(t0))
            run(state, as_of=t0 + timedelta(minutes=5), **paths)
            t1 = t0 + timedelta(days=2)
            paths["pending_decisions_path"].write_text(json.dumps(fixture_pending(t1)), encoding="utf-8")
            paths["analysis_path"].write_text(json.dumps(fixture_analysis(t1)), encoding="utf-8")
            report = run(state, as_of=t1 + timedelta(minutes=1), **paths)
            self.assertEqual(report["sample"]["pair_sets_total"], 1)
            pair = report["paired_sets"][0]
            item = pair["items"][0]
            self.assertEqual(item["instrument"], "alpha")
            self.assertEqual(item["without_action"], "WATCH")
            self.assertGreater(item["belief_modifier_score_points"], 0.0)
            self.assertEqual(pair["decision_world_state_id"], "world-a")
            self.assertFalse(pair["engine_consumed_belief"])

    def test_unbound_forecast_never_creates_pair(self) -> None:
        t0 = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        f_at = t0 + timedelta(hours=1)
        world, wr = fixture_world(world_id="world-a", created=t0 + timedelta(minutes=30))
        core = fixture_core(forecast_id="f1", forecast_at=f_at, target_at=t0 + timedelta(days=100))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state = root / "state"
            paths = self._paths(root, core=core, world=world, world_report=wr, pending=fixture_pending(t0), analysis=fixture_analysis(t0))
            run(state, as_of=t0 + timedelta(minutes=5), **paths)
            t1 = t0 + timedelta(days=2)
            paths["pending_decisions_path"].write_text(json.dumps(fixture_pending(t1)), encoding="utf-8")
            paths["analysis_path"].write_text(json.dumps(fixture_analysis(t1)), encoding="utf-8")
            report = run(state, as_of=t1 + timedelta(minutes=1), **paths)
            self.assertEqual(report["sample"]["pair_sets_total"], 0)
            self.assertEqual(report["terminal_unpaired_sets"][-1]["status"], "no_parity_eligible_entity_forecasts")

    def test_pair_economics_use_same_prices_horizon_and_cost_contract(self) -> None:
        t0 = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        f_at = t0 + timedelta(hours=1)
        world, wr = fixture_world(world_id="world-a", created=t0 + timedelta(minutes=30), forecast_id="f1", forecast_at=f_at)
        # Score 55 WATCH; +2 max can cross to HOLD. Both retain current weight,
        # so the pair is economically identical: a useful null-effect parity test.
        core = fixture_core(forecast_id="f1", forecast_at=f_at, target_at=t0 + timedelta(days=100), probability=1.0, confidence=1.0)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state = root / "state"
            paths = self._paths(root, core=core, world=world, world_report=wr, pending=fixture_pending(t0), analysis=fixture_analysis(t0))
            run(state, as_of=t0 + timedelta(minutes=5), **paths)
            decision_at = t0 + timedelta(days=2)
            paths["pending_decisions_path"].write_text(json.dumps(fixture_pending(decision_at)), encoding="utf-8")
            paths["analysis_path"].write_text(json.dumps(fixture_analysis(decision_at)), encoding="utf-8")
            created = run(state, as_of=decision_at + timedelta(minutes=1), **paths)
            self.assertEqual(created["sample"]["pair_sets_total"], 1)

            evaluation = decision_at + timedelta(days=ECONOMIC_HORIZON_DAYS + 1)
            # Current market data advances. The recommendation snapshot is also
            # advanced to preserve source parity for the current decision set;
            # this may create a second prospective set but must resolve the first.
            paths["analysis_path"].write_text(json.dumps(fixture_analysis(evaluation, price=110.0, market_date=evaluation.date().isoformat())), encoding="utf-8")
            paths["pending_decisions_path"].write_text(json.dumps(fixture_pending(evaluation, price=110.0)), encoding="utf-8")
            report = run(state, as_of=evaluation + timedelta(minutes=1), **paths)
            matured = [x for x in report["economic_outcomes"] if x.get("status") == "matured"]
            self.assertGreaterEqual(len(matured), 1)
            outcome = matured[0]
            self.assertTrue(outcome["same_instrument_entry_evaluation_and_cost_contract"])
            self.assertAlmostEqual(outcome["without_return"], outcome["with_return"])
            economics = report["with_without_economics"]
            self.assertIn("ENGINE_ORIGINAL_WITHOUT_BELIEF", economics)
            self.assertIn("ENGINE_PLUS_HYPOTHETICAL_BELIEF_WITH_BELIEF", economics)
            self.assertIn("DELTA", economics)
            self.assertIn("drawdown_improvement", economics["required_drawdown_fields"])

    def test_append_only_pair_history_survives_newer_decision_set(self) -> None:
        t0 = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        f_at = t0 + timedelta(hours=1)
        world, wr = fixture_world(world_id="world-a", created=t0 + timedelta(minutes=30), forecast_id="f1", forecast_at=f_at)
        core = fixture_core(forecast_id="f1", forecast_at=f_at, target_at=t0 + timedelta(days=100))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state = root / "state"
            paths = self._paths(root, core=core, world=world, world_report=wr, pending=fixture_pending(t0), analysis=fixture_analysis(t0))
            run(state, as_of=t0 + timedelta(minutes=5), **paths)
            t1 = t0 + timedelta(days=2)
            paths["pending_decisions_path"].write_text(json.dumps(fixture_pending(t1)), encoding="utf-8")
            paths["analysis_path"].write_text(json.dumps(fixture_analysis(t1)), encoding="utf-8")
            first = run(state, as_of=t1 + timedelta(minutes=1), **paths)
            frozen = json.loads(json.dumps(first["paired_sets"][0]))
            t2 = t1 + timedelta(days=1)
            paths["pending_decisions_path"].write_text(json.dumps(fixture_pending(t2, score=56.0, action="HOLD")), encoding="utf-8")
            paths["analysis_path"].write_text(json.dumps(fixture_analysis(t2, score=56.0)), encoding="utf-8")
            second = run(state, as_of=t2 + timedelta(minutes=1), **paths)
            self.assertIn(frozen, second["paired_sets"])

    def test_report_always_contains_with_without_even_at_zero_n(self) -> None:
        t0 = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        world, wr = fixture_world(world_id="world-a", created=t0 - timedelta(hours=1))
        core = {"schema_version": 2, "forecasts": [], "verifications": []}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = run(root / "state", as_of=t0, **self._paths(root, core=core, world=world, world_report=wr, pending=fixture_pending(t0 - timedelta(minutes=1)), analysis=fixture_analysis(t0 - timedelta(minutes=1))))
            economics = report["with_without_economics"]
            self.assertIsNone(economics["ENGINE_ORIGINAL_WITHOUT_BELIEF"]["pnl_pln"])
            self.assertIsNone(economics["ENGINE_PLUS_HYPOTHETICAL_BELIEF_WITH_BELIEF"]["pnl_pln"])
            self.assertFalse(report["promotion_readiness"]["eligible_for_promotion_review"])
            self.assertEqual(report["promotion_readiness"]["status"], "NOT_ELIGIBLE_FOR_PROMOTION_REVIEW")
            self.assertTrue((root / "state" / STATE_FILENAME).exists())
            self.assertTrue((root / "state" / REPORT_FILENAME).exists())

    def test_governance_contract_has_no_active_authority(self) -> None:
        self.assertTrue(safety_controls())
        self.assertTrue(all(value is False for value in safety_controls().values()))
        caps = capabilities()
        self.assertTrue(caps["prospective_paired_without_with_capture_enabled"])
        self.assertFalse(caps["primary_modifier_consumed_by_brace_enabled"])
        self.assertFalse(caps["candidate_ranking_override_enabled"])
        self.assertFalse(caps["engine_sizing_override_enabled"])
        self.assertFalse(caps["promotion_gate_enabled"])
        standard = promotion_evidence_standard()
        self.assertTrue(standard["with_without_required"])
        self.assertFalse(standard["effective_n_threshold_defined_here"])
        self.assertFalse(standard["automatic_promotion"])
        self.assertEqual(CONTRACT_VERSION, "brace-entity-belief-shadow-bridge-contract-v1")


if __name__ == "__main__":
    unittest.main()
