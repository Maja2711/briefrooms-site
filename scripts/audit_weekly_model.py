#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Integrity gate for weekly investment files.

Legacy weeks are reported but not rewritten. Version 2 weeks fail the workflow
when a neutral scenario has an entry, an entry is outside the frozen window, a
forecast hash is missing, or a closed week changes after it was sealed.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
WEEKLY = ROOT / "data" / "investments" / "weekly"
MANIFEST = ROOT / "data" / "investments" / "closed_week_manifest.json"
REPORT = ROOT / "data" / "investments" / "model_audit.json"
TZ = ZoneInfo("Europe/Warsaw")


def read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def parse(value: Any) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ)
    except Exception:
        return None


def number(value: Any) -> bool:
    try:
        float(value)
        return True
    except Exception:
        return False


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def week_closed(data: Dict[str, Any]) -> bool:
    target = parse((data.get("market_window") or {}).get("exit_target_local"))
    if target is None or datetime.now(TZ) < target:
        return False
    for item in data.get("instruments", []):
        if str(item.get("direction")) in {"long", "short"} and number(item.get("entry_price")) and not number(item.get("exit_price")):
            return False
    return True


def main() -> None:
    manifest = read(MANIFEST, {"sealed": {}})
    sealed = manifest.setdefault("sealed", {})
    errors = []
    warnings = []
    checked = 0

    for path in sorted(WEEKLY.glob("*.json")):
        data = read(path, {})
        if not data:
            continue
        checked += 1
        version = str(data.get("method_version") or "legacy")
        week_id = str(data.get("week_id") or path.stem)
        is_v2 = version.startswith("2.")

        if is_v2 and not data.get("forecast_hash"):
            errors.append({"week": week_id, "error": "missing_forecast_hash"})

        target = parse((data.get("market_window") or {}).get("entry_target_local"))
        latest = parse((data.get("market_window") or {}).get("entry_latest_local"))
        for item in data.get("instruments", []):
            inst = item.get("instrument_id")
            side = str(item.get("direction") or "neutral")
            entry = item.get("entry_price")
            captured = parse(item.get("entry_captured_at"))
            if side == "neutral" and number(entry):
                row = {"week": week_id, "instrument": inst, "error": "neutral_has_entry"}
                (errors if is_v2 else warnings).append(row)
            if is_v2 and side in {"long", "short"} and number(entry):
                if target is None or captured is None or captured < target or (latest is not None and captured > latest):
                    errors.append({"week": week_id, "instrument": inst, "error": "entry_outside_frozen_window", "captured_at": item.get("entry_captured_at")})
                if not isinstance(item.get("risk_plan"), dict):
                    errors.append({"week": week_id, "instrument": inst, "error": "missing_frozen_risk_plan"})

        if is_v2 and week_closed(data):
            current_hash = digest(path)
            previous_hash = sealed.get(week_id)
            if previous_hash and previous_hash != current_hash:
                errors.append({"week": week_id, "error": "closed_history_changed", "expected": previous_hash, "actual": current_hash})
            elif not previous_hash:
                sealed[week_id] = current_hash

    manifest["updated_at"] = datetime.now(TZ).isoformat(timespec="seconds")
    report = {"checked_weeks": checked, "errors": errors, "legacy_warnings": warnings, "status": "failed" if errors else "passed", "updated_at": datetime.now(TZ).isoformat(timespec="seconds")}
    write(MANIFEST, manifest)
    write(REPORT, report)
    print(json.dumps(report, ensure_ascii=False))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
