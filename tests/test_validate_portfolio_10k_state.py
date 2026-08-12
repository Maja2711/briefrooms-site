from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import validate_portfolio_10k_state as validator


def position(identifier: str, symbol: str, weight: float, entry: float, current: float, status: str = "active") -> dict:
    payload = {
        "id": identifier,
        "broker_symbol": symbol,
        "target_weight": weight,
        "status": status,
        "report_monitoring": {
            "enabled": True,
            "price_alerts": {"daily_move_percent": 0.07},
        },
        "entry_price": 100.0,
        "entry_fx_to_pln": 1.0,
        "quantity": entry / 100.0,
        "entry_value_local": entry,
        "entry_notional_pln": entry,
        "entry_fee_pln": 0.0,
        "entry_value_pln": entry,
        "current_value_pln": current,
        "entry_timestamp_utc": "2026-07-20T12:00:00+00:00",
        "dividends_pln": 0.0,
    }
    if status == "closed":
        payload["review_flag"] = "SOLD"
    return payload


def execution(symbol: str, entry: float) -> dict:
    return {
        "symbol": symbol,
        "price": 100.0,
        "fx_to_pln": 1.0,
        "entry_value_pln": entry,
    }


class Portfolio10KStateTests(unittest.TestCase):
    def test_legacy_open_portfolio_still_validates(self) -> None:
        data = {
            "status": "active",
            "starting_capital_pln": 10000.0,
            "positions": [position("core", "CORE.DE", 1.0, 9500.0, 9700.0)],
            "closed_positions": [],
            "staged_entry_batches": [{"opened": [execution("CORE.DE", 9500.0)]}],
            "base_cash_pln": 500.0,
            "cash_pln": 500.0,
            "total_value_pln": 10200.0,
            "total_return_pln": 200.0,
        }
        self.assertEqual(validator.validate_state(data), [])

    def test_reconciled_exit_uses_ledger_cash_and_closed_history(self) -> None:
        data = {
            "status": "active",
            "starting_capital_pln": 10000.0,
            "positions": [position("core", "CORE.DE", 0.6, 6000.0, 6200.0)],
            "closed_positions": [position("sold", "SOLD.DE", 0.4, 4000.0, 3900.0, status="closed")],
            "staged_entry_batches": [{
                "opened": [execution("CORE.DE", 6000.0), execution("SOLD.DE", 4000.0)]
            }],
            "base_cash_pln": 3900.0,
            "cash_pln": 3900.0,
            "cash_balance_pln": 3900.0,
            "total_value_pln": 10100.0,
            "total_return_pln": 100.0,
            "execution_reconciliation": {
                "executed_exit_instruments": ["sold"],
                "applied_order_ids": ["order-1"],
                "executed_actions": ["EXIT"],
                "affected_instruments": ["sold"],
            },
        }
        self.assertEqual(validator.validate_state(data), [])

    def test_reconciled_reduce_uses_ledger_cash_without_full_exit(self) -> None:
        # Regression for the 2026-08-10 GOOGL REDUCE: no full EXIT exists, but
        # cash from the partial sale is still authoritative and must not be
        # recomputed from original entry allocations.
        core = position("core", "CORE.DE", 0.85, 8500.0, 8500.0)
        googl = position("googl", "GOOGL.US", 0.15, 500.0, 525.0)
        googl["entry_value_local"] = 500.0
        googl["entry_notional_pln"] = 497.5
        googl["entry_fee_pln"] = 2.5
        googl["entry_value_pln"] = 500.0
        googl["quantity"] = 5.0
        data = {
            "status": "active",
            "starting_capital_pln": 10000.0,
            "positions": [core, googl],
            "closed_positions": [],
            "staged_entry_batches": [{
                "opened": [execution("CORE.DE", 8500.0), execution("GOOGL.US", 1500.0)]
            }],
            "base_cash_pln": 1000.0,
            "cash_pln": 1000.0,
            "cash_balance_pln": 1000.0,
            "total_value_pln": 10025.0,
            "total_return_pln": 25.0,
            "execution_reconciliation": {
                "executed_exit_instruments": [],
                "applied_order_ids": ["order-reduce-googl"],
                "executed_actions": ["REDUCE"],
                "affected_instruments": ["googl"],
            },
        }
        self.assertEqual(validator.validate_state(data), [])

    def test_reconciled_exit_rejects_inconsistent_cash(self) -> None:
        data = {
            "status": "active",
            "starting_capital_pln": 10000.0,
            "positions": [position("core", "CORE.DE", 0.6, 6000.0, 6200.0)],
            "closed_positions": [position("sold", "SOLD.DE", 0.4, 4000.0, 3900.0, status="closed")],
            "staged_entry_batches": [{
                "opened": [execution("CORE.DE", 6000.0), execution("SOLD.DE", 4000.0)]
            }],
            "base_cash_pln": 3800.0,
            "cash_pln": 3900.0,
            "cash_balance_pln": 3900.0,
            "total_value_pln": 10100.0,
            "total_return_pln": 100.0,
            "execution_reconciliation": {
                "executed_exit_instruments": ["sold"],
                "applied_order_ids": ["order-1"],
                "executed_actions": ["EXIT"],
            },
        }
        errors = validator.validate_state(data)
        self.assertTrue(any("base_cash_pln" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
