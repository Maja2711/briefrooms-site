#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-closed integrity gate for weekly paper-trading records.

The audit verifies chronology, direction/risk ordering and P/L arithmetic for all
governed model versions. Known historical records may be explicitly quarantined;
quarantined rows are excluded from public totals/history and are reported here,
but a new unquarantined violation fails publication.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
WEEKLY = ROOT / "data" / "investments" / "weekly"
MANIFEST = ROOT / "data" / "investments" / "closed_week_manifest.json"
REPORT = ROOT / "data" / "investments" / "model_audit.json"
QUARANTINE = ROOT / "data" / "investments" / "public_quarantine.json"
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


def numeric(value: Any) -> Optional[float]:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except Exception:
        return None


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def major_version(value: Any) -> int:
    try:
        return int(str(value or "0").split(".", 1)[0])
    except Exception:
        return 0


def quarantine_index(data: Dict[str, Any]) -> Dict[tuple[str, str], Dict[str, Any]]:
    result: Dict[tuple[str, str], Dict[str, Any]] = {}
    for row in data.get("records", []) if isinstance(data, dict) else []:
        if not isinstance(row, dict) or row.get("public_status") != "withheld":
            continue
        week_id = str(row.get("week_id") or "")
        instrument_id = str(row.get("instrument_id") or "")
        if week_id and instrument_id:
            result[(week_id, instrument_id)] = row
    return result


def expected_metrics(item: Dict[str, Any], method_version: Optional[str] = None) -> Optional[Dict[str, float]]:
    side = str(item.get("direction") or "")
    entry = numeric(item.get("entry_price"))
    exit_price = numeric(item.get("exit_price"))
    if side not in {"long", "short"} or entry is None or entry <= 0 or exit_price is None or exit_price <= 0:
        return None
    move = exit_price - entry if side == "long" else entry - exit_price
    percent = move / entry * 100.0
    instrument_id = str(item.get("instrument_id") or "")
    if instrument_id == "eurusd":
        notional = numeric(item.get("notional_eur")) or 10000.0
        value = move * notional
        units = move / 0.0001
    else:
        notional = numeric(item.get("notional_usd")) or 10000.0
        value = move / entry * notional
        legacy_btc_price_units = (
            instrument_id == "btcusd"
            and method_version is not None
            and (major_version(method_version) < 2 or "reconstructed" in method_version)
        )
        units = move if legacy_btc_price_units else percent if instrument_id == "btcusd" else move
    return {"value": value, "units": units, "percent": percent}


def mismatch(actual: Any, expected: float, tolerance: float) -> bool:
    value = numeric(actual)
    return value is not None and abs(value - expected) > tolerance


def violation(code: str, **details: Any) -> Dict[str, Any]:
    row = {"error": code}
    row.update(details)
    return row


def item_violations(item: Dict[str, Any], method_version: Optional[str] = None) -> list[Dict[str, Any]]:
    issues: list[Dict[str, Any]] = []
    instrument_id = str(item.get("instrument_id") or "")
    side = str(item.get("direction") or "neutral")
    entry = numeric(item.get("entry_price"))
    exit_price = numeric(item.get("exit_price"))
    entry_at = parse(item.get("entry_captured_at"))
    exit_at = parse(item.get("exit_captured_at"))
    status = str(item.get("trade_status") or "")
    plan = item.get("risk_plan") if isinstance(item.get("risk_plan"), dict) else {}
    sl = numeric(plan.get("stop_loss_price"))
    tp = numeric(plan.get("take_profit_price"))

    if side in {"long", "short"} and entry is None:
        issues.append(violation("directional_missing_entry"))
    if entry is not None and entry_at is None:
        issues.append(violation("missing_entry_timestamp"))
    if exit_price is not None and exit_at is None:
        issues.append(violation("missing_exit_timestamp"))
    if entry_at is not None and exit_at is not None and exit_at < entry_at:
        issues.append(violation(
            "exit_before_entry",
            entry_captured_at=item.get("entry_captured_at"),
            exit_captured_at=item.get("exit_captured_at"),
        ))
    if status == "open" and exit_price is not None:
        issues.append(violation("open_has_exit"))
    if status == "closed" and exit_price is None:
        issues.append(violation("closed_missing_exit"))

    if entry is not None and sl is not None and tp is not None:
        if side == "long" and not (sl < entry < tp):
            issues.append(violation("invalid_long_risk_order", entry=entry, stop_loss=sl, take_profit=tp))
        if side == "short" and not (tp < entry < sl):
            issues.append(violation("invalid_short_risk_order", entry=entry, stop_loss=sl, take_profit=tp))

    metrics = expected_metrics(item, method_version)
    if metrics is not None:
        unit_tolerance = 0.15 if instrument_id == "eurusd" else 0.05
        if mismatch(item.get("result_units"), metrics["units"], unit_tolerance):
            issues.append(violation("result_units_mismatch", stored=item.get("result_units"), expected=round(metrics["units"], 8)))
        if mismatch(item.get("result_percent"), metrics["percent"], 0.02):
            issues.append(violation("result_percent_mismatch", stored=item.get("result_percent"), expected=round(metrics["percent"], 8)))
        if mismatch(item.get("result_value"), metrics["value"], 0.05):
            issues.append(violation("result_value_mismatch", stored=item.get("result_value"), expected=round(metrics["value"], 8)))

        price_tolerance = 0.00002 if instrument_id == "eurusd" else 0.05
        reason = str(item.get("exit_reason") or "")
        if reason == "stop_loss" and sl is not None:
            if side == "long" and exit_price is not None and exit_price > sl + price_tolerance:
                issues.append(violation("stop_exit_above_stop", exit=exit_price, stop_loss=sl))
            if side == "short" and exit_price is not None and exit_price < sl - price_tolerance:
                issues.append(violation("stop_exit_below_stop", exit=exit_price, stop_loss=sl))
        if reason == "take_profit" and tp is not None:
            if side == "long" and exit_price is not None and exit_price < tp - price_tolerance:
                issues.append(violation("take_exit_below_target", exit=exit_price, take_profit=tp))
            if side == "short" and exit_price is not None and exit_price > tp + price_tolerance:
                issues.append(violation("take_exit_above_target", exit=exit_price, take_profit=tp))

    return issues


def week_closed(data: Dict[str, Any]) -> bool:
    target = parse((data.get("market_window") or {}).get("exit_target_local"))
    if target is None or datetime.now(TZ) < target:
        return False
    for item in data.get("instruments", []):
        if str(item.get("direction")) in {"long", "short"} and numeric(item.get("entry_price")) is not None and numeric(item.get("exit_price")) is None:
            return False
    return True


def audit_paths(paths: Iterable[Path]) -> Dict[str, Any]:
    manifest = read(MANIFEST, {"sealed": {}})
    sealed = manifest.setdefault("sealed", {})
    quarantine = quarantine_index(read(QUARANTINE, {}))
    errors: list[Dict[str, Any]] = []
    warnings: list[Dict[str, Any]] = []
    quarantined: list[Dict[str, Any]] = []
    checked = 0
    seen_quarantine: set[tuple[str, str]] = set()

    for path in sorted(paths):
        data = read(path, {})
        if not data:
            continue
        checked += 1
        version = str(data.get("method_version") or "legacy")
        major = major_version(version)
        governed = major >= 2
        is_v2 = version.startswith("2.")
        week_id = str(data.get("week_id") or path.stem)

        if is_v2 and not data.get("forecast_hash"):
            errors.append({"week": week_id, "error": "missing_forecast_hash"})

        target = parse((data.get("market_window") or {}).get("entry_target_local"))
        latest = parse((data.get("market_window") or {}).get("entry_latest_local"))
        for item in data.get("instruments", []):
            instrument_id = str(item.get("instrument_id") or "")
            side = str(item.get("direction") or "neutral")
            entry = numeric(item.get("entry_price"))
            captured = parse(item.get("entry_captured_at"))

            if side == "neutral" and entry is not None:
                row = {"week": week_id, "instrument": instrument_id, "error": "neutral_has_entry"}
                (errors if governed else warnings).append(row)

            # Preserve the legacy v2 frozen-window rule. Later continuous-exposure
            # versions legitimately create separately timestamped re-entry legs.
            if is_v2 and side in {"long", "short"} and entry is not None:
                if target is None or captured is None or captured < target or (latest is not None and captured > latest):
                    errors.append({
                        "week": week_id,
                        "instrument": instrument_id,
                        "error": "entry_outside_frozen_window",
                        "captured_at": item.get("entry_captured_at"),
                    })
                if not isinstance(item.get("risk_plan"), dict):
                    errors.append({"week": week_id, "instrument": instrument_id, "error": "missing_frozen_risk_plan"})

            issues = item_violations(item, version)
            if not issues:
                continue
            key = (week_id, instrument_id)
            base = {"week": week_id, "instrument": instrument_id, "violations": issues}
            if key in quarantine:
                seen_quarantine.add(key)
                quarantined.append({**base, "quarantine": quarantine[key]})
            elif governed:
                errors.append(base)
            else:
                warnings.append(base)

        if is_v2 and week_closed(data):
            current_hash = digest(path)
            previous_hash = sealed.get(week_id)
            if previous_hash and previous_hash != current_hash:
                errors.append({"week": week_id, "error": "closed_history_changed", "expected": previous_hash, "actual": current_hash})
            elif not previous_hash:
                sealed[week_id] = current_hash

    for key, row in quarantine.items():
        if key not in seen_quarantine:
            warnings.append({
                "week": key[0],
                "instrument": key[1],
                "warning": "stale_quarantine_entry",
                "quarantine": row,
            })

    now = datetime.now(TZ).isoformat(timespec="seconds")
    manifest["updated_at"] = now
    status = "failed" if errors else "passed_with_quarantine" if quarantined else "passed"
    report = {
        "checked_weeks": checked,
        "errors": errors,
        "quarantined": quarantined,
        "legacy_warnings": warnings,
        "status": status,
        "updated_at": now,
    }
    write(MANIFEST, manifest)
    write(REPORT, report)
    return report


def main() -> None:
    report = audit_paths(WEEKLY.glob("*.json"))
    print(json.dumps(report, ensure_ascii=False))
    if report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
