from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "investments_wes_spx_brace_bridge.py"
spec = importlib.util.spec_from_file_location("wes_spx_bridge", MODULE_PATH)
bridge = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(bridge)


def active_shadow(exposure: float = 0.9, updated_at: str = "2026-08-19T09:00:00+00:00") -> dict:
    return {
        "schema_version": "6.0.0",
        "generation_id": "spx-orthogonal-core-v6",
        "candidate_signature": "sig",
        "holdout_accessed": False,
        "live_orders": False,
        "autonomous_trading": False,
        "updated_at": updated_at,
        "observations_collected": 70,
        "warmup_required": 70,
        "latest_market_date": "2026-08-19",
        "status": "shadow_active_no_orders",
        "latest_regime": "risk_on_test",
        "family_scores": {
            "price_trend": 0.7,
            "rates": 0.2,
            "liquidity": 0.6,
            "options_vix": 0.4,
        },
        "single_champion_selected": False,
        "candidate_snapshots": [
            {"candidate_name": f"c{i}", "target_exposure_next_session": exposure}
            for i in range(8)
        ],
    }


def warmup_shadow() -> dict:
    return {
        "generation_id": "spx-orthogonal-core-v6",
        "holdout_accessed": False,
        "live_orders": False,
        "autonomous_trading": False,
        "updated_at": "2026-08-19T09:00:00+00:00",
        "observations_collected": 12,
        "warmup_required": 70,
        "status": "warming_up",
        "candidate_snapshots": [],
    }


def week(risk_model: str = "v5-plan", closed: bool = False) -> dict:
    item = {
        "instrument_id": "sp500_futures",
        "direction": "long",
        "entry_price": 6500.0,
        "entry_captured_at": "2026-08-19T10:00:00+00:00",
        "continuous_entry_decision": {
            "direction": "long",
            "strategy_id": "weekly_trend",
            "raw_score": 55.0,
        },
        "risk_plan": {
            "model_version": risk_model,
            "stop_loss_price": 6400.0,
            "take_profit_price": 6700.0,
        },
        "position_legs": [],
    }
    if closed:
        item["position_legs"] = [
            {
                "entry_captured_at": "2026-08-19T10:00:00+00:00",
                "exit_captured_at": "2026-08-21T20:00:00+00:00",
                "exit_reason": "scheduled_week_close",
                "net_result_percent": 1.25,
            }
        ]
    return {
        "week_id": "2026-W34",
        "forecast_locked_at": "2026-08-17T01:24:18+00:00",
        "instruments": [item],
    }


def report() -> dict:
    return {"checked_at": "2026-08-19T10:00:30+00:00", "actions": []}


def test_warmup_emits_no_opinion():
    state = bridge.brace_specialist_state(warmup_shadow())
    assert state["available"] is False
    assert state["stance"] == "unavailable"
    assert state["reason"] == "brace_spx_warmup_no_opinion"


def test_active_long_risk_on_is_strong_agreement():
    state = bridge.brace_specialist_state(active_shadow(0.9))
    pit = bridge.point_in_time_status("2026-08-19T10:00:00+00:00", state)
    rel = bridge.relationship("long", state, pit)
    assert state["stance"] == "risk_on"
    assert pit["eligible"] is True
    assert rel["class"] == "STRONG_AGREEMENT"
    assert rel["alpha_eligible"] is True


def test_active_long_defensive_is_strong_conflict():
    state = bridge.brace_specialist_state(active_shadow(0.1))
    pit = bridge.point_in_time_status("2026-08-19T10:00:00+00:00", state)
    rel = bridge.relationship("long", state, pit)
    assert state["stance"] == "defensive"
    assert rel["class"] == "STRONG_CONFLICT"


def test_retrospective_brace_state_is_excluded_from_alpha():
    state = bridge.brace_specialist_state(active_shadow(0.9, updated_at="2026-08-19T11:00:00+00:00"))
    pit = bridge.point_in_time_status("2026-08-19T10:00:00+00:00", state)
    rel = bridge.relationship("long", state, pit)
    assert pit["eligible"] is False
    assert pit["reason"] == "brace_state_created_after_wes_decision"
    assert rel["class"] == "UNAVAILABLE"
    assert rel["alpha_eligible"] is False


def test_governance_failure_cannot_emit_opinion():
    shadow = active_shadow(0.9)
    shadow["holdout_accessed"] = True
    state = bridge.brace_specialist_state(shadow)
    assert state["available"] is False
    assert state["reason"] == "brace_spx_governance_guard_failed"


def test_pre_and_post_freeze_baseline_then_wes_without_duplicate():
    ledger = bridge.capture(
        stage="pre-wes",
        week=week("v5-plan"),
        wes_report=report(),
        brace_shadow=active_shadow(0.9),
        ledger=bridge._new_ledger(),
        captured_at=datetime(2026, 8, 19, 10, 1, tzinfo=timezone.utc),
    )
    assert len(ledger["records"]) == 1
    row = ledger["records"][0]
    assert row["v5_counterfactual"]["risk_plan"]["model_version"] == "v5-plan"
    assert row["wes_actual"] is None
    frozen_brace = row["brace_spx"]

    ledger2 = bridge.capture(
        stage="post-wes",
        week=week("WES-1.0.0"),
        wes_report=report(),
        brace_shadow=active_shadow(0.1, updated_at="2026-08-19T10:00:30+00:00"),
        ledger=ledger,
        captured_at=datetime(2026, 8, 19, 10, 2, tzinfo=timezone.utc),
    )
    assert len(ledger2["records"]) == 1
    row2 = ledger2["records"][0]
    assert row2["v5_counterfactual"]["risk_plan"]["model_version"] == "v5-plan"
    assert row2["wes_actual"]["risk_plan"]["model_version"] == "WES-1.0.0"
    assert row2["brace_spx"] == frozen_brace
    assert row2["active_decision_influence"] is False
    assert row2["counterfactual_overlay"]["bounded_modifier_applied"] is False


def test_actual_outcome_settles_but_v5_counterfactual_stays_pending():
    ledger = bridge.capture(
        stage="pre-wes",
        week=week("v5-plan"),
        wes_report=report(),
        brace_shadow=active_shadow(0.9),
        ledger=bridge._new_ledger(),
        captured_at=datetime(2026, 8, 19, 10, 1, tzinfo=timezone.utc),
    )
    ledger = bridge.capture(
        stage="post-wes",
        week=week("WES-1.0.0", closed=True),
        wes_report=report(),
        brace_shadow=active_shadow(0.9),
        ledger=ledger,
        captured_at=datetime(2026, 8, 21, 20, 1, tzinfo=timezone.utc),
    )
    row = ledger["records"][0]
    assert row["outcome"]["wes_net_result_percent"] == 1.25
    assert row["outcome"]["incremental_wes_vs_v5_percent"] is None
    alpha = bridge.build_alpha_report(ledger)
    assert alpha["active_decision_influence"] is False
    assert alpha["bounded_influence_enabled"] is False
    assert alpha["by_relationship"]["STRONG_AGREEMENT"]["settled_wes_outcomes"] == 1
    assert alpha["counterfactual"]["resolved_wes_vs_v5_pairs"] == 0


def test_monitoring_observation_never_enters_alpha():
    w = {
        "week_id": "2026-W34",
        "instruments": [
            {
                "instrument_id": "sp500_futures",
                "direction": "neutral",
                "entry_price": None,
                "entry_captured_at": None,
                "risk_plan": None,
            }
        ],
    }
    r = {
        "checked_at": "2026-08-19T10:00:00+00:00",
        "actions": [
            {
                "instrument_id": "sp500_futures",
                "action": "monitor_no_trade",
                "direction": "long",
                "strategy_id": "weekly_trend",
                "raw_score": 40.0,
                "entry_class": "midweek_trigger",
            }
        ],
    }
    ledger = bridge.capture(
        stage="pre-wes",
        week=w,
        wes_report=r,
        brace_shadow=active_shadow(0.9, updated_at="2026-08-19T09:00:00+00:00"),
        ledger=bridge._new_ledger(),
        captured_at=datetime(2026, 8, 19, 10, 0, 1, tzinfo=timezone.utc),
    )
    row = ledger["records"][0]
    assert row["decision_type"] == "no_trade_monitoring"
    assert row["relationship"]["alpha_eligible"] is False
    assert row["relationship"]["reason"] == "non_actionable_monitoring_observation"
