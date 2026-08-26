#!/usr/bin/env python3
"""Canonical state reconciliation for the BRACE Portfolio 10K public/control layers.

The paper portfolio is the source of truth while BRACE is in paper-control mode.
This module deliberately keeps closed positions out of live assessments and reconciles
proposed decisions with the paper order/transaction ledger before publication.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

CONTROLLED_STATES = {"PROBATIONARY_CONTROL", "ACTIVE_PAPER_CONTROL", "ACTIVE_CONTROL"}
EXECUTED_ORDER_STATES = {"PAPER_EXECUTED", "EXECUTED", "FILLED"}
PENDING_ORDER_STATES = {"WAITING_FOR_MARKET", "READY", "QUEUED", "PENDING", "PROPOSED"}
TERMINAL_ORDER_STATES = {"FAILED", "EXPIRED", "CANCELLED", "CANCELED", "REJECTED"}


def _id(value: Any) -> str:
    return str(value or "").strip().lower()


def active_positions(portfolio: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in (portfolio or {}).get("positions") or []:
        if not isinstance(raw, Mapping):
            continue
        quantity = raw.get("quantity")
        try:
            if quantity is not None and float(quantity) <= 0:
                continue
        except (TypeError, ValueError):
            continue
        status = str(raw.get("status") or "active").strip().lower()
        if "closed" in status or "sold" in status or status in {"inactive", "exited"}:
            continue
        if not _id(raw.get("id") or raw.get("instrument_id")):
            continue
        rows.append(deepcopy(dict(raw)))
    return rows


def active_position_ids(portfolio: Mapping[str, Any] | None) -> set[str]:
    return {_id(row.get("id") or row.get("instrument_id")) for row in active_positions(portfolio)}


def closed_position_ids(portfolio: Mapping[str, Any] | None) -> set[str]:
    result: set[str] = set()
    for raw in (portfolio or {}).get("closed_positions") or []:
        if isinstance(raw, Mapping):
            value = _id(raw.get("id") or raw.get("instrument_id"))
            if value:
                result.add(value)
    return result


def live_analysis_portfolio(
    registry: Mapping[str, Any] | None,
    baseline: Mapping[str, Any],
    paper_portfolio: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    """Use current paper holdings for live BRACE analysis while control is active.

    Research/benchmark callers may still explicitly pass the immutable baseline. The
    orchestration layer decides whether a run is a live control run or a research run.
    """
    controller = str((registry or {}).get("controller_state") or "").upper()
    if controller in CONTROLLED_STATES and active_positions(paper_portfolio):
        return paper_portfolio or baseline
    return baseline


def filter_position_recommendations(
    recommendations: Iterable[Mapping[str, Any]] | None,
    portfolio: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    active = active_position_ids(portfolio)
    output: list[dict[str, Any]] = []
    for raw in recommendations or []:
        if not isinstance(raw, Mapping):
            continue
        instrument = _id(raw.get("instrument") or raw.get("instrument_id") or raw.get("id"))
        if instrument and instrument in active:
            output.append(deepcopy(dict(raw)))
    return output


def _transactions_by_decision(portfolio: Mapping[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for raw in (portfolio or {}).get("transactions") or []:
        if not isinstance(raw, Mapping):
            continue
        decision_id = str(raw.get("decision_id") or "").strip()
        if decision_id:
            result.setdefault(decision_id, []).append(deepcopy(dict(raw)))
    return result


def _orders_by_decision(order_payload: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in (order_payload or {}).get("orders") or []:
        if not isinstance(raw, Mapping):
            continue
        decision_id = str(raw.get("decision_id") or "").strip()
        if decision_id:
            result[decision_id] = deepcopy(dict(raw))
    return result


def reconcile_public_decisions(
    decisions: Iterable[Mapping[str, Any]] | None,
    portfolio: Mapping[str, Any] | None,
    order_payload: Mapping[str, Any] | None,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return only actionable or already-completed decisions for the frontend.

    FAILED/EXPIRED proposals disappear unless the live portfolio already reflects the
    requested state. This makes the UI a state view, not a dump of stale queue records.
    """
    active = active_position_ids(portfolio)
    closed = closed_position_ids(portfolio)
    transactions = _transactions_by_decision(portfolio)
    orders = _orders_by_decision(order_payload)
    reconciled: list[dict[str, Any]] = []

    for raw in decisions or []:
        if not isinstance(raw, Mapping):
            continue
        row = deepcopy(dict(raw))
        decision_id = str(row.get("decision_id") or "").strip()
        action = str(row.get("action") or "").upper()
        source = _id(row.get("instrument") or row.get("instrument_id"))
        replacement = _id(row.get("replacement_instrument") or row.get("replacement_instrument_id"))
        order = orders.get(decision_id) or {}
        order_status = str(order.get("status") or "").upper()
        tx = transactions.get(decision_id) or []

        execution_status = ""
        reason = ""
        executed_at = None

        if tx or order_status in EXECUTED_ORDER_STATES:
            execution_status = "EXECUTED"
            reason = "ledger_execution_confirmed"
            executed_at = next(
                (item.get("executed_at") or item.get("timestamp") or item.get("generated_at") for item in reversed(tx) if isinstance(item, Mapping)),
                None,
            ) or order.get("executed_at")
        elif action == "REPLACE" and source and source not in active and replacement and replacement in active:
            execution_status = "ALREADY_APPLIED"
            reason = "portfolio_state_already_reflects_replacement"
        elif action == "EXIT" and source and source not in active and (source in closed or bool(source)):
            execution_status = "ALREADY_APPLIED"
            reason = "portfolio_state_already_reflects_exit"
        elif order_status in PENDING_ORDER_STATES:
            execution_status = "PENDING"
            reason = str(order.get("failure_reason") or "awaiting_execution").lower()
        elif order_status in TERMINAL_ORDER_STATES:
            continue
        elif action == "REPLACE" and source in active:
            execution_status = "PENDING"
            reason = "awaiting_execution"
        elif action == "EXIT" and source in active:
            execution_status = "PENDING"
            reason = "awaiting_execution"
        elif action == "ADD" and source and source not in active:
            execution_status = "PENDING"
            reason = "awaiting_execution"
        else:
            continue

        row["execution_status"] = execution_status
        row["execution_reason"] = reason
        row["order_status"] = order_status or None
        row["executed_at"] = executed_at
        reconciled.append(row)

    # Keep live pending items first, then recently completed state changes.
    priority = {"PENDING": 0, "EXECUTED": 1, "ALREADY_APPLIED": 2}
    reconciled.sort(
        key=lambda item: (
            priority.get(str(item.get("execution_status")), 9),
            str(item.get("generated_at") or ""),
        )
    )
    return reconciled[: max(1, int(limit))]
