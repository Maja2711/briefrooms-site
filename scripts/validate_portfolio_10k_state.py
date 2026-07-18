#!/usr/bin/env python3
"""Validate execution and accounting consistency of the public 10K portfolio."""
from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any, Dict, List

import portfolio_10k_weekly as base


def close(left: Any, right: Any, tolerance: float = 0.03) -> bool:
    a = base.finite(left)
    b = base.finite(right)
    return a is not None and b is not None and abs(a - b) <= tolerance


def validate_state(data: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    positions = data.get("positions")
    if not isinstance(positions, list) or not positions:
        return ["portfolio.positions: expected a non-empty list"]
    ids = [str(position.get("id") or "") for position in positions]
    symbols = [str(position.get("broker_symbol") or "") for position in positions]
    if len(set(ids)) != len(ids) or "" in ids:
        errors.append("portfolio.positions.id: values must be non-empty and unique")
    if len(set(symbols)) != len(symbols) or "" in symbols:
        errors.append("portfolio.positions.broker_symbol: values must be non-empty and unique")
    weights = [base.finite(position.get("target_weight")) for position in positions]
    if any(weight is None or weight <= 0 for weight in weights):
        errors.append("portfolio.positions.target_weight: all weights must be finite and positive")
    elif not math.isclose(sum(weights), 1.0, abs_tol=1e-8):
        errors.append("portfolio.positions.target_weight: weights must sum to 1")

    active = []
    for position in positions:
        label = f"position[{position.get('id')}]"
        status = position.get("status")
        if status not in {"active", "pending"}:
            errors.append(f"{label}.status: expected active or pending")
            continue
        monitoring = position.get("report_monitoring") or {}
        threshold = base.finite((monitoring.get("price_alerts") or {}).get("daily_move_percent"))
        if monitoring.get("enabled") is not True or threshold is None or threshold <= 0:
            errors.append(f"{label}.report_monitoring: enabled price monitoring with a positive daily threshold is required")
        if status == "pending":
            if any(position.get(key) is not None for key in ("entry_price", "entry_timestamp_utc", "quantity", "entry_value_pln")):
                errors.append(f"{label}: pending position contains execution fields")
            continue
        active.append(position)
        for key in (
            "entry_price", "entry_fx_to_pln", "quantity", "entry_value_local",
            "entry_notional_pln", "entry_value_pln", "current_value_pln",
        ):
            value = base.finite(position.get(key))
            if value is None or value <= 0:
                errors.append(f"{label}.{key}: expected a finite positive number")
        if not position.get("entry_timestamp_utc"):
            errors.append(f"{label}.entry_timestamp_utc: required for active position")
        notional = base.finite(position.get("entry_notional_pln"))
        fee = base.finite(position.get("entry_fee_pln")) or 0.0
        entry_value = base.finite(position.get("entry_value_pln"))
        if notional is not None and entry_value is not None and not close(notional + fee, entry_value):
            errors.append(f"{label}.entry_value_pln: must equal entry_notional_pln plus entry_fee_pln")

    active_count = len(active)
    expected_status = "active" if active_count == len(positions) else "partially_active" if active_count else "pending_open"
    if data.get("status") != expected_status:
        errors.append(f"portfolio.status: expected {expected_status}, got {data.get('status')}")

    executions: Dict[str, Dict[str, Any]] = {}
    for batch_index, batch in enumerate(data.get("staged_entry_batches") or []):
        for execution_index, execution in enumerate(batch.get("opened") or []):
            symbol = str(execution.get("symbol") or "")
            label = f"staged_entry_batches[{batch_index}].opened[{execution_index}]"
            if symbol in executions:
                errors.append(f"{label}.symbol: duplicate staged execution for {symbol}")
                continue
            executions[symbol] = execution
    by_symbol = {position.get("broker_symbol"): position for position in positions}
    for symbol, execution in executions.items():
        position = by_symbol.get(symbol)
        if not position:
            errors.append(f"staged execution {symbol}: symbol is not in portfolio")
            continue
        if position.get("status") != "active":
            errors.append(f"position[{position.get('id')}].status: audited staged execution must be active")
        for source_key, position_key in (
            ("price", "entry_price"), ("fx_to_pln", "entry_fx_to_pln"),
            ("entry_value_pln", "entry_value_pln"),
        ):
            if not close(execution.get(source_key), position.get(position_key), tolerance=1e-5):
                errors.append(f"position[{position.get('id')}].{position_key}: differs from staged execution")

    start = base.finite(data.get("starting_capital_pln"))
    if start is None or start <= 0:
        errors.append("portfolio.starting_capital_pln: expected a finite positive number")
        return errors
    spent = sum(base.finite(position.get("entry_value_pln")) or 0.0 for position in active)
    expected_base_cash = round(start - spent, 2)
    if not close(data.get("base_cash_pln"), expected_base_cash):
        errors.append(f"portfolio.base_cash_pln: expected {expected_base_cash:.2f}")
    dividends = sum(base.finite(position.get("dividends_pln")) or 0.0 for position in active)
    expected_cash = round(expected_base_cash + dividends, 2)
    if not close(data.get("cash_pln"), expected_cash):
        errors.append(f"portfolio.cash_pln: expected {expected_cash:.2f}")
    current_value = sum(base.finite(position.get("current_value_pln")) or 0.0 for position in active)
    expected_total = round(expected_cash + current_value, 2)
    if not close(data.get("total_value_pln"), expected_total):
        errors.append(f"portfolio.total_value_pln: expected {expected_total:.2f}")
    if not close(data.get("total_return_pln"), expected_total - start):
        errors.append(f"portfolio.total_return_pln: expected {expected_total - start:.2f}")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--portfolio", type=Path, default=base.DATA_PATH)
    args = parser.parse_args()
    errors = validate_state(base.load_json(args.portfolio))
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        raise SystemExit(1)
    print("Portfolio 10K state is valid")


if __name__ == "__main__":
    main()
