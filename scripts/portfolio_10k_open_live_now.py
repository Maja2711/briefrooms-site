#!/usr/bin/env python3
"""Open only currently tradable 10K model-portfolio positions.

This module is intentionally conservative:
- it uses only a completed 5-minute bar that is still fresh;
- it records the exact timestamp used for every position;
- it never uses a stale close for a market that is already shut;
- unavailable positions remain pending and their allocation stays in cash;
- repeated runs are idempotent and never rewrite an existing entry.

The user explicitly authorised a staged launch on 17 July 2026 after the original
all-or-nothing launch window had passed.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

import pandas as pd
import yfinance as yf

import portfolio_10k_weekly as base
import portfolio_10k_weekly_v2 as cost

FRESHNESS_MINUTES = 15
BAR_MINUTES = 5
ENTRY_TYPE = "latest_completed_fresh_5m_close_user_authorized_staged_entry"
PARTIAL_STATUS = "partially_active"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalise_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty or "Close" not in frame:
        raise RuntimeError("No intraday data")
    index = pd.DatetimeIndex(pd.to_datetime(frame.index))
    if index.tz is None:
        index = index.tz_localize(timezone.utc)
    else:
        index = index.tz_convert(timezone.utc)
    clean = frame.copy()
    clean.index = index
    return clean[clean["Close"].notna()].sort_index()


def fresh_completed_quote(symbol: str, now: datetime) -> Tuple[float, datetime]:
    frame = yf.Ticker(symbol).history(
        period="1d", interval="5m", auto_adjust=False, actions=False, prepost=False
    )
    clean = _normalise_frame(frame)
    completed_before = now - timedelta(minutes=BAR_MINUTES)
    eligible = clean[clean.index <= completed_before]
    if eligible.empty:
        raise RuntimeError(f"No completed 5-minute bar for {symbol}")
    timestamp = eligible.index[-1].to_pydatetime().astimezone(timezone.utc)
    bar_end = timestamp + timedelta(minutes=BAR_MINUTES)
    age = now - bar_end
    if age > timedelta(minutes=FRESHNESS_MINUTES):
        raise RuntimeError(
            f"Latest completed bar for {symbol} is stale: {timestamp.isoformat(timespec='minutes')}"
        )
    price = base.finite(eligible.iloc[-1]["Close"])
    if price is None or price <= 0:
        raise RuntimeError(f"Invalid price for {symbol}")
    return float(price), timestamp


def fx_quote(currency: str, now: datetime) -> Tuple[float, datetime]:
    if currency == "PLN":
        return 1.0, now
    symbol = base.FX_SYMBOLS.get(currency)
    if not symbol:
        raise RuntimeError(f"Unsupported currency {currency}")
    return fresh_completed_quote(symbol, now)


def already_spent(data: Dict[str, Any]) -> float:
    return sum(
        base.finite(position.get("entry_value_pln")) or 0.0
        for position in data.get("positions", [])
        if position.get("status") == "active"
    )


def _execution_timestamp(value: Any) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise RuntimeError(f"Invalid staged execution timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise RuntimeError(f"Staged execution timestamp has no timezone: {value}")
    return parsed.astimezone(timezone.utc)


def portfolio_status(data: Dict[str, Any]) -> str:
    positions = data.get("positions", [])
    active_count = sum(1 for position in positions if position.get("status") == "active")
    if positions and active_count == len(positions):
        return "active"
    if active_count:
        return PARTIAL_STATUS
    return "pending_open"


def reconcile_staged_entries(data: Dict[str, Any]) -> bool:
    """Promote audited staged executions into their canonical position records.

    The staged batch is the source of truth. Re-running this function never changes
    an already active position and rejects conflicting duplicate executions.
    """
    before = copy.deepcopy(data)
    positions = {
        str(position.get("broker_symbol") or ""): position
        for position in data.get("positions", [])
    }
    executions: Dict[str, Dict[str, Any]] = {}
    for batch in data.get("staged_entry_batches", []):
        _execution_timestamp(batch.get("executed_at_utc"))
        for execution in batch.get("opened", []):
            symbol = str(execution.get("symbol") or "")
            if symbol not in positions:
                raise RuntimeError(f"Staged execution references unknown symbol: {symbol}")
            price = base.finite(execution.get("price"))
            rate = base.finite(execution.get("fx_to_pln"))
            entry_value = base.finite(execution.get("entry_value_pln"))
            timestamp = _execution_timestamp(execution.get("timestamp_utc"))
            if not price or price <= 0 or not rate or rate <= 0 or not entry_value or entry_value <= 0:
                raise RuntimeError(f"Incomplete staged execution for {symbol}")
            normalized = {
                "symbol": symbol,
                "price": float(price),
                "fx_to_pln": float(rate),
                "entry_value_pln": float(entry_value),
                "timestamp": timestamp,
            }
            previous = executions.get(symbol)
            if previous and previous != normalized:
                raise RuntimeError(f"Conflicting staged executions for {symbol}")
            executions[symbol] = normalized

    fx_fee = cost.fee_rate(data)
    for symbol, execution in executions.items():
        position = positions[symbol]
        if position.get("status") == "active":
            continue
        currency = str(position.get("currency") or "PLN")
        applied_fee = fx_fee if currency != "PLN" else 0.0
        entry_value = execution["entry_value_pln"]
        notional = entry_value / (1.0 + applied_fee)
        fee = entry_value - notional
        quantity = notional / (execution["price"] * execution["fx_to_pln"])
        timestamp = execution["timestamp"]
        position.update({
            "status": "active",
            "quantity": round(quantity, 6),
            "entry_date": timestamp.date().isoformat(),
            "entry_timestamp_utc": timestamp.isoformat(timespec="minutes"),
            "entry_price": round(execution["price"], 6),
            "entry_price_type": ENTRY_TYPE,
            "entry_fx_to_pln": round(execution["fx_to_pln"], 6),
            "entry_value_local": round(quantity * execution["price"], 6),
            "entry_notional_pln": round(notional, 2),
            "entry_fee_pln": round(fee, 2),
            "entry_value_pln": round(entry_value, 2),
            "current_price": round(execution["price"], 6),
            "current_fx_to_pln": round(execution["fx_to_pln"], 6),
            "current_value_pln": round(notional, 2),
            "pnl_pln": round(-fee, 2),
            "pnl_percent": round(-fee / entry_value, 6),
            "dividends_pln": 0.0,
            "market_date": timestamp.date().isoformat(),
            "review_flag": "HOLD",
        })
        if currency == "USD":
            data.setdefault("reporting_fx", {})["usd_pln"] = round(execution["fx_to_pln"], 6)

    data["status"] = portfolio_status(data)
    data.update(recompute_totals(data))
    if executions:
        latest_market_date = max(item["timestamp"].date() for item in executions.values())
        data["last_market_session"] = latest_market_date.isoformat()
    if executions and data["status"] != "pending_open":
        data["execution_model_version"] = "2.2-staged-reconciled"
    return data != before


def initialise_benchmark_if_possible(
    data: Dict[str, Any], position: Dict[str, Any], price: float, rate: float, timestamp: datetime
) -> None:
    if position.get("id") != "fwia":
        return
    benchmark = data.setdefault("benchmark", {})
    if base.finite(benchmark.get("units")):
        return
    start_capital = base.finite(data.get("starting_capital_pln")) or 10000.0
    applied_fee = cost.fee_rate(data) if benchmark.get("currency") != "PLN" else 0.0
    units = start_capital / (price * rate * (1.0 + applied_fee))
    notional = units * price * rate
    benchmark.update({
        "entry_price": round(price, 6),
        "entry_fx_to_pln": round(rate, 6),
        "entry_fee_pln": round(notional * applied_fee, 2),
        "units": round(units, 8),
        "entry_date": timestamp.date().isoformat(),
        "entry_timestamp_utc": timestamp.isoformat(timespec="minutes"),
        "entry_price_type": ENTRY_TYPE,
        "current_price": round(price, 6),
        "current_fx_to_pln": round(rate, 6),
        "current_value_pln": round(notional, 2),
        "return_percent": round(notional / start_capital - 1.0, 6),
        "market_date": timestamp.date().isoformat(),
    })


def recompute_totals(data: Dict[str, Any]) -> Dict[str, Any]:
    start_capital = base.finite(data.get("starting_capital_pln")) or 10000.0
    spent = already_spent(data)
    base_cash = round(start_capital - spent, 2)
    dividends = sum(
        base.finite(position.get("dividends_pln")) or 0.0
        for position in data.get("positions", [])
        if position.get("status") == "active"
    )
    cash = round(base_cash + dividends, 2)
    position_value = sum(
        base.finite(position.get("current_value_pln")) or 0.0
        for position in data.get("positions", [])
        if position.get("status") == "active"
    )
    total_value = round(cash + position_value, 2)
    for position in data.get("positions", []):
        value = base.finite(position.get("current_value_pln"))
        position["current_weight"] = (
            round(value / total_value, 6) if value is not None and total_value else None
        )
    benchmark = data.get("benchmark", {})
    return {
        "cash_pln": cash,
        "cash_balance_pln": cash,
        "base_cash_pln": base_cash,
        "dividends_pln": round(dividends, 2),
        "total_value_pln": total_value,
        "total_return_pln": round(total_value - start_capital, 2),
        "total_return_percent": round(total_value / start_capital - 1.0, 6),
        "benchmark_value_pln": base.round_or_none(benchmark.get("current_value_pln"), 2),
        "benchmark_return_percent": base.round_or_none(benchmark.get("return_percent"), 6),
    }


def append_snapshot(data: Dict[str, Any], summary: Dict[str, Any], now: datetime) -> None:
    snapshot = {
        "date": now.date().isoformat(),
        "timestamp_utc": now.isoformat(timespec="minutes"),
        "total_value_pln": summary["total_value_pln"],
        "benchmark_value_pln": summary.get("benchmark_value_pln"),
        "cash_pln": summary["cash_pln"],
        "reporting_usd_pln": data.get("reporting_fx", {}).get("usd_pln"),
        "event": "staged_live_entry",
    }
    snapshots = data.setdefault("snapshots", [])
    if not snapshots or snapshots[-1].get("timestamp_utc") != snapshot["timestamp_utc"]:
        snapshots.append(snapshot)


def run(data_path: Path = base.DATA_PATH, reconcile_only: bool = False) -> None:
    data = base.load_json(data_path)
    base.validate_config(data)
    changed = reconcile_staged_entries(data)
    if reconcile_only:
        if changed:
            base.write_json_atomic(data_path, data)
            print("Reconciled audited staged executions with portfolio positions")
        else:
            print("Staged executions already reconciled")
        return
    if data.get("status") == "active":
        if changed:
            base.write_json_atomic(data_path, data)
        print("Portfolio already fully active; no staged entries required")
        return

    now = utc_now()
    start_capital = base.finite(data.get("starting_capital_pln")) or 10000.0
    fx_fee = cost.fee_rate(data)
    opened = []
    pending_reasons = []
    fx_cache: Dict[str, Tuple[float, datetime]] = {}

    for position in data.get("positions", []):
        if position.get("status") == "active":
            continue
        try:
            price, timestamp = fresh_completed_quote(str(position["market_symbol"]), now)
            currency = str(position.get("currency") or "PLN")
            if currency not in fx_cache:
                fx_cache[currency] = fx_quote(currency, now)
            rate, fx_timestamp = fx_cache[currency]
            if currency != "PLN" and abs(timestamp - fx_timestamp) > timedelta(minutes=5):
                raise RuntimeError(f"FX quote for {currency} is not synchronized")

            allocation = start_capital * float(position["target_weight"])
            applied_fee = fx_fee if currency != "PLN" else 0.0
            quantity = round(allocation / (price * rate * (1.0 + applied_fee)), 6)
            notional = quantity * price * rate
            fee = notional * applied_fee
            entry_value = notional + fee
            position.update({
                "status": "active",
                "quantity": quantity,
                "entry_date": timestamp.date().isoformat(),
                "entry_timestamp_utc": timestamp.isoformat(timespec="minutes"),
                "entry_price": round(price, 6),
                "entry_price_type": ENTRY_TYPE,
                "entry_fx_to_pln": round(rate, 6),
                "entry_value_local": round(quantity * price, 6),
                "entry_notional_pln": round(notional, 2),
                "entry_fee_pln": round(fee, 2),
                "entry_value_pln": round(entry_value, 2),
                "current_price": round(price, 6),
                "current_fx_to_pln": round(rate, 6),
                "current_value_pln": round(notional, 2),
                "pnl_pln": round(-fee, 2),
                "pnl_percent": round(-fee / entry_value, 6) if entry_value else None,
                "dividends_pln": 0.0,
                "market_date": timestamp.date().isoformat(),
                "review_flag": "HOLD",
            })
            initialise_benchmark_if_possible(data, position, price, rate, timestamp)
            opened.append({
                "symbol": position.get("broker_symbol"),
                "timestamp_utc": timestamp.isoformat(timespec="minutes"),
                "price": round(price, 6),
                "fx_to_pln": round(rate, 6),
                "quantity": quantity,
                "entry_value_pln": round(entry_value, 2),
            })
        except Exception as exc:
            pending_reasons.append({
                "symbol": position.get("broker_symbol"),
                "reason": str(exc),
            })

    active_count = sum(1 for p in data.get("positions", []) if p.get("status") == "active")
    total_count = len(data.get("positions", []))
    data["status"] = portfolio_status(data)
    if not opened:
        if changed:
            base.write_json_atomic(data_path, data)
        print(f"Opened 0 positions; {total_count - active_count} remain pending")
        for item in pending_reasons:
            print(f"PENDING {item['symbol']}: {item['reason']}")
        return
    summary = recompute_totals(data)
    data.update(summary)
    data["cost_model_version"] = cost.COST_MODEL_VERSION
    data["execution_model_version"] = "2.2-staged-reconciled"
    data["last_updated_at"] = base.now_local().isoformat(timespec="seconds")
    data["last_market_session"] = now.date().isoformat() if opened else data.get("last_market_session")
    data["last_run_error"] = None
    data.setdefault("staged_entry_authorized_at_utc", now.isoformat(timespec="seconds"))
    data["staged_entry_batches"] = data.get("staged_entry_batches", []) + [{
        "executed_at_utc": now.isoformat(timespec="seconds"),
        "opened": opened,
        "still_pending": pending_reasons,
    }]
    data["pending_entry_rule_pl"] = (
        "Portfel jest uruchamiany partiami wyłącznie po świeżych, zakończonych świecach 5-minutowych. "
        "Pozycje z zamkniętych rynków pozostają w gotówce do najbliższej sesji; żadna cena nie jest wstawiana wstecznie."
    )
    data["pending_entry_rule_en"] = (
        "The portfolio is launched in stages using only fresh completed 5-minute bars. "
        "Positions on closed markets remain in cash until their next session; no price is backfilled."
    )
    data["initialization_note_pl"] = (
        "Na wyraźne polecenie użytkownika z 17 lipca 2026 model przeszedł na etapowe wejście. "
        "Każda cena ma własny timestamp, a zamknięte rynki pozostają w gotówce."
    )
    data["initialization_note_en"] = (
        "Following the user's explicit instruction on 17 July 2026, the model switched to staged entry. "
        "Each price has its own timestamp and closed markets remain in cash."
    )
    if "USD" in fx_cache:
        data.setdefault("reporting_fx", {})["usd_pln"] = round(fx_cache["USD"][0], 6)
    append_snapshot(data, summary, now)
    base.write_json_atomic(data_path, data)
    print(f"Opened {len(opened)} positions; {total_count - active_count} remain pending")
    for item in opened:
        print(f"OPEN {item['symbol']} {item['price']} at {item['timestamp_utc']}")
    for item in pending_reasons:
        print(f"PENDING {item['symbol']}: {item['reason']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reconcile-only", action="store_true")
    args = parser.parse_args()
    run(reconcile_only=args.reconcile_only)


if __name__ == "__main__":
    main()
