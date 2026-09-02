#!/usr/bin/env python3
"""Intraday ledger monitor for the single-position US Daily Stock book.

The monitor never re-ranks the universe. It follows the already-open position,
uses the ordered 1-minute path to determine whether TP or SL was touched first,
marks the position, applies the end-of-week time stop, updates canonical history
and refreshes the public card/history.
"""
from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, time as clock_time, timezone
from typing import Any, Mapping

try:
    from scripts import build_daily_stock_history_index as shared_history
    from scripts import us_daily_stock as us
    from scripts import us_daily_stock_integrity as integrity
    from scripts import us_daily_stock_position_lifecycle as lifecycle
    from scripts import us_daily_stock_runtime as runtime
except ModuleNotFoundError:
    import build_daily_stock_history_index as shared_history
    import us_daily_stock as us
    import us_daily_stock_integrity as integrity
    import us_daily_stock_position_lifecycle as lifecycle
    import us_daily_stock_runtime as runtime

SESSION_START = clock_time(9, 30)
SESSION_END = clock_time(16, 2)


def _refresh_indexes(now: datetime) -> None:
    runtime.build_history_index()
    payload = shared_history.build(now.astimezone(timezone.utc))
    shared_history.atomic(shared_history.OUT, payload)


def _opened_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=us.NEW_YORK)
    return parsed.astimezone(us.NEW_YORK)


def position_path(symbol: str, *, opened_at: str | None, now: datetime) -> list[dict[str, Any]]:
    """Return ordered regular-session 1m bars occurring after entry."""
    opened = _opened_at(opened_at)
    params = urllib.parse.urlencode({
        "range": "1d",
        "interval": "1m",
        "events": "history",
        "includePrePost": "false",
    })
    encoded = urllib.parse.quote(symbol, safe="")
    failures: list[str] = []
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            payload = json.loads(us.request_bytes(f"https://{host}/v8/finance/chart/{encoded}?{params}"))
            result = (payload.get("chart", {}).get("result") or [None])[0]
            if not result:
                raise ValueError("empty chart")
            stamps = result.get("timestamp") or []
            quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
            rows: list[dict[str, Any]] = []
            for i, stamp in enumerate(stamps):
                values = {
                    key: (quote.get(key) or [None] * len(stamps))[i]
                    for key in ("open", "high", "low", "close", "volume")
                }
                if any(values[key] is None for key in ("open", "high", "low", "close")):
                    continue
                at = datetime.fromtimestamp(int(stamp), us.NEW_YORK)
                if at.date() != now.date() or not (SESSION_START <= at.time() <= clock_time(16, 0)):
                    continue
                if opened is not None and opened.date() == now.date() and at <= opened:
                    continue
                rows.append({
                    "at": at,
                    "open": float(values["open"]),
                    "high": float(values["high"]),
                    "low": float(values["low"]),
                    "close": float(values["close"]),
                    "volume": int(values["volume"] or 0),
                })
            rows.sort(key=lambda row: row["at"])
            if not rows:
                raise ValueError("no post-entry regular-session 1m bars")
            return rows
        except Exception as exc:
            failures.append(f"{host}:{type(exc).__name__}")
    raise us.PublicationError(f"US 1m position path unavailable for {symbol}: {' | '.join(failures)}")


def snapshot_from_path(symbol: str, path: list[dict[str, Any]], *, now: datetime) -> dict[str, Any]:
    first, last = path[0], path[-1]
    return {
        "provider": "Yahoo",
        "symbol": symbol,
        "date": now.date().isoformat(),
        "observed_at": last["at"].isoformat(timespec="seconds"),
        "path_start_at": first["at"].isoformat(timespec="seconds"),
        "open": us.round2(first["open"]),
        "high": us.round2(max(row["high"] for row in path)),
        "low": us.round2(min(row["low"] for row in path)),
        "last": us.round2(last["close"]),
        "volume": sum(max(int(row["volume"]), 0) for row in path),
        "status": "ordered_1m_path",
        "bars": len(path),
    }


def first_touch(position: Mapping[str, Any], path: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Resolve first TP/SL touch by minute; same-minute ambiguity is STOP-conservative."""
    stop = float(position["stop"])
    target = float(position["target"])
    for row in path:
        hit_stop = float(row["low"]) <= stop
        hit_target = float(row["high"]) >= target
        if hit_stop and hit_target:
            return {"reason": "stop", "price": stop, "same_bar": True, "bar": row}
        if hit_stop:
            return {"reason": "stop", "price": stop, "same_bar": False, "bar": row}
        if hit_target:
            return {"reason": "target", "price": target, "same_bar": False, "bar": row}
    return None


def _close_from_touch(
    book: Mapping[str, Any],
    position: Mapping[str, Any],
    touch: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    stop = float(position["stop"])
    target = float(position["target"])
    entry = float(position["entry"])
    reason = str(touch["reason"])
    same_bar = bool(touch.get("same_bar"))
    at = touch["bar"]["at"]
    if same_bar:
        synthetic = {"high": target, "low": stop, "last": stop}
    elif reason == "target":
        synthetic = {"high": target, "low": (entry + stop) / 2.0, "last": target}
    else:
        synthetic = {"high": (entry + target) / 2.0, "low": stop, "last": stop}
    updated, closure = lifecycle.reconcile_open_position(
        book,
        synthetic,
        now=at,
        horizon_exit_allowed=False,
    )
    if not closure:
        raise us.PublicationError("First-touch resolver failed to close the US position.")
    closure["exit_bar_at"] = at.isoformat(timespec="seconds")
    closure["resolution"] = "ordered_1m_first_touch"
    closure["same_minute_tp_sl"] = same_bar
    if updated.get("closed_positions"):
        updated["closed_positions"][-1] = dict(closure)
    return updated, closure


def run(now: datetime | None = None) -> str:
    now = now or us.now_ny()
    config = us.load_config()

    # Repair canonical OPEN state and weekly deadline before every monitoring pass.
    integrity.repair(now=now)
    book = lifecycle.load_or_bootstrap(runtime.BOOK_PATH, us.HISTORY_DIR, now=now)
    position = book.get("open_position")

    if not isinstance(position, Mapping):
        _refresh_indexes(now)
        return "NO_OPEN_POSITION"
    if not us.is_session_day(now.date(), config) or not (SESSION_START <= now.time() <= SESSION_END):
        _refresh_indexes(now)
        return "OUTSIDE_US_SESSION"

    path = position_path(
        str(position["symbol"]),
        opened_at=str(position.get("opened_at") or ""),
        now=now,
    )
    snapshot = snapshot_from_path(str(position["symbol"]), path, now=now)
    touch = first_touch(position, path)

    if touch:
        book, closure = _close_from_touch(book, position, touch)
    else:
        book, closure = lifecycle.reconcile_open_position(
            book,
            snapshot,
            now=now,
            horizon_exit_allowed=now.time() >= integrity.WEEK_END_EXIT_TIME,
        )
        if closure:
            closure["resolution"] = "end_of_trading_week_time_stop"
            closure["exit_bar_at"] = snapshot["observed_at"]
            if book.get("closed_positions"):
                book["closed_positions"][-1] = dict(closure)

    lifecycle.save_book(runtime.BOOK_PATH, book, now=now)

    if closure:
        lifecycle.apply_closure_to_history(us.HISTORY_DIR, closure)
        public = runtime._closed_today_payload(now, config, closure)
        public["last_position"] = dict(closure)
        public["reason"] = (
            "US position closed at the first 1-minute TP/SL touch."
            if closure.get("exit_reason") in {"target", "stop"}
            else "US position closed at the end-of-trading-week time stop."
        )
        us.validate_payload(public, now=now)
        us.atomic_json(us.PUBLIC_PATH, public)
        us.atomic_json(us.METRICS_PATH, public.get("metrics") or {})
        integrity.repair(now=now)
        _refresh_indexes(now)
        return f"CLOSED_{str(closure.get('exit_reason') or 'UNKNOWN').upper()}"

    hold = runtime._hold_from_book(
        book,
        now=now,
        snapshot=snapshot,
        reason="Existing US position remains open; ordered 1-minute path refreshed mark and TP/SL state.",
    )
    hold["metrics"] = us.metric_summary()
    hold["outcome"] = {
        "status": "OPEN",
        "activated": True,
        "activated_at": book["open_position"].get("opened_at"),
        "entry_price": book["open_position"].get("entry"),
        "position_id": book["open_position"].get("position_id"),
    }
    us.validate_payload(hold, now=now)
    us.atomic_json(us.PUBLIC_PATH, hold)
    us.atomic_json(us.METRICS_PATH, hold.get("metrics") or {})
    integrity.repair(now=now)
    _refresh_indexes(now)
    return "HOLD_UPDATED"


def main() -> int:
    result = run()
    print("US_DAILY_STOCK_POSITION_MONITOR", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
