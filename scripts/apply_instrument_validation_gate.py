#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply one version-independent validation gate to new weekly entries.

The gate is defensive and deliberately narrow:
- every model layer reads the same ``enabled_for_new_positions`` flag;
- existing open paper positions are grandfathered and remain risk-managed;
- closed historical weeks are never rewritten;
- a closed leg in the still-active week may be marked ineligible for re-entry.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
METHOD = ROOT / "data" / "investments" / "methodology.json"
WEEKLY = ROOT / "data" / "investments" / "weekly"
TZ = ZoneInfo("Europe/Warsaw")


def read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)


def parsed(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if out.tzinfo is None:
            out = out.replace(tzinfo=TZ)
        return out.astimezone(TZ)
    except Exception:
        return None


def is_open(item: Dict[str, Any]) -> bool:
    return (
        item.get("entry_price") is not None
        and item.get("exit_price") is None
        and item.get("direction") in {"long", "short"}
    )


def is_closed(item: Dict[str, Any]) -> bool:
    return item.get("entry_price") is not None and item.get("exit_price") is not None


def week_can_still_change(week: Dict[str, Any], now: datetime) -> bool:
    """True only for an active/future week or a week with an open position."""
    target = parsed((week.get("market_window") or {}).get("exit_target_local"))
    if target is not None and now <= target:
        return True
    return any(is_open(item) for item in week.get("instruments", []))


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
            "Existing position preserved; frozen SL/TP and thesis controls remain active, "
            "but no model layer may open a new leg."
        )
        return True

    if is_closed(item):
        changed = (
            item.get("validation_gate") != reason
            or item.get("next_entry_status") != "no_trade"
            or item.get("next_entry_reason") != "blocked_by_common_validation_gate"
        )
        item["validation_gate"] = reason
        item["validation_gate_reason"] = reason
        item["next_entry_status"] = "no_trade"
        item["next_entry_reason"] = "blocked_by_common_validation_gate"
        return changed

    before = (
        item.get("direction"), item.get("trade_status"), item.get("validation_gate"),
        item.get("entry_price"), item.get("risk_plan"),
    )
    item["pre_gate_direction"] = item.get("direction")
    item["pre_gate_score"] = item.get("score")
    item.update({
        "direction": "neutral",
        "score": 0,
        "trade_status": "no_trade",
        "entry_quality_status": "blocked_by_common_validation_gate",
        "entry_price": None,
        "entry_captured_at": None,
        "entry_source": None,
        "risk_plan": None,
        "result": "no_trade",
        "result_value": 0.0,
        "result_percent": 0.0,
        "validation_gate": reason,
        "validation_gate_reason": reason,
        "no_trade_decision": True,
        "no_trade_reason": "blocked_by_common_validation_gate",
        "rationale_pl": [
            "Brak pozycji: wspólna bramka walidacyjna nie dopuściła instrumentu do nowych wejść."
        ],
        "rationale_en": [
            "No position: the shared validation gate did not approve the instrument for new entries."
        ],
    })
    after = (
        item.get("direction"), item.get("trade_status"), item.get("validation_gate"),
        item.get("entry_price"), item.get("risk_plan"),
    )
    return before != after


def main() -> None:
    method = read(METHOD, {})
    policy = {str(row.get("id")): row for row in method.get("instruments", [])}
    now = datetime.now(TZ)
    changed_files = 0
    skipped_closed_history = 0

    for path in sorted(WEEKLY.glob("*.json"), reverse=True)[:8]:
        week = read(path, {})
        if not week:
            continue
        if not week_can_still_change(week, now):
            skipped_closed_history += 1
            continue

        changed = False
        for item in week.get("instruments", []):
            changed = apply_item(item, policy.get(str(item.get("instrument_id")), {})) or changed

        marker = {
            "version": "5.0.1",
            "applied_at": now.isoformat(timespec="seconds"),
            "scope": "active_or_future_weeks_new_entries_only",
            "closed_history_rewritten": False,
        }
        previous = week.get("common_validation_gate") if isinstance(week.get("common_validation_gate"), dict) else {}
        if any(previous.get(key) != marker[key] for key in ("version", "scope", "closed_history_rewritten")):
            week["common_validation_gate"] = marker
            changed = True

        if changed:
            write(path, week)
            changed_files += 1

    print(
        f"Common validation gate applied to {changed_files} active/future weekly files; "
        f"closed historical files skipped: {skipped_closed_history}"
    )


if __name__ == "__main__":
    main()
