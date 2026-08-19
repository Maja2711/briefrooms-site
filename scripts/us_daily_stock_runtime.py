#!/usr/bin/env python3
"""Parallel, audited runtime wrapper for US Daily Stock."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

try:
    from scripts import us_daily_stock as us
except ModuleNotFoundError:
    import us_daily_stock as us

LAST_PREFETCH_AUDIT: dict[str, Any] = {}
_ORIGINAL_FETCH = us.fetch_resilient_bars


def prefetch(config: dict[str, Any]) -> dict[str, tuple[list[us.Bar], dict[str, Any]]]:
    global LAST_PREFETCH_AUDIT
    symbols = [str(row["symbol"]) for row in config["universe"]]
    cache: dict[str, tuple[list[us.Bar], dict[str, Any]]] = {}
    failures: dict[str, str] = {}
    provider_usage: dict[str, str] = {}

    def one(symbol: str):
        try:
            bars, meta = _ORIGINAL_FETCH(symbol)
            return symbol, bars, meta, None
        except Exception as exc:
            return symbol, None, None, f"{type(exc).__name__}: {str(exc)[:320]}"

    with ThreadPoolExecutor(max_workers=min(12, len(symbols))) as pool:
        futures = [pool.submit(one, symbol) for symbol in symbols]
        for future in as_completed(futures):
            symbol, bars, meta, error = future.result()
            if bars and meta:
                cache[symbol] = (bars, meta)
                provider_usage[symbol] = str(meta.get("provider") or "unknown")
            else:
                failures[symbol] = error or "unknown"

    LAST_PREFETCH_AUDIT = {
        "requested": len(symbols),
        "received": len(cache),
        "complete_ratio": round(len(cache) / max(len(symbols), 1), 4),
        "provider_usage": provider_usage,
        "failures": failures,
        "latest_sessions": {
            symbol: bars[-1].day.isoformat() if bars else None
            for symbol, (bars, _meta) in cache.items()
        },
    }
    return cache


def install_cache(cache: dict[str, tuple[list[us.Bar], dict[str, Any]]]) -> None:
    def cached(symbol: str, *, range_value: str = "6mo"):
        if range_value != "6mo":
            return _ORIGINAL_FETCH(symbol, range_value=range_value)
        if symbol not in cache:
            raise us.PublicationError(f"No prefetched US market history for {symbol}.")
        return cache[symbol]
    us.fetch_resilient_bars = cached


def generate(now: datetime | None = None) -> dict[str, Any]:
    now = now or us.now_ny()
    config = us.load_config()
    if not us.is_session_day(now.date(), config) or now.time() < us.parse_clock(config["analysis_not_before"]):
        return us.generate(now)
    current = us.load_json(us.PUBLIC_PATH)
    if isinstance(current, dict) and current.get("date") == now.date().isoformat() and current.get("locked") and current.get("decision") in {"TRADE", "NO_TRADE"}:
        return current
    cache = prefetch(config)
    install_cache(cache)
    payload = us.generate(now)
    payload.setdefault("data_quality", {})["prefetch"] = LAST_PREFETCH_AUDIT
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("auto", "validate"), default="auto")
    args = parser.parse_args()
    now = us.now_ny()
    if args.mode == "validate":
        us.validate_payload(us.load_json(us.PUBLIC_PATH), require_today=True, now=now)
        print(f"OK: {us.PUBLIC_PATH.relative_to(us.ROOT)}")
        return 0
    try:
        payload = generate(now)
    except Exception as exc:
        config = us.load_config()
        payload = us.base_payload(now, config, "DATA_ERROR", f"US Daily Stock runtime stopped: {type(exc).__name__}.")
        payload["data_quality"] = {
            "status": "failed", "failed_stage": "runtime", "error": str(exc)[:500], "prefetch": LAST_PREFETCH_AUDIT,
        }
        print(f"Fail-closed runtime: {exc}")
    us.publish(payload)
    us.validate_payload(us.load_json(us.PUBLIC_PATH), require_today=True, now=now)
    print(f"Published {payload['decision']} for {payload['date']} ({now:%H:%M} ET).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
