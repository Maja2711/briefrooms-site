#!/usr/bin/env python3
"""Apply one validation gate to every weekly model layer.

The gate only controls new entries. Existing open positions are grandfathered and
remain managed by their frozen SL/TP and thesis rules. Closed history is never
rewritten.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
METHOD = ROOT / "data/investments/methodology.json"
WEEKLY = ROOT / "data/investments/weekly"
TZ = ZoneInfo("Europe/Warsaw")


def read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def is_open(item: Dict[str, Any]) -> bool:
    return item.get("entry_price") is not None and item.get("exit_price") is None and item.get("direction") in {"long", "short"}


def is_closed(item: Dict[str, Any]) -> bool:
    return item.get("entry_price") is not None and item.get("exit_price") is not None


def apply_item(item: Dict[str, Any], cfg: Dict[str, Any]) -> bool:
    enabled = bool(cfg.get("enabled_for_new_positions", False))
    reason = str(cfg.get("validation_gate_reason") or "instrument_not_validated")
    if enabled:
        marker = "enabled_for_paper_trading"
        if item.get("validation_gate") == marker and item.get("validation_gate_reason") is None:
            return False
        item["validation_gate"] = marker
        item["validation_gate_reason"] = None
        return True

    if is_open(item):
        marker = "grandfathered_existing_position_no_new_entries"
        if item.get("validation_gate") == marker and item.get("validation_gate_reason") == reason:
            return False
        item["validation_gate"] = marker
        item["validation_gate_reason"] = reason
        item["validation_gate_note"] = (
            "Existing position preserved; frozen risk controls remain active, but no model layer may open a new leg."
        )
        return True

    if is_closed(item):
        changed = item.get("validation_gate") != reason or item.get("next_entry_status") != "no_trade"
        item["validation_gate"] = reason
        item["validation_gate_reason"] = reason
        item["next_entry_status"] = "no_trade"
        item["next_entry_reason"] = "blocked_by_common_validation_gate"
        return changed

    before = (item.get("direction"), item.get("trade_status"), item.get("validation_gate"))
    item["pre_gate_direction"] = item.get("direction")
    item["pre_gate_score"] = item.get("score")
    item.update({
        "direction": "neutral", "score": 0, "trade_status": "no_trade",
        "entry_quality_status": "blocked_by_common_validation_gate", "risk_plan": None,
        "result": "no_trade", "result_value": 0.0, "result_percent": 0.0,
        "validation_gate": reason, "validation_gate_reason": reason,
        "no_trade_decision": True, "no_trade_reason": "blocked_by_common_validation_gate",
        "rationale_pl": ["Brak pozycji: wspólna bramka walidacyjna nie dopuściła instrumentu do nowych wejść."],
        "rationale_en": ["No position: the shared validation gate did not approve the instrument for new entries."],
    })
    return before != (item.get("direction"), item.get("trade_status"), item.get("validation_gate"))


def main() -> None:
    method = read(METHOD, {})
    policy = {str(row.get("id")): row for row in method.get("instruments", [])}
    changed_files = 0
    # The gate is defensive and version-independent. Limiting to recent files avoids
    # mutating archived legacy history while still covering the active/future weeks.
    for path in sorted(WEEKLY.glob("*.json"), reverse=True)[:8]:
        week = read(path, {})
        changed = False
        for item in week.get("instruments", []):
            changed = apply_item(item, policy.get(str(item.get("instrument_id")), {})) or changed
        if changed:
            week["common_validation_gate"] = {
                "version": "5.0.0", "applied_at": datetime.now(TZ).isoformat(timespec="seconds"),
                "scope": "all_model_layers_new_entries_only", "closed_history_rewritten": False,
            }
            write(path, week)
            changed_files += 1
    print(f"Common validation gate applied to {changed_files} weekly files")


if __name__ == "__main__":
    main()
