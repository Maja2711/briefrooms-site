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


def allocation_weight(position: Dict[str, Any]) -> float | None:
    weight = base.finite(position.get("target_weight"))
    return weight if weight is not None and weight > 0 else None


def validate_state(data: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    positions = data.get("positions")
    if not isinstance(positions, list) or not positions:
        return ["portfolio.positions: expected a non-empty list"]

    closed_positions = data.get("closed_positions") or []
    if not isinstance(closed_positions, list):
        errors.append("portfolio.closed_positions: expected a list")
        closed_positions = []

    ids = [str(position.get("id") or "") for position in positions]
    symbols = [str(position.get("broker_symbol") or "") for position in positions]
    if len(set(ids)) != len(ids) or "" in ids:
        errors.append("portfolio.positions.id: values must be non-empty and unique")
    if len(set(symbols)) != len(symbols) or "" in symbols:
        errors.append("portfolio.positions.broker_symbol: values must be non-empty and unique")

    active_weights = [allocation_weight(position) for position in positions]
    if any(weight is None for weight in active_weights):
        errors.append("portfolio.positions.target_weight: all weights must be finite and positive")
    else:
        active_weight_sum = sum(weight for weight in active_weights if weight is not None)
        if active_weight_sum > 1.0 + 1e-8:
            errors.append("portfolio.positions.target_weight: active weights cannot exceed 1")

        # After an executed exit, the active list legitimately sums to less than 1.
        # The original allocation remains auditable across active and closed positions.
        closed_weights = [allocation_weight(position) for position in closed_positions]
        if closed_positions and all(weight is not None for weight in closed_weights):
            combined = active_weight_sum + sum(weight for weight in closed_weights if weight is not None)
            if not math.isclose(combined, 1.0, abs_tol=1e-8):
                errors.append("portfolio active and closed target weights must sum to 1")
        elif not closed_positions and not math.isclose(active_weight_sum, 1.0, abs_tol=1e-8):
            errors.append("portfolio.positions.target_weight: weights must sum to 1")

    active: List[Dict[str, Any]] = []
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

    for position in closed_positions:
        label = f"closed_position[{position.get('id')}]"
        if position.get("status") != "closed":
            errors.append(f"{label}.status: expected closed")
        if not position.get("broker_symbol"):
            errors.append(f"{label}.broker_symbol: required")

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
    closed_by_symbol = {position.get("broker_symbol"): position for position in closed_positions}
    for symbol, execution in executions.items():
        position = by_symbol.get(symbol) or closed_by_symbol.get(symbol)
        if not position:
            errors.append(f"staged execution {symbol}: symbol is not in active or closed portfolio history")
            continue
        expected_position_status = "active" if symbol in by_symbol else "closed"
        if position.get("status") != expected_position_status:
            errors.append(
                f"position[{position.get('id')}].status: audited staged execution must be {expected_position_status}"
            )
        # Entry-audit fields describe the original staged fill. After REDUCE/ADD
        # the live ledger can legitimately carry proportionally adjusted notionals.
        # Price and FX remain immutable audit anchors; entry_value is checked only
        # for positions untouched by executed allocation actions.
        for source_key, position_key in (("price", "entry_price"), ("fx_to_pln", "entry_fx_to_pln")):
            if not close(execution.get(source_key), position.get(position_key), tolerance=1e-5):
                errors.append(f"position[{position.get('id')}].{position_key}: differs from staged execution")

    start = base.finite(data.get("starting_capital_pln"))
    if start is None or start <= 0:
        errors.append("portfolio.starting_capital_pln: expected a finite positive number")
        return errors

    reconciliation = data.get("execution_reconciliation") or {}
    reconciled_exits = reconciliation.get("executed_exit_instruments") or []
    applied_order_ids = reconciliation.get("applied_order_ids") or []
    executed_actions = reconciliation.get("executed_actions") or []
    affected_instruments = reconciliation.get("affected_instruments") or []
    # Any executed allocation action (EXIT, REDUCE, ADD or REPLACE) makes the
    # paper cash ledger authoritative. Previously only full EXIT was recognised,
    # which made every hourly run fail after a partial GOOGL REDUCE.
    reconciled = bool(applied_order_ids or executed_actions or affected_instruments or reconciled_exits)

    if reconciled:
        expected_cash = base.finite(data.get("cash_pln"))
        if expected_cash is None or expected_cash < 0:
            errors.append("portfolio.cash_pln: expected a finite non-negative number")
            expected_cash = 0.0
        if not close(data.get("base_cash_pln"), expected_cash):
            errors.append(f"portfolio.base_cash_pln: expected reconciled cash {expected_cash:.2f}")
        if data.get("cash_balance_pln") is not None and not close(data.get("cash_balance_pln"), expected_cash):
            errors.append(f"portfolio.cash_balance_pln: expected reconciled cash {expected_cash:.2f}")
        closed_ids = {str(position.get("id") or "").lower() for position in closed_positions}
        missing_closed = sorted(str(value).lower() for value in reconciled_exits if str(value).lower() not in closed_ids)
        if missing_closed:
            errors.append(f"portfolio.closed_positions: missing reconciled exits {', '.join(missing_closed)}")
    else:
        spent = sum(base.finite(position.get("entry_value_pln")) or 0.0 for position in active)
        expected_base_cash = round(start - spent, 2)
        if not close(data.get("base_cash_pln"), expected_base_cash):
            errors.append(f"portfolio.base_cash_pln: expected {expected_base_cash:.2f}")
        dividends = sum(base.finite(position.get("dividends_pln")) or 0.0 for position in active)
        expected_cash = round(expected_base_cash + dividends, 2)
        if not close(data.get("cash_pln"), expected_cash):
            errors.append(f"portfolio.cash_pln: expected {expected_cash:.2f}")

    current_value = sum(base.finite(position.get("current_value_pln")) or 0.0 for position in active)
    expected_total = round((expected_cash or 0.0) + current_value, 2)
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
