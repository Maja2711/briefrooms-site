#!/usr/bin/env python3
"""Apply append-only manual corrections for confirmed SL/TP breaches.

This is a deterministic recovery path for cases where the scheduled market-data
watcher failed to persist a threshold event. It never invents an execution price:
all exits are applied at the frozen SL/TP level recorded in the override file.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OVERRIDES = ROOT / "data/investments/manual_risk_exit_overrides.json"
WEEKLY_DIR = ROOT / "data/investments/weekly"


def read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def sf(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def set_result(item: dict[str, Any], exit_price: float) -> None:
    entry = sf(item.get("entry_price"))
    if entry is None:
        return
    side = str(item.get("direction") or "")
    move = exit_price - entry if side == "long" else entry - exit_price
    pct = move / entry * 100.0
    iid = str(item.get("instrument_id") or "")
    if iid == "eurusd":
        notional = sf(item.get("notional_eur")) or 10_000.0
        value = move * notional
        units = move / 0.0001
    else:
        notional = sf(item.get("notional_usd")) or 10_000.0
        value = move / entry * notional
        units = pct if iid == "btcusd" else move
    item["result"] = "profit" if value > 0 else "loss" if value < 0 else "flat"
    item["result_value"] = round(value, 8)
    item["result_percent"] = round(pct, 4)
    item["result_units"] = round(units, 8)
    item["result_currency"] = "USD"


def apply() -> dict[str, Any]:
    override = read(OVERRIDES)
    week_id = str(override.get("week_id") or "")
    path = WEEKLY_DIR / f"{week_id}.json"
    week = read(path)
    report: dict[str, Any] = {"week_id": week_id, "changed": False, "applied": [], "skipped": []}
    if not week:
        report["status"] = "week_not_found"
        return report

    by_id = {str(x.get("instrument_id")): x for x in week.get("instruments", []) if isinstance(x, dict)}
    changed = False
    for row in override.get("exits") or []:
        if not isinstance(row, dict):
            continue
        iid = str(row.get("instrument_id") or "")
        item = by_id.get(iid)
        if item is None:
            report["skipped"].append({"instrument_id": iid, "reason": "instrument_not_found"})
            continue
        exit_price = sf(row.get("exit_price"))
        if exit_price is None:
            report["skipped"].append({"instrument_id": iid, "reason": "invalid_exit_price"})
            continue
        if sf(item.get("exit_price")) is not None:
            report["skipped"].append({"instrument_id": iid, "reason": "already_closed"})
            continue

        confirmed_at = str(row.get("confirmed_at") or override.get("created_at") or "")
        reason = str(row.get("exit_reason") or "stop_loss")
        until = str((week.get("market_window") or {}).get("exit_target_local") or "")
        item["exit_price"] = exit_price
        item["exit_captured_at"] = confirmed_at
        item["exit_observed_at"] = confirmed_at
        item["exit_source"] = "manual_confirmed_intraday_threshold_override"
        item["exit_reason"] = reason
        item["exit_execution_model"] = "frozen_planned_level_manual_recovery"
        item["risk_status"] = "stop_loss_hit" if reason == "stop_loss" else "take_profit_hit"
        item["trade_status"] = "closed"
        item["continuous_exposure_active"] = False
        item["continuous_exposure_status"] = "closed_by_risk_exit"
        item["next_entry_status"] = "blocked_after_risk_exit"
        item["reentry_lock"] = {
            "active": True,
            "scope": "same_week",
            "until": until,
            "reason": reason,
            "policy": "sl_tp_exit_blocks_same_week_reentry",
            "created_from_exit_at": confirmed_at,
        }
        item["manual_correction"] = {
            "applied": True,
            "applied_at": override.get("created_at"),
            "reason": override.get("reason"),
            "source_note": override.get("source_note"),
        }
        set_result(item, exit_price)
        changed = True
        report["applied"].append({"instrument_id": iid, "exit_price": exit_price, "reason": reason})

    if changed:
        write(path, week)
    report["changed"] = changed
    report["status"] = "completed"
    return report


if __name__ == "__main__":
    print(json.dumps(apply(), ensure_ascii=False))
