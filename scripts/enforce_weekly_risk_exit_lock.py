#!/usr/bin/env python3
"""Prevent same-week re-entry after a governed SL/TP paper exit.

The 15-minute exposure watcher runs the threshold engine first. When that
engine records a stop-loss or take-profit exit, this script persists a lock
through the frozen weekly close. The continuous-exposure layer must therefore
not archive the exit and immediately reopen the same instrument.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WEEKLY_DIR = ROOT / "data" / "investments" / "weekly"


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def current_week_path() -> Path | None:
    candidates = sorted(WEEKLY_DIR.glob("*.json"), reverse=True)
    for path in candidates:
        week = read_json(path)
        instruments = week.get("instruments")
        if isinstance(instruments, list) and any(
            isinstance(item, dict)
            and item.get("entry_price") is not None
            and item.get("exit_price") is not None
            and str(item.get("exit_reason") or "") in {"stop_loss", "take_profit"}
            for item in instruments
        ):
            return path
    return candidates[0] if candidates else None


def apply_lock() -> dict[str, Any]:
    path = current_week_path()
    report: dict[str, Any] = {
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "changed": False,
        "locked": [],
    }
    if path is None:
        report["status"] = "no_week_file"
        return report

    week = read_json(path)
    until = str((week.get("market_window") or {}).get("exit_target_local") or "")
    if not until:
        report["status"] = "missing_weekly_exit_target"
        return report

    changed = False
    for item in week.get("instruments") or []:
        if not isinstance(item, dict):
            continue
        reason = str(item.get("exit_reason") or "")
        if reason not in {"stop_loss", "take_profit"} or item.get("exit_price") is None:
            continue

        instrument_id = str(item.get("instrument_id") or "unknown")
        desired = {
            "active": True,
            "scope": "same_week",
            "until": until,
            "reason": reason,
            "policy": "sl_tp_exit_blocks_same_week_reentry",
            "created_from_exit_at": item.get("exit_captured_at"),
        }
        if item.get("reentry_lock") != desired:
            item["reentry_lock"] = desired
            changed = True
        if item.get("next_entry_status") != "blocked_after_risk_exit":
            item["next_entry_status"] = "blocked_after_risk_exit"
            changed = True
        item["continuous_exposure_active"] = False
        item["continuous_exposure_status"] = "closed_by_risk_exit"
        report["locked"].append({"instrument_id": instrument_id, "reason": reason, "until": until})

    if changed:
        write_json(path, week)
    report["changed"] = changed
    report["status"] = "completed"
    report["week_path"] = str(path.relative_to(ROOT))
    return report


if __name__ == "__main__":
    print(json.dumps(apply_lock(), ensure_ascii=False))
