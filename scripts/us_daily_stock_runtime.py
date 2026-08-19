#!/usr/bin/env python3
"""Parallel, audited runtime wrapper for US Daily Stock."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time as clock_time
from typing import Any

try:
    from scripts import us_daily_stock as us
except ModuleNotFoundError:
    import us_daily_stock as us

LAST_PREFETCH_AUDIT: dict[str, Any] = {}
_ORIGINAL_FETCH = us.fetch_resilient_bars
RECOVERY_CUTOFF = clock_time(11, 30)


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


def _generate_with_recovery(now: datetime, config: dict[str, Any]) -> dict[str, Any]:
    """Reuse the canonical selector after a missed 09:45 ET run, never after 11:30 ET.

    The only temporary change is the operational publication cutoff. Ranking,
    source gates, Gemini analysis/review, risk controls and the fresh intraday
    execution snapshot remain the canonical ``us_daily_stock.generate`` path.
    """
    normal_cutoff = us.parse_clock(config["publication_cutoff"])
    if now.time() < normal_cutoff:
        return us.generate(now)
    if now.time() >= RECOVERY_CUTOFF:
        return us.generate(now)

    effective = dict(config)
    effective["publication_cutoff"] = RECOVERY_CUTOFF.strftime("%H:%M")
    original_load_config = us.load_config
    us.load_config = lambda: effective
    try:
        payload = us.generate(now)
    finally:
        us.load_config = original_load_config

    quality = payload.setdefault("data_quality", {})
    quality["late_recovery"] = True
    quality["normal_publication_cutoff"] = config["publication_cutoff"]
    quality["recovery_cutoff"] = RECOVERY_CUTOFF.strftime("%H:%M")
    quality["recovery_policy"] = "same_selector_same_hard_gates_fresh_intraday_snapshot"
    if payload.get("decision") == "TRADE":
        payload["reason"] = "Late operational recovery after the normal publication window. " + str(payload.get("reason") or "")
    return payload


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
    payload = _generate_with_recovery(now, config)
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
