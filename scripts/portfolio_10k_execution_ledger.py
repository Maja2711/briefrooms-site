#!/usr/bin/env python3
"""Canonical executed-order view derived from the append-only paper transaction ledger.

`paper_orders.json` is a mutable queue and must not be used as the historical
authority for already executed allocation changes.  Executions are reconstructed
from `paper_portfolio.transactions`, grouped by order_id.  The current queue is
used only as a compatibility fallback when an executed order has not yet produced
transaction rows.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping

SUPPORTED_ACTIONS = {"ADD", "REDUCE", "EXIT", "REPLACE"}


def _instrument_id(position: Mapping[str, Any]) -> str:
    return str(position.get("id") or position.get("instrument_id") or "").strip().lower()


def _active_ids(paper: Mapping[str, Any]) -> set[str]:
    return {
        _instrument_id(position)
        for position in paper.get("positions", []) or []
        if _instrument_id(position) and position.get("status") in {"active", "paper_active"}
    }


def _closed_ids(paper: Mapping[str, Any]) -> set[str]:
    return {
        _instrument_id(position)
        for position in paper.get("closed_positions", []) or []
        if _instrument_id(position)
    }


def _unique_instrument(rows: list[Mapping[str, Any]], side: str) -> str | None:
    values = {
        str(row.get("instrument_id") or "").strip().lower()
        for row in rows
        if str(row.get("side") or "").upper() == side
        and str(row.get("instrument_id") or "").strip()
    }
    if not values:
        return None
    if len(values) != 1:
        raise ValueError(f"Ambiguous {side} execution group: {sorted(values)}")
    return next(iter(values))


def executions_from_transactions(paper: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Reconstruct durable executed allocation actions from transaction history."""
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in paper.get("transactions", []) or []:
        side = str(row.get("side") or "").upper()
        if side not in {"BUY", "SELL"} or not row.get("executed_at"):
            continue
        key = str(row.get("order_id") or row.get("transaction_id") or "").strip()
        if not key:
            continue
        groups[key].append(row)

    active_ids = _active_ids(paper)
    closed_ids = _closed_ids(paper)
    executions: list[dict[str, Any]] = []

    for order_id, rows in groups.items():
        rows = sorted(rows, key=lambda row: str(row.get("executed_at") or ""))
        buy_id = _unique_instrument(rows, "BUY")
        sell_id = _unique_instrument(rows, "SELL")

        if buy_id and sell_id:
            action = "REPLACE"
        elif buy_id:
            action = "ADD"
        elif sell_id:
            if sell_id in active_ids:
                action = "REDUCE"
            elif sell_id in closed_ids:
                action = "EXIT"
            else:
                raise ValueError(
                    f"Cannot infer executed SELL action for {sell_id}: "
                    "instrument is neither active nor closed in paper ledger"
                )
        else:
            continue

        latest = rows[-1]
        executions.append(
            {
                "order_id": order_id,
                "decision_id": latest.get("decision_id"),
                "action": action,
                "sell_instrument": sell_id,
                "buy_instrument": buy_id,
                "status": "PAPER_EXECUTED",
                "executed_at": max(str(row.get("executed_at") or "") for row in rows),
                "signal_at": latest.get("signal_at"),
                "rationale_pl": latest.get("rationale_pl"),
                "rationale_en": latest.get("rationale_en"),
                "execution_authority": "paper_portfolio.transactions",
            }
        )

    return sorted(executions, key=lambda item: (str(item.get("executed_at") or ""), str(item.get("order_id") or "")))


def authoritative_executions(
    paper: Mapping[str, Any],
    queue: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return all durable executions, with mutable queue data as fallback only."""
    durable = executions_from_transactions(paper)
    by_id = {str(item.get("order_id") or ""): item for item in durable}

    for order in (queue or {}).get("orders", []) or []:
        order_id = str(order.get("order_id") or "").strip()
        action = str(order.get("action") or "").upper()
        if (
            not order_id
            or order_id in by_id
            or str(order.get("status") or "") != "PAPER_EXECUTED"
            or action not in SUPPORTED_ACTIONS
        ):
            continue
        item = dict(order)
        item["action"] = action
        item["execution_authority"] = "paper_orders_fallback"
        by_id[order_id] = item

    return sorted(
        by_id.values(),
        key=lambda item: (str(item.get("executed_at") or ""), str(item.get("order_id") or "")),
    )
