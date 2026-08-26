#!/usr/bin/env python3
"""Cheap intraday TP/SL/mark monitor for the one-position US Daily Stock book.

Unlike the daily selector this monitor never calls Gemini or re-ranks the US
universe. It only marks the already-open stock, resolves TP/SL/horizon, updates
the canonical originating trade and refreshes the public HOLD card/history.
"""
from __future__ import annotations

from datetime import time as clock_time, timezone
from typing import Mapping

try:
    from scripts import build_daily_stock_history_index as shared_history
    from scripts import daily_stock_trade_timestamp_normalizer as timestamp_normalizer
    from scripts import us_daily_stock as us
    from scripts import us_daily_stock_position_lifecycle as lifecycle
    from scripts import us_daily_stock_runtime as runtime
except ModuleNotFoundError:
    import build_daily_stock_history_index as shared_history
    import daily_stock_trade_timestamp_normalizer as timestamp_normalizer
    import us_daily_stock as us
    import us_daily_stock_position_lifecycle as lifecycle
    import us_daily_stock_runtime as runtime

SESSION_START = clock_time(9, 30)
SESSION_END = clock_time(16, 2)


def _refresh_indexes(now) -> None:
    # Normalize canonical source history first so both dedicated and shared
    # indexes expose the same trustworthy entry/exit timestamp contract.
    timestamp_normalizer.normalize_us_history(us.HISTORY_DIR, runtime.BOOK_PATH)
    runtime.build_history_index()
    payload = shared_history.build(now.astimezone(timezone.utc))
    shared_history.atomic(shared_history.OUT, payload)


def run(now=None) -> str:
    now = now or us.now_ny()
    config = us.load_config()
    book = lifecycle.load_or_bootstrap(runtime.BOOK_PATH, us.HISTORY_DIR, now=now)
    position = book.get("open_position")

    # Migration/index refresh is still useful even if there is no live position.
    if not isinstance(position, Mapping):
        _refresh_indexes(now)
        return "NO_OPEN_POSITION"
    if not us.is_session_day(now.date(), config) or not (SESSION_START <= now.time() <= SESSION_END):
        _refresh_indexes(now)
        return "OUTSIDE_US_SESSION"

    snapshot = runtime.position_snapshot(
        str(position["symbol"]),
        opened_at=str(position.get("opened_at") or ""),
        now=now,
    )
    book, closure = lifecycle.reconcile_open_position(
        book,
        snapshot,
        now=now,
        horizon_exit_allowed=now.time() >= runtime.HORIZON_EXIT_TIME,
    )
    lifecycle.save_book(runtime.BOOK_PATH, book, now=now)

    if closure:
        lifecycle.apply_closure_to_history(us.HISTORY_DIR, closure)
        public = runtime._closed_today_payload(now, config, closure)
        # A closure is state, not a new daily selection: never overwrite a raw
        # same-day signal file (including legacy SUPPRESSED audit records).
        us.validate_payload(public, now=now)
        us.atomic_json(us.PUBLIC_PATH, public)
        us.atomic_json(us.METRICS_PATH, public.get("metrics") or {})
        _refresh_indexes(now)
        return f"CLOSED_{str(closure.get('exit_reason') or 'UNKNOWN').upper()}"

    hold = runtime._hold_from_book(
        book,
        now=now,
        snapshot=snapshot,
        reason="Existing US position remains open; intraday monitor refreshed mark and TP/SL state.",
    )
    hold["metrics"] = us.metric_summary()
    us.validate_payload(hold, now=now)
    us.atomic_json(us.PUBLIC_PATH, hold)
    us.atomic_json(us.METRICS_PATH, hold.get("metrics") or {})
    _refresh_indexes(now)
    return "HOLD_UPDATED"


def main() -> int:
    result = run()
    print("US_DAILY_STOCK_POSITION_MONITOR", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
