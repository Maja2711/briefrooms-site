#!/usr/bin/env python3
"""Durable lifecycle event outbox for Weekly Engine Star notifications.

WES emits OPEN/CLOSE at the exact lifecycle transition. The outbox is canonical:
notification delivery may read these persisted events, but must never reconstruct
Weekly events from before/after position snapshots.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
OUTBOX_PATH = ROOT / "data" / "notifications" / "wes-event-outbox.json"
SCHEMA_VERSION = "briefrooms-wes-notification-outbox-v1"


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": SCHEMA_VERSION, "events": []}
    if not isinstance(payload, dict):
        raise ValueError("WES notification outbox must be a JSON object")
    if payload.get("schema_version") not in {None, SCHEMA_VERSION}:
        raise ValueError("unsupported WES notification outbox schema")
    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError("WES notification outbox events must be a list")
    return {"schema_version": SCHEMA_VERSION, "events": events}


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def event_id(event_type: str, position_id: str) -> str:
    raw = f"weekly|{str(event_type).upper()}|{position_id}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def weekly_position_id(week_id: str, item: Mapping[str, Any]) -> str:
    explicit = str(item.get("position_id") or "").strip()
    if explicit:
        return explicit
    instrument_id = str(item.get("instrument_id") or item.get("symbol") or "unknown")
    anchor = item.get("entry_captured_at") or item.get("entry_price")
    if not anchor:
        raise ValueError("WES lifecycle event requires a persisted entry identity")
    return f"{week_id}:{instrument_id}:{anchor}"


def is_wes_week(week: Mapping[str, Any]) -> bool:
    version = str(week.get("method_version") or "")
    return version.startswith("5.") or isinstance(week.get("wes"), dict)


def build_weekly_event(week_id: str, item: Mapping[str, Any], event_type: str) -> dict[str, Any]:
    kind = str(event_type or "").upper()
    if kind not in {"OPEN", "CLOSE"}:
        raise ValueError(f"unsupported WES lifecycle event: {event_type}")
    position_id = weekly_position_id(week_id, item)
    opened_at = item.get("entry_captured_at")
    if not opened_at:
        raise ValueError("WES lifecycle event requires entry_captured_at")
    observed_at = opened_at if kind == "OPEN" else item.get("exit_captured_at")
    if not observed_at:
        raise ValueError(f"WES {kind} lifecycle event requires transition timestamp")
    event = {
        "event_id": event_id(kind, position_id),
        "engine": "weekly",
        "event_type": kind,
        "position_id": position_id,
        "week_id": str(week_id),
        "instrument": str(item.get("label_pl") or item.get("label_en") or item.get("symbol") or item.get("instrument_id") or position_id),
        "market": None,
        "direction": str(item.get("direction") or "").upper() or None,
        "entry": _finite(item.get("entry_price")),
        "opened_at": opened_at,
        "observed_at": observed_at,
        "source": "wes_lifecycle_transition_outbox",
    }
    if kind == "CLOSE":
        event.update({
            "exit_reason": item.get("exit_reason"),
            "exit_price": _finite(item.get("exit_price")),
            "closed_at": item.get("exit_captured_at"),
            "r_multiple": _finite(item.get("r_multiple")),
        })
    return event


def emit_weekly_event(
    week_id: str,
    item: dict[str, Any],
    event_type: str,
    *,
    path: Path | None = None,
) -> tuple[dict[str, Any], bool]:
    target = path or OUTBOX_PATH
    event = build_weekly_event(week_id, item, event_type)
    item.setdefault("position_id", event["position_id"])
    payload = _read(target)
    events = list(payload.get("events") or [])
    for existing in events:
        if not isinstance(existing, dict) or str(existing.get("event_id") or "") != event["event_id"]:
            continue
        immutable_keys = ("engine", "event_type", "position_id", "week_id", "opened_at")
        if any(existing.get(key) != event.get(key) for key in immutable_keys):
            raise RuntimeError(f"WES event_id collision: {event['event_id']}")
        return existing, False
    events.append(event)
    _atomic_write(target, {"schema_version": SCHEMA_VERSION, "events": events})
    return event, True
