#!/usr/bin/env python3
"""Paper-only order queue and execution with deterministic safety gates."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Protocol, Sequence

from brace_portfolio_config import EngineConfig
from brace_portfolio_data import (
    BASELINE_PORTFOLIO_PATH,
    ENGINE_DATA_ROOT,
    assert_baseline_unchanged,
    parse_timestamp,
    read_json,
    write_json_atomic,
)
from brace_portfolio_decision import deterministic_id

PAPER_PORTFOLIO_PATH = ENGINE_DATA_ROOT / "paper_portfolio.json"
ORDER_STATUSES = {
    "PROPOSED",
    "QUEUED",
    "WAITING_FOR_MARKET",
    "READY",
    "PAPER_EXECUTED",
    "EXPIRED",
    "REJECTED_BY_RISK",
    "CANCELLED",
    "FAILED",
}
CONTROLLING_STATUSES = {"PROBATIONARY_CONTROL", "ACTIVE_PAPER_CONTROL"}


class QuoteProvider(Protocol):
    def quote(self, market_symbol: str, currency: str) -> Mapping[str, Any]:
        """Return price, fx_to_pln, completed_at and market_open."""


class YFinancePaperQuoteProvider:
    """Latest completed five-minute candle adapter; never submits an order."""

    FX_SYMBOLS = {
        "USD": "USDPLN=X",
        "EUR": "EURPLN=X",
        "DKK": "DKKPLN=X",
        "PLN": None,
    }

    def _completed_quote(self, symbol: str) -> Mapping[str, Any]:
        import yfinance as yf

        frame = yf.Ticker(symbol).history(
            period="5d",
            interval="5m",
            auto_adjust=True,
            actions=False,
            prepost=False,
        )
        if frame is None or frame.empty:
            return {}
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=5)
        rows = []
        for index, row in frame.iterrows():
            observed = index.to_pydatetime()
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            observed = observed.astimezone(timezone.utc)
            if observed <= cutoff:
                rows.append((observed, float(row.get("Close") or 0.0)))
        if not rows:
            return {}
        observed, price = rows[-1]
        return {
            "price": price,
            "completed_at": observed.isoformat(timespec="seconds"),
            "market_open": now - observed <= timedelta(minutes=15),
        }

    def quote(self, market_symbol: str, currency: str) -> Mapping[str, Any]:
        market = dict(self._completed_quote(market_symbol))
        if not market:
            return {}
        fx_symbol = self.FX_SYMBOLS.get(currency)
        if fx_symbol is None:
            fx = 1.0
        else:
            fx_quote = self._completed_quote(fx_symbol)
            if not fx_quote:
                return {}
            fx = float(fx_quote["price"])
        market["fx_to_pln"] = fx
        return market


def initialize_paper_portfolio(
    baseline: Mapping[str, Any],
    created_at: datetime,
) -> Dict[str, Any]:
    paper = copy.deepcopy(dict(baseline))
    paper["portfolio_id"] = "briefrooms-brace-paper-10k"
    paper["name_pl"] = "BRACE Inwestycje 10K - portfel paper"
    paper["name_en"] = "BRACE 10K Investing - paper portfolio"
    paper["status"] = "paper_shadow_copy"
    paper["paper_only"] = True
    paper["real_broker_connected"] = False
    paper["source_baseline_portfolio_id"] = baseline.get("portfolio_id")
    paper["source_baseline_snapshot_at"] = created_at.isoformat(timespec="seconds")
    paper["transactions"] = []
    paper["closed_positions"] = copy.deepcopy(baseline.get("closed_positions") or [])
    return paper


def prepare_orders(
    pending: Mapping[str, Any],
    controller_status: str,
    generated_at: datetime,
    existing_queue: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    existing = {
        str(item.get("order_id")): item
        for item in (existing_queue or {}).get("orders", []) or []
    }
    orders = []
    for decision in pending.get("decisions", []) or []:
        if decision.get("action") == "NO_ACTION":
            continue
        action = str(decision.get("action") or "")
        sell_instrument = (
            decision.get("instrument")
            if action in {"REPLACE", "REDUCE", "EXIT"}
            else None
        )
        buy_instrument = (
            decision.get("replacement_instrument")
            if action == "REPLACE"
            else decision.get("instrument")
            if action == "ADD"
            else None
        )
        payload = {
            "decision_id": decision.get("decision_id"),
            "signal_at": decision.get("generated_at"),
            "from": sell_instrument,
            "to": buy_instrument,
        }
        order_id = deterministic_id("paper-order", payload)
        previous = existing.get(order_id)
        if previous:
            orders.append(copy.deepcopy(dict(previous)))
            continue
        orders.append(
            {
                "order_id": order_id,
                "decision_id": decision.get("decision_id"),
                "action": action,
                "sell_instrument": sell_instrument,
                "buy_instrument": buy_instrument,
                "target_weight": decision.get("proposed_weight"),
                "confidence": decision.get("confidence"),
                "rationale_pl": decision.get("rationale_pl"),
                "rationale_en": decision.get("rationale_en"),
                "signal_at": decision.get("generated_at"),
                "queued_at": generated_at.isoformat(timespec="seconds"),
                "status": (
                    "QUEUED"
                    if controller_status in CONTROLLING_STATUSES
                    else "PROPOSED"
                ),
                "paper_only": True,
                "real_order_id": None,
            }
        )
    return {
        "schema_version": "1.0.0",
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "methodology_version": pending.get("methodology_version"),
        "data_freshness": pending.get("data_freshness"),
        "source_metadata": {
            "engine": "brace_portfolio_execution.py",
            "paper_only": True,
            "broker_integration": False,
        },
        "controller_status": controller_status,
        "orders": orders,
    }


def _position_by_id(portfolio: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(item.get("id")): item
        for item in portfolio.get("positions", []) or []
        if item.get("id")
    }


def _instrument_by_id(universe: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(item.get("instrument_id")): dict(item)
        for item in universe.get("instruments", []) or []
        if item.get("instrument_id")
    }


def _recent_opposite_trade(
    transactions: Sequence[Mapping[str, Any]],
    instrument_id: str,
    side: str,
    now: datetime,
    cooldown_days: int,
) -> bool:
    opposite = "BUY" if side == "SELL" else "SELL"
    for row in reversed(transactions):
        if row.get("instrument_id") != instrument_id or row.get("side") != opposite:
            continue
        executed = parse_timestamp(row.get("executed_at"))
        if executed and now - executed < timedelta(days=cooldown_days):
            return not bool(row.get("fundamental_override"))
    return False


def _validate_quote(
    quote: Mapping[str, Any],
    now: datetime,
) -> Optional[str]:
    try:
        price = float(quote.get("price") or 0.0)
        fx = float(quote.get("fx_to_pln") or 0.0)
    except (TypeError, ValueError):
        return "INVALID_QUOTE"
    completed_at = parse_timestamp(quote.get("completed_at"))
    if price <= 0 or fx <= 0 or completed_at is None:
        return "INVALID_QUOTE"
    if completed_at > now:
        return "FUTURE_QUOTE"
    if now - completed_at > timedelta(minutes=15):
        return "STALE_QUOTE"
    return None


def _append_transaction(
    transactions: list[Dict[str, Any]],
    result: Mapping[str, Any],
    instrument_id: str,
    side: str,
    price: float,
    fx: float,
    quantity: float,
    costs: float,
    slippage_bps: float,
    now: datetime,
) -> None:
    transactions.append(
        {
            "transaction_id": deterministic_id(
                "paper-trade",
                {
                    "order": result.get("order_id"),
                    "side": side,
                    "executed_at": now.isoformat(timespec="seconds"),
                },
            ),
            "order_id": result.get("order_id"),
            "decision_id": result.get("decision_id"),
            "instrument_id": instrument_id,
            "side": side,
            "price": round(price, 8),
            "fx_to_pln": round(fx, 8),
            "quantity": round(quantity, 8),
            "transaction_cost_pln": round(costs, 2),
            "slippage_bps": slippage_bps,
            "signal_at": result.get("signal_at"),
            "executed_at": now.isoformat(timespec="seconds"),
            "paper_only": True,
            "rationale_pl": result.get("rationale_pl"),
            "rationale_en": result.get("rationale_en"),
        }
    )


def _finalize_portfolio(
    portfolio: Dict[str, Any],
    result: Dict[str, Any],
    now: datetime,
    execution: Mapping[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    portfolio["last_updated_at"] = now.isoformat(timespec="seconds")
    portfolio["status"] = "active_paper_control"
    portfolio["cash_pln"] = round(float(portfolio.get("cash_pln") or 0.0), 2)
    portfolio["total_value_pln"] = round(
        sum(
            float(item.get("current_value_pln") or 0.0)
            for item in portfolio.get("positions", [])
        )
        + portfolio["cash_pln"],
        2,
    )
    result["status"] = "PAPER_EXECUTED"
    result["executed_at"] = now.isoformat(timespec="seconds")
    result["execution"] = {**dict(execution), "paper_only": True}
    return portfolio, result


def _execute_cash_adjustment(
    portfolio: Dict[str, Any],
    result: Dict[str, Any],
    positions: Mapping[str, Dict[str, Any]],
    instruments: Mapping[str, Dict[str, Any]],
    quote_provider: QuoteProvider,
    config: EngineConfig,
    controller_status: str,
    signal_at: datetime,
    now: datetime,
    transaction_cost_bps: float,
    fx_cost_bps: float,
    slippage_bps: float,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    action = str(result.get("action") or "")
    instrument_id = str(
        result.get("buy_instrument")
        if action == "ADD"
        else result.get("sell_instrument")
        or ""
    )
    meta = instruments.get(instrument_id, {})
    position = positions.get(instrument_id)
    if action in {"REDUCE", "EXIT"} and position is None:
        result["status"] = "FAILED"
        result["failure_reason"] = "POSITION_NOT_AVAILABLE"
        return portfolio, result
    if action == "ADD" and not meta:
        result["status"] = "FAILED"
        result["failure_reason"] = "INSTRUMENT_NOT_AVAILABLE"
        return portfolio, result
    side = "BUY" if action == "ADD" else "SELL"
    transactions = portfolio.setdefault("transactions", [])
    if _recent_opposite_trade(
        transactions,
        instrument_id,
        side,
        now,
        config.rotation_cooldown_days,
    ):
        result["status"] = "REJECTED_BY_RISK"
        result["failure_reason"] = "ANTI_OSCILLATION_COOLDOWN"
        return portfolio, result
    source = position or meta
    market_symbol = source.get("market_symbol") or source.get("data_symbol")
    quote = dict(
        quote_provider.quote(str(market_symbol), str(source.get("currency")))
    )
    if not quote.get("market_open"):
        result["status"] = "WAITING_FOR_MARKET"
        result["failure_reason"] = "MARKET_CLOSED"
        return portfolio, result
    quote_error = _validate_quote(quote, now)
    observed = parse_timestamp(quote.get("completed_at"))
    if quote_error:
        result["status"] = "EXPIRED" if quote_error == "STALE_QUOTE" else "FAILED"
        result["failure_reason"] = quote_error
        return portfolio, result
    if observed is None or observed <= signal_at:
        result["status"] = "WAITING_FOR_MARKET"
        result["failure_reason"] = "WAITING_FOR_POST_SIGNAL_CANDLE"
        return portfolio, result

    portfolio_value = float(portfolio.get("total_value_pln") or 0.0)
    if portfolio_value <= 0:
        portfolio_value = sum(
            float(item.get("current_value_pln") or 0.0)
            for item in portfolio.get("positions", [])
        ) + float(portfolio.get("cash_pln") or 0.0)
    target_weight = max(0.0, float(result.get("target_weight") or 0.0))
    raw_price = float(quote["price"])
    fx = float(quote["fx_to_pln"])
    cost_rate = (transaction_cost_bps + fx_cost_bps) / 10000.0

    if side == "SELL":
        price = raw_price * (1.0 - slippage_bps / 10000.0)
        current_quantity = float(position.get("quantity") or 0.0)
        current_value = current_quantity * price * fx
        target_value = 0.0 if action == "EXIT" else portfolio_value * target_weight
        gross = max(0.0, current_value - target_value)
        quantity = min(current_quantity, gross / max(price * fx, 1e-12))
        if quantity <= 0:
            result["status"] = "REJECTED_BY_RISK"
            result["failure_reason"] = "NON_POSITIVE_ORDER_SIZE"
            return portfolio, result
        gross = quantity * price * fx
        costs = gross * cost_rate
        proceeds = gross - costs
        remaining = max(0.0, current_quantity - quantity)
        if remaining <= 1e-8:
            portfolio["positions"] = [
                item
                for item in portfolio.get("positions", [])
                if item.get("id") != instrument_id
            ]
            closed = copy.deepcopy(position)
            closed.update(
                {
                    "status": "paper_closed",
                    "exit_price": round(price, 8),
                    "exit_fx_to_pln": round(fx, 8),
                    "exit_timestamp_utc": now.isoformat(timespec="seconds"),
                    "exit_value_pln": round(proceeds, 2),
                    "exit_fee_pln": round(costs, 2),
                }
            )
            portfolio.setdefault("closed_positions", []).append(closed)
        else:
            position["quantity"] = round(remaining, 8)
            position["current_price"] = round(price, 8)
            position["current_fx_to_pln"] = round(fx, 8)
            position["current_value_pln"] = round(remaining * price * fx, 2)
            position["current_price_updated_at"] = quote.get("completed_at")
        portfolio["cash_pln"] = float(portfolio.get("cash_pln") or 0.0) + proceeds
    else:
        price = raw_price * (1.0 + slippage_bps / 10000.0)
        cash = float(portfolio.get("cash_pln") or 0.0)
        current_value = float(position.get("current_value_pln") or 0.0) if position else 0.0
        desired = max(0.0, portfolio_value * target_weight - current_value)
        budget = min(cash, desired)
        notional = budget / (1.0 + cost_rate)
        costs = notional * cost_rate
        quantity = notional / max(price * fx, 1e-12)
        if quantity <= 0:
            result["status"] = "REJECTED_BY_RISK"
            result["failure_reason"] = "INSUFFICIENT_PAPER_CASH"
            return portfolio, result
        if (
            controller_status == "PROBATIONARY_CONTROL"
            and position is None
            and notional / max(portfolio_value, 1.0)
            > config.max_probation_new_position_weight
        ):
            result["status"] = "REJECTED_BY_RISK"
            result["failure_reason"] = "PROBATION_NEW_POSITION_LIMIT"
            return portfolio, result
        if position:
            position["quantity"] = round(
                float(position.get("quantity") or 0.0) + quantity,
                8,
            )
            position["current_price"] = round(price, 8)
            position["current_fx_to_pln"] = round(fx, 8)
            position["current_value_pln"] = round(current_value + notional, 2)
            position["current_price_updated_at"] = quote.get("completed_at")
        else:
            portfolio.setdefault("positions", []).append(
                {
                    "id": instrument_id,
                    "label": meta.get("label"),
                    "broker_symbol": meta.get("broker_symbol"),
                    "market_symbol": meta.get("data_symbol"),
                    "currency": meta.get("currency"),
                    "asset_type": meta.get("asset_type"),
                    "status": "paper_active",
                    "quantity": round(quantity, 8),
                    "entry_date": now.date().isoformat(),
                    "entry_timestamp_utc": now.isoformat(timespec="seconds"),
                    "entry_price": round(price, 8),
                    "entry_price_type": "fresh_completed_5m_close_after_signal_paper",
                    "entry_fx_to_pln": round(fx, 8),
                    "entry_notional_pln": round(notional, 2),
                    "entry_fee_pln": round(costs, 2),
                    "entry_value_pln": round(notional + costs, 2),
                    "current_price": round(price, 8),
                    "current_fx_to_pln": round(fx, 8),
                    "current_value_pln": round(notional, 2),
                    "current_price_updated_at": quote.get("completed_at"),
                    "review_flag": "HOLD",
                }
            )
        portfolio["cash_pln"] = cash - notional - costs

    _append_transaction(
        transactions,
        result,
        instrument_id,
        side,
        price,
        fx,
        quantity,
        costs,
        slippage_bps,
        now,
    )
    return _finalize_portfolio(
        portfolio,
        result,
        now,
        {
            "side": side,
            "price": round(price, 8),
            "completed_candle_at": quote.get("completed_at"),
            "costs_pln": round(costs, 2),
        },
    )


def execute_order(
    paper_portfolio: Mapping[str, Any],
    order: Mapping[str, Any],
    universe: Mapping[str, Any],
    quote_provider: QuoteProvider,
    config: EngineConfig,
    controller_status: str,
    now: datetime,
    *,
    transaction_cost_bps: float = 20.0,
    fx_cost_bps: float = 15.0,
    slippage_bps: float = 10.0,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    portfolio = copy.deepcopy(dict(paper_portfolio))
    result = copy.deepcopy(dict(order))
    if result.get("status") not in {"QUEUED", "WAITING_FOR_MARKET", "READY"}:
        return portfolio, result
    if controller_status not in CONTROLLING_STATUSES:
        result["status"] = "CANCELLED"
        result["failure_reason"] = "METHODOLOGY_DOES_NOT_CONTROL_PAPER_PORTFOLIO"
        return portfolio, result
    signal_at = parse_timestamp(result.get("signal_at"))
    if signal_at is None or now - signal_at > timedelta(hours=24):
        result["status"] = "EXPIRED"
        result["failure_reason"] = "SIGNAL_EXPIRED"
        return portfolio, result
    if controller_status == "PROBATIONARY_CONTROL" and float(
        result.get("confidence") or 0.0
    ) < config.probationary_minimum_confidence:
        result["status"] = "REJECTED_BY_RISK"
        result["failure_reason"] = "PROBATION_CONFIDENCE_TOO_LOW"
        return portfolio, result

    positions = _position_by_id(portfolio)
    instruments = _instrument_by_id(universe)
    if str(result.get("action") or "") in {"REDUCE", "EXIT", "ADD"}:
        return _execute_cash_adjustment(
            portfolio,
            result,
            positions,
            instruments,
            quote_provider,
            config,
            controller_status,
            signal_at,
            now,
            transaction_cost_bps,
            fx_cost_bps,
            slippage_bps,
        )
    sell_id = str(result.get("sell_instrument") or "")
    buy_id = str(result.get("buy_instrument") or "")
    if sell_id not in positions or buy_id not in instruments:
        result["status"] = "FAILED"
        result["failure_reason"] = "INSTRUMENT_NOT_AVAILABLE"
        return portfolio, result
    transactions = portfolio.setdefault("transactions", [])
    if _recent_opposite_trade(
        transactions, sell_id, "SELL", now, config.rotation_cooldown_days
    ) or _recent_opposite_trade(
        transactions, buy_id, "BUY", now, config.rotation_cooldown_days
    ):
        result["status"] = "REJECTED_BY_RISK"
        result["failure_reason"] = "ANTI_OSCILLATION_COOLDOWN"
        return portfolio, result

    sell = positions[sell_id]
    buy_meta = instruments[buy_id]
    sell_quote = dict(
        quote_provider.quote(str(sell.get("market_symbol")), str(sell.get("currency")))
    )
    buy_quote = dict(
        quote_provider.quote(
            str(buy_meta.get("market_symbol") or buy_meta.get("data_symbol")),
            str(buy_meta.get("currency")),
        )
    )
    if not sell_quote.get("market_open") or not buy_quote.get("market_open"):
        result["status"] = "WAITING_FOR_MARKET"
        result["failure_reason"] = "MARKET_CLOSED"
        return portfolio, result
    quote_error = _validate_quote(sell_quote, now) or _validate_quote(buy_quote, now)
    if quote_error:
        result["status"] = "EXPIRED" if quote_error == "STALE_QUOTE" else "FAILED"
        result["failure_reason"] = quote_error
        return portfolio, result
    if (
        parse_timestamp(sell_quote.get("completed_at")) <= signal_at
        or parse_timestamp(buy_quote.get("completed_at")) <= signal_at
    ):
        result["status"] = "WAITING_FOR_MARKET"
        result["failure_reason"] = "WAITING_FOR_POST_SIGNAL_CANDLE"
        return portfolio, result

    sell_price = float(sell_quote["price"]) * (1.0 - slippage_bps / 10000.0)
    sell_fx = float(sell_quote["fx_to_pln"])
    quantity = float(sell.get("quantity") or 0.0)
    gross_pln = quantity * sell_price * sell_fx
    sell_cost = gross_pln * (transaction_cost_bps + fx_cost_bps) / 10000.0
    proceeds = max(0.0, gross_pln - sell_cost)
    buy_price = float(buy_quote["price"]) * (1.0 + slippage_bps / 10000.0)
    buy_fx = float(buy_quote["fx_to_pln"])
    buy_cost_rate = (transaction_cost_bps + fx_cost_bps) / 10000.0
    buy_notional = proceeds / (1.0 + buy_cost_rate)
    buy_cost = buy_notional * buy_cost_rate
    buy_quantity = buy_notional / (buy_price * buy_fx)
    if buy_quantity <= 0:
        result["status"] = "REJECTED_BY_RISK"
        result["failure_reason"] = "NON_POSITIVE_ORDER_SIZE"
        return portfolio, result

    if controller_status == "PROBATIONARY_CONTROL":
        portfolio_value = float(portfolio.get("total_value_pln") or 10000.0)
        if buy_notional / max(portfolio_value, 1.0) > config.max_probation_new_position_weight:
            result["status"] = "REJECTED_BY_RISK"
            result["failure_reason"] = "PROBATION_NEW_POSITION_LIMIT"
            return portfolio, result

    portfolio["positions"] = [
        item for item in portfolio.get("positions", []) if item.get("id") != sell_id
    ]
    closed = copy.deepcopy(sell)
    closed.update(
        {
            "status": "paper_closed",
            "exit_price": round(sell_price, 8),
            "exit_fx_to_pln": round(sell_fx, 8),
            "exit_timestamp_utc": now.isoformat(timespec="seconds"),
            "exit_value_pln": round(proceeds, 2),
            "exit_fee_pln": round(sell_cost, 2),
        }
    )
    portfolio.setdefault("closed_positions", []).append(closed)
    new_position = {
        "id": buy_id,
        "label": buy_meta.get("label"),
        "broker_symbol": buy_meta.get("broker_symbol"),
        "market_symbol": buy_meta.get("market_symbol") or buy_meta.get("data_symbol"),
        "currency": buy_meta.get("currency"),
        "asset_type": buy_meta.get("asset_type"),
        "status": "paper_active",
        "quantity": round(buy_quantity, 8),
        "entry_date": now.date().isoformat(),
        "entry_timestamp_utc": now.isoformat(timespec="seconds"),
        "entry_price": round(buy_price, 8),
        "entry_price_type": "fresh_completed_5m_close_after_signal_paper",
        "entry_fx_to_pln": round(buy_fx, 8),
        "entry_notional_pln": round(buy_notional, 2),
        "entry_fee_pln": round(buy_cost, 2),
        "entry_value_pln": round(buy_notional + buy_cost, 2),
        "current_price": round(buy_price, 8),
        "current_fx_to_pln": round(buy_fx, 8),
        "current_value_pln": round(buy_notional, 2),
        "current_price_updated_at": buy_quote.get("completed_at"),
        "review_flag": "HOLD",
        "thesis_pl": buy_meta.get("thesis_pl"),
        "thesis_en": buy_meta.get("thesis_en"),
    }
    portfolio["positions"].append(new_position)
    for side, instrument_id, price, fx, units, costs in (
        ("SELL", sell_id, sell_price, sell_fx, quantity, sell_cost),
        ("BUY", buy_id, buy_price, buy_fx, buy_quantity, buy_cost),
    ):
        _append_transaction(
            transactions,
            result,
            instrument_id,
            side,
            price,
            fx,
            units,
            costs,
            slippage_bps,
            now,
        )
    return _finalize_portfolio(
        portfolio,
        result,
        now,
        {
            "sell_price": round(sell_price, 8),
            "buy_price": round(buy_price, 8),
            "sell_completed_candle_at": sell_quote.get("completed_at"),
            "buy_completed_candle_at": buy_quote.get("completed_at"),
            "costs_pln": round(sell_cost + buy_cost, 2),
        },
    )


def execute_queue(
    baseline_path: Path,
    paper_path: Path,
    queue: Mapping[str, Any],
    universe: Mapping[str, Any],
    quote_provider: QuoteProvider,
    config: EngineConfig,
    controller_status: str,
    now: datetime,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    baseline_before = read_json(baseline_path)
    paper = read_json(paper_path) or initialize_paper_portfolio(baseline_before, now)
    updated_orders = []
    for order in queue.get("orders", []) or []:
        paper, updated = execute_order(
            paper,
            order,
            universe,
            quote_provider,
            config,
            controller_status,
            now,
        )
        updated_orders.append(updated)
    baseline_after = read_json(baseline_path)
    assert_baseline_unchanged(baseline_before, baseline_after)
    write_json_atomic(paper_path, paper)
    updated_queue = copy.deepcopy(dict(queue))
    updated_queue["generated_at"] = now.isoformat(timespec="seconds")
    updated_queue["orders"] = updated_orders
    return paper, updated_queue


def main() -> int:
    from brace_portfolio_config import load_config

    parser = argparse.ArgumentParser()
    parser.add_argument("--network", action="store_true")
    args = parser.parse_args()
    registry = read_json(ENGINE_DATA_ROOT / "methodology_registry.json")
    controller_status = str(registry.get("controller_state") or "ACTIVE_BASELINE")
    if controller_status not in CONTROLLING_STATUSES:
        print(f"Paper execution skipped: controller={controller_status}")
        return 0
    if not args.network:
        raise ValueError("--network is required for fresh paper execution quotes")
    pending = read_json(ENGINE_DATA_ROOT / "pending_decisions.json")
    queue = prepare_orders(
        pending,
        controller_status,
        datetime.now(timezone.utc),
        read_json(ENGINE_DATA_ROOT / "paper_orders.json"),
    )
    config, _ = load_config()
    _, queue = execute_queue(
        BASELINE_PORTFOLIO_PATH,
        PAPER_PORTFOLIO_PATH,
        queue,
        read_json(ENGINE_DATA_ROOT / "universe.json"),
        YFinancePaperQuoteProvider(),
        config,
        controller_status,
        datetime.now(timezone.utc),
    )
    write_json_atomic(ENGINE_DATA_ROOT / "paper_orders.json", queue)
    executed = sum(
        1 for item in queue.get("orders", []) if item.get("status") == "PAPER_EXECUTED"
    )
    print(f"Paper execution complete: {executed} order(s) executed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
