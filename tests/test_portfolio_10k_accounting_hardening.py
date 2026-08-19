from __future__ import annotations

from datetime import datetime, timezone

from scripts.portfolio_10k_cash_yield_backfill import correction_from_ledger
from scripts.sync_portfolio_10k_paper_mtm import sync


def _public():
    return {
        "last_updated_at": "2026-08-19T12:30:00+00:00",
        "starting_capital_pln": 10000.0,
        "base_cash_pln": 1423.92,
        "cash_pln": 1423.92,
        "total_value_pln": 9904.56,
        "positions": [
            {
                "id": "jpm",
                "status": "active",
                "quantity": 0.53742229,
                "current_price": 363.25,
                "current_fx_to_pln": 3.72425,
                "current_value_pln": 727.04,
                "current_price_updated_at": "2026-08-18T19:55:00+00:00",
            }
        ],
        "snapshots": [
            {"timestamp_utc": "2026-08-02T20:59:08+00:00", "cash_pln": 0.0}
        ],
    }


def _paper():
    return {
        "cash_pln": 1423.92,
        "total_value_pln": 9901.0,
        "positions": [
            {
                "id": "jpm",
                "status": "paper_active",
                "quantity": 0.53742229,
                "current_value_pln": 726.01,
            }
        ],
        "transactions": [
            {
                "instrument_id": "novo",
                "side": "SELL",
                "price": 306.14356219,
                "fx_to_pln": 0.57528001,
                "quantity": 2.593985,
                "transaction_cost_pln": 1.60,
                "executed_at": "2026-08-03T10:03:00+00:00",
            },
            {
                "instrument_id": "googl",
                "side": "SELL",
                "price": 353.89575,
                "fx_to_pln": 3.72670007,
                "quantity": 0.73608088,
                "transaction_cost_pln": 3.40,
                "executed_at": "2026-08-10T18:28:44+00:00",
            },
        ],
    }


def test_pre_execution_mtm_sync_uses_fresh_public_total_and_market_fields():
    public = _public()
    paper = _paper()
    receipt = sync(public, paper, datetime(2026, 8, 19, 12, 31, tzinfo=timezone.utc))
    assert receipt["quantity_parity"] is True
    assert receipt["cash_parity"] is True
    assert paper["total_value_pln"] == 9904.56
    assert paper["positions"][0]["current_price"] == 363.25
    assert paper["positions"][0]["current_value_pln"] == 727.04


def test_pre_execution_mtm_sync_fails_closed_on_quantity_drift():
    public = _public()
    paper = _paper()
    paper["positions"][0]["quantity"] += 0.01
    try:
        sync(public, paper, datetime(2026, 8, 19, 12, 31, tzinfo=timezone.utc))
    except AssertionError as exc:
        assert "Quantity mismatch" in str(exc)
    else:
        raise AssertionError("quantity drift must fail closed")


def test_ledger_backfill_reconstructs_missing_pre_activation_interest():
    public = _public()
    paper = _paper()
    activation = datetime(2026, 8, 10, 18, 43, 44, tzinfo=timezone.utc)
    pln, events, initial = correction_from_ledger(
        public, paper, activation=activation, annual_rate=0.0375
    )
    usd, _, _ = correction_from_ledger(
        public, paper, activation=activation, annual_rate=0.03625
    )
    assert initial == 0.0
    assert len(events) == 2
    assert abs(pln - 0.34535) < 0.0001
    assert abs(usd - 0.33384) < 0.0001
