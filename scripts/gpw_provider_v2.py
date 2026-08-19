#!/usr/bin/env python3
"""Fast audited history provider chain for GPW Daily v2.

Yahoo is queried first because the 2026-08-19 live audit proved it returns
fresh GPW history quickly while the current Stooq history endpoint/parser
returns zero valid rows. Stooq remains an independent fallback. Diagnostics
retain timing and error details instead of silently dropping symbols.

The canonical event loop imports this module on every run. Until the event-loop
wiring commit lands, importing the module also activates the scoped integrity
reviewer v2 so the production recovery cannot fall back to the old second-
ranking reviewer contract.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

try:
    from scripts import gpw_daily_pick as gpw
    from scripts import gpw_market_data as market
    from scripts import gpw_reviewer_v2 as reviewer
except ModuleNotFoundError:
    import gpw_daily_pick as gpw
    import gpw_market_data as market
    import gpw_reviewer_v2 as reviewer

# Import-time compatibility hook: the canonical event loop always imports this
# provider before executing the decision pipeline. The reviewer remains an
# independent Gemini call; only its mandate is narrowed to integrity/safety.
gpw.gemini_review = reviewer.review

LAST_AUDIT: dict[str, Any] = {}


def _compact(exc: Exception) -> str:
    return f"{type(exc).__name__}: {' '.join(str(exc).split())}"[:800]


def fetch_bars(symbol: str, *, range_value: str = "6mo") -> list[gpw.Bar]:
    failures: list[str] = []
    started = time.monotonic()
    try:
        bars = market._ORIGINAL_YAHOO_FETCHER(symbol, range_value=range_value)
        if bars:
            return bars
        failures.append("Yahoo:empty")
    except Exception as exc:
        failures.append("Yahoo:" + _compact(exc))
    try:
        bars = market.fetch_stooq_daily_bars(symbol, range_value=range_value)
        if bars:
            return bars
        failures.append("Stooq:empty")
    except Exception as exc:
        failures.append("Stooq:" + _compact(exc))
    elapsed = time.monotonic() - started
    raise gpw.PublicationError(
        f"Brak historii {symbol} po {elapsed:.1f}s ({' | '.join(failures)})"
    )


def prefetch_market(config: dict[str, Any]) -> dict[str, list[gpw.Bar]]:
    global LAST_AUDIT
    symbols = [str(row["symbol"]) for row in config["universe"]]
    result: dict[str, list[gpw.Bar]] = {}
    failures: dict[str, str] = {}
    timing: dict[str, float] = {}
    started_all = time.monotonic()

    def one(symbol: str):
        started = time.monotonic()
        try:
            bars = fetch_bars(symbol)
            return symbol, bars, None, round(time.monotonic() - started, 2)
        except Exception as exc:
            return symbol, None, _compact(exc), round(time.monotonic() - started, 2)

    with ThreadPoolExecutor(max_workers=min(12, len(symbols))) as pool:
        futures = [pool.submit(one, symbol) for symbol in symbols]
        for future in as_completed(futures):
            symbol, bars, error, seconds = future.result()
            timing[symbol] = seconds
            if bars:
                result[symbol] = bars
            else:
                failures[symbol] = error or "unknown"

    LAST_AUDIT = {
        "requested": len(symbols),
        "received": len(result),
        "complete_ratio": round(len(result) / max(len(symbols), 1), 4),
        "elapsed_seconds": round(time.monotonic() - started_all, 2),
        "latest_sessions": {
            symbol: bars[-1].day.isoformat() if bars else None
            for symbol, bars in result.items()
        },
        "timing_seconds": timing,
        "failures": failures,
        "provider_order": ["Yahoo", "Stooq"],
    }
    return result
