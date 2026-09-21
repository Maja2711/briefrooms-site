#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Runtime-aware wrapper for the weekly model integrity audit.

The weekly ledger separates a frozen directional forecast from execution:
`planned` is a valid forecast-only state, `pending` is a frozen execution
decision waiting for the first eligible bar, and only executed states require
an entry price. Unresolved planned/pending states become invalid after the
governed position deadline. The wrapper adds that time-aware invariant while
the base audit validates the lifecycle contract itself.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import audit_weekly_model as base

_REAL_READ = base.read
_REAL_ITEM_VIOLATIONS = base.item_violations


def planned_entry_is_valid(item: Dict[str, Any], deadline: Optional[datetime], now: datetime) -> bool:
    """Return True for a forecast/pending state that has not claimed a fill."""
    if str(item.get("direction") or "").lower() not in base.DIRECTIONAL:
        return False
    if base.numeric(item.get("entry_price")) is not None:
        return False
    status = base.normalized_trade_status(item)
    if status not in {"planned", "pending"}:
        return False
    if deadline is not None and now >= deadline:
        return False
    return not base.pending_entry_contract_violations(item)


def _read_with_entry_deadline(path: Path, default: Any) -> Any:
    data = _REAL_READ(path, default)
    if not isinstance(data, dict) or path.parent != base.WEEKLY:
        return data
    deadline = (data.get("market_window") or {}).get("exit_target_local")
    for item in data.get("instruments", []) if isinstance(data.get("instruments"), list) else []:
        if isinstance(item, dict):
            item["_audit_position_deadline_local"] = deadline
    return data


def _runtime_item_violations(item: Dict[str, Any], method_version: Optional[str] = None) -> list[Dict[str, Any]]:
    issues = _REAL_ITEM_VIOLATIONS(item, method_version)
    deadline = base.parse(item.get("_audit_position_deadline_local"))
    now = datetime.now(base.TZ)
    status = base.normalized_trade_status(item)
    if (
        status in {"planned", "pending"}
        and base.numeric(item.get("entry_price")) is None
        and deadline is not None
        and now >= deadline
        and not any(row.get("error") == "unresolved_entry_after_week_close" for row in issues)
    ):
        issues.append(base.violation(
            "unresolved_entry_after_week_close",
            deadline=deadline.isoformat(),
        ))
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
