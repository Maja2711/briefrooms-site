#!/usr/bin/env python3
"""Parallel, audited runtime wrapper for US Daily Stock.

PR26 makes the selector position-aware. The expensive daily ranking still runs,
but an already-open US position has portfolio authority: repeated same-symbol
signals become HOLD, and a different stock may replace it only under the explicit
rotation policy in us_daily_stock_position_lifecycle.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time as clock_time
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts import us_daily_stock as us
    from scripts import daily_stock_us_adapter as core_adapter
    from scripts import us_daily_stock_position_lifecycle as lifecycle
except ModuleNotFoundError:
    import us_daily_stock as us
    import daily_stock_us_adapter as core_adapter
    import us_daily_stock_position_lifecycle as lifecycle

# Existing workflow entry points keep working, but the selector now uses the
# shared Daily Stock Core before any runtime objects capture engine functions.
core_adapter.install()

LAST_PREFETCH_AUDIT: dict[str, Any] = {}
_ORIGINAL_FETCH = us.fetch_resilient_bars
RECOVERY_CUTOFF = clock_time(11, 30)
HORIZON_EXIT_TIME = clock_time(15, 55)
BOOK_PATH = us.ROOT / "data/investments/us_daily_stock_position.json"


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
    """Reuse the canonical selector after a missed 09:45 ET run, never after 11:30 ET."""
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


def _canonical_payload(position: Mapping[str, Any]) -> dict[str, Any]:
    source_date = str(position.get("source_history_date") or "")
    payload = us.load_json(us.HISTORY_DIR / f"{source_date}.json")
    if not isinstance(payload, dict) or payload.get("decision") != "TRADE":
        raise us.PublicationError(f"Canonical open US trade {source_date} is missing.")
    return payload


def _position_view(position: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": "OPEN",
        "position_id": position.get("position_id"),
        "opened_at": position.get("opened_at"),
        "source_history_date": position.get("source_history_date"),
        "symbol": position.get("symbol"),
        "entry": position.get("entry"),
        "stop": position.get("stop"),
        "target": position.get("target"),
        "mark": position.get("last_mark"),
        "unrealized_percent": position.get("unrealized_percent"),
        "current_r": position.get("current_r"),
    }


def _closed_today_payload(now: datetime, config: dict[str, Any], closure: Mapping[str, Any]) -> dict[str, Any]:
    payload = us.base_payload(now, config, "NO_TRADE", "US position closed in this session; no same-session re-entry after TP/SL/horizon exit.")
    payload["locked"] = True
    payload["position_action"] = "CLOSED"
    payload["last_position"] = dict(closure)
    payload["metrics"] = us.metric_summary()
    return payload


def generate(now: datetime | None = None) -> dict[str, Any]:
    now = now or us.now_ny()
    config = us.load_config()

    # Position state is authoritative across sessions and is bootstrapped once
    # from legacy history. This migration collapses the 19/20/21 Aug MRK overlap
    # into the earliest actually activated paper position without rewriting raw
    # signal history.
    book = lifecycle.load_or_bootstrap(BOOK_PATH, us.HISTORY_DIR, now=now)
    open_position = book.get("open_position")
    current_snapshot: dict[str, Any] | None = None
    if isinstance(open_position, Mapping) and us.is_session_day(now.date(), config):
        try:
            current_snapshot = us.opening_snapshot(str(open_position["symbol"]), now=now)
            book, closure = lifecycle.reconcile_open_position(
                book,
                current_snapshot,
                now=now,
                horizon_exit_allowed=now.time() >= HORIZON_EXIT_TIME,
            )
            if closure:
                lifecycle.apply_closure_to_history(us.HISTORY_DIR, closure)
                lifecycle.save_book(BOOK_PATH, book, now=now)
                return _closed_today_payload(now, config, closure)
            lifecycle.save_book(BOOK_PATH, book, now=now)
        except Exception as exc:
            # Never invent an exit if the current-session mark is unavailable.
            payload = us.base_payload(now, config, "DATA_ERROR", f"Open US position could not be marked: {type(exc).__name__}.")
            payload["locked"] = False
            payload["position_action"] = "HOLD_MARK_ERROR"
            payload["position"] = _position_view(open_position)
            return payload

    if not us.is_session_day(now.date(), config) or now.time() < us.parse_clock(config["analysis_not_before"]):
        if isinstance(book.get("open_position"), Mapping):
            canonical = _canonical_payload(book["open_position"])
            snapshot = current_snapshot or (canonical.get("selection") or {}).get("market_snapshot") or {}
            return lifecycle.hold_payload(
                canonical,
                book,
                now=now,
                current_snapshot=snapshot,
                reason="Existing US position remains open outside the new-entry analysis window.",
            )
        return us.generate(now)

    # Same-day lock applies only when there is no open portfolio position. With
    # an open position we still need to transform a repeated daily signal into
    # HOLD and evaluate rotation opportunity.
    if book.get("open_position") is None:
        current = us.load_json(us.PUBLIC_PATH)
        if isinstance(current, dict) and current.get("date") == now.date().isoformat() and current.get("locked") and current.get("decision") in {"TRADE", "NO_TRADE"}:
            if current.get("decision") == "TRADE" and lifecycle.activated_at_selection(current):
                book = lifecycle.open_from_payload(book, current)
                lifecycle.save_book(BOOK_PATH, book, now=now)
                current["position_action"] = "OPEN"
                current["position"] = _position_view(book["open_position"])
            return current

    cache = prefetch(config)
    install_cache(cache)
    candidate = _generate_with_recovery(now, config)
    candidate.setdefault("data_quality", {})["prefetch"] = LAST_PREFETCH_AUDIT

    open_position = book.get("open_position")
    if isinstance(open_position, Mapping):
        rotate, reason = lifecycle.should_rotate(open_position, candidate)
        if rotate and candidate.get("decision") == "TRADE":
            book, old_closure = lifecycle.close_for_rotation(book, now=now)
            lifecycle.apply_closure_to_history(us.HISTORY_DIR, old_closure)
            if not lifecycle.activated_at_selection(candidate):
                # Defensive: a rotation candidate must itself be executable in
                # its published entry zone; otherwise keep the portfolio flat.
                lifecycle.save_book(BOOK_PATH, book, now=now)
                return _closed_today_payload(now, config, old_closure)
            book = lifecycle.open_from_payload(book, candidate)
            lifecycle.save_book(BOOK_PATH, book, now=now)
            candidate["position_action"] = "ROTATE_OPEN"
            candidate["rotation"] = {
                "reason": reason,
                "closed_position": old_closure,
                "new_position_id": book["open_position"].get("position_id"),
            }
            candidate["position"] = _position_view(book["open_position"])
            return candidate

        if candidate.get("decision") == "TRADE":
            book = lifecycle.record_suppressed_signal(book, candidate, reason=reason)
        lifecycle.save_book(BOOK_PATH, book, now=now)
        canonical = _canonical_payload(book["open_position"])
        snapshot = current_snapshot or (canonical.get("selection") or {}).get("market_snapshot") or {}
        hold_reason = (
            "Repeated signal for the held stock was suppressed; the original position remains open."
            if reason == "same_symbol_hold"
            else f"Open US position retained; replacement candidate rejected by rotation policy ({reason})."
        )
        hold = lifecycle.hold_payload(
            canonical,
            book,
            now=now,
            current_snapshot=snapshot,
            candidate_watch=candidate,
            reason=hold_reason,
        )
        hold["data_quality"] = candidate.get("data_quality") or {}
        hold["metrics"] = us.metric_summary()
        return hold

    if candidate.get("decision") == "TRADE" and lifecycle.activated_at_selection(candidate):
        book = lifecycle.open_from_payload(book, candidate)
        lifecycle.save_book(BOOK_PATH, book, now=now)
        candidate["position_action"] = "OPEN"
        candidate["position"] = _position_view(book["open_position"])
    return candidate


def publish(payload: dict[str, Any], *, now: datetime) -> None:
    """Publish public state but never create a second history row for HOLD."""
    us.validate_payload(payload, now=now)
    us.atomic_json(us.PUBLIC_PATH, payload)
    us.atomic_json(us.METRICS_PATH, payload.get("metrics") or {})
    action = str(payload.get("position_action") or "")
    if payload.get("decision") in {"TRADE", "NO_TRADE"} and action != "HOLD":
        us.atomic_json(us.HISTORY_DIR / f"{payload['date']}.json", payload)


def _history_row(payload: Mapping[str, Any]) -> dict[str, Any]:
    selection = payload.get("selection") or {}
    return {
        "market": "us",
        "date": payload.get("date"),
        "generated_at": payload.get("generated_at"),
        "ticker": selection.get("ticker") or selection.get("symbol"),
        "symbol": selection.get("symbol"),
        "name": selection.get("name"),
        "sector": selection.get("sector"),
        "score": selection.get("score"),
        "entry_zone": selection.get("entry_zone"),
        "stop": selection.get("stop"),
        "target": selection.get("target"),
        "valid_until": selection.get("valid_until"),
        "outcome": payload.get("outcome") or {},
    }


def build_history_index() -> dict[str, Any]:
    canonical, suppressed = lifecycle.canonical_history_payloads(us.HISTORY_DIR)
    trades = [_history_row(payload) for payload in reversed(canonical)]
    index = {
        "schema_version": "us-daily-stock-history-index-v2",
        "updated_at": us.now_ny().isoformat(timespec="seconds"),
        "selected_trades": len(trades),
        "resolved_trades": sum(1 for row in trades if (row.get("outcome") or {}).get("status") == "RESOLVED"),
        "suppressed_overlapping_signals": len(suppressed),
        "suppressed_signals": suppressed,
        "trades": trades,
    }
    us.atomic_json(us.HISTORY_DIR / "index.json", index)
    return index


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("auto", "validate"), default="auto")
    args = parser.parse_args()
    now = us.now_ny()
    if args.mode == "validate":
        us.validate_payload(us.load_json(us.PUBLIC_PATH), require_today=True, now=now)
        book = lifecycle.load_or_bootstrap(BOOK_PATH, us.HISTORY_DIR, now=now)
        if book.get("schema_version") != lifecycle.BOOK_SCHEMA:
            raise SystemExit("invalid US position book")
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
    publish(payload, now=now)
    build_history_index()
    us.validate_payload(us.load_json(us.PUBLIC_PATH), require_today=True, now=now)
    print(f"Published {payload['decision']} / {payload.get('position_action') or 'DAILY'} for {payload['date']} ({now:%H:%M} ET).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
