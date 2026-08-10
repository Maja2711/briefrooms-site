from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from portfolio_10k_guardian import cash_deployment_plan, health_receipt  # noqa: E402


POLICY = {
    "minimum_position_weight": 0.05,
    "max_positions": 12,
    "minimum_confidence": 0.65,
    "probationary_minimum_confidence": 0.70,
    "target_annual_return": 0.10,
    "max_expected_drawdown": 0.25,
    "analysis_max_price_age_hours": 72.0,
    "max_probation_new_position_weight": 0.10,
    "max_single_stock_weight": 0.18,
    "max_broad_etf_weight": 0.30,
    "transaction_cost_buffer": 0.01,
    "monitoring_max_price_age_hours": 6.0,
}


def portfolio(now, cash=455.25):
    return {
        "last_updated_at": now.isoformat(),
        "total_value_pln": 10000.0,
        "cash_pln": cash,
        "positions": [
            {
                "id": "core",
                "status": "active",
                "current_value_pln": 10000.0 - cash,
                "current_price_updated_at": now.isoformat(),
            }
        ],
    }


def analysis(now):
    return {
        "generated_at": now.isoformat(),
        "methodology_version": "brace-test",
        "optimization": {"rules_passed": True},
        "candidates": [
            {
                "instrument_id": "jpm",
                "broker_symbol": "JPM.US",
                "asset_type": "STOCK",
                "eligible_for_rotation": True,
                "confidence_score": 0.90,
                "expected_return_base": 0.18,
                "expected_drawdown": 0.20,
                "risk_adjusted_score": 0.80,
                "final_score": 66.0,
            }
        ],
    }


def test_current_cash_below_minimum_does_not_force_purchase():
    now = datetime(2026, 8, 10, 17, 0, tzinfo=timezone.utc)
    plan, decision = cash_deployment_plan(
        portfolio(now, cash=455.25), analysis(now), {}, {}, {}, POLICY, now
    )
    assert plan["status"] == "WAITING_MINIMUM_CASH"
    assert decision is None


def test_funded_cash_and_fresh_strong_candidate_create_add_signal():
    now = datetime(2026, 8, 10, 17, 0, tzinfo=timezone.utc)
    plan, decision = cash_deployment_plan(
        portfolio(now, cash=700.0), analysis(now), {}, {}, {}, POLICY, now
    )
    assert plan["status"] == "CASH_DEPLOYMENT_SIGNAL_CREATED"
    assert decision is not None
    assert decision["action"] == "ADD"
    assert decision["instrument"] == "jpm"
    assert 0.05 <= decision["proposed_weight"] <= 0.10
    assert decision["checks"]["paper_only"] is True


def test_stale_analysis_blocks_cash_deployment():
    now = datetime(2026, 8, 10, 17, 0, tzinfo=timezone.utc)
    old = now - timedelta(hours=73)
    plan, decision = cash_deployment_plan(
        portfolio(now, cash=700.0), analysis(old), {}, {}, {}, POLICY, now
    )
    assert plan["status"] == "BLOCKED_STALE_BRACE_ANALYSIS"
    assert decision is None


def test_guardian_detects_accounting_corruption():
    now = datetime(2026, 8, 10, 17, 0, tzinfo=timezone.utc)
    broken = portfolio(now, cash=455.25)
    broken["total_value_pln"] = 9990.0
    receipt = health_receipt(broken, POLICY, now)
    assert receipt["status"] == "DEGRADED"
    assert "ACCOUNTING_INVARIANT_FAILED" in receipt["errors"]
