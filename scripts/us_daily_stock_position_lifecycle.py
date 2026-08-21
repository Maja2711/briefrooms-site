#!/usr/bin/env python3
"""Single-position lifecycle for US Daily Stock.

PR26 turns the daily selector into a portfolio-aware paper-trading process:
- at most one US position may be OPEN at a time,
- a repeated signal for the already-held symbol is HOLD, never a second entry,
- legacy overlapping pending signals stay available for audit but are permanently
  marked SUPPRESSED so they can never reappear as phantom trades,
- TP / SL / horizon exits are deterministic and written back to the canonical
  originating history record,
- rotation to a different stock is deliberately hard: the new setup must have a
  material score edge while the current trade is already profitable in R terms.

The module is provider-agnostic. Runtime code supplies market snapshots so unit
tests stay deterministic and no network access is required.
"""
from __future__ import annotations

import json
import tempfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

BOOK_SCHEMA = "us-daily-stock-position-book-v1"
ROTATION_SCORE_EDGE = 10.0
ROTATION_MIN_CURRENT_R = 0.25
SUPPRESSED_STATUS = "SUPPRESSED"
SUPPRESSED_LIFECYCLE_STATUS = "SUPPRESSED_DUPLICATE_SIGNAL"


def _load(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(body)
        name = handle.name
    Path(name).replace(path)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def empty_book(now: datetime | None = None) -> dict[str, Any]:
    return {
        "schema_version": BOOK_SCHEMA,
        "updated_at": _iso(now) if now else None,
        "open_position": None,
        "closed_positions": [],
        "suppressed_signals": [],
        "policy": {
            "max_open_positions": 1,
            "same_symbol_repeat": "HOLD_NO_NEW_ENTRY",
            "rotation_score_edge": ROTATION_SCORE_EDGE,
            "rotation_min_current_r": ROTATION_MIN_CURRENT_R,
            "same_bar_tp_sl": "STOP_CONSERVATIVE",
        },
    }


def _trade_payloads(history_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    rows: list[tuple[Path, dict[str, Any]]] = []
    if not history_dir.exists():
        return rows
    for path in sorted(history_dir.glob("????-??-??.json")):
        payload = _load(path)
        if isinstance(payload, dict) and payload.get("decision") == "TRADE":
            rows.append((path, payload))
    return rows


def _outcome_status(payload: Mapping[str, Any]) -> str:
    return str(((payload.get("outcome") or {}).get("status") or "")).upper()


def _outcome_resolved(payload: Mapping[str, Any]) -> bool:
    return _outcome_status(payload) == "RESOLVED"


def _outcome_suppressed(payload: Mapping[str, Any]) -> bool:
    lifecycle = payload.get("position_lifecycle") or {}
    return (
        _outcome_status(payload) == SUPPRESSED_STATUS
        or str(lifecycle.get("status") or "") == SUPPRESSED_LIFECYCLE_STATUS
    )


def _selection(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    value = payload.get("selection") or {}
    return value if isinstance(value, Mapping) else {}


def _symbol(payload: Mapping[str, Any]) -> str:
    selection = _selection(payload)
    return str(selection.get("symbol") or selection.get("ticker") or "").upper()


def activated_at_selection(payload: Mapping[str, Any]) -> bool:
    """Infer activation only when the published mark was inside the entry zone."""
    if _outcome_suppressed(payload):
        return False
    selection = _selection(payload)
    zone = selection.get("entry_zone") or []
    snapshot = selection.get("market_snapshot") or {}
    try:
        low, high = float(zone[0]), float(zone[1])
        mark = float(snapshot.get("last", selection.get("reference_price")))
    except (TypeError, ValueError, IndexError):
        return False
    return low <= mark <= high


def position_from_trade(payload: Mapping[str, Any]) -> dict[str, Any]:
    selection = _selection(payload)
    symbol = _symbol(payload)
    if not symbol:
        raise ValueError("US position requires symbol")
    entry = float(selection["reference_price"])
    stop = float(selection["stop"])
    target = float(selection["target"])
    if not stop < entry < target:
        raise ValueError("US LONG position requires stop < entry < target")
    return {
        "position_id": f"us:{payload.get('date')}:{symbol}",
        "status": "OPEN",
        "symbol": symbol,
        "ticker": str(selection.get("ticker") or symbol),
        "name": selection.get("name"),
        "sector": selection.get("sector"),
        "source_history_date": payload.get("date"),
        "opened_at": payload.get("generated_at"),
        "entry": entry,
        "stop": stop,
        "target": target,
        "entry_score": float(selection.get("score") or 0.0),
        "valid_until": selection.get("valid_until"),
        "entry_selection": deepcopy(dict(selection)),
        "last_mark": entry,
        "unrealized_percent": 0.0,
        "current_r": 0.0,
        "last_evaluated_at": payload.get("generated_at"),
    }


def _suppression(payload: Mapping[str, Any], canonical: Mapping[str, Any], reason: str) -> dict[str, Any]:
    selection = _selection(payload)
    return {
        "date": payload.get("date"),
        "generated_at": payload.get("generated_at"),
        "symbol": _symbol(payload),
        "score": selection.get("score"),
        "reason": reason,
        "canonical_position_id": canonical.get("position_id"),
        "canonical_source_history_date": canonical.get("source_history_date"),
    }


def _existing_suppression(payload: Mapping[str, Any]) -> dict[str, Any]:
    meta = payload.get("position_lifecycle") or {}
    return {
        "date": payload.get("date"),
        "generated_at": payload.get("generated_at"),
        "symbol": _symbol(payload),
        "score": _selection(payload).get("score"),
        "reason": meta.get("reason") or "legacy_overlap_while_position_open",
        "canonical_position_id": meta.get("canonical_position_id"),
        "canonical_source_history_date": meta.get("canonical_source_history_date"),
    }


def mark_history_suppressed(path: Path, canonical: Mapping[str, Any], *, reason: str) -> dict[str, Any]:
    """Persist that a legacy daily TRADE was only a duplicate signal, not a fill."""
    payload = _load(path)
    if not isinstance(payload, dict):
        raise ValueError(f"cannot suppress missing US history file: {path}")
    if _outcome_resolved(payload):
        raise ValueError("cannot suppress an already resolved US trade")
    row = _suppression(payload, canonical, reason)
    payload["position_lifecycle"] = {
        "status": SUPPRESSED_LIFECYCLE_STATUS,
        "reason": reason,
        "canonical_position_id": canonical.get("position_id"),
        "canonical_source_history_date": canonical.get("source_history_date"),
    }
    payload["outcome"] = {
        "status": SUPPRESSED_STATUS,
        "activated": False,
        "reason": reason,
        "canonical_position_id": canonical.get("position_id"),
        "canonical_source_history_date": canonical.get("source_history_date"),
    }
    _atomic(path, payload)
    return row


def bootstrap_book(history_dir: Path, *, now: datetime | None = None, persist_suppression: bool = True) -> dict[str, Any]:
    """Migrate legacy pending daily signals into one canonical open position."""
    book = empty_book(now)
    for path, payload in _trade_payloads(history_dir):
        if _outcome_suppressed(payload):
            book["suppressed_signals"].append(_existing_suppression(payload))
            continue
        if _outcome_resolved(payload) or not activated_at_selection(payload):
            continue
        if book["open_position"] is None:
            book["open_position"] = position_from_trade(payload)
            continue
        reason = "legacy_overlap_while_position_open"
        if persist_suppression:
            row = mark_history_suppressed(path, book["open_position"], reason=reason)
        else:
            row = _suppression(payload, book["open_position"], reason)
        book["suppressed_signals"].append(row)
    return book


def load_or_bootstrap(book_path: Path, history_dir: Path, *, now: datetime | None = None) -> dict[str, Any]:
    payload = _load(book_path)
    if isinstance(payload, dict) and payload.get("schema_version") == BOOK_SCHEMA:
        return payload
    book = bootstrap_book(history_dir, now=now, persist_suppression=True)
    _atomic(book_path, book)
    return book


def save_book(book_path: Path, book: Mapping[str, Any], *, now: datetime) -> None:
    payload = deepcopy(dict(book))
    payload["schema_version"] = BOOK_SCHEMA
    payload["updated_at"] = _iso(now)
    _atomic(book_path, payload)


def mark_for_position(position: Mapping[str, Any], mark: float) -> tuple[float, float]:
    entry = float(position["entry"])
    risk = entry - float(position["stop"])
    pnl = float(mark) - entry
    result_percent = 0.0 if entry == 0 else pnl / entry * 100.0
    r_multiple = 0.0 if risk <= 0 else pnl / risk
    return result_percent, r_multiple


def _closure(position: Mapping[str, Any], *, exit_reason: str, exit_price: float, now: datetime, conservative_same_bar: bool = False) -> dict[str, Any]:
    result_percent, r_multiple = mark_for_position(position, exit_price)
    return {
        "position_id": position.get("position_id"),
        "symbol": position.get("symbol"),
        "source_history_date": position.get("source_history_date"),
        "opened_at": position.get("opened_at"),
        "closed_at": _iso(now),
        "entry": round(float(position["entry"]), 4),
        "exit_price": round(float(exit_price), 4),
        "stop": round(float(position["stop"]), 4),
        "target": round(float(position["target"]), 4),
        "exit_reason": exit_reason,
        "return_percent": round(result_percent, 5),
        "r_multiple": round(r_multiple, 4),
        "outcome": "WIN" if r_multiple > 0 else "LOSS" if r_multiple < 0 else "FLAT",
        "conservative_same_bar": bool(conservative_same_bar),
    }


def reconcile_open_position(
    book: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    now: datetime,
    horizon_exit_allowed: bool = False,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Mark one open LONG position and close it on TP, SL or elapsed horizon."""
    updated = deepcopy(dict(book))
    position = updated.get("open_position")
    if not isinstance(position, Mapping):
        return updated, None
    try:
        high = float(snapshot["high"])
        low = float(snapshot["low"])
        last = float(snapshot["last"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("US position reconciliation requires high/low/last")

    stop = float(position["stop"])
    target = float(position["target"])
    same_bar = low <= stop and high >= target
    closure: dict[str, Any] | None = None
    if same_bar:
        closure = _closure(position, exit_reason="stop", exit_price=stop, now=now, conservative_same_bar=True)
    elif low <= stop:
        closure = _closure(position, exit_reason="stop", exit_price=stop, now=now)
    elif high >= target:
        closure = _closure(position, exit_reason="target", exit_price=target, now=now)
    else:
        valid_until = str(position.get("valid_until") or "")
        if horizon_exit_allowed and valid_until and now.date().isoformat() >= valid_until:
            closure = _closure(position, exit_reason="horizon", exit_price=last, now=now)

    if closure:
        closed = [dict(row) for row in updated.get("closed_positions") or []]
        closed.append(closure)
        updated["closed_positions"] = closed[-100:]
        updated["open_position"] = None
        return updated, closure

    result_percent, r_multiple = mark_for_position(position, last)
    marked = deepcopy(dict(position))
    marked["last_mark"] = round(last, 4)
    marked["unrealized_percent"] = round(result_percent, 5)
    marked["current_r"] = round(r_multiple, 4)
    marked["last_evaluated_at"] = _iso(now)
    updated["open_position"] = marked
    return updated, None


def apply_closure_to_history(history_dir: Path, closure: Mapping[str, Any]) -> Path:
    source_date = str(closure.get("source_history_date") or "")
    if not source_date:
        raise ValueError("closure missing source_history_date")
    path = history_dir / f"{source_date}.json"
    payload = _load(path)
    if not isinstance(payload, dict):
        raise ValueError(f"canonical US history missing for {source_date}")
    payload["position_lifecycle"] = {
        "status": "CLOSED",
        "position_id": closure.get("position_id"),
        "closed_at": closure.get("closed_at"),
        "exit_reason": closure.get("exit_reason"),
    }
    payload["outcome"] = {
        "status": "RESOLVED",
        "activated": True,
        "entry_price": closure.get("entry"),
        "exit_price": closure.get("exit_price"),
        "exit_reason": closure.get("exit_reason"),
        "closed_at": closure.get("closed_at"),
        "return_percent": closure.get("return_percent"),
        "r_multiple": closure.get("r_multiple"),
        "outcome": closure.get("outcome"),
        "conservative_same_bar": closure.get("conservative_same_bar", False),
    }
    _atomic(path, payload)
    return path


def record_suppressed_signal(book: Mapping[str, Any], payload: Mapping[str, Any], *, reason: str) -> dict[str, Any]:
    updated = deepcopy(dict(book))
    position = updated.get("open_position")
    if not isinstance(position, Mapping):
        return updated
    row = _suppression(payload, position, reason)
    existing = [dict(item) for item in updated.get("suppressed_signals") or []]
    key = (row.get("date"), row.get("symbol"), row.get("reason"))
    if not any((x.get("date"), x.get("symbol"), x.get("reason")) == key for x in existing):
        existing.append(row)
    updated["suppressed_signals"] = existing[-250:]
    return updated


def should_rotate(position: Mapping[str, Any], candidate_payload: Mapping[str, Any]) -> tuple[bool, str]:
    candidate = _selection(candidate_payload)
    new_symbol = _symbol(candidate_payload)
    old_symbol = str(position.get("symbol") or "").upper()
    if not new_symbol:
        return False, "no_trade_candidate"
    if new_symbol == old_symbol:
        return False, "same_symbol_hold"
    score_edge = float(candidate.get("score") or 0.0) - float(position.get("entry_score") or 0.0)
    current_r = float(position.get("current_r") or 0.0)
    if score_edge < ROTATION_SCORE_EDGE:
        return False, "score_edge_too_small"
    if current_r < ROTATION_MIN_CURRENT_R:
        return False, "current_trade_not_profitable_enough_to_rotate"
    return True, "stronger_setup_and_current_trade_profitable"


def close_for_rotation(book: Mapping[str, Any], *, now: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    updated = deepcopy(dict(book))
    position = updated.get("open_position")
    if not isinstance(position, Mapping):
        raise ValueError("cannot rotate without open US position")
    mark = float(position.get("last_mark") or position["entry"])
    closure = _closure(position, exit_reason="rotation", exit_price=mark, now=now)
    closed = [dict(row) for row in updated.get("closed_positions") or []]
    closed.append(closure)
    updated["closed_positions"] = closed[-100:]
    updated["open_position"] = None
    return updated, closure


def open_from_payload(book: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    updated = deepcopy(dict(book))
    if updated.get("open_position") is not None:
        raise ValueError("US position book already has an open position")
    updated["open_position"] = position_from_trade(payload)
    return updated


def canonical_history_payloads(history_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return trade history with duplicate daily signals permanently excluded."""
    included: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None
    for _path, payload in _trade_payloads(history_dir):
        if _outcome_suppressed(payload):
            suppressed.append(_existing_suppression(payload))
            continue
        if _outcome_resolved(payload):
            included.append(payload)
            if active and str(active.get("date")) == str(payload.get("date")):
                active = None
            continue
        if not activated_at_selection(payload):
            included.append(payload)
            continue
        if active is None:
            included.append(payload)
            active = payload
            continue
        suppressed.append({
            "date": payload.get("date"),
            "symbol": _symbol(payload),
            "reason": "overlap_while_prior_us_position_open",
            "canonical_date": active.get("date"),
            "canonical_symbol": _symbol(active),
        })
    return included, suppressed


def hold_payload(
    canonical_payload: Mapping[str, Any],
    book: Mapping[str, Any],
    *,
    now: datetime,
    candidate_watch: Mapping[str, Any] | None = None,
    reason: str = "Existing US position remains open.",
) -> dict[str, Any]:
    position = book.get("open_position") or {}
    if not isinstance(position, Mapping):
        raise ValueError("HOLD requires open position")
    payload = deepcopy(dict(canonical_payload))
    payload["date"] = now.date().isoformat()
    payload["generated_at"] = _iso(now)
    payload["decision"] = "TRADE"
    payload["locked"] = True
    payload["position_action"] = "HOLD"
    payload["reason"] = reason
    selection = deepcopy(dict(payload.get("selection") or {}))
    selection["reference_price"] = round(float(position["entry"]), 2)
    selection["stop"] = round(float(position["stop"]), 2)
    selection["target"] = round(float(position["target"]), 2)
    selection["valid_until"] = position.get("valid_until")
    payload["selection"] = selection
    payload["position"] = {
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
    if candidate_watch and candidate_watch.get("decision") == "TRADE":
        watch = _selection(candidate_watch)
        payload["candidate_watch"] = {
            "symbol": _symbol(candidate_watch),
            "score": watch.get("score"),
            "same_as_open_position": _symbol(candidate_watch) == str(position.get("symbol") or "").upper(),
        }
    payload["outcome"] = {"status": "OPEN", "activated": True}
    return payload
