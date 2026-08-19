#!/usr/bin/env python3
"""Persist the append-only BRACE execution ledger into public Portfolio 10K.

This is the durable bridge between the paper execution ledger and the public
PL/EN portfolio.  `paper_orders.json` is intentionally not the historical source
of truth: it is a mutable queue.  We reconstruct completed actions from
`paper_portfolio.transactions`, repair stale reconciliation markers when the
holdings do not reflect an execution, then reuse the accounting-safe reconciler.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from portfolio_10k_execution_ledger import authoritative_executions
from reconcile_portfolio_10k_executions import (
    PAPER_PATH,
    PUBLIC_PATH,
    find_position,
    finite,
    instrument_id,
    load,
    reconcile,
    write_atomic,
)


def _closed_ids(public: dict[str, Any]) -> set[str]:
    return {
        instrument_id(position)
        for position in public.get("closed_positions", []) or []
        if instrument_id(position)
    }


def _paper_active(paper: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        instrument_id(position): position
        for position in paper.get("positions", []) or []
        if instrument_id(position) and position.get("status") in {"active", "paper_active"}
    }


def _active_quantity_matches(
    public_positions: list[dict[str, Any]],
    paper_active: dict[str, dict[str, Any]],
    position_id: str,
) -> bool:
    public_position = find_position(public_positions, position_id)
    paper_position = paper_active.get(position_id)
    if public_position is None or paper_position is None:
        return False
    return abs(finite(public_position.get("quantity")) - finite(paper_position.get("quantity"))) <= 1e-8


def execution_is_reflected(
    public: dict[str, Any],
    paper: dict[str, Any],
    execution: dict[str, Any],
) -> bool:
    """Do not trust an applied marker unless holdings actually match the ledger."""
    action = str(execution.get("action") or "").upper()
    sell_id = str(execution.get("sell_instrument") or "").strip().lower()
    buy_id = str(execution.get("buy_instrument") or "").strip().lower()
    public_positions = list(public.get("positions", []) or [])
    paper_active = _paper_active(paper)
    closed_ids = _closed_ids(public)

    if action == "ADD":
        return bool(buy_id) and _active_quantity_matches(public_positions, paper_active, buy_id)
    if action == "REDUCE":
        return bool(sell_id) and _active_quantity_matches(public_positions, paper_active, sell_id)
    if action == "EXIT":
        return bool(sell_id) and find_position(public_positions, sell_id) is None and sell_id in closed_ids
    if action == "REPLACE":
        return (
            bool(sell_id and buy_id)
            and find_position(public_positions, sell_id) is None
            and sell_id in closed_ids
            and _active_quantity_matches(public_positions, paper_active, buy_id)
        )
    return False


def clear_false_applied_markers(
    public: dict[str, Any],
    paper: dict[str, Any],
    executions: list[dict[str, Any]],
) -> list[str]:
    state = dict(public.get("execution_reconciliation") or {})
    applied = {str(value) for value in state.get("applied_order_ids", []) or []}
    repaired: list[str] = []
    for execution in executions:
        order_id = str(execution.get("order_id") or "")
        if order_id and order_id in applied and not execution_is_reflected(public, paper, execution):
            applied.remove(order_id)
            repaired.append(order_id)
    if repaired:
        state["applied_order_ids"] = sorted(applied)
        state["false_applied_markers_cleared"] = sorted(repaired)
        public["execution_reconciliation"] = state
    return repaired


def ensure_public_allocation_metadata(public: dict[str, Any]) -> list[str]:
    """Guarantee a usable allocation basis for holdings created by paper trades."""
    starting = finite(public.get("starting_capital_pln"), 10000.0)
    repaired: list[str] = []
    if starting <= 0:
        return repaired
    for position in public.get("positions", []) or []:
        if finite(position.get("target_weight")) > 0:
            continue
        entry_value = finite(position.get("entry_value_pln"))
        if entry_value <= 0:
            entry_value = finite(position.get("current_value_pln"))
        if entry_value <= 0:
            continue
        position["target_weight"] = round(entry_value / starting, 8)
        repaired.append(instrument_id(position))
    return sorted(set(repaired))


def sync(public: dict[str, Any], paper: dict[str, Any]) -> dict[str, Any]:
    executions = authoritative_executions(paper, {"orders": []})
    if not executions:
        return {
            "changed": False,
            "execution_count": 0,
            "authority": "paper_portfolio.transactions",
        }

    cleared = clear_false_applied_markers(public, paper, executions)
    synthetic_queue = {"orders": executions}
    result = reconcile(public, paper, synthetic_queue)
    metadata_repaired = ensure_public_allocation_metadata(public)

    state = dict(public.get("execution_reconciliation") or {})
    state.update(
        {
            "version": "3.0.0-transaction-ledger-sync",
            "execution_authority": "paper_portfolio.transactions",
            "queue_role": "not_historical_authority",
            "durable_execution_count": len(executions),
            "false_applied_markers_cleared": sorted(set(cleared)),
            "allocation_metadata_repaired": metadata_repaired,
        }
    )
    public["execution_reconciliation"] = state
    result.update(
        {
            "execution_count": len(executions),
            "authority": "paper_portfolio.transactions",
            "false_applied_markers_cleared": cleared,
            "allocation_metadata_repaired": metadata_repaired,
        }
    )
    if cleared or metadata_repaired:
        result["changed"] = True
    return result


def main() -> None:
    public = load(PUBLIC_PATH)
    paper = load(PAPER_PATH)
    result = sync(public, paper)
    write_atomic(PUBLIC_PATH, public)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
