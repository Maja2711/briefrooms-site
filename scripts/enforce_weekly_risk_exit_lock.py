#!/usr/bin/env python3
"""Govern WES re-entry after SL/TP paper exits.

A Monday/Tuesday risk exit is placed into a WES trigger-monitoring lock rather
than being permanently blocked for the rest of the week. It may reopen only
after the production WES runner sees a fresh qualified signal. SL/TP exits from
Wednesday onward remain blocked through the frozen weekly close.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
WEEKLY_DIR = ROOT / "data" / "investments" / "weekly"
TZ = ZoneInfo("Europe/Warsaw")
EARLY_CLOSE_WEEKDAYS = {0, 1}


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


def parse_local(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ)
    except Exception:
        return None


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
        "early_reentry_monitoring": [],
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
        exit_at = parse_local(item.get("exit_captured_at"))
        early = exit_at is not None and exit_at.weekday() in EARLY_CLOSE_WEEKDAYS
        if early:
            desired = {
                "active": True,
                "scope": "wes_early_reentry_pending",
                "until": until,
                "reason": reason,
                "policy": "early_week_risk_exit_requires_fresh_wes_signal",
                "created_from_exit_at": item.get("exit_captured_at"),
            }
            if item.get("reentry_lock") != desired:
                item["reentry_lock"] = desired
                changed = True
            early_updates = {
                "wes_early_reentry_eligible": True,
                "wes_early_reentry_source_exit_at": item.get("exit_captured_at"),
                "wes_early_reentry_source_exit_reason": reason,
                "wes_early_reentry_source_risk_status": item.get("risk_status"),
                "next_entry_status": "wes_early_reentry_waiting_for_signal",
            }
            for key, value in early_updates.items():
                if item.get(key) != value:
                    item[key] = value
                    changed = True
            report["early_reentry_monitoring"].append({
                "instrument_id": instrument_id,
                "reason": reason,
                "exit_at": item.get("exit_captured_at"),
                "admission": "fresh_wes_signal_required",
            })
        else:
            desired = {
                "active": True,
                "scope": "same_week",
                "until": until,
                "reason": reason,
                "policy": "sl_tp_exit_blocks_same_week_reentry_from_wednesday",
                "created_from_exit_at": item.get("exit_captured_at"),
            }
            if item.get("reentry_lock") != desired:
                item["reentry_lock"] = desired
                changed = True
            if item.get("next_entry_status") != "blocked_after_risk_exit":
                item["next_entry_status"] = "blocked_after_risk_exit"
                changed = True
            report["locked"].append({"instrument_id": instrument_id, "reason": reason, "until": until})

        if item.get("continuous_exposure_active") is not False:
            item["continuous_exposure_active"] = False
            changed = True
        if item.get("continuous_exposure_status") != "closed_by_risk_exit":
            item["continuous_exposure_status"] = "closed_by_risk_exit"
            changed = True

    if changed:
        write_json(path, week)
    report["changed"] = changed
    report["status"] = "completed"
    report["week_path"] = str(path.relative_to(ROOT))
    return report


if __name__ == "__main__":
    print(json.dumps(apply_lock(), ensure_ascii=False))
