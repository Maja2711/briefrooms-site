#!/usr/bin/env python3
"""Reconcile completed BRACE paper trades into the public Portfolio 10K state.

The public portfolio mirrors authorised PAPER_EXECUTED allocation changes.  EXIT
removes a holding, REDUCE synchronises the reduced quantity, ADD synchronises or
creates the bought holding, and REPLACE applies both legs.  Only instruments
actually touched by executed orders are copied from the paper ledger, so stale
paper quotes cannot overwrite unrelated current holdings.
"""
from __future__ import annotations

import json
import math
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_PATH = ROOT / "data" / "investments" / "portfolio_10k.json"
PAPER_PATH = ROOT / "data" / "portfolio10k" / "paper_portfolio.json"
ORDERS_PATH = ROOT / "data" / "portfolio10k" / "paper_orders.json"
WARSAW = ZoneInfo("Europe/Warsaw")
SUPPORTED_ACTIONS = {"EXIT", "REDUCE", "ADD", "REPLACE"}


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def write_atomic(path: Path, payload: dict[str, Any]) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        temp = Path(tmp.name)
    temp.replace(path)


def instrument_id(position: dict[str, Any]) -> str:
    return str(position.get("id") or position.get("instrument_id") or "").strip().lower()


def find_position(positions: list[dict[str, Any]], position_id: str) -> dict[str, Any] | None:
    return next((p for p in positions if instrument_id(p) == position_id), None)


def sync_active_position(
    active_positions: list[dict[str, Any]],
    paper_active: dict[str, dict[str, Any]],
    position_id: str,
) -> bool:
    """Synchronise execution-sensitive fields for one active paper holding."""
    paper_source = paper_active.get(position_id)
    if paper_source is None:
        raise ValueError(f"Executed paper order references missing active position: {position_id}")
    current = find_position(active_positions, position_id)
    if current is None:
        new_position = dict(paper_source)
        new_position["id"] = position_id
        new_position["status"] = "active"
        active_positions.append(new_position)
        return True

    changed = False
    execution_fields = (
        "quantity",
        "entry_date",
        "entry_timestamp_utc",
        "entry_price",
        "entry_price_type",
        "entry_fx_to_pln",
        "entry_notional_pln",
        "entry_fee_pln",
        "entry_value_pln",
        "current_price",
        "current_fx_to_pln",
        "current_value_pln",
        "current_price_updated_at",
        "current_fx_updated_at",
        "review_flag",
    )
    for key in execution_fields:
        if key not in paper_source:
            continue
        if current.get(key) != paper_source.get(key):
            current[key] = paper_source.get(key)
            changed = True
    if current.get("status") != "active":
        current["status"] = "active"
        changed = True
    return changed


def close_position(
    active_positions: list[dict[str, Any]],
    existing_closed: dict[str, dict[str, Any]],
    paper_closed: dict[str, dict[str, Any]],
    position_id: str,
    order: dict[str, Any],
) -> tuple[bool, bool]:
    matching = find_position(active_positions, position_id)
    removed = False
    changed = False
    if matching is not None:
        active_positions.remove(matching)
        removed = True
        changed = True

    closed_source = paper_closed.get(position_id)
    if closed_source is not None:
        closed = dict(matching or existing_closed.get(position_id) or {})
        closed.update(closed_source)
        closed.update({
            "id": position_id,
            "status": "closed",
            "review_flag": "SOLD",
            "paper_order_id": order.get("order_id"),
            "paper_decision_id": order.get("decision_id"),
            "paper_execution_status": "PAPER_EXECUTED",
        })
        if existing_closed.get(position_id) != closed:
            existing_closed[position_id] = closed
            changed = True
    return changed, removed


def reconcile(public: dict[str, Any], paper: dict[str, Any], orders: dict[str, Any]) -> dict[str, Any]:
    executed = [
        order for order in orders.get("orders", [])
        if str(order.get("status")) == "PAPER_EXECUTED"
        and str(order.get("action")) in SUPPORTED_ACTIONS
    ]
    if not executed:
        return {"changed": False, "executed_orders": 0, "affected": []}

    paper_active = {
        instrument_id(position): position
        for position in paper.get("positions", [])
        if instrument_id(position) and position.get("status") in {"active", "paper_active"}
    }
    paper_closed = {
        instrument_id(position): position
        for position in paper.get("closed_positions", [])
        if instrument_id(position)
    }
    existing_closed = {
        instrument_id(position): position
        for position in public.get("closed_positions", [])
        if instrument_id(position)
    }
    active_positions = list(public.get("positions", []))
    affected: set[str] = set()
    removed: list[str] = []
    changed = False

    state = dict(public.get("execution_reconciliation") or {})
    applied = set(str(value) for value in state.get("applied_order_ids", []))

    for order in executed:
        order_id = str(order.get("order_id") or "")
        action = str(order.get("action") or "")
        sell_id = str(order.get("sell_instrument") or "").strip().lower()
        buy_id = str(order.get("buy_instrument") or "").strip().lower()

        if action in {"EXIT", "REPLACE"} and sell_id:
            item_changed, item_removed = close_position(
                active_positions, existing_closed, paper_closed, sell_id, order
            )
            changed = changed or item_changed
            if item_removed:
                removed.append(sell_id)
            affected.add(sell_id)
        elif action == "REDUCE" and sell_id:
            changed = sync_active_position(active_positions, paper_active, sell_id) or changed
            affected.add(sell_id)

        if action in {"ADD", "REPLACE"} and buy_id:
            changed = sync_active_position(active_positions, paper_active, buy_id) or changed
            affected.add(buy_id)

        if order_id and order_id not in applied:
            applied.add(order_id)
            changed = True

    public["positions"] = active_positions
    public["closed_positions"] = list(existing_closed.values())

    # The paper ledger is authoritative for cash created/consumed by executed
    # paper trades. Preserve sub-cent precision because the cash-yield ledger
    # accrues continuously and public display rounding happens later.
    paper_cash = finite(paper.get("cash_pln"), finite(public.get("cash_pln")))
    if abs(finite(public.get("base_cash_pln"), -1.0) - paper_cash) > 1e-10:
        public["base_cash_pln"] = round(paper_cash, 8)
        changed = True
    public["cash_pln"] = round(paper_cash, 8)
    public["cash_balance_pln"] = round(paper_cash, 8)

    # Keep cumulative cash-yield metadata aligned with the authoritative ledger.
    if paper.get("cash_yield"):
        public["cash_yield"] = dict(paper.get("cash_yield") or {})
        public["cash_interest_accrued_pln"] = round(
            finite(paper.get("cash_interest_accrued_pln")), 8
        )

    active_value = sum(
        finite(p.get("current_value_pln"))
        for p in active_positions
        if p.get("status") == "active"
    )
    total_value = round(active_value + paper_cash, 2)
    starting = finite(public.get("starting_capital_pln"), 10000.0)
    public["total_value_pln"] = total_value
    public["total_return_pln"] = round(total_value - starting, 2)
    public["total_return_percent"] = round((total_value - starting) / starting, 6) if starting else None
    for position in active_positions:
        value = finite(position.get("current_value_pln"))
        position["current_weight"] = round(value / total_value, 6) if total_value else None

    now = datetime.now(timezone.utc).astimezone(WARSAW).isoformat(timespec="seconds")
    public["execution_reconciliation"] = {
        "version": "2.0.0",
        "checked_at": now,
        "source_orders": "/data/portfolio10k/paper_orders.json",
        "source_paper_portfolio": "/data/portfolio10k/paper_portfolio.json",
        "applied_order_ids": sorted(applied),
        "executed_actions": sorted({str(o.get("action")) for o in executed}),
        "affected_instruments": sorted(affected),
        "executed_exit_instruments": sorted({
            str(o.get("sell_instrument"))
            for o in executed
            if str(o.get("action")) in {"EXIT", "REPLACE"} and o.get("sell_instrument")
        }),
    }
    if changed:
        public["last_updated_at"] = now

    return {
        "changed": changed,
        "executed_orders": len(executed),
        "affected": sorted(affected),
        "removed": removed,
        "cash_pln": public.get("cash_pln"),
        "total_value_pln": public.get("total_value_pln"),
    }


def main() -> None:
    public = load(PUBLIC_PATH)
    paper = load(PAPER_PATH)
    orders = load(ORDERS_PATH)
    result = reconcile(public, paper, orders)
    write_atomic(PUBLIC_PATH, public)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
