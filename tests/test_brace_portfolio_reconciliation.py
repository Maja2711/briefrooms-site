from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from reconcile_portfolio_10k_executions import reconcile


def _public(applied: bool) -> dict:
    payload = {
        "starting_capital_pln": 10000.0,
        "cash_pln": 1000.0,
        "base_cash_pln": 1000.0,
        "positions": [
            {
                "id": "googl",
                "status": "active",
                "quantity": 0.4,
                "entry_price": 300.0,
                "entry_fx_to_pln": 4.0,
                "entry_notional_pln": 480.0,
                "entry_fee_pln": 2.4,
                "entry_value_pln": 482.4,
                "current_price": 360.0,
                "current_fx_to_pln": 3.73,
                "current_value_pln": 537.12,
                "current_price_updated_at": "2026-08-18T17:00:00+00:00",
                "current_fx_updated_at": "2026-08-18T17:00:00+00:00",
                "review_flag": "HOLD",
            }
        ],
        "closed_positions": [],
    }
    if applied:
        payload["execution_reconciliation"] = {
            "applied_order_ids": ["paper-order-reduce-googl"]
        }
    return payload


def _paper() -> dict:
    return {
        "cash_pln": 1000.0,
        "positions": [
            {
                "id": "googl",
                "status": "active",
                "quantity": 0.35,
                "entry_price": 300.0,
                "entry_fx_to_pln": 4.0,
                "entry_notional_pln": 480.0,
                "entry_fee_pln": 2.4,
                "entry_value_pln": 482.4,
                "current_price": 350.0,
                "current_fx_to_pln": 3.70,
                "current_value_pln": 453.25,
                "current_price_updated_at": "2026-08-10T18:20:00+00:00",
                "current_fx_updated_at": "2026-07-31T21:00:00+00:00",
                "review_flag": "REDUCE",
            }
        ],
        "closed_positions": [],
    }


def _orders() -> dict:
    return {
        "orders": [
            {
                "order_id": "paper-order-reduce-googl",
                "status": "PAPER_EXECUTED",
                "action": "REDUCE",
                "sell_instrument": "googl",
            }
        ]
    }


def test_already_applied_reduce_is_idempotent_and_preserves_fresh_market_data():
    public = _public(applied=True)
    before = copy.deepcopy(public["positions"][0])

    result = reconcile(public, _paper(), _orders())

    after = public["positions"][0]
    assert result["newly_applied_orders"] == 0
    assert after["current_price"] == before["current_price"]
    assert after["current_fx_to_pln"] == before["current_fx_to_pln"]
    assert after["current_value_pln"] == before["current_value_pln"]
    assert after["current_price_updated_at"] == before["current_price_updated_at"]
    assert after["current_fx_updated_at"] == before["current_fx_updated_at"]
    assert after["quantity"] == before["quantity"]


def test_new_reduce_updates_execution_state_without_regressing_market_observations():
    public = _public(applied=False)
    fresh = copy.deepcopy(public["positions"][0])

    result = reconcile(public, _paper(), _orders())

    after = public["positions"][0]
    assert result["newly_applied_orders"] == 1
    assert after["quantity"] == 0.35
    assert after["review_flag"] == "REDUCE"
    assert after["current_price"] == fresh["current_price"]
    assert after["current_fx_to_pln"] == fresh["current_fx_to_pln"]
    assert after["current_value_pln"] == fresh["current_value_pln"]
    assert after["current_price_updated_at"] == fresh["current_price_updated_at"]
    assert after["current_fx_updated_at"] == fresh["current_fx_updated_at"]
    assert "paper-order-reduce-googl" in public["execution_reconciliation"]["applied_order_ids"]
    assert public["execution_reconciliation"]["market_data_authority"] == "portfolio_10k_hourly_prices.py"
