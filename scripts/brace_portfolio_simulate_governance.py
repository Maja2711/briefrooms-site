#!/usr/bin/env python3
"""Reproducible in-memory promotion and fallback demonstration."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

from brace_portfolio_config import load_config
from brace_portfolio_data import ENGINE_DATA_ROOT, read_json, write_json_atomic
from brace_portfolio_promotion_controller import evaluate_and_apply


def validation() -> dict:
    return {
        "oos_return_after_costs": 0.12,
        "oos_excess_vs_baseline": 0.03,
        "no_lookahead_audit": True,
        "costs_and_fx_included": True,
        "regime_stability": True,
        "observations": 500,
        "parameter_neighborhood_stable": True,
        "not_single_instrument_dependent": True,
        "reproducible_run": True,
        "full_manifest": True,
        "maximum_drawdown": -0.16,
        "drawdown_disadvantage": 0.0,
        "concentration_within_limits": True,
        "no_leverage": True,
        "no_short_sales": True,
        "no_cfds": True,
        "annual_turnover": 0.8,
        "expected_shortfall": -0.12,
        "downside_volatility": 0.18,
        "validation_window": {
            "out_of_sample": {"from": "2024-01-01", "to": "2026-01-01"}
        },
    }


def operational() -> dict:
    return {
        "stale_data": False,
        "consecutive_workflow_failures": 0,
        "price_fx_inconsistent": False,
        "history_integrity_failed": False,
        "unexplained_parameter_change": False,
        "missing_rationale": False,
        "risk_data_missing": False,
        "live_validation_divergence": False,
        "critical_data_errors": 0,
        "decisions_reproducible": True,
        "entry_history_unchanged": True,
        "timestamps_complete": True,
        "workflow_stable": True,
        "public_internal_consistent": True,
        "integrity_tests_pass": True,
    }


def run() -> dict:
    config, _ = load_config()
    registry = copy.deepcopy(
        read_json(ENGINE_DATA_ROOT / "methodology_registry.json")
    )
    challenger = next(
        item
        for item in registry["methodologies"]
        if item["methodology_id"] == "brace-portfolio-engine"
    )
    challenger["status"] = "SHADOW"
    challenger.setdefault("validation_results", {})[
        "shadow_started_at"
    ] = "2026-01-01T00:00:00+00:00"
    registry["controller_state"] = "ACTIVE_BASELINE"
    registry["champion_methodology_id"] = "portfolio-10k-baseline"
    shadow = {
        "decisions": 24,
        "completed_trades": 9,
        "shadow_return": 0.08,
        "baseline_return": 0.05,
        "shadow_risk_adjusted_return": 0.9,
        "baseline_risk_adjusted_return": 0.6,
        "shadow_max_drawdown": -0.12,
        "shadow_turnover": 0.7,
        "excess_return_ci_low": 0.002,
    }
    risk = {
        "maximum_drawdown": -0.1,
        "current_drawdown": -0.03,
        "annual_turnover": 0.5,
    }
    probation = {
        "max_rotations_per_day": 1,
        "max_position_changes_per_week": 2,
        "maximum_weekly_turnover": 0.10,
        "maximum_new_position_weight": 0.08,
        "minimum_confidence": 0.75,
        "full_portfolio_replacement": False,
        "transaction_cost_buffer_applied": True,
        "cooldown_applied": True,
        "material_advantage_required": True,
        "fallback_trigger": False,
    }
    first_at = datetime(2026, 3, 5, tzinfo=timezone.utc)
    probationary, history, first = evaluate_and_apply(
        registry,
        validation(),
        shadow,
        {},
        operational(),
        risk,
        config,
        first_at,
    )
    active_at = first_at + timedelta(days=31)
    active, history, second = evaluate_and_apply(
        probationary,
        validation(),
        shadow,
        probation,
        operational(),
        risk,
        config,
        active_at,
        history,
    )
    failure = operational()
    failure["stale_data"] = True
    fallback, history, third = evaluate_and_apply(
        active,
        validation(),
        shadow,
        probation,
        failure,
        risk,
        config,
        active_at + timedelta(hours=1),
        history,
    )
    return {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "methodology_version": "promotion-controller-v1.0.0",
        "data_freshness": "synthetic_governance_test",
        "source_metadata": {
            "engine": "brace_portfolio_simulate_governance.py",
            "simulation_only": True,
            "production_registry_modified": False,
        },
        "initial_state": "ACTIVE_BASELINE + BRACE_SHADOW",
        "promotion_to_probation": first,
        "promotion_to_active_paper": second,
        "automatic_fallback": third,
        "final_simulated_controller_state": fallback.get("controller_state"),
        "records": history.get("records") or [],
    }


def main() -> int:
    output = run()
    write_json_atomic(
        ENGINE_DATA_ROOT / "simulations" / "governance_simulation.json",
        output,
    )
    print(
        "Governance simulation: SHADOW -> PROBATIONARY_CONTROL -> "
        "ACTIVE_PAPER_CONTROL -> FALLBACK_BASELINE"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
