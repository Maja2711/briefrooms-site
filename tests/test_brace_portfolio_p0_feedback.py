from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import brace_portfolio_decision as decision
import brace_portfolio_self_learning as learning
import brace_portfolio_user_control as user_control


def config() -> SimpleNamespace:
    return SimpleNamespace(
        transaction_cost_buffer=0.01,
        minimum_score_improvement=8.0,
        minimum_expected_alpha=0.025,
        minimum_holding_period_days=30,
        rotation_cooldown_days=21,
        minimum_confidence=0.65,
        max_single_stock_weight=0.18,
        max_broad_etf_weight=0.30,
        autonomy_mode="PAPER_EXECUTION",
    )


def replacement_pending() -> dict:
    positions = [
        {
            "instrument_id": "old",
            "broker_symbol": "OLD",
            "entry_date": "2025-01-01",
            "current_weight": 0.10,
            "target_weight": 0.10,
            "final_score": 40.0,
            "risk_adjusted_score": 0.1,
            "risk_score": 50.0,
            "confidence_score": 0.90,
            "expected_return_base": 0.05,
            "expected_drawdown": 0.20,
            "current_price": 100.0,
            "current_fx_to_pln": 4.0,
        }
    ]
    candidates = [
        {
            "instrument_id": "new",
            "broker_symbol": "NEW",
            "asset_type": "STOCK",
            "eligible_for_rotation": True,
            "final_score": 90.0,
            "risk_adjusted_score": 2.0,
            "expected_return_base": 0.20,
            "expected_drawdown": 0.10,
            "confidence_score": 0.90,
            "current_price": 50.0,
            "current_fx_to_pln": 4.0,
        }
    ]
    return decision.build_pending_decisions(
        positions,
        candidates,
        {"target_weights": {"old": 0.10}, "rules_passed": True},
        config(),
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        "3.0.0",
        "2026-01-01T00:00:00+00:00",
        False,
    )


def market() -> dict:
    return {
        "instruments": {
            "old": {
                "history": [
                    {"date": "2026-01-01", "close": 100.0},
                    {"date": "2026-01-08", "close": 105.0},
                ]
            },
            "new": {
                "history": [
                    {"date": "2026-01-01", "close": 50.0},
                    {"date": "2026-01-08", "close": 60.0},
                ]
            },
        }
    }


def test_replace_enters_economic_shadow_stream_with_both_legs():
    pending = replacement_pending()
    assert pending["decisions"][0]["action"] == "REPLACE"
    assert pending["decisions"][0]["economic_decision_id"]

    shadow = decision.shadow_record(
        pending,
        [{"id": "old", "review_flag": "HOLD"}],
        datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert len(shadow["economic_decisions"]) == 1
    row = shadow["economic_decisions"][0]
    assert row["record_type"] == "economic_decision"
    assert row["learning_eligible"] is True
    assert row["instrument"] == "old"
    assert row["replacement_instrument"] == "new"
    assert row["signal_price"] == 100.0
    assert row["replacement_signal_price"] == 50.0
    assert row["costs"] == 0.01


def test_replace_outcome_is_replacement_minus_incumbent_after_cost():
    shadow = decision.shadow_record(
        replacement_pending(),
        [{"id": "old", "review_flag": "HOLD"}],
        datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    created = learning.collect_due_outcomes(
        {"runs": [shadow]}, market(), [], date(2026, 1, 8)
    )

    assert len(created) == 1
    outcome = created[0]
    assert outcome["comparison_basis"] == (
        "replacement_minus_incumbent_after_incremental_cost"
    )
    assert outcome["incumbent_return"] == 0.05
    assert outcome["replacement_return"] == 0.20
    assert outcome["gross_rotation_delta"] == 0.15
    assert outcome["incremental_cost"] == 0.01
    assert outcome["signed_excess_return"] == 0.14
    assert outcome["direction_correct"] is True


def test_replace_missing_second_leg_fails_closed():
    shadow = decision.shadow_record(
        replacement_pending(),
        [{"id": "old", "review_flag": "HOLD"}],
        datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    shadow["economic_decisions"][0]["replacement_signal_price"] = None

    assert learning.collect_due_outcomes(
        {"runs": [shadow]}, market(), [], date(2026, 1, 8)
    ) == []


def test_three_horizons_never_exceed_one_effective_sample_per_decision():
    events = []
    for horizon, weight in learning.HORIZON_WEIGHTS.items():
        events.append(
            {
                "outcome_event_id": f"econ:{horizon}",
                "decision_id": "decision-1",
                "economic_decision_id": "economic-1",
                "action": "REPLACE",
                "horizon_days": horizon,
                "horizon_weight": weight,
                "eligible_for_learning": True,
                "direction_correct": True,
                "signed_excess_return": 0.02,
            }
        )

    stats = learning.learning_statistics(events)
    assert stats["economic_decisions"] == 1
    assert stats["effective_samples"] == 1.0
    assert stats["by_action"]["REPLACE"]["effective_samples"] == 1.0

    duplicate = dict(events[0], outcome_event_id="duplicate-event")
    duplicate_stats = learning.learning_statistics(events + [duplicate])
    assert duplicate_stats["effective_samples"] == 1.0


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_explicit_human_rearm_restores_probationary_paper_control_after_fallback(
    tmp_path, monkeypatch
):
    paths = {
        "AUTH": tmp_path / "control_authorization.json",
        "REGISTRY": tmp_path / "methodology_registry.json",
        "PAPER": tmp_path / "paper_portfolio.json",
        "ANALYSIS": tmp_path / "analysis.json",
        "PENDING": tmp_path / "pending_decisions.json",
        "SHADOW": tmp_path / "shadow_log.json",
        "HISTORY": tmp_path / "promotion_history.json",
        "OPERATIONAL": tmp_path / "operational_state.json",
        "ADAPTIVE": tmp_path / "adaptive_policy.json",
        "LEARNING": tmp_path / "learning_state.json",
        "BASELINE_PORTFOLIO_PATH": tmp_path / "portfolio_10k.json",
    }
    for name, path in paths.items():
        monkeypatch.setattr(user_control, name, path)

    _write_json(
        paths["AUTH"],
        {
            "schema_version": "brace-control-authorization-v1",
            "enabled": True,
            "paper_only": True,
            "real_broker_connected": False,
            "controller_status": "PROBATIONARY_CONTROL",
            "authorized_at": "2026-08-02T20:37:00+00:00",
            "authorized_by": "site_owner",
            "reason_pl": "test",
            "reason_en": "test",
        },
    )
    _write_json(paths["BASELINE_PORTFOLIO_PATH"], {"portfolio_id": "baseline", "positions": []})
    _write_json(paths["PAPER"], {"portfolio_id": "paper", "paper_only": True})
    _write_json(paths["HISTORY"], {"schema_version": "1.0.0", "records": []})
    for name in ("ANALYSIS", "PENDING", "SHADOW", "OPERATIONAL", "ADAPTIVE", "LEARNING"):
        _write_json(paths[name], {})
    _write_json(
        paths["REGISTRY"],
        {
            "controller_state": "FALLBACK_BASELINE",
            "champion_methodology_id": "portfolio-10k-baseline",
            "challenger_methodology_id": "brace-portfolio-engine",
            "baseline_methodology_id": "portfolio-10k-baseline",
            "methodologies": [
                {
                    "methodology_id": "portfolio-10k-baseline",
                    "status": "ACTIVE_BASELINE",
                    "parameters": {},
                    "validation_results": {},
                },
                {
                    "methodology_id": "brace-portfolio-engine",
                    "status": "FALLBACK_BASELINE",
                    "parameters": {"real_broker_access": False},
                    "validation_results": {
                        "user_authorized_paper_control": {
                            "paper_only": True,
                            "remaining_automatic_promotion_gates_preserved": True,
                        }
                    },
                },
            ],
        },
    )

    monkeypatch.setattr(user_control, "public_snapshot", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(user_control, "publish", lambda *_args, **_kwargs: None)

    result = user_control.activate(datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc))
    registry = json.loads(paths["REGISTRY"].read_text(encoding="utf-8"))
    methods = {row["methodology_id"]: row for row in registry["methodologies"]}

    assert result == {
        "controller_status": "PROBATIONARY_CONTROL",
        "previous_controller": "FALLBACK_BASELINE",
    }
    assert registry["controller_state"] == "PROBATIONARY_CONTROL"
    assert registry["champion_methodology_id"] == "brace-portfolio-engine"
    assert methods["brace-portfolio-engine"]["status"] == "PROBATIONARY_CONTROL"
    assert methods["portfolio-10k-baseline"]["status"] == "FALLBACK_BASELINE"
    assert methods["brace-portfolio-engine"]["parameters"]["real_broker_access"] is False
    assert methods["brace-portfolio-engine"]["validation_results"]["user_authorized_paper_control"]["paper_only"] is True
