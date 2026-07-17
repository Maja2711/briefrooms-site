#!/usr/bin/env python3
"""Market-hours execution layer for the BriefRooms 10K model portfolio.

The first published version incorrectly treated the latest available daily close as
an executed entry even when some exchanges were closed. This layer fixes that by:

- invalidating the synthetic initial entries while preserving an audit correction;
- keeping the portfolio in ``pending_open`` until all instruments share an open
  market window;
- freezing every entry from the first completed 5-minute bar at or after
  14:40 UTC on a common weekday session;
- requiring every stock, ETF and FX conversion quote before any position opens;
- never backfilling a missed entry with the previous session's close.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Tuple

import pandas as pd
import yfinance as yf

import portfolio_10k_weekly as base
import portfolio_10k_weekly_v2 as cost

EXECUTION_MODEL_VERSION = "2.0"
ENTRY_BAR_UTC = time(14, 40)
ENTRY_WINDOW_END_UTC = time(15, 20)
ENTRY_BAR_MAX_DELAY_MINUTES = 15

POSITION_EXECUTION_FIELDS = {
    "quantity", "entry_date", "entry_timestamp_utc", "entry_price", "entry_price_type",
    "entry_fx_to_pln", "entry_notional_pln", "entry_fee_pln", "entry_value_pln",
    "current_price", "current_fx_to_pln", "current_value_pln", "pnl_pln", "pnl_percent",
    "dividends_pln", "market_date", "ma50", "ma200", "return_6m", "drawdown_52w",
    "volatility_20d", "next_earnings_date", "model_score", "positive_signals",
    "risk_signals", "recent_news", "current_weight", "review_flag",
}
BENCHMARK_EXECUTION_FIELDS = {
    "entry_price", "entry_fx_to_pln", "entry_fee_pln", "units", "entry_date",
    "entry_timestamp_utc", "entry_price_type", "current_price", "current_fx_to_pln",
    "current_value_pln", "return_percent", "market_date",
}


@dataclass(frozen=True)
class IntradayQuote:
    symbol: str
    price: float
    timestamp_utc: datetime


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def target_timestamp(now: datetime) -> datetime:
    current = now.astimezone(timezone.utc)
    return datetime.combine(current.date(), ENTRY_BAR_UTC, tzinfo=timezone.utc)


def is_common_entry_window(now: datetime) -> bool:
    current = now.astimezone(timezone.utc)
    return current.weekday() < 5 and ENTRY_BAR_UTC <= current.time() <= ENTRY_WINDOW_END_UTC


def _clear_keys(payload: Dict[str, Any], keys: Iterable[str]) -> None:
    for key in keys:
        payload.pop(key, None)


def migrate_invalid_initialization(data: Dict[str, Any], now: datetime) -> bool:
    """Invalidate the close-price launch without pretending it was a real trade."""
    if data.get("execution_model_version") == EXECUTION_MODEL_VERSION:
        return False

    legacy_active = data.get("status") == "active" and any(
        position.get("status") == "active" and position.get("entry_price") is not None
        for position in data.get("positions", [])
    )
    if legacy_active:
        correction = {
            "corrected_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "reason_pl": (
                "Pierwsza inicjalizacja użyła ostatnich cen zamknięcia, mimo że rynki USA "
                "nie były jeszcze otwarte. Te wpisy nie były transakcjami i zostały unieważnione."
            ),
            "reason_en": (
                "The first initialization used latest daily closes while US markets were not yet open. "
                "Those records were not executions and have been invalidated."
            ),
            "invalidated_total_value_pln": data.get("total_value_pln"),
            "invalidated_total_return_pln": data.get("total_return_pln"),
            "invalidated_positions": [
                {
                    "broker_symbol": p.get("broker_symbol"),
                    "entry_date": p.get("entry_date"),
                    "entry_price": p.get("entry_price"),
                    "entry_price_type": "latest_daily_close_not_execution",
                }
                for p in data.get("positions", [])
                if p.get("entry_price") is not None
            ],
        }
        data.setdefault("audit_corrections", []).append(correction)

    for position in data.get("positions", []):
        _clear_keys(position, POSITION_EXECUTION_FIELDS)
        position["status"] = "pending"

    benchmark = data.setdefault("benchmark", {})
    _clear_keys(benchmark, BENCHMARK_EXECUTION_FIELDS)

    starting = base.finite(data.get("starting_capital_pln")) or 10000.0
    invalidated_snapshots = len(data.get("snapshots", []))
    invalidated_reviews = len(data.get("weekly_reviews", []))
    data["invalidated_history_count"] = {
        "snapshots": invalidated_snapshots,
        "weekly_reviews": invalidated_reviews,
    }
    data["snapshots"] = []
    data["weekly_reviews"] = []
    data["closed_positions"] = data.get("closed_positions", [])
    data.update({
        "status": "pending_open",
        "cash_pln": round(starting, 2),
        "base_cash_pln": round(starting, 2),
        "cash_balance_pln": round(starting, 2),
        "total_value_pln": round(starting, 2),
        "dividends_pln": 0.0,
        "total_return_pln": 0.0,
        "total_return_percent": 0.0,
        "benchmark_value_pln": None,
        "benchmark_return_percent": None,
        "last_market_session": None,
        "last_run_error": None,
        "execution_model_version": EXECUTION_MODEL_VERSION,
        "pending_entry_target_utc": "14:40",
        "pending_entry_rule_pl": (
            "Portfel oczekuje na wspólną sesję. Wszystkie pozycje zostaną otwarte jednocześnie "
            "po pierwszej zakończonej świecy 5-minutowej od 14:40 UTC, gdy rynki USA i Europy są otwarte."
        ),
        "pending_entry_rule_en": (
            "The portfolio is waiting for a common session. All positions will open together "
            "using the first completed 5-minute bar from 14:40 UTC while US and European markets are open."
        ),
        "broker_note_pl": (
            "To portfel modelowy, nie rachunek brokerski. Cena wejścia nie jest wstecznie pobierana z zamknięcia. "
            "Model czeka na wspólną sesję i używa zsynchronizowanej ceny 5-minutowej; rzeczywisty BID/ASK XTB może się różnić."
        ),
        "broker_note_en": (
            "This is a model portfolio, not a brokerage account. Entry is never backfilled from a prior close. "
            "The model waits for a common session and uses a synchronized 5-minute price; actual XTB bid/ask may differ."
        ),
    })
    return True


def fetch_intraday_quote(symbol: str, target: datetime) -> IntradayQuote:
    frame = yf.Ticker(symbol).history(
        period="5d", interval="5m", auto_adjust=False, actions=False, prepost=False
    )
    if frame is None or frame.empty or "Close" not in frame:
        raise RuntimeError(f"No intraday data for {symbol}")

    index = pd.DatetimeIndex(pd.to_datetime(frame.index))
    if index.tz is None:
        index = index.tz_localize(timezone.utc)
    else:
        index = index.tz_convert(timezone.utc)
    clean = frame.copy()
    clean.index = index
    clean = clean[clean["Close"].notna()]

    latest_allowed = target + timedelta(minutes=ENTRY_BAR_MAX_DELAY_MINUTES)
    eligible = clean[(clean.index >= target) & (clean.index <= latest_allowed)]
    if eligible.empty:
        raise RuntimeError(
            f"No completed 5-minute bar for {symbol} between "
            f"{target.isoformat()} and {latest_allowed.isoformat()}"
        )
    timestamp = eligible.index[0].to_pydatetime().astimezone(timezone.utc)
    price = base.finite(eligible.iloc[0]["Close"])
    if price is None or price <= 0:
        raise RuntimeError(f"Invalid intraday price for {symbol}")
    return IntradayQuote(symbol=symbol, price=price, timestamp_utc=timestamp)


def fetch_daily_markets(data: Dict[str, Any]) -> Dict[str, base.MarketRecord]:
    markets: Dict[str, base.MarketRecord] = {}
    errors: List[str] = []
    for position in data.get("positions", []):
        try:
            markets[position["id"]] = base.fetch_market(
                str(position["market_symbol"]),
                str(position["currency"]),
                include_earnings=position.get("asset_type") == "Stock",
            )
        except Exception as exc:
            errors.append(f"{position.get('market_symbol')}: {exc}")
    if errors:
        raise RuntimeError("; ".join(errors))
    return markets


def fetch_entry_quotes(
    data: Dict[str, Any], target: datetime
) -> Tuple[Dict[str, IntradayQuote], Dict[str, IntradayQuote]]:
    markets: Dict[str, IntradayQuote] = {}
    fx_quotes: Dict[str, IntradayQuote] = {}
    errors: List[str] = []

    for position in data.get("positions", []):
        try:
            markets[position["id"]] = fetch_intraday_quote(str(position["market_symbol"]), target)
        except Exception as exc:
            errors.append(f"{position.get('market_symbol')}: {exc}")

    for currency in sorted({str(p.get("currency")) for p in data.get("positions", [])}):
        if currency == "PLN":
            continue
        symbol = base.FX_SYMBOLS.get(currency)
        if not symbol:
            errors.append(f"Unsupported currency: {currency}")
            continue
        try:
            fx_quotes[currency] = fetch_intraday_quote(symbol, target)
        except Exception as exc:
            errors.append(f"{symbol}: {exc}")

    if errors:
        raise RuntimeError("; ".join(errors))

    timestamps = [q.timestamp_utc for q in markets.values()] + [q.timestamp_utc for q in fx_quotes.values()]
    if not timestamps or max(timestamps) - min(timestamps) > timedelta(minutes=5):
        raise RuntimeError("Entry quotes are not synchronized within five minutes")
    return markets, fx_quotes


def initialize_from_intraday(
    data: Dict[str, Any],
    quotes: Dict[str, IntradayQuote],
    fx_quotes: Dict[str, IntradayQuote],
    target: datetime,
) -> None:
    start_capital = base.finite(data.get("starting_capital_pln")) or 10000.0
    fx_fee = cost.fee_rate(data)
    spent = 0.0

    def fx_for(currency: str) -> float:
        return 1.0 if currency == "PLN" else fx_quotes[currency].price

    for position in data.get("positions", []):
        quote = quotes[position["id"]]
        rate = fx_for(str(position["currency"]))
        allocation = start_capital * float(position["target_weight"])
        applied_fee = fx_fee if position.get("currency") != "PLN" else 0.0
        quantity = round(allocation / (quote.price * rate * (1.0 + applied_fee)), 6)
        notional = quantity * quote.price * rate
        fee = notional * applied_fee
        entry_value = notional + fee
        position.update({
            "status": "active",
            "quantity": quantity,
            "entry_date": target.date().isoformat(),
            "entry_timestamp_utc": quote.timestamp_utc.isoformat(timespec="minutes"),
            "entry_price": round(quote.price, 6),
            "entry_price_type": "first_completed_5m_close_at_or_after_14_40_utc",
            "entry_fx_to_pln": round(rate, 6),
            "entry_notional_pln": round(notional, 2),
            "entry_fee_pln": round(fee, 2),
            "entry_value_pln": round(entry_value, 2),
        })
        spent += entry_value

    cash = round(start_capital - spent, 2)
    data.update({
        "status": "active",
        "base_cash_pln": cash,
        "cash_pln": cash,
        "cash_balance_pln": cash,
        "cost_model_version": cost.COST_MODEL_VERSION,
        "execution_model_version": EXECUTION_MODEL_VERSION,
        "model_entry_date": target.date().isoformat(),
        "model_entry_timestamp_utc": target.isoformat(timespec="minutes"),
        "initialization_note_pl": (
            "Pozycje otwarto modelowo jednocześnie na pierwszej zakończonej świecy 5-minutowej "
            "od 14:40 UTC podczas wspólnej sesji USA i Europy. Nie są to potwierdzenia zleceń XTB."
        ),
        "initialization_note_en": (
            "Positions were opened simultaneously in the model using the first completed 5-minute bar "
            "from 14:40 UTC during a common US-European session. These are not XTB execution confirmations."
        ),
    })

    benchmark = data["benchmark"]
    quote = quotes["fwia"]
    rate = fx_for(str(benchmark.get("currency") or "EUR"))
    applied_fee = fx_fee if benchmark.get("currency") != "PLN" else 0.0
    units = start_capital / (quote.price * rate * (1.0 + applied_fee))
    notional = units * quote.price * rate
    benchmark.update({
        "entry_price": round(quote.price, 6),
        "entry_fx_to_pln": round(rate, 6),
        "entry_fee_pln": round(notional * applied_fee, 2),
        "units": round(units, 8),
        "entry_date": target.date().isoformat(),
        "entry_timestamp_utc": quote.timestamp_utc.isoformat(timespec="minutes"),
        "entry_price_type": "first_completed_5m_close_at_or_after_14_40_utc",
    })


def entry_markets(
    daily: Dict[str, base.MarketRecord], quotes: Dict[str, IntradayQuote]
) -> Dict[str, base.MarketRecord]:
    return {
        position_id: replace(
            record,
            price=quotes[position_id].price,
            market_date=quotes[position_id].timestamp_utc.date().isoformat(),
        )
        for position_id, record in daily.items()
    }


def save_pending(data: Dict[str, Any], now: datetime, message: str | None = None) -> None:
    data["status"] = "pending_open"
    data["last_updated_at"] = base.now_local().isoformat(timespec="seconds")
    data["last_run_error"] = message
    base.write_json_atomic(base.DATA_PATH, data)


def run(mode: str) -> None:
    data = base.load_json(base.DATA_PATH)
    base.validate_config(data)
    now = utc_now()
    migrated = migrate_invalid_initialization(data, now)

    if mode == "migrate":
        save_pending(data, now)
        print("Portfolio 10K migrated to pending synchronized entry")
        return

    if data.get("status") in {"planned", "pending_open"}:
        if not is_common_entry_window(now):
            save_pending(data, now)
            print("Portfolio 10K remains pending: common entry window is closed")
            return
        target = target_timestamp(now)
        try:
            daily = fetch_daily_markets(data)
            quotes, fx_quotes = fetch_entry_quotes(data, target)
            initialize_from_intraday(data, quotes, fx_quotes, target)
            summary = cost.update_current_state(data, entry_markets(daily, quotes), {})
            data.update(summary)
            data["last_market_session"] = target.date().isoformat()
            data["last_updated_at"] = base.now_local().isoformat(timespec="seconds")
            data["last_run_error"] = None
            base.upsert_snapshot(data, summary, target.date().isoformat())
            base.upsert_weekly_review(data, summary)
            base.write_json_atomic(base.DATA_PATH, data)
            print(f"Portfolio 10K opened from synchronized intraday bars: {summary['total_value_pln']:.2f} PLN")
            return
        except Exception as exc:
            save_pending(data, now, str(exc))
            raise

    if data.get("status") != "active":
        save_pending(data, now, "Unsupported portfolio status")
        return

    if mode == "auto" and now.weekday() != 6:
        if migrated:
            base.write_json_atomic(base.DATA_PATH, data)
        print("Portfolio 10K active; no weekly review due today")
        return

    try:
        markets = fetch_daily_markets(data)
        summary = cost.update_current_state(data, markets, {})
        market_date = max(record.market_date for record in markets.values())
        data.update(summary)
        data["last_market_session"] = market_date
        data["last_updated_at"] = base.now_local().isoformat(timespec="seconds")
        data["last_run_error"] = None
        base.upsert_snapshot(data, summary, market_date)
        base.upsert_weekly_review(data, summary)
        base.write_json_atomic(base.DATA_PATH, data)
        print(f"Portfolio 10K reviewed for {market_date}: {summary['total_value_pln']:.2f} PLN")
    except Exception as exc:
        data["last_run_error"] = str(exc)
        data["last_updated_at"] = base.now_local().isoformat(timespec="seconds")
        base.write_json_atomic(base.DATA_PATH, data)
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("auto", "initialize", "review", "migrate"), default="auto")
    args = parser.parse_args()
    run(args.mode)


if __name__ == "__main__":
    main()
