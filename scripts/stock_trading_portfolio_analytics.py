#!/usr/bin/env python3
"""Read-only costed portfolio economics and predeclared deployment monitoring.

No writer, quote fetcher, settlement or production-policy mutation lives here.
PLN and USD are deliberately evaluated separately; no invented FX conversion.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

try:
    from scripts import stock_trading_portfolio as portfolio
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_portfolio as portfolio


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _time(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return result.astimezone(timezone.utc) if result.tzinfo is not None else None


def position_economics(position: Mapping[str, Any]) -> dict[str, Any]:
    """Explicit paper net economics; absent costs never become zero costs."""
    entry, quantity = _number(position.get("entry")), _number(position.get("quantity"))
    closed = str(position.get("status") or "OPEN").upper() == "CLOSED"
    mark = _number(position.get("exit_price") if closed else position.get("last_mark"))
    model, entry_cost = position.get("transaction_cost_model"), _number(position.get("entry_cost_amount"))
    missing = []
    if entry is None or entry <= 0 or quantity is None or quantity <= 0 or mark is None or mark <= 0:
        missing.append("position_price_or_quantity_missing")
    if not isinstance(model, Mapping) or entry_cost is None or entry_cost < 0:
        missing.append("frozen_transaction_costs_missing")
    if missing:
        return {"status": "UNSCORABLE", "position_id": position.get("position_id"), "reasons": missing}
    exit_cost = portfolio.transaction_cost(mark * quantity, model)
    gross = (mark - entry) * quantity
    return {
        "status": "COMPLETE", "position_id": position.get("position_id"),
        "is_closed": closed, "gross_pnl_amount": gross,
        "entry_cost_amount": entry_cost, "exit_cost_amount": exit_cost,
        "cost_amount": entry_cost + exit_cost, "net_pnl_amount": gross - entry_cost - exit_cost,
        "valuation_basis": "REALIZED_NET" if closed else "MARK_TO_LIQUIDATION_NET_WITH_ESTIMATED_EXIT_COST",
    }


def market_economics(
    state: Mapping[str, Any], market: str, *, initial_capital: float,
    baseline_closed_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Whole-book economics relative to an explicit experiment's initial cash."""
    market = market.upper()
    if not math.isfinite(float(initial_capital)) or initial_capital <= 0:
        raise ValueError("initial capital must be finite and positive")
    ignored = set(str(value) for value in baseline_closed_ids)
    row = portfolio.market_state(state, market)
    positions = portfolio.open_positions(state, market) + [
        dict(position) for position in row.get("closed_positions") or []
        if isinstance(position, Mapping) and str(position.get("position_id")) not in ignored
    ]
    details = [position_economics(position) for position in positions]
    incomplete = [item for item in details if item["status"] != "COMPLETE"]
    if incomplete:
        return {"status": "UNSCORABLE", "market": market, "missing": incomplete, "net_nav": None}
    gross = sum(item["gross_pnl_amount"] for item in details)
    costs = sum(item["cost_amount"] for item in details)
    net = gross - costs
    realised = sum(item["net_pnl_amount"] for item in details if item["is_closed"])
    return {
        "status": "COMPLETE", "market": market, "currency": portfolio.POSITION_CURRENCY[market],
        "initial_capital": float(initial_capital), "net_nav": float(initial_capital) + net,
        "gross_pnl_amount": gross, "net_pnl_amount": net, "cost_amount": costs,
        "realized_net_pnl_amount": realised, "unrealized_liquidation_net_pnl_amount": net - realised,
        "net_return_percent": net / initial_capital * 100.0, "positions": details,
    }


def prospective_period_economics(
    state: Mapping[str, Any], initial_state: Mapping[str, Any], market: str, *,
    policy: Mapping[str, Any], initial_capital: float,
) -> dict[str, Any]:
    """Future-only economics from a frozen inherited book, without cost backfill.

    Past entry costs are outside this registered period, whether known or not.
    Inherited positions start at their frozen initial mark and liquidation
    reserve. Only subsequent mark/exit changes and reserve changes contribute.
    A missing legacy exit model uses the trial's explicit prospective stress
    assumption; no synthetic historical entry_cost_amount/model is constructed.
    Positions opened after this boundary retain full two-leg frozen economics.
    """
    market = market.upper()
    if not math.isfinite(float(initial_capital)) or initial_capital <= 0:
        raise ValueError("initial capital must be finite and positive")
    initial_row = portfolio.market_state(initial_state, market)
    initial_open = portfolio.open_positions(initial_state, market)
    inherited = {str(item.get("position_id") or ""): item for item in initial_open}
    ignored_closed = {str(item.get("position_id")) for item in initial_row.get("closed_positions") or []}
    current_row = portfolio.market_state(state, market)
    positions = portfolio.open_positions(state, market) + [dict(item) for item in current_row.get("closed_positions") or []
                if isinstance(item, Mapping) and str(item.get("position_id")) not in ignored_closed]
    details, missing, seen = [], [], set()
    if "" in inherited or len(inherited) != len(initial_open):
        missing.append({"reason": "initial_position_identity_missing_or_duplicated"})
    for position in positions:
        position_id = str(position.get("position_id") or "")
        if not position_id or position_id in seen:
            missing.append({"position_id": position_id, "reason": "period_position_identity_missing_or_duplicated"})
            continue
        seen.add(position_id)
        anchor = inherited.get(position_id)
        if anchor is None:
            result = position_economics(position)
            if result["status"] != "COMPLETE":
                missing.append(result)
            else:
                details.append(result)
            continue
        quantity, initial_quantity = _number(position.get("quantity")), _number(anchor.get("quantity"))
        initial_mark = _number(anchor.get("last_mark"))
        closed = str(position.get("status") or "OPEN").upper() == "CLOSED"
        mark = _number(position.get("exit_price") if closed else position.get("last_mark"))
        model = anchor.get("transaction_cost_model")
        cost_basis = "INHERITED_FROZEN_ENTRY_EXIT_MODEL"
        if not isinstance(model, Mapping):
            model = (policy.get("markets") or {}).get(market, {}).get("transaction_costs")
            cost_basis = "FROZEN_TRIAL_PROSPECTIVE_EXIT_STRESS_NOT_HISTORICAL_ENTRY_COST"
        if (initial_mark is None or initial_mark <= 0 or mark is None or mark <= 0
                or quantity is None or quantity <= 0 or quantity != initial_quantity
                or position.get("symbol") != anchor.get("symbol")):
            missing.append({"position_id": position_id, "reason": "frozen_initial_mark_quantity_or_identity_missing"})
            continue
        try:
            initial_reserve = portfolio.transaction_cost(initial_mark * quantity, model or {})
            exit_stress = portfolio.transaction_cost(mark * quantity, model or {})
        except (ValueError, TypeError):
            missing.append({"position_id": position_id, "reason": "frozen_prospective_exit_cost_assumption_missing"})
            continue
        gross, reserve_change = (mark - initial_mark) * quantity, exit_stress - initial_reserve
        details.append({
            "status": "COMPLETE", "position_id": position_id, "symbol": position.get("symbol"), "is_closed": closed,
            "gross_pnl_amount": gross, "net_pnl_amount": gross - reserve_change, "cost_amount": reserve_change,
            "initial_mark": initial_mark, "initial_liquidation_cost_reserve": initial_reserve,
            "prospective_exit_cost_amount": exit_stress, "prospective_exit_cost_change_amount": reserve_change,
            "cost_basis": cost_basis, "historical_entry_cost_status": "OUTSIDE_PROSPECTIVE_PERIOD_NOT_ESTIMATED",
            "valuation_basis": "FUTURE_MARK_CHANGE_LESS_EXIT_RESERVE_CHANGE_FROM_FROZEN_INITIAL_BOOK",
        })
    for position_id in inherited.keys() - seen:
        missing.append({"position_id": position_id, "reason": "inherited_position_disappeared_without_exit_record"})
    if missing:
        return {"status": "UNSCORABLE", "market": market, "missing": missing, "net_nav": None}
    gross, costs = sum(item["gross_pnl_amount"] for item in details), sum(item["cost_amount"] for item in details)
    net = gross - costs
    realised = sum(item["net_pnl_amount"] for item in details if item["is_closed"])
    return {
        "status": "COMPLETE", "market": market, "currency": portfolio.POSITION_CURRENCY[market],
        "economic_basis": "FROZEN_PROSPECTIVE_INITIAL_MARK_AND_EXIT_RESERVE",
        "initial_capital": float(initial_capital), "net_nav": float(initial_capital) + net,
        "gross_pnl_amount": gross, "net_pnl_amount": net, "cost_amount": costs,
        "realized_net_pnl_amount": realised, "unrealized_liquidation_net_pnl_amount": net - realised,
        "net_return_percent": net / initial_capital * 100.0, "positions": details,
    }


def curve_metrics(curve: Sequence[Mapping[str, Any]], *, initial_capital: float, positions: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Drawdown includes open positions; winner dependency includes their marks."""
    peak = float(initial_capital)
    max_drawdown = 0.0
    for point in curve:
        nav = _number(point.get("net_nav"))
        if nav is None:
            raise ValueError("curve contains an unscorable NAV")
        peak = max(peak, nav)
        max_drawdown = max(max_drawdown, (peak - nav) / peak * 100.0 if peak > 0 else 0.0)
    last = dict(curve[-1]) if curve else {"net_nav": float(initial_capital), "net_pnl_amount": 0.0, "cost_amount": 0.0}
    net = float(last["net_nav"]) - initial_capital
    contribution = sorted([dict(item) for item in positions], key=lambda item: float(item.get("net_pnl_amount") or 0.0), reverse=True)
    largest = max(0.0, float(contribution[0].get("net_pnl_amount") or 0.0)) if contribution else 0.0
    positive_sum = sum(max(0.0, float(item.get("net_pnl_amount") or 0.0)) for item in contribution)
    benchmark = _number(last.get("benchmark_return_percent"))
    result = {
        "initial_capital": initial_capital, "net_nav": float(last["net_nav"]),
        "net_pnl_amount": net, "net_return_percent": net / initial_capital * 100.0,
        "max_drawdown_percent": max_drawdown, "cost_amount": float(last.get("cost_amount") or 0.0),
        "benchmark_return_percent": benchmark,
        "excess_return_percent": net / initial_capital * 100.0 - benchmark if benchmark is not None else None,
        "largest_winner_position_id": contribution[0].get("position_id") if largest > 0 else None,
        "largest_winner_net_pnl_amount": largest,
        "top_winner_share": largest / positive_sum if positive_sum > 0 else None,
        "top_winner_share_of_net_profit": largest / net if net > 0 else None,
        "pnl_excluding_top_winner": net - largest,
    }
    return result


def deployment_economic_monitor(
    deployment: Mapping[str, Any], observations: Sequence[Mapping[str, Any]], *, as_of: datetime,
) -> dict[str, Any]:
    """Evaluate one frozen future window, recommending only (no automatic mutation).

    Manifest monitoring_contract must be frozen no later than activation, with
    immutable ID, policy/cost hashes, currency, capital, end and loss limits.
    Observations must be whole-portfolio paired actual/control net NAVs.
    """
    spec = deployment.get("monitoring_contract") or {}
    activated = _time(deployment.get("activated_at"))
    frozen = _time(spec.get("frozen_at"))
    end = _time(spec.get("window_end_at"))
    capital = _number(spec.get("initial_capital"))
    drawdown_limit = _number(spec.get("rollback_max_drawdown_percent"))
    minimum_delta = _number(spec.get("rollback_min_net_delta_percent"))
    minimum = _number(spec.get("minimum_paired_observations"))
    required_identity = ("deployment_id", "policy_sha256", "cost_model_sha256", "market")
    valid = all(str(deployment.get(key) or "") for key in required_identity)
    valid = valid and activated is not None and frozen is not None and end is not None and frozen <= activated < end
    valid = valid and capital is not None and capital > 0 and drawdown_limit is not None and drawdown_limit > 0 and minimum_delta is not None and minimum is not None and minimum >= 2
    if not valid:
        return {"status": "UNSCORABLE", "recommendation": "NO_DECISION", "reason": "missing_or_nonprospective_monitoring_contract"}
    rows, previous = [], None
    for raw in observations:
        moment = _time(raw.get("observed_at"))
        if moment is None or moment < activated or moment > min(as_of, end):
            continue
        if any(str(raw.get(key) or "") != str(deployment[key]) for key in required_identity):
            continue
        if raw.get("status") != "COMPLETE" or _number(raw.get("net_nav")) is None or _number(raw.get("control_net_nav")) is None:
            continue
        if previous is not None and moment <= previous:
            return {"status": "UNSCORABLE", "recommendation": "NO_DECISION", "reason": "monitoring_observations_not_strictly_chronological"}
        previous = moment
        rows.append(dict(raw))
    if len(rows) < int(minimum):
        return {"status": "PENDING", "recommendation": "WAIT_FOR_EVIDENCE", "paired_observations": len(rows), "window_end_at": end.isoformat()}
    metrics = curve_metrics(rows, initial_capital=capital)
    delta = (float(rows[-1]["net_nav"]) - float(rows[-1]["control_net_nav"])) / capital * 100.0
    rollback = metrics["max_drawdown_percent"] >= drawdown_limit
    reason = "predeclared_max_drawdown_breached" if rollback else "fixed_window_not_complete"
    if as_of >= end:
        if delta < minimum_delta:
            rollback, reason = True, "fixed_window_net_underperformance"
        elif not rollback:
            reason = "fixed_window_within_predeclared_limits"
    return {
        "status": "COMPLETE" if as_of >= end else "MONITORING", "deployment_id": deployment["deployment_id"],
        "recommendation": "RECOMMEND_ROLLBACK" if rollback else ("RETAIN" if as_of >= end else "CONTINUE_MONITORING"),
        "reason": reason, "net_delta_percent": delta, "metrics": metrics,
        "paired_observations": len(rows), "window_end_at": end.isoformat(), "production_mutated": False,
    }
