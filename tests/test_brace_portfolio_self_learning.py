from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import brace_portfolio_config as config_module
import brace_portfolio_self_learning as learning


def base_config() -> dict:
    return {
        "policy": {
            "target_annual_return": 0.1,
            "autonomy_mode": "RECOMMEND_ONLY",
            "max_single_stock_weight": 0.18,
            "max_broad_etf_weight": 0.3,
            "max_sector_weight": 0.35,
            "max_currency_weight": 0.75,
            "max_region_weight": 0.75,
            "minimum_position_weight": 0.05,
            "max_positions": 12,
            "minimum_holding_period_days": 30,
            "rotation_cooldown_days": 21,
            "minimum_confidence": 0.65,
            "probationary_minimum_confidence": 0.7,
            "minimum_score_improvement": 8.0,
            "minimum_expected_alpha": 0.025,
            "transaction_cost_buffer": 0.01,
            "max_expected_drawdown": 0.25,
            "emergency_drawdown": 0.3,
            "max_annual_turnover": 1.5,
            "max_weekly_turnover_probation": 0.15,
            "maximum_missing_instruments": 0,
            "monitoring_max_price_age_hours": 6.0,
            "analysis_max_price_age_hours": 72.0,
            "maximum_single_price_jump": 0.35,
            "minimum_shadow_calendar_days": 60,
            "minimum_shadow_decisions": 20,
            "minimum_shadow_completed_trades": 8,
            "minimum_probation_calendar_days": 30,
            "max_probation_rotations_per_day": 1,
            "max_probation_position_changes_per_week": 2,
            "max_probation_new_position_weight": 0.1,
            "target_probability_floor": 0.55,
            "risk_free_rate": 0.025,
            "safe_mode_on_stale_data": True,
            "paper_execution_enabled_after_promotion": True,
            "real_broker_integration_enabled": False,
        }
    }


def passing_validation() -> dict:
    return {
        "no_lookahead_audit": True,
        "costs_and_fx_included": True,
        "observations": 500,
        "not_single_instrument_dependent": True,
        "no_leverage": True,
        "no_short_sales": True,
        "no_cfds": True,
        "reproducible_run": True,
        "full_manifest": True,
    }


def stats(samples: float, accuracy: float, signed_excess: float) -> dict:
    return {
        "effective_samples": samples,
        "directional_accuracy": accuracy,
        "mean_signed_excess_return": signed_excess,
    }


def test_warmup_never_changes_parameters_before_sample_gate():
    candidate, reason = learning.propose_candidate(
        base_config()["policy"], {}, stats(11.9, 0.2, -0.5)
    )
    assert candidate == {}
    assert reason == "WARMUP_INSUFFICIENT_MATURE_ACTIONABLE_OUTCOMES"


def test_weak_results_tighten_only_governed_fields():
    candidate, reason = learning.propose_candidate(
        base_config()["policy"], {}, stats(20, 0.50, -0.01)
    )
    assert reason == "TIGHTEN_WEAK_DIRECTIONAL_OR_EXCESS_RESULTS"
    assert candidate == {
        "minimum_confidence": 0.67,
        "minimum_score_improvement": 8.5,
        "minimum_expected_alpha": 0.0275,
    }


def test_strong_results_relax_cautiously():
    candidate, reason = learning.propose_candidate(
        base_config()["policy"], {}, stats(20, 0.75, 0.02)
    )
    assert reason == "CAUTIOUSLY_RELAX_STRONG_STABLE_RESULTS"
    assert candidate == {
        "minimum_confidence": 0.64,
        "minimum_score_improvement": 7.75,
        "minimum_expected_alpha": 0.024,
    }


def test_two_identical_confirmations_required_before_shadow_activation():
    at1 = datetime(2026, 8, 2, tzinfo=timezone.utc)
    first = learning.advance_adaptive_policy(
        {}, base_config(), stats(20, 0.50, -0.01), passing_validation(), at1
    )
    assert first["status"] == "CANDIDATE_PENDING_CONFIRMATION"
    assert first["active_overrides"] == {}
    assert first["consecutive_confirmations"] == 1

    second = learning.advance_adaptive_policy(
        first,
        base_config(),
        stats(20, 0.50, -0.01),
        passing_validation(),
        datetime(2026, 8, 9, tzinfo=timezone.utc),
    )
    assert second["status"] == "ACTIVE_SHADOW_PARAMETERS"
    assert second["apply_to_shadow_decisions"] is True
    assert second["active_overrides"]["minimum_confidence"] == 0.67


def test_failed_research_gate_blocks_activation():
    invalid = passing_validation()
    invalid["no_lookahead_audit"] = False
    first = learning.advance_adaptive_policy(
        {},
        base_config(),
        stats(20, 0.50, -0.01),
        invalid,
        datetime(2026, 8, 2, tzinfo=timezone.utc),
    )
    second = learning.advance_adaptive_policy(
        first,
        base_config(),
        stats(20, 0.50, -0.01),
        invalid,
        datetime(2026, 8, 9, tzinfo=timezone.utc),
    )
    assert second["status"] == "CANDIDATE_BLOCKED_BY_RESEARCH_GATE"
    assert second["active_overrides"] == {}


def test_collect_due_outcome_is_append_only_and_benchmark_relative():
    shadow = {
        "runs": [
            {
                "shadow_run_id": "r1",
                "generated_at": "2026-01-01T12:00:00+00:00",
                "decisions": [
                    {
                        "instrument": "abc",
                        "brace_decision": "ADD",
                        "signal_price": 100.0,
                    }
                ],
            }
        ]
    }
    market = {
        "instruments": {
            "abc": {
                "history": [
                    {"date": "2026-01-01", "close": 100},
                    {"date": "2026-01-08", "close": 110},
                ]
            },
            "fwia": {
                "history": [
                    {"date": "2026-01-01", "close": 200},
                    {"date": "2026-01-08", "close": 204},
                ]
            },
        }
    }
    created = learning.collect_due_outcomes(shadow, market, [], date(2026, 1, 8))
    assert len(created) == 1
    assert created[0]["horizon_days"] == 7
    assert created[0]["excess_return"] == pytest.approx(0.08)
    assert created[0]["direction_correct"] is True
    assert learning.collect_due_outcomes(shadow, market, created, date(2026, 1, 8)) == []


def test_config_applies_only_active_whitelisted_shadow_overrides(tmp_path: Path):
    base = base_config()
    config_path = tmp_path / "config.json"
    adaptive_path = tmp_path / "adaptive.json"
    config_path.write_text(json.dumps(base), encoding="utf-8")
    adaptive = {
        "schema_version": "brace-adaptive-policy-v1",
        "status": "ACTIVE_SHADOW_PARAMETERS",
        "apply_to_shadow_decisions": True,
        "never_apply_to_real_broker": True,
        "base_config_sha256": learning.canonical_sha256(base),
        "active_overrides": {"minimum_confidence": 0.69},
        "content_sha256": "test",
    }
    adaptive_path.write_text(json.dumps(adaptive), encoding="utf-8")
    config, raw = config_module.load_config(config_path, adaptive_path)
    assert config.minimum_confidence == 0.69
    assert raw["adaptive_policy_runtime"]["applied"] is True


def test_config_rejects_unknown_adaptive_field(tmp_path: Path):
    base = base_config()
    config_path = tmp_path / "config.json"
    adaptive_path = tmp_path / "adaptive.json"
    config_path.write_text(json.dumps(base), encoding="utf-8")
    adaptive = {
        "schema_version": "brace-adaptive-policy-v1",
        "status": "ACTIVE_SHADOW_PARAMETERS",
        "apply_to_shadow_decisions": True,
        "never_apply_to_real_broker": True,
        "base_config_sha256": learning.canonical_sha256(base),
        "active_overrides": {"real_broker_integration_enabled": True},
    }
    adaptive_path.write_text(json.dumps(adaptive), encoding="utf-8")
    with pytest.raises(ValueError, match="non-whitelisted"):
        config_module.load_config(config_path, adaptive_path)
