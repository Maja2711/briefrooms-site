#!/usr/bin/env python3
"""Persist and verify the governed v5 metadata for the current weekly ledger.

The runtime may legitimately leave all position fields unchanged during a review.
This final integrity step still persists the active governance version and fails if
an entry governed by v5 precedes its frozen decision or violates an active lock.
It never creates or closes a position and never sends broker orders.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import investments_weekly as legacy
import investments_weekly_v2 as v2

VERSION = "5.0.0-experimental"


def read(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)


def parsed(value: Any) -> Optional[datetime]:
    return v2.parse_dt(value)


def verify_item(item: Dict[str, Any]) -> None:
    entry = parsed(item.get("entry_captured_at"))
    decision = parsed(item.get("entry_decision_at"))
    if entry is not None and decision is not None and entry < decision:
        raise RuntimeError(
            f"retroactive entry forbidden for {item.get('instrument_id')}: "
            f"entry={entry.isoformat()} decision={decision.isoformat()}"
        )

    no_trade = item.get("no_trade_decision")
    if no_trade and item.get("trade_status") == "open":
        raise RuntimeError(f"NO_TRADE opened a position for {item.get('instrument_id')}")

    lock = item.get("reentry_lock") if isinstance(item.get("reentry_lock"), dict) else {}
    lock_created = parsed(lock.get("created_at"))
    lock_until = parsed(lock.get("until"))
    if lock.get("active") and entry and lock_created and lock_until and lock_created <= entry < lock_until:
        raise RuntimeError(
            f"active re-entry lock violated for {item.get('instrument_id')}: "
            f"entry={entry.isoformat()} lock_until={lock_until.isoformat()}"
        )


def apply(path: Optional[Path] = None) -> Dict[str, Any]:
    now = legacy.now_local()
    path = path or v2.current_week_path(now)
    week = read(path)
    if not week:
        return {"status": "skipped", "reason": "current_week_missing"}

    for item in week.get("instruments", []):
        verify_item(item)

    week.setdefault("base_method_version", week.get("method_version"))
    week.setdefault("base_forecast_hash", week.get("forecast_hash"))
    week.setdefault("governance_started_at", now.isoformat(timespec="seconds"))
    week["method_version"] = VERSION
    week["model_status"] = "experimental_governed_execution_parity_paper_only"
    week["multi_instrument_exposure_layer"] = {
        "enabled": True,
        "version": VERSION,
        "common_validation_gate": True,
        "retroactive_entries_forbidden": True,
        "same_week_reentry_block_after_invalidation": True,
        "no_trade_first_class": True,
        "execution_parity_report": "data/investments/weekly_execution_parity_v5.json",
        "last_checked_at": now.isoformat(timespec="seconds"),
    }
    write(path, week)
    return {"status": "completed", "week_id": week.get("week_id"), "version": VERSION}


if __name__ == "__main__":
    print(json.dumps(apply(), ensure_ascii=False))
