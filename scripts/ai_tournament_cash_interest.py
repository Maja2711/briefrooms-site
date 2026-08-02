#!/usr/bin/env python3
"""Deterministic daily IORB accrual for AI Tournament cash balances."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "data" / "ai_tournament" / "cash_rate_policy.json"


class CashInterestError(ValueError):
    """Raised when the cash-rate policy or an accrual request is invalid."""


@dataclass(frozen=True)
class AccrualResult:
    opening_balance: float
    closing_balance: float
    interest_earned: float
    calendar_days: int
    segments: tuple[dict[str, Any], ...]


def load_policy(path: Path = DEFAULT_POLICY) -> dict[str, Any]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    validate_policy(policy)
    return policy


def validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("schema_version") != "ai-tournament-cash-rate-policy-v1":
        raise CashInterestError("unsupported cash-rate policy schema")
    if policy.get("benchmark") != "IORB":
        raise CashInterestError("cash benchmark must be IORB")
    if policy.get("day_count") != "ACT/365" or policy.get("compounding") != "daily":
        raise CashInterestError("cash must use daily ACT/365 compounding")
    schedule = policy.get("rate_schedule")
    if not isinstance(schedule, list) or not schedule:
        raise CashInterestError("rate_schedule must contain at least one entry")
    dates: list[date] = []
    for row in schedule:
        if not isinstance(row, dict):
            raise CashInterestError("invalid rate schedule entry")
        effective = date.fromisoformat(str(row.get("effective_date")))
        rate = float(row.get("annual_rate_pct"))
        if rate < 0 or rate > 100:
            raise CashInterestError("IORB rate is outside a reasonable range")
        dates.append(effective)
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise CashInterestError("rate_schedule must be strictly chronological")


def _rate_for_day(day: date, schedule: list[dict[str, Any]]) -> float:
    applicable: float | None = None
    for row in schedule:
        effective = date.fromisoformat(str(row["effective_date"]))
        if effective <= day:
            applicable = float(row["annual_rate_pct"])
        else:
            break
    if applicable is None:
        raise CashInterestError(f"no IORB rate available for {day.isoformat()}")
    return applicable


def accrue_cash(
    opening_balance: float,
    from_date: str | date,
    to_date: str | date,
    policy: dict[str, Any] | None = None,
) -> AccrualResult:
    """Accrue cash for each calendar day in [from_date, to_date)."""
    if opening_balance < 0:
        raise CashInterestError("opening balance cannot be negative")
    start = date.fromisoformat(from_date) if isinstance(from_date, str) else from_date
    end = date.fromisoformat(to_date) if isinstance(to_date, str) else to_date
    if end < start:
        raise CashInterestError("to_date cannot be earlier than from_date")
    active_policy = policy or load_policy()
    validate_policy(active_policy)
    schedule = active_policy["rate_schedule"]

    balance = float(opening_balance)
    day = start
    segments: list[dict[str, Any]] = []
    current_segment: dict[str, Any] | None = None
    while day < end:
        annual_rate_pct = _rate_for_day(day, schedule)
        daily_rate = annual_rate_pct / 100.0 / 365.0
        balance *= 1.0 + daily_rate
        if current_segment and current_segment["annual_rate_pct"] == annual_rate_pct:
            current_segment["days"] += 1
            current_segment["to_date_exclusive"] = (day + timedelta(days=1)).isoformat()
        else:
            current_segment = {
                "from_date": day.isoformat(),
                "to_date_exclusive": (day + timedelta(days=1)).isoformat(),
                "days": 1,
                "annual_rate_pct": annual_rate_pct,
            }
            segments.append(current_segment)
        day += timedelta(days=1)

    return AccrualResult(
        opening_balance=round(float(opening_balance), 12),
        closing_balance=round(balance, 12),
        interest_earned=round(balance - float(opening_balance), 12),
        calendar_days=(end - start).days,
        segments=tuple(segments),
    )


if __name__ == "__main__":
    result = accrue_cash(1000.0, "2026-08-03", "2026-11-03")
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2, default=list))
