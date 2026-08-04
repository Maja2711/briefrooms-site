#!/usr/bin/env python3
"""Reconcile completed BRACE paper exits into the public Portfolio 10K state."""
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


def reconcile(public: dict[str, Any], paper: dict[str, Any], orders: dict[str, Any]) -> dict[str, Any]:
    executed = [
        order for order in orders.get("orders", [])
        if str(order.get("status")) == "PAPER_EXECUTED"
        and str(order.get("action")) == "EXIT"
        and order.get("sell_instrument")
    ]
    if not executed:
        return {"changed": False, "executed_exits": 0, "removed": []}

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
    removed: list[str] = []
    changed = False

    state = dict(public.get("execution_reconciliation") or {})
    applied = set(str(value) for value in state.get("applied_order_ids", []))

    for order in executed:
        order_id = str(order.get("order_id") or "")
        position_id = str(order.get("sell_instrument") or "").strip().lower()
        matching = next((p for p in active_positions if instrument_id(p) == position_id), None)

        if matching is not None:
            active_positions.remove(matching)
            removed.append(position_id)
            changed = True

        closed_source = paper_closed.get(position_id)
        if closed_source is not None:
            closed = dict(matching or existing_closed.get(position_id) or {})
            closed.update(closed_source)
            closed.update({
                "id": position_id,
                "status": "closed",
                "review_flag": "SOLD",
                "paper_order_id": order_id,
                "paper_decision_id": order.get("decision_id"),
                "paper_execution_status": "PAPER_EXECUTED",
            })
            if existing_closed.get(position_id) != closed:
                existing_closed[position_id] = closed
                changed = True

        if order_id and order_id not in applied:
            applied.add(order_id)
            changed = True

    public["positions"] = active_positions
    public["closed_positions"] = list(existing_closed.values())

    # The paper ledger is authoritative for cash created by executed paper trades.
    paper_cash = finite(paper.get("cash_pln"), finite(public.get("cash_pln")))
    if finite(public.get("base_cash_pln"), -1.0) != paper_cash:
        public["base_cash_pln"] = round(paper_cash, 2)
        changed = True
    public["cash_pln"] = round(paper_cash, 2)
    public["cash_balance_pln"] = round(paper_cash, 2)

    active_value = sum(finite(p.get("current_value_pln")) for p in active_positions if p.get("status") == "active")
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
        "version": "1.0.0",
        "checked_at": now,
        "source_orders": "/data/portfolio10k/paper_orders.json",
        "source_paper_portfolio": "/data/portfolio10k/paper_portfolio.json",
        "applied_order_ids": sorted(applied),
        "executed_exit_instruments": sorted({str(o.get("sell_instrument")) for o in executed}),
    }
    if changed:
        public["last_updated_at"] = now

    return {
        "changed": changed,
        "executed_exits": len(executed),
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
