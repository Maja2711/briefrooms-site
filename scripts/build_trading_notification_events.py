#!/usr/bin/env python3
"""Build read-only OPEN/CLOSE notification events from canonical trading state.

This layer has no execution authority. It only observes persisted production/paper
state after the owning engine has written it. First run seeds state and emits no
historical notifications.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "investments"
NOTIFY = ROOT / "data" / "notifications"
STATE_PATH = NOTIFY / "trading-state.json"
EVENTS_PATH = NOTIFY / "trading-events.json"

STATE_SCHEMA = "briefrooms-trading-notification-state-v1"
EVENTS_SCHEMA = "briefrooms-trading-notification-events-v1"
MAX_EVENTS = 200


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json_if_changed(path: Path, payload: Any) -> bool:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def compact_position(
    *,
    engine: str,
    position_id: str,
    instrument: str,
    direction: str | None,
    opened_at: str | None,
    entry: Any = None,
    market: str | None = None,
) -> dict[str, Any]:
    out = {
        "engine": engine,
        "position_id": str(position_id),
        "instrument": str(instrument),
        "direction": (str(direction).upper() if direction else None),
        "opened_at": opened_at,
        "entry": entry,
    }
    if market:
        out["market"] = market
    return out


def daily_open_positions() -> list[dict[str, Any]]:
    payload = read_json(DATA / "eurusd_daily_spot.json", {})
    metadata = payload.get("metadata") if isinstance(payload, dict) else {}
    position = metadata.get("position") if isinstance(metadata, dict) else None
    if not isinstance(position, dict) or str(position.get("status", "")).upper() != "OPEN":
        return []
    pid = position.get("trade_id") or f"daily:{position.get('opened_at')}:{position.get('direction')}"
    return [compact_position(
        engine="daily",
        position_id=str(pid),
        instrument="EUR/USD",
        direction=position.get("direction"),
        opened_at=position.get("opened_at"),
        entry=position.get("entry"),
    )]


def latest_weekly_payload() -> dict[str, Any]:
    paths = sorted((DATA / "weekly").glob("20??-W??.json"))
    return read_json(paths[-1], {}) if paths else {}


def weekly_open_positions() -> list[dict[str, Any]]:
    payload = latest_weekly_payload()
    week_id = str(payload.get("week_id") or "weekly")
    rows = payload.get("instruments")
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = str(row.get("trade_status") or "").lower()
        entry = row.get("entry_price")
        exit_price = row.get("exit_price")
        is_open = status in {"open", "opened", "active", "holding"} or (
            entry is not None and exit_price is None and status not in {"pending", "no_trade", "closed", "cancelled"}
        )
        if not is_open:
            continue
        instrument_id = str(row.get("instrument_id") or row.get("symbol") or "unknown")
        label = row.get("label_pl") or row.get("label_en") or row.get("symbol") or instrument_id
        pid = row.get("position_id") or f"{week_id}:{instrument_id}:{row.get('entry_captured_at') or entry}"
        out.append(compact_position(
            engine="weekly",
            position_id=str(pid),
            instrument=str(label),
            direction=row.get("direction"),
            opened_at=row.get("entry_captured_at"),
            entry=entry,
        ))
    return out


def stock_open_positions() -> list[dict[str, Any]]:
    payload = read_json(DATA / "stock_trading_portfolio.json", {})
    markets = payload.get("markets") if isinstance(payload, dict) else {}
    if not isinstance(markets, dict):
        return []
    out: list[dict[str, Any]] = []
    for market, block in markets.items():
        if not isinstance(block, dict):
            continue
        rows = block.get("open_positions")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or str(row.get("status") or "OPEN").upper() != "OPEN":
                continue
            pid = row.get("position_id") or f"{market}:{row.get('symbol')}:{row.get('opened_at')}"
            instrument = row.get("ticker") or row.get("symbol") or row.get("name") or pid
            direction = row.get("direction") or "LONG"
            out.append(compact_position(
                engine="stock",
                position_id=str(pid),
                instrument=str(instrument),
                direction=str(direction),
                opened_at=row.get("opened_at"),
                entry=row.get("entry"),
                market=str(market),
            ))
    return out


def current_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA,
        "initialized": True,
        "engines": {
            "daily": {"open_positions": sorted(daily_open_positions(), key=lambda x: x["position_id"])},
            "weekly": {"open_positions": sorted(weekly_open_positions(), key=lambda x: x["position_id"])},
            "stock": {"open_positions": sorted(stock_open_positions(), key=lambda x: x["position_id"])},
        },
    }


def index_positions(state: dict[str, Any], engine: str) -> dict[str, dict[str, Any]]:
    engines = state.get("engines") if isinstance(state, dict) else {}
    block = engines.get(engine) if isinstance(engines, dict) else {}
    rows = block.get("open_positions") if isinstance(block, dict) else []
    if not isinstance(rows, list):
        return {}
    return {str(x.get("position_id")): x for x in rows if isinstance(x, dict) and x.get("position_id")}


def event_id(engine: str, event_type: str, position_id: str) -> str:
    raw = f"{engine}|{event_type}|{position_id}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def make_event(engine: str, event_type: str, pos: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    event = {
        "event_id": event_id(engine, event_type, str(pos["position_id"])),
        "engine": engine,
        "event_type": event_type,
        "position_id": pos.get("position_id"),
        "instrument": pos.get("instrument"),
        "market": pos.get("market"),
        "direction": pos.get("direction"),
        "entry": pos.get("entry"),
        "opened_at": pos.get("opened_at"),
        "observed_at": now,
        "source": "persisted_trading_state_transition",
    }
    if engine == "daily" and event_type == "CLOSE":
        history = read_json(DATA / "eurusd_daily_history.json", {})
        trades = history.get("trades") if isinstance(history, dict) else []
        if isinstance(trades, list):
            trade = next(
                (
                    row for row in reversed(trades)
                    if isinstance(row, dict)
                    and str(row.get("trade_id") or "") == str(pos.get("position_id") or "")
                ),
                None,
            )
            if trade:
                event["exit_reason"] = trade.get("exit_reason")
                event["exit_price"] = trade.get("exit_price")
                event["closed_at"] = trade.get("closed_at")
                event["r_multiple"] = trade.get("r_multiple")
    return event


def build_events(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    if not previous.get("initialized"):
        return []
    events: list[dict[str, Any]] = []
    for engine in ("daily", "weekly", "stock"):
        before = index_positions(previous, engine)
        after = index_positions(current, engine)
        for pid in sorted(after.keys() - before.keys()):
            events.append(make_event(engine, "OPEN", after[pid]))
        for pid in sorted(before.keys() - after.keys()):
            events.append(make_event(engine, "CLOSE", before[pid]))
    return events


def main() -> int:
    previous = read_json(STATE_PATH, {"initialized": False, "engines": {}})
    current = current_state()
    new_events = build_events(previous, current)

    history_payload = read_json(EVENTS_PATH, {"schema_version": EVENTS_SCHEMA, "events": []})
    history = history_payload.get("events") if isinstance(history_payload, dict) else []
    if not isinstance(history, list):
        history = []
    existing_ids = {str(x.get("event_id")) for x in history if isinstance(x, dict)}
    for event in new_events:
        if event["event_id"] not in existing_ids:
            history.append(event)
            existing_ids.add(event["event_id"])

    history = history[-MAX_EVENTS:]
    changed_state = write_json_if_changed(STATE_PATH, current)
    changed_events = write_json_if_changed(EVENTS_PATH, {
        "schema_version": EVENTS_SCHEMA,
        "events": history,
    })

    print(json.dumps({
        "status": "ok",
        "new_events": len(new_events),
        "state_changed": changed_state,
        "events_changed": changed_events,
        "open_counts": {
            engine: len(index_positions(current, engine))
            for engine in ("daily", "weekly", "stock")
        },
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
