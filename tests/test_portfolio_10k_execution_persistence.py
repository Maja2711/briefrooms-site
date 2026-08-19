from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from portfolio_10k_execution_ledger import authoritative_executions
from sync_portfolio_10k_from_transaction_ledger import sync


def _spgi() -> dict:
    return {
        "id": "spgi",
        "label": "S&P Global",
        "broker_symbol": "SPGI.US",
        "market_symbol": "SPGI",
        "currency": "USD",
        "status": "active",
        "quantity": 0.464792,
        "entry_price": 451.299988,
        "entry_fx_to_pln": 3.7949,
        "entry_notional_pln": 795.02,
        "entry_fee_pln": 4.98,
        "entry_value_pln": 800.0,
        "current_price": 418.04,
        "current_fx_to_pln": 3.7358,
        "current_value_pln": 724.30,
        "review_flag": "THESIS_REVIEW",
    }


def _jpm() -> dict:
    return {
        "id": "jpm",
        "label": "JPMorgan Chase",
        "broker_symbol": "JPM.US",
        "market_symbol": "JPM",
        "currency": "USD",
        "asset_type": "STOCK",
        "status": "paper_active",
        "quantity": 0.53742229,
        "entry_date": "2026-08-18",
        "entry_timestamp_utc": "2026-08-18T17:56:50+00:00",
        "entry_price": 361.631259,
        "entry_price_type": "fresh_completed_5m_close_after_signal_paper",
        "entry_fx_to_pln": 3.73559999,
        "entry_notional_pln": 726.01,
        "entry_fee_pln": 2.54,
        "entry_value_pln": 728.55,
        "current_price": 361.631259,
        "current_fx_to_pln": 3.73559999,
        "current_value_pln": 726.01,
        "current_price_updated_at": "2026-08-18T17:50:00+00:00",
        "review_flag": "HOLD",
    }


def _paper() -> dict:
    spgi_closed = _spgi()
    spgi_closed.update({
        "status": "paper_closed",
        "exit_price": 421.0785,
        "exit_fx_to_pln": 3.73559999,
        "exit_timestamp_utc": "2026-08-18T17:56:50+00:00",
        "exit_value_pln": 728.55,
    })
    rationale_pl = "Rotacja SPGI.US do JPM.US wykonana przez BRACE."
    rationale_en = "BRACE executed the SPGI.US to JPM.US rotation."
    return {
        "starting_capital_pln": 10000.0,
        "cash_pln": 1423.91,
        "positions": [_jpm()],
        "closed_positions": [spgi_closed],
        "transactions": [
            {
                "transaction_id": "paper-trade-sell-spgi",
                "order_id": "paper-order-rotation-spgi-jpm",
                "decision_id": "rotation-spgi-jpm",
                "instrument_id": "spgi",
                "side": "SELL",
                "price": 421.0785,
                "fx_to_pln": 3.73559999,
                "quantity": 0.464792,
                "transaction_cost_pln": 2.56,
                "executed_at": "2026-08-18T17:56:50+00:00",
                "rationale_pl": rationale_pl,
                "rationale_en": rationale_en,
            },
            {
                "transaction_id": "paper-trade-buy-jpm",
                "order_id": "paper-order-rotation-spgi-jpm",
                "decision_id": "rotation-spgi-jpm",
                "instrument_id": "jpm",
                "side": "BUY",
                "price": 361.631259,
                "fx_to_pln": 3.73559999,
                "quantity": 0.53742229,
                "transaction_cost_pln": 2.54,
                "executed_at": "2026-08-18T17:56:50+00:00",
                "rationale_pl": rationale_pl,
                "rationale_en": rationale_en,
            },
        ],
    }


def _public(*, false_applied_marker: bool = False) -> dict:
    payload = {
        "starting_capital_pln": 10000.0,
        "cash_pln": 1423.91,
        "base_cash_pln": 1423.91,
        "positions": [_spgi()],
        "closed_positions": [],
    }
    if false_applied_marker:
        payload["execution_reconciliation"] = {
            "applied_order_ids": ["paper-order-rotation-spgi-jpm"]
        }
    return payload


def test_transaction_history_reconstructs_replace_when_mutable_queue_lost_execution():
    paper = _paper()
    queue = {
        "orders": [
            {
                "order_id": "new-unrelated-order",
                "status": "WAITING_FOR_MARKET",
                "action": "ADD",
                "buy_instrument": "jpm",
            }
        ]
    }
    executions = authoritative_executions(paper, queue)
    rotation = next(item for item in executions if item["order_id"] == "paper-order-rotation-spgi-jpm")
    assert rotation["action"] == "REPLACE"
    assert rotation["sell_instrument"] == "spgi"
    assert rotation["buy_instrument"] == "jpm"
    assert rotation["execution_authority"] == "paper_portfolio.transactions"


def test_transaction_ledger_sync_replaces_public_holding_without_historical_queue():
    public = _public()
    paper = _paper()

    result = sync(public, paper)

    ids = {position["id"] for position in public["positions"]}
    assert ids == {"jpm"}
    assert {position["id"] for position in public["closed_positions"]} == {"spgi"}
    assert "paper-order-rotation-spgi-jpm" in public["execution_reconciliation"]["applied_order_ids"]
    assert public["execution_reconciliation"]["execution_authority"] == "paper_portfolio.transactions"
    assert result["authority"] == "paper_portfolio.transactions"
    jpm = public["positions"][0]
    assert round(float(jpm["target_weight"]), 6) == 0.072855


def test_false_applied_marker_is_repaired_when_holdings_do_not_match_execution():
    public = _public(false_applied_marker=True)
    paper = _paper()

    result = sync(public, paper)

    ids = {position["id"] for position in public["positions"]}
    assert ids == {"jpm"}
    assert result["false_applied_markers_cleared"] == ["paper-order-rotation-spgi-jpm"]
    assert public["execution_reconciliation"]["false_applied_markers_cleared"] == ["paper-order-rotation-spgi-jpm"]
