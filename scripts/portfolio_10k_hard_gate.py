#!/usr/bin/env python3
"""Minimal fail-closed publication gate for Portfolio 10K.

This gate intentionally checks only invariants that must hold before a fresh MTM
can be published. Broader schema/legacy audits run separately and must never
freeze a correct valuation because their assumptions lag an executed paper trade.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PLN_PATH = ROOT / "data" / "investments" / "portfolio_10k.json"
USD_PATH = ROOT / "data" / "investments" / "portfolio_10k_usd.json"
GUARDIAN_PATH = ROOT / "data" / "portfolio10k" / "guardian_state.json"
MAX_GENERATED_AGE_MINUTES = 30.0
TOLERANCE = 0.08


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_time(value: Any) -> datetime | None:
    raw = str(value or "").strip().replace("Z", "+00:00")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def assert_close(label: str, actual: Any, expected: float) -> None:
    number = finite(actual)
    if number is None or abs(number - expected) > TOLERANCE:
        raise AssertionError(f"{label}: {actual!r} != {expected:.2f}")


def validate_book(payload: dict[str, Any], currency: str) -> dict[str, float]:
    suffix = "usd" if currency == "USD" else "pln"
    start_key = f"starting_capital_{suffix}"
    cash_key = f"cash_{suffix}"
    total_key = f"total_value_{suffix}"
    return_key = f"total_return_{suffix}"
    position_value_key = f"current_value_{suffix}"

    start = finite(payload.get(start_key))
    cash = finite(payload.get(cash_key))
    declared_total = finite(payload.get(total_key))
    if start is None or start <= 0:
        raise AssertionError(f"{start_key}: invalid")
    if cash is None or cash < 0:
        raise AssertionError(f"{cash_key}: invalid")
    if declared_total is None or declared_total <= 0:
        raise AssertionError(f"{total_key}: invalid")

    updated = parse_time(payload.get("last_updated_at"))
    if updated is None:
        raise AssertionError("last_updated_at: missing or invalid")
    age_minutes = (datetime.now(timezone.utc) - updated).total_seconds() / 60.0
    if age_minutes < -5 or age_minutes > MAX_GENERATED_AGE_MINUTES:
        raise AssertionError(f"last_updated_at age {age_minutes:.1f} min exceeds publication SLA")

    positions = [p for p in payload.get("positions", []) if p.get("status") == "active"]
    if not positions:
        raise AssertionError("positions: no active positions")
    ids = [str(p.get("id") or p.get("instrument_id") or "") for p in positions]
    if "" in ids or len(ids) != len(set(ids)):
        raise AssertionError("positions: duplicate or empty instrument id")

    position_total = 0.0
    for position in positions:
        identifier = str(position.get("id") or position.get("instrument_id") or "?")
        quantity = finite(position.get("quantity"))
        value = finite(position.get(position_value_key))
        if quantity is None or quantity <= 0:
            raise AssertionError(f"position[{identifier}].quantity: invalid")
        if value is None or value < 0:
            raise AssertionError(f"position[{identifier}].{position_value_key}: invalid")
        position_total += value

    expected_total = round(position_total + cash, 2)
    assert_close(total_key, declared_total, expected_total)
    if payload.get(return_key) is not None:
        assert_close(return_key, payload.get(return_key), expected_total - start)

    return {
        "start": start,
        "cash": cash,
        "position_total": round(position_total, 2),
        "total": expected_total,
        "age_minutes": round(age_minutes, 3),
    }


def validate_guardian(payload: dict[str, Any]) -> None:
    health = payload.get("health") or {}
    status = str(health.get("status") or "")
    if status not in {"ACTIVE", "DEGRADED"}:
        raise AssertionError(f"guardian.health.status: {status!r}")
    fatal = {
        "NON_POSITIVE_PORTFOLIO_VALUE",
        "NEGATIVE_CASH",
        "ACCOUNTING_INVARIANT_FAILED",
        "DUPLICATE_POSITION_ID",
        "INVALID_POSITION_VALUE",
    }
    errors = {str(item) for item in health.get("errors", []) or []}
    overlap = sorted(fatal.intersection(errors))
    if overlap:
        raise AssertionError(f"guardian fatal errors: {', '.join(overlap)}")
    if payload.get("paper_only") is not True or payload.get("real_broker_connected") is not False:
        raise AssertionError("guardian paper-only contract violated")


def main() -> None:
    pln = validate_book(load(PLN_PATH), "PLN")
    usd = validate_book(load(USD_PATH), "USD")
    validate_guardian(load(GUARDIAN_PATH))
    print(json.dumps({"status": "PASS", "PLN": pln, "USD": usd}, ensure_ascii=False))


if __name__ == "__main__":
    main()
