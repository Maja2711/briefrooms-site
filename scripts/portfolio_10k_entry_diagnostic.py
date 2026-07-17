#!/usr/bin/env python3
"""Reconstruct and test the 10K model-entry data without opening positions.

The diagnostic reads the declared 14:40 UTC entry bar, checks every portfolio and
FX symbol, verifies timestamp synchronisation, and simulates initialization on a
deep copy. It never modifies portfolio_10k.json.
"""
from __future__ import annotations

import argparse
import copy
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

import portfolio_10k_weekly as base
import portfolio_10k_weekly_v3 as execution

REPORT_PATH = Path("data/investments/portfolio_10k_entry_diagnostic.json")


def inspect_symbol(symbol: str, target: datetime) -> dict[str, Any]:
    result: dict[str, Any] = {"symbol": symbol, "status": "error", "bars": []}
    try:
        frame = yf.Ticker(symbol).history(
            period="5d", interval="5m", auto_adjust=False, actions=False, prepost=False
        )
        if frame is None or frame.empty or "Close" not in frame:
            raise RuntimeError("no 5-minute history returned")
        index = pd.DatetimeIndex(pd.to_datetime(frame.index))
        index = index.tz_localize(timezone.utc) if index.tz is None else index.tz_convert(timezone.utc)
        clean = frame.copy()
        clean.index = index
        clean = clean[clean["Close"].notna()]
        start = target
        end = target + timedelta(minutes=execution.ENTRY_BAR_MAX_DELAY_MINUTES)
        eligible = clean[(clean.index >= start) & (clean.index <= end)]
        result["returned_rows"] = int(len(clean))
        result["eligible_rows"] = int(len(eligible))
        result["bars"] = [
            {
                "timestamp_utc": ts.to_pydatetime().astimezone(timezone.utc).isoformat(timespec="minutes"),
                "close": round(float(row["Close"]), 8),
            }
            for ts, row in eligible.iterrows()
        ]
        if eligible.empty:
            raise RuntimeError(f"no completed bar from {start.isoformat()} to {end.isoformat()}")
        first_ts = eligible.index[0].to_pydatetime().astimezone(timezone.utc)
        first_price = float(eligible.iloc[0]["Close"])
        result.update(
            status="ok",
            first_timestamp_utc=first_ts.isoformat(timespec="minutes"),
            first_price=round(first_price, 8),
        )
    except Exception as exc:  # diagnostic must always publish its findings
        result["error"] = str(exc)
    return result


def next_common_window(after: datetime) -> dict[str, str]:
    current = after.astimezone(timezone.utc)
    candidate = current.date()
    if current.time() > execution.ENTRY_WINDOW_END_UTC:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    target = datetime.combine(candidate, execution.ENTRY_BAR_UTC, tzinfo=timezone.utc)
    first_attempt = target + timedelta(minutes=10)
    return {
        "target_bar_utc": target.isoformat(timespec="minutes"),
        "first_attempt_utc": first_attempt.isoformat(timespec="minutes"),
        "first_attempt_europe_warsaw": first_attempt.astimezone().isoformat(timespec="minutes"),
    }


def build_report(target_day: date) -> dict[str, Any]:
    data = base.load_json(base.DATA_PATH)
    target = datetime.combine(target_day, execution.ENTRY_BAR_UTC, tzinfo=timezone.utc)

    instruments = [
        {
            "id": p["id"],
            "kind": "instrument",
            "symbol": str(p["market_symbol"]),
            "currency": str(p["currency"]),
        }
        for p in data.get("positions", [])
    ]
    currencies = sorted({str(p.get("currency")) for p in data.get("positions", []) if p.get("currency") != "PLN"})
    fx_items = [
        {
            "id": currency,
            "kind": "fx",
            "symbol": str(base.FX_SYMBOLS[currency]),
            "currency": currency,
        }
        for currency in currencies
    ]

    checked: list[dict[str, Any]] = []
    for item in instruments + fx_items:
        item_result = inspect_symbol(item["symbol"], target)
        checked.append({**item, **item_result})

    failures = [item for item in checked if item["status"] != "ok"]
    timestamps = [
        datetime.fromisoformat(item["first_timestamp_utc"])
        for item in checked
        if item["status"] == "ok"
    ]
    sync_spread_minutes = None
    synchronized = False
    if timestamps and len(timestamps) == len(checked):
        sync_spread_minutes = (max(timestamps) - min(timestamps)).total_seconds() / 60.0
        synchronized = sync_spread_minutes <= 5.0

    simulation: dict[str, Any] = {"status": "not_run"}
    if not failures and synchronized:
        try:
            cloned = copy.deepcopy(data)
            quote_map = {
                item["id"]: execution.IntradayQuote(
                    symbol=item["symbol"],
                    price=float(item["first_price"]),
                    timestamp_utc=datetime.fromisoformat(item["first_timestamp_utc"]),
                )
                for item in checked
                if item["kind"] == "instrument"
            }
            fx_map = {
                item["currency"]: execution.IntradayQuote(
                    symbol=item["symbol"],
                    price=float(item["first_price"]),
                    timestamp_utc=datetime.fromisoformat(item["first_timestamp_utc"]),
                )
                for item in checked
                if item["kind"] == "fx"
            }
            execution.initialize_from_intraday(cloned, quote_map, fx_map, target)
            simulation = {
                "status": "pass",
                "portfolio_status": cloned.get("status"),
                "entry_timestamp_utc": cloned.get("model_entry_timestamp_utc"),
                "positions_active": sum(p.get("status") == "active" for p in cloned.get("positions", [])),
                "cash_pln": cloned.get("cash_pln"),
                "entries": [
                    {
                        "symbol": p.get("broker_symbol"),
                        "entry_price": p.get("entry_price"),
                        "entry_fx_to_pln": p.get("entry_fx_to_pln"),
                        "quantity": p.get("quantity"),
                        "entry_value_pln": p.get("entry_value_pln"),
                    }
                    for p in cloned.get("positions", [])
                ],
            }
        except Exception as exc:
            simulation = {"status": "fail", "error": str(exc)}

    now = datetime.now(timezone.utc)
    return {
        "schema_version": "1.0.0",
        "generated_at_utc": now.isoformat(timespec="seconds"),
        "diagnostic_target_utc": target.isoformat(timespec="minutes"),
        "portfolio_status_at_test": data.get("status"),
        "declared_entry_rule": {
            "entry_bar_utc": execution.ENTRY_BAR_UTC.isoformat(timespec="minutes"),
            "window_end_utc": execution.ENTRY_WINDOW_END_UTC.isoformat(timespec="minutes"),
            "bar_max_delay_minutes": execution.ENTRY_BAR_MAX_DELAY_MINUTES,
            "all_or_nothing": True,
            "max_timestamp_spread_minutes": 5,
        },
        "checks": checked,
        "summary": {
            "items_checked": len(checked),
            "items_ok": len(checked) - len(failures),
            "items_failed": len(failures),
            "failed_symbols": [item["symbol"] for item in failures],
            "all_quotes_available": not failures,
            "sync_spread_minutes": sync_spread_minutes,
            "timestamps_synchronized": synchronized,
            "historical_entry_would_have_passed": not failures and synchronized and simulation.get("status") == "pass",
        },
        "simulation": simulation,
        "next_declared_window": next_common_window(now),
        "interpretation_pl": (
            "Raport odtwarza dostępność danych, a nie tworzy transakcji wstecznej. "
            "Jeżeli wszystkie dane są dostępne teraz, nie dowodzi to, że Yahoo zwróciło je bez opóźnienia podczas pierwotnego przebiegu."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="UTC session date in YYYY-MM-DD")
    parser.add_argument("--output", default=str(REPORT_PATH))
    args = parser.parse_args()
    target_day = date.fromisoformat(args.date)
    report = build_report(target_day)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
