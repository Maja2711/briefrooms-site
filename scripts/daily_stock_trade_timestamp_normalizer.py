#!/usr/bin/env python3
"""Durable entry/exit timestamp contract for Daily GPW and US stock paper trades.

The module never invents historical timestamps. US entry time is backfilled only
from the exact market snapshot that actually satisfied the entry-zone condition.
Future records (from DEFAULT_ENFORCE_FROM onward) fail closed when a canonical
position lacks the timestamp evidence required by the public trade history.
"""
from __future__ import annotations

import json
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENFORCE_FROM = date(2026, 8, 26)
US_HISTORY_DIR = ROOT / "data/investments/us_daily_stock_history"
US_BOOK_PATH = ROOT / "data/investments/us_daily_stock_position.json"
GPW_HISTORY_DIR = ROOT / "data/investments/gpw_daily_pick_history"


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


def _trade_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _valid_iso_timestamp(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return text


def trusted_us_snapshot_activation(payload: Mapping[str, Any]) -> tuple[str, float] | None:
    """Return real US entry evidence only when snapshot mark was inside entry zone."""
    selection = payload.get("selection") or {}
    snapshot = selection.get("market_snapshot") or {}
    observed_at = _valid_iso_timestamp(snapshot.get("observed_at"))
    zone = selection.get("entry_zone") or []
    if observed_at is None or len(zone) != 2:
        return None
    try:
        low, high = float(zone[0]), float(zone[1])
        mark = float(snapshot.get("last"))
    except (TypeError, ValueError, IndexError):
        return None
    if not low <= mark <= high:
        return None
    return observed_at, mark


def normalize_us_history(
    history_dir: Path = US_HISTORY_DIR,
    book_path: Path = US_BOOK_PATH,
    *,
    enforce_from: date = DEFAULT_ENFORCE_FROM,
) -> dict[str, int]:
    """Persist trustworthy entry/exit timestamps for canonical US positions.

    Entry time comes from selection.market_snapshot.observed_at only when that
    snapshot price was inside the published entry zone. Exit time comes only from
    the canonical lifecycle book's closed_at. Legacy records with missing evidence
    are left blank; future canonical positions fail closed instead.
    """
    book = _load(book_path) or {}
    positions: list[dict[str, Any]] = []
    open_position = book.get("open_position")
    if isinstance(open_position, dict):
        positions.append(open_position)
    positions.extend(row for row in (book.get("closed_positions") or []) if isinstance(row, dict))

    changed_files = 0
    changed_book = False
    for position in positions:
        source_date = str(position.get("source_history_date") or "")
        if not source_date:
            continue
        path = history_dir / f"{source_date}.json"
        payload = _load(path)
        if not isinstance(payload, dict) or payload.get("decision") != "TRADE":
            continue

        trade_day = _trade_date(payload.get("date"))
        evidence = trusted_us_snapshot_activation(payload)
        if evidence is None:
            if trade_day is not None and trade_day >= enforce_from:
                raise ValueError(f"US timestamp contract: {source_date} lacks trustworthy entry snapshot evidence")
            continue

        activated_at, snapshot_mark = evidence
        outcome = dict(payload.get("outcome") or {})
        lifecycle = dict(payload.get("position_lifecycle") or {})
        before = json.dumps(payload, sort_keys=True, ensure_ascii=False)

        outcome["activated"] = True
        outcome["activated_at"] = activated_at
        if outcome.get("status") not in {"RESOLVED", "SUPPRESSED"}:
            outcome["status"] = "PENDING"
        if outcome.get("entry_price") is None:
            position_entry = position.get("entry")
            outcome["entry_price"] = position_entry if position_entry is not None else snapshot_mark
        lifecycle["opened_at"] = activated_at

        closed_at = _valid_iso_timestamp(position.get("closed_at"))
        if closed_at:
            outcome["closed_at"] = closed_at
            lifecycle["closed_at"] = closed_at
            if outcome.get("exit_price") is None and position.get("exit_price") is not None:
                outcome["exit_price"] = position.get("exit_price")
        elif str(position.get("status") or "").upper() == "CLOSED" and trade_day is not None and trade_day >= enforce_from:
            raise ValueError(f"US timestamp contract: {source_date} closed position lacks closed_at")

        payload["outcome"] = outcome
        if lifecycle:
            payload["position_lifecycle"] = lifecycle

        if position.get("opened_at") != activated_at:
            position["opened_at"] = activated_at
            changed_book = True

        after = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        if after != before:
            _atomic(path, payload)
            changed_files += 1

    if changed_book:
        _atomic(book_path, book)

    return {"changed_history_files": changed_files, "changed_book": int(changed_book)}


def validate_gpw_history(
    history_dir: Path = GPW_HISTORY_DIR,
    *,
    enforce_from: date = DEFAULT_ENFORCE_FROM,
) -> dict[str, int]:
    """Fail closed for future GPW trades missing durable entry/exit timestamps."""
    checked = 0
    violations: list[str] = []
    for path in sorted(history_dir.glob("????-??-??.json")):
        payload = _load(path)
        if not isinstance(payload, dict) or payload.get("decision") != "TRANSAKCJA":
            continue
        trade_day = _trade_date(payload.get("date"))
        if trade_day is None or trade_day < enforce_from:
            continue
        checked += 1
        outcome = payload.get("outcome") or {}
        if outcome.get("activated") is True and not _valid_iso_timestamp(outcome.get("activated_at")):
            violations.append(f"{path.name}: activated trade missing activated_at")
        if str(outcome.get("status") or "").upper() == "RESOLVED" and outcome.get("activated") is True:
            exit_at = outcome.get("exit_bar_at") or outcome.get("closed_at")
            if not _valid_iso_timestamp(exit_at):
                violations.append(f"{path.name}: resolved trade missing exit timestamp")
    if violations:
        raise ValueError("GPW timestamp contract: " + "; ".join(violations))
    return {"checked_future_trades": checked, "violations": 0}


if __name__ == "__main__":
    print(json.dumps({
        "us": normalize_us_history(),
        "gpw": validate_gpw_history(),
    }, ensure_ascii=False, indent=2))
