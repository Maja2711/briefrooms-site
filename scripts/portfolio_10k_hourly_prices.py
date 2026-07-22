#!/usr/bin/env python3
"""Refresh Portfolio 10K prices from recent completed intraday bars.

Entry prices and quantities are immutable. This script only updates current
quotes, FX rates, valuation, P/L, weights and the benchmark. It is intended to
run once per hour while at least one portfolio market may be trading.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "investments" / "portfolio_10k.json"
WARSAW = ZoneInfo("Europe/Warsaw")
UTC = timezone.utc
FX_SYMBOLS = {"PLN": None, "USD": "USDPLN=X", "EUR": "EURPLN=X", "DKK": "DKKPLN=X"}


@dataclass(frozen=True)
class Session:
    timezone_name: str
    open_time: time
    close_time: time


@dataclass(frozen=True)
class Quote:
    price: float
    timestamp_utc: datetime
    source: str


def finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        temp_name = tmp.name
    os.replace(temp_name, path)


def session_for(position: Dict[str, Any]) -> Session:
    symbol = str(position.get("market_symbol") or "")
    if symbol.endswith(".DE"):
        return Session("Europe/Berlin", time(9, 0), time(17, 30))
    if symbol.endswith(".CO") or str(position.get("currency")) == "DKK":
        return Session("Europe/Copenhagen", time(9, 0), time(17, 0))
    return Session("America/New_York", time(9, 30), time(16, 0))


def market_is_open(position: Dict[str, Any], now_utc: datetime) -> bool:
    session = session_for(position)
    local = now_utc.astimezone(ZoneInfo(session.timezone_name))
    return local.weekday() < 5 and session.open_time <= local.time().replace(tzinfo=None) < session.close_time


def normalized_intraday(frame: pd.DataFrame, assumed_tz: str) -> pd.DataFrame:
    if frame is None or frame.empty or "Close" not in frame:
        return pd.DataFrame()
    out = frame.copy()
    out = out[out["Close"].notna()]
    if out.empty:
        return out
    index = pd.DatetimeIndex(out.index)
    if index.tz is None:
        index = index.tz_localize(ZoneInfo(assumed_tz))
    out.index = index.tz_convert(UTC)
    return out


def fetch_completed_quote(symbol: str, assumed_tz: str, now_utc: datetime) -> Quote:
    frame = yf.Ticker(symbol).history(
        period="5d",
        interval="5m",
        auto_adjust=False,
        actions=False,
        prepost=False,
    )
    frame = normalized_intraday(frame, assumed_tz)
    if frame.empty:
        raise RuntimeError(f"No intraday data for {symbol}")

    # yfinance labels five-minute bars by their start, so wait until the bar is complete.
    completed_before = pd.Timestamp(now_utc - timedelta(minutes=5))
    eligible = frame[frame.index <= completed_before]
    if eligible.empty:
        raise RuntimeError(f"No completed intraday bar for {symbol}")

    timestamp = pd.Timestamp(eligible.index[-1]).to_pydatetime().astimezone(UTC)
    price = finite(eligible["Close"].iloc[-1])
    if price is None or price <= 0:
        raise RuntimeError(f"Invalid intraday price for {symbol}")
    return Quote(price=price, timestamp_utc=timestamp, source=f"Yahoo Finance:{symbol}:5d:5m:last_completed")


def quote_is_usable(quote: Quote, open_now: bool, now_utc: datetime) -> bool:
    age = now_utc - quote.timestamp_utc
    if age < timedelta(minutes=-1):
        return False
    # During trading reject a feed that has stopped moving. Outside trading use the
    # latest completed session quote rather than falling back to the purchase price.
    return age <= (timedelta(minutes=90) if open_now else timedelta(days=7))


def fx_quote(currency: str, now_utc: datetime, cache: Dict[str, Quote]) -> Tuple[float, datetime, str]:
    if currency == "PLN":
        return 1.0, now_utc, "fixed:PLN"
    symbol = FX_SYMBOLS.get(currency)
    if not symbol:
        raise RuntimeError(f"Unsupported currency: {currency}")
    if currency not in cache:
        quote = fetch_completed_quote(symbol, "Europe/Warsaw", now_utc)
        if now_utc - quote.timestamp_utc > timedelta(days=4):
            raise RuntimeError(f"Stale FX quote for {currency}")
        cache[currency] = quote
    quote = cache[currency]
    return quote.price, quote.timestamp_utc, quote.source


def update_portfolio(data: Dict[str, Any], now_utc: datetime) -> Dict[str, Any]:
    fx_cache: Dict[str, Quote] = {}
    updated = 0
    errors = []
    latest_market_dates = []

    for position in data.get("positions", []):
        if position.get("status") != "active":
            continue
        session = session_for(position)
        open_now = market_is_open(position, now_utc)
        position["market_status"] = "open" if open_now else "closed"
        position["market_timezone"] = session.timezone_name
        try:
            quote = fetch_completed_quote(str(position.get("market_symbol") or ""), session.timezone_name, now_utc)
            if not quote_is_usable(quote, open_now, now_utc):
                raise RuntimeError(
                    f"Stale quote: {quote.timestamp_utc.isoformat()} while market_status={position['market_status']}"
                )
            rate, fx_timestamp, fx_source = fx_quote(str(position.get("currency") or "PLN"), now_utc, fx_cache)
            quantity = finite(position.get("quantity")) or 0.0
            current_value = quantity * quote.price * rate
            dividends = finite(position.get("dividends_pln")) or 0.0
            entry_value = finite(position.get("entry_value_pln")) or 0.0
            pnl = current_value + dividends - entry_value
            position.update({
                "current_price": round(quote.price, 6),
                "current_price_updated_at": quote.timestamp_utc.isoformat(timespec="seconds"),
                "current_price_source": quote.source,
                "current_fx_to_pln": round(rate, 6),
                "current_fx_updated_at": fx_timestamp.isoformat(timespec="seconds"),
                "current_fx_source": fx_source,
                "current_value_pln": round(current_value, 2),
                "pnl_pln": round(pnl, 2),
                "pnl_percent": round(pnl / entry_value, 6) if entry_value else None,
                "market_date": quote.timestamp_utc.astimezone(ZoneInfo(session.timezone_name)).date().isoformat(),
                "quote_update_error": None,
            })
            latest_market_dates.append(position["market_date"])
            updated += 1
        except Exception as exc:
            position["quote_update_error"] = str(exc)
            errors.append(f"{position.get('market_symbol')}: {exc}")

    active = [p for p in data.get("positions", []) if p.get("status") == "active"]
    position_value = sum(finite(p.get("current_value_pln")) or 0.0 for p in active)
    dividends_total = sum(finite(p.get("dividends_pln")) or 0.0 for p in active)
    base_cash = finite(data.get("base_cash_pln"))
    if base_cash is None:
        base_cash = round((finite(data.get("starting_capital_pln")) or 10000.0) - sum(finite(p.get("entry_value_pln")) or 0.0 for p in active), 2)
        data["base_cash_pln"] = base_cash
    cash = round(base_cash + dividends_total, 2)
    total_value = round(position_value + cash, 2)
    starting = finite(data.get("starting_capital_pln")) or 10000.0

    for position in active:
        value = finite(position.get("current_value_pln"))
        position["current_weight"] = round(value / total_value, 6) if value is not None and total_value else None

    data["cash_pln"] = cash
    data["cash_balance_pln"] = cash
    data["total_value_pln"] = total_value
    data["total_return_pln"] = round(total_value - starting, 2)
    data["total_return_percent"] = round((total_value - starting) / starting, 6)

    benchmark = data.get("benchmark") or {}
    benchmark_position = next((p for p in active if p.get("market_symbol") == benchmark.get("market_symbol")), None)
    if benchmark_position:
        price = finite(benchmark_position.get("current_price"))
        rate = finite(benchmark_position.get("current_fx_to_pln"))
        units = finite(benchmark.get("units"))
        if price and rate and units:
            value = units * price * rate
            benchmark.update({
                "current_price": round(price, 6),
                "current_price_updated_at": benchmark_position.get("current_price_updated_at"),
                "current_price_source": benchmark_position.get("current_price_source"),
                "current_fx_to_pln": round(rate, 6),
                "current_fx_updated_at": benchmark_position.get("current_fx_updated_at"),
                "current_value_pln": round(value, 2),
                "return_percent": round(value / starting - 1.0, 6),
                "market_date": benchmark_position.get("market_date"),
            })
            data["benchmark_value_pln"] = round(value, 2)
            data["benchmark_return_percent"] = round(value / starting - 1.0, 6)

    usd_rate = fx_cache.get("USD")
    if usd_rate:
        data["reporting_fx"] = {
            "usd_pln": round(usd_rate.price, 6),
            "updated_at": usd_rate.timestamp_utc.isoformat(timespec="seconds"),
            "source": usd_rate.source,
        }

    now_local = now_utc.astimezone(WARSAW)
    data["last_updated_at"] = now_local.isoformat(timespec="seconds")
    if latest_market_dates:
        data["last_market_session"] = max(latest_market_dates)
    data["hourly_valuation"] = {
        "policy_version": "1.0.0",
        "checked_at": now_local.isoformat(timespec="seconds"),
        "updated_instruments": updated,
        "active_instruments": len(active),
        "errors": errors,
        "rule_pl": "Bieżąca wycena jest odświeżana co godzinę w dni handlowe z ostatniej zakończonej świecy 5-minutowej. Po zamknięciu rynku pozostaje ostatnia dostępna cena sesyjna; cena zakupu nie zastępuje ceny bieżącej.",
        "rule_en": "Current valuation is refreshed hourly on trading days from the latest completed five-minute bar. After the market closes, the latest available session price remains visible; the purchase price is never substituted for the current price.",
    }
    data["last_run_error"] = "; ".join(errors) if errors and not updated else None

    if updated:
        hour_key = now_utc.strftime("%Y-%m-%dT%H:00Z")
        snapshots = data.setdefault("snapshots", [])
        snapshots[:] = [s for s in snapshots if s.get("hour_key") != hour_key]
        snapshots.append({
            "date": now_local.date().isoformat(),
            "timestamp_utc": now_utc.isoformat(timespec="seconds"),
            "hour_key": hour_key,
            "event": "hourly_market_valuation",
            "total_value_pln": total_value,
            "benchmark_value_pln": data.get("benchmark_value_pln"),
            "cash_pln": cash,
            "reporting_usd_pln": (data.get("reporting_fx") or {}).get("usd_pln"),
        })
        data["snapshots"] = snapshots[-1000:]

    return {"updated": updated, "errors": errors, "total_value_pln": total_value}


def main() -> None:
    data = load_json(DATA_PATH)
    if data.get("status") not in {"active", "partially_active"}:
        print(f"Portfolio status {data.get('status')} does not require hourly valuation")
        return
    result = update_portfolio(data, datetime.now(UTC))
    write_json_atomic(DATA_PATH, data)
    print(json.dumps(result, ensure_ascii=False))
    if result["updated"] == 0:
        raise SystemExit("No instrument quote could be refreshed")


if __name__ == "__main__":
    main()
