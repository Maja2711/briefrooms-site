#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Runtime-aware wrapper for the weekly model integrity audit.

A governed directional record may legitimately be `planned` with no entry price
while the live entry window is still open. The base fail-closed audit is kept
unchanged for every other case and becomes strict again immediately after
`entry_latest_local`.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import audit_weekly_model as base

_REAL_READ = base.read
_REAL_ITEM_VIOLATIONS = base.item_violations


def planned_entry_is_valid(item: Dict[str, Any], latest: Optional[datetime], now: datetime) -> bool:
    """True only for a genuine governed pending entry before its hard deadline."""
    if str(item.get("direction") or "") not in {"long", "short"}:
        return False
    if base.numeric(item.get("entry_price")) is not None:
        return False
    if str(item.get("trade_status") or "") != "planned":
        return False
    pending = item.get("pending_entry_decision")
    if not isinstance(pending, dict) or not isinstance(pending.get("decision"), dict):
        return False
    decided_at = base.parse(pending.get("decided_at"))
    entry_not_before = base.parse(pending.get("entry_not_before"))
    if decided_at is None or entry_not_before is None or latest is None:
        return False
    if entry_not_before < decided_at:
        return False
    return now <= latest


def _read_with_entry_deadline(path: Path, default: Any) -> Any:
    data = _REAL_READ(path, default)
    if not isinstance(data, dict) or path.parent != base.WEEKLY:
        return data
    latest = (data.get("market_window") or {}).get("entry_latest_local")
    for item in data.get("instruments", []) if isinstance(data.get("instruments"), list) else []:
        if isinstance(item, dict):
            item["_audit_entry_latest_local"] = latest
    return data


def _runtime_item_violations(item: Dict[str, Any], method_version: Optional[str] = None) -> list[Dict[str, Any]]:
    issues = _REAL_ITEM_VIOLATIONS(item, method_version)
    latest = base.parse(item.get("_audit_entry_latest_local"))
    now = datetime.now(base.TZ)
    if planned_entry_is_valid(item, latest, now):
        issues = [row for row in issues if row.get("error") != "directional_missing_entry"]
    return issues


def main() -> None:
    base.read = _read_with_entry_deadline
    base.item_violations = _runtime_item_violations
    report = base.audit_paths(base.WEEKLY.glob("*.json"))
    print(json.dumps(report, ensure_ascii=False))
    if report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
