#!/usr/bin/env python3
"""Durable ledger repair/verification for BriefRooms US Daily Stock.

Contract:
- once an activated trade is OPEN it remains visible and canonical until closed,
- canonical history and position book must agree on the same position id/symbol,
- open positions are held through the final regular US session of the selection week,
- closed positions are always reflected as RESOLVED in canonical history,
- pre-market/overnight refreshes never turn a valid OPEN position into a data error.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts import us_daily_stock as us
    from scripts import us_daily_stock_position_lifecycle as lifecycle
    from scripts import us_daily_stock_runtime as runtime
except ModuleNotFoundError:
    import us_daily_stock as us
    import us_daily_stock_position_lifecycle as lifecycle
    import us_daily_stock_runtime as runtime

SESSION_START = clock_time(9, 30)
SESSION_END = clock_time(16, 2)
WEEK_END_EXIT_TIME = clock_time(15, 55)


def final_session_of_week(day: date, config: dict[str, Any]) -> date:
    """Return the last regular US session in the ISO week containing *day*."""
    friday = day + timedelta(days=4 - day.weekday())
    cursor = friday
    while cursor >= day - timedelta(days=4):
        if us.is_session_day(cursor, config):
            return cursor
        cursor -= timedelta(days=1)
    raise us.PublicationError(f"No regular US session found for week of {day.isoformat()}.")


def _history_path(history_dir: Path, source_date: str) -> Path:
    return history_dir / f"{source_date}.json"


def _load_trade(path: Path) -> dict[str, Any]:
    value = us.load_json(path)
    if not isinstance(value, dict) or value.get("decision") != "TRADE":
        raise us.PublicationError(f"Canonical US trade is missing or invalid: {path.name}")
    return value


def _weekly_text(selection: dict[str, Any], week_end: str) -> None:
    selection["valid_until"] = week_end
    selection["time_stop"] = "Hold through the final regular US session of the selection week; exit near 15:55 ET if neither TP nor SL has fired."
    selection["time_stop_pl"] = "Trzymaj do końca tygodnia handlowego; jeżeli wcześniej nie zadziała TP ani SL, zamknij pozycję około 15:55 ET w ostatniej sesji tygodnia."
    selection["holding_policy"] = "END_OF_TRADING_WEEK"


def _sync_open_history(
    book: dict[str, Any],
    history_dir: Path,
    *,
    config: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    position = book.get("open_position")
    if not isinstance(position, Mapping):
        raise us.PublicationError("Open-position sync requires an open US position.")

    source_date = str(position.get("source_history_date") or "")
    if not source_date:
        raise us.PublicationError("Open US position is missing source_history_date.")
    try:
        selected_day = date.fromisoformat(source_date)
    except ValueError as exc:
        raise us.PublicationError("Open US position has invalid source_history_date.") from exc

    week_end = final_session_of_week(selected_day, config).isoformat()
    canonical_path = _history_path(history_dir, source_date)
    canonical = _load_trade(canonical_path)
    symbol = str(position.get("symbol") or "").upper()
    canonical_symbol = str(((canonical.get("selection") or {}).get("symbol") or "")).upper()
    if not symbol or symbol != canonical_symbol:
        raise us.PublicationError("Open US position and canonical history symbol disagree.")

    updated_position = deepcopy(dict(position))
    updated_position["valid_until"] = week_end
    entry_selection = deepcopy(dict(updated_position.get("entry_selection") or {}))
    _weekly_text(entry_selection, week_end)
    updated_position["entry_selection"] = entry_selection
    book["open_position"] = updated_position

    selection = deepcopy(dict(canonical.get("selection") or {}))
    _weekly_text(selection, week_end)
    canonical["selection"] = selection

    opened_at = str(updated_position.get("opened_at") or canonical.get("generated_at") or "")
    entry = float(updated_position.get("entry") or selection.get("reference_price") or 0.0)
    canonical["position_lifecycle"] = {
        "status": "OPEN",
        "position_id": updated_position.get("position_id"),
        "opened_at": opened_at,
        "holding_policy": "END_OF_TRADING_WEEK",
        "valid_until": week_end,
    }
    canonical["outcome"] = {
        "status": "OPEN",
        "activated": True,
        "activated_at": opened_at,
        "entry_price": entry,
        "position_id": updated_position.get("position_id"),
    }
    us.atomic_json(canonical_path, canonical)
    return book, canonical


def _sync_closed_history(book: Mapping[str, Any], history_dir: Path) -> int:
    repaired = 0
    for closure in book.get("closed_positions") or []:
        if not isinstance(closure, Mapping):
            continue
        source_date = str(closure.get("source_history_date") or "")
        if not source_date:
            continue
        path = _history_path(history_dir, source_date)
        payload = us.load_json(path)
        if not isinstance(payload, dict):
            continue
        outcome = payload.get("outcome") or {}
        if str(outcome.get("status") or "").upper() == "RESOLVED":
            continue
        lifecycle.apply_closure_to_history(history_dir, closure)
        repaired += 1
    return repaired


def _public_hold(
    canonical: Mapping[str, Any],
    book: Mapping[str, Any],
    *,
    now: datetime,
    previous_public: Mapping[str, Any] | None,
) -> dict[str, Any]:
    position = book.get("open_position") or {}
    snapshot = {}
    if isinstance(previous_public, Mapping):
        prior_selection = previous_public.get("selection") or {}
        if isinstance(prior_selection, Mapping) and str(prior_selection.get("symbol") or "").upper() == str(position.get("symbol") or "").upper():
            snapshot = dict(prior_selection.get("market_snapshot") or {})
    if not snapshot:
        snapshot = dict(((canonical.get("selection") or {}).get("market_snapshot") or {}))

    hold = lifecycle.hold_payload(
        canonical,
        book,
        now=now,
        reason="Existing US position remains open; overnight/pre-market state is carried forward until the next regular-session mark.",
    )
    selection = deepcopy(dict(hold.get("selection") or {}))
    if snapshot:
        selection["market_snapshot"] = snapshot
    _weekly_text(selection, str(position.get("valid_until") or selection.get("valid_until") or ""))
    hold["selection"] = selection
    hold["position_action"] = "HOLD"
    hold["position"] = runtime._position_view(position)
    hold["outcome"] = {
        "status": "OPEN",
        "activated": True,
        "activated_at": position.get("opened_at"),
        "entry_price": position.get("entry"),
        "position_id": position.get("position_id"),
    }
    hold.setdefault("data_quality", {})["position_integrity"] = {
        "status": "carried_forward",
        "market_session_open": SESSION_START <= now.time() <= SESSION_END,
        "holding_policy": "END_OF_TRADING_WEEK",
    }
    return hold


def repair(
    *,
    now: datetime | None = None,
    book_path: Path | None = None,
    history_dir: Path | None = None,
    public_path: Path | None = None,
) -> dict[str, Any]:
    now = now or us.now_ny()
    config = us.load_config()
    book_path = book_path or runtime.BOOK_PATH
    history_dir = history_dir or us.HISTORY_DIR
    public_path = public_path or us.PUBLIC_PATH

    book = lifecycle.load_or_bootstrap(book_path, history_dir, now=now)
    repaired_closed = _sync_closed_history(book, history_dir)
    position = book.get("open_position")
    if not isinstance(position, Mapping):
        return {"status": "OK", "open_position": False, "repaired_closed": repaired_closed}

    book, canonical = _sync_open_history(book, history_dir, config=config)
    lifecycle.save_book(book_path, book, now=now)

    previous_public = us.load_json(public_path)
    regular_session = us.is_session_day(now.date(), config) and SESSION_START <= now.time() <= SESSION_END
    public_symbol = str((((previous_public or {}).get("selection") or {}).get("symbol") or "")).upper() if isinstance(previous_public, Mapping) else ""
    open_symbol = str((book.get("open_position") or {}).get("symbol") or "").upper()
    public_has_open = (
        isinstance(previous_public, Mapping)
        and previous_public.get("decision") == "TRADE"
        and public_symbol == open_symbol
        and str(((previous_public.get("position") or {}).get("status") or "")).upper() == "OPEN"
    )

    if public_has_open and regular_session:
        public = deepcopy(dict(previous_public))
        selection = deepcopy(dict(public.get("selection") or {}))
        _weekly_text(selection, str((book.get("open_position") or {}).get("valid_until") or ""))
        public["selection"] = selection
        public["position"] = runtime._position_view(book["open_position"])
        public["outcome"] = {
            "status": "OPEN",
            "activated": True,
            "activated_at": book["open_position"].get("opened_at"),
            "entry_price": book["open_position"].get("entry"),
            "position_id": book["open_position"].get("position_id"),
        }
    else:
        public = _public_hold(canonical, book, now=now, previous_public=previous_public if isinstance(previous_public, Mapping) else None)

    us.atomic_json(public_path, public)
    return {
        "status": "OK",
        "open_position": True,
        "symbol": open_symbol,
        "position_id": book["open_position"].get("position_id"),
        "valid_until": book["open_position"].get("valid_until"),
        "repaired_closed": repaired_closed,
    }


def verify(
    *,
    now: datetime | None = None,
    book_path: Path | None = None,
    history_dir: Path | None = None,
    public_path: Path | None = None,
) -> dict[str, Any]:
    now = now or us.now_ny()
    config = us.load_config()
    book_path = book_path or runtime.BOOK_PATH
    history_dir = history_dir or us.HISTORY_DIR
    public_path = public_path or us.PUBLIC_PATH
    book = us.load_json(book_path)
    if not isinstance(book, dict):
        raise us.PublicationError("US position book missing during integrity verification.")
    position = book.get("open_position")
    if not isinstance(position, Mapping):
        return {"status": "OK", "open_position": False}

    source_date = str(position.get("source_history_date") or "")
    canonical = _load_trade(_history_path(history_dir, source_date))
    outcome = canonical.get("outcome") or {}
    if outcome.get("status") != "OPEN" or outcome.get("activated") is not True:
        raise us.PublicationError("Canonical US history does not expose the open position as OPEN/activated.")
    expected_week_end = final_session_of_week(date.fromisoformat(source_date), config).isoformat()
    if str(position.get("valid_until") or "") != expected_week_end:
        raise us.PublicationError("US open position is not held through the end of its trading week.")
    if str(((canonical.get("selection") or {}).get("valid_until") or "")) != expected_week_end:
        raise us.PublicationError("Canonical US trade has the wrong weekly holding deadline.")

    public = us.load_json(public_path)
    if not isinstance(public, Mapping):
        raise us.PublicationError("US public feed missing during integrity verification.")
    if str(((public.get("selection") or {}).get("symbol") or "")).upper() != str(position.get("symbol") or "").upper():
        raise us.PublicationError("US public feed does not expose the open position symbol.")
    if str(((public.get("position") or {}).get("status") or "")).upper() != "OPEN":
        raise us.PublicationError("US public feed does not expose OPEN position state.")
    return {"status": "OK", "open_position": True, "symbol": position.get("symbol"), "valid_until": expected_week_end}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = verify() if args.verify else repair()
    print("US_DAILY_POSITION_INTEGRITY", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
