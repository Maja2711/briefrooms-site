#!/usr/bin/env python3
"""Complete declared Portfolio 10K ETF allocations from recent live-session trades.

Some UCITS ETFs trade less frequently than large-cap stocks. The standard staged
entry requires a trade completed within 15 minutes, which can reject a valid open-
session ETF quote by only a few minutes. This completion layer keeps the same
transparent timestamped execution model, but allows the latest completed 5-minute
ETF trade up to 45 minutes old while the workflow runs during European market
hours. It never uses a previous-session close and never rewrites active positions.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Tuple

import pandas as pd
import yfinance as yf

import portfolio_10k_open_live_now as staged
import portfolio_10k_weekly as base
import portfolio_10k_weekly_v2 as cost

BAR_MINUTES = 5
ETF_MAX_AGE_MINUTES = 45
ETF_FX_SYNC_MINUTES = 30
ENTRY_TYPE = "latest_completed_recent_open_session_5m_trade_declared_allocation"


def recent_etf_quote(symbol: str, now: datetime) -> Tuple[float, datetime]:
    frame = yf.Ticker(symbol).history(
        period="1d", interval="5m", auto_adjust=False, actions=False, prepost=False
    )
    clean = staged._normalise_frame(frame)
    completed_before = now - timedelta(minutes=BAR_MINUTES)
    eligible = clean[clean.index <= completed_before]
    if eligible.empty:
        raise RuntimeError(f"No completed 5-minute trade for {symbol}")
    timestamp = eligible.index[-1].to_pydatetime().astimezone(timezone.utc)
    age = now - (timestamp + timedelta(minutes=BAR_MINUTES))
    if age > timedelta(minutes=ETF_MAX_AGE_MINUTES):
        raise RuntimeError(
            f"Latest open-session trade for {symbol} is too old: "
            f"{timestamp.isoformat(timespec='minutes')}"
        )
    price = base.finite(eligible.iloc[-1]["Close"])
    if price is None or price <= 0:
        raise RuntimeError(f"Invalid price for {symbol}")
    return float(price), timestamp


def run() -> None:
    data = base.load_json(base.DATA_PATH)
    base.validate_config(data)
    staged.reconcile_staged_entries(data)
    if data.get("status") == "active":
        print("Portfolio already fully active")
        return

    now = staged.utc_now()
    start_capital = base.finite(data.get("starting_capital_pln")) or 10000.0
    fx_fee = cost.fee_rate(data)
    opened = []
    pending_reasons = []

    for position in data.get("positions", []):
        if position.get("status") == "active" or position.get("asset_type") != "ETF":
            continue
        try:
            price, timestamp = recent_etf_quote(str(position["market_symbol"]), now)
            currency = str(position.get("currency") or "PLN")
            rate, fx_timestamp = staged.fx_quote(currency, now)
            if currency != "PLN" and abs(timestamp - fx_timestamp) > timedelta(minutes=ETF_FX_SYNC_MINUTES):
                raise RuntimeError(f"FX quote for {currency} is not sufficiently synchronized")

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
            staged.initialise_benchmark_if_possible(data, position, price, rate, timestamp)
            opened.append({
                "symbol": position.get("broker_symbol"),
                "timestamp_utc": timestamp.isoformat(timespec="minutes"),
                "price": round(price, 6),
                "fx_to_pln": round(rate, 6),
                "quantity": quantity,
                "entry_value_pln": round(entry_value, 2),
            })
        except Exception as exc:
            pending_reasons.append({"symbol": position.get("broker_symbol"), "reason": str(exc)})

    if not opened:
        print("No pending ETF allocation could be completed")
        for item in pending_reasons:
            print(f"PENDING {item['symbol']}: {item['reason']}")
        return

    data["status"] = staged.portfolio_status(data)
    summary = staged.recompute_totals(data)
    data.update(summary)
    data["cost_model_version"] = cost.COST_MODEL_VERSION
    data["execution_model_version"] = "2.3-declared-allocation-completion"
    data["last_updated_at"] = base.now_local().isoformat(timespec="seconds")
    data["last_market_session"] = now.date().isoformat()
    data["last_run_error"] = None
    data.setdefault("staged_entry_batches", []).append({
        "executed_at_utc": now.isoformat(timespec="seconds"),
        "opened": opened,
        "still_pending": pending_reasons,
    })
    data["pending_entry_rule_pl"] = (
        "Wszystkie zadeklarowane pozycje są otwierane podczas aktywnej sesji. Dla mniej płynnych ETF-ów "
        "model dopuszcza ostatnią zakończoną transakcję 5-minutową z bieżącej sesji do 45 minut, zawsze z timestampem."
    )
    data["pending_entry_rule_en"] = (
        "All declared positions are opened during an active session. For less liquid ETFs, the model may use "
        "the latest completed 5-minute trade from the current session up to 45 minutes old, always timestamped."
    )
    staged.append_snapshot(data, summary, now)
    base.write_json_atomic(base.DATA_PATH, data)
    print(f"Completed {len(opened)} declared ETF positions")
    for item in opened:
        print(f"OPEN {item['symbol']} {item['price']} at {item['timestamp_utc']}")


if __name__ == "__main__":
    run()
