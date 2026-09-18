#!/usr/bin/env python3
"""BRACE daily-analysis liveness contract and watchdog helper.

The BRACE control plane expects one completed daily analysis for every weekday
22:30 UTC schedule slot.  GitHub cron is treated as a trigger, not as the source
of truth: this module derives the latest slot that should already have completed
and marks the analysis overdue when analysis.json predates that slot.

A 90-minute grace period prevents false alerts for delayed GitHub Actions jobs.
Weekend handling is calendar-safe: before Monday's slot, Friday remains the
latest required analysis.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_PATH = ROOT / "data" / "portfolio10k" / "analysis.json"
SCHEDULE_UTC = time(hour=22, minute=30, tzinfo=timezone.utc)
GRACE_MINUTES = 90


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def latest_required_slot(
    now: datetime,
    *,
    grace_minutes: int = GRACE_MINUTES,
) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    grace = timedelta(minutes=max(0, int(grace_minutes)))
    for days_back in range(0, 10):
        day = (now - timedelta(days=days_back)).date()
        if day.weekday() >= 5:
            continue
        slot = datetime.combine(day, SCHEDULE_UTC)
        if now >= slot + grace:
            return slot
    raise RuntimeError("Could not resolve a required BRACE analysis slot")


def assess_analysis_liveness(
    analysis: Mapping[str, Any] | None,
    now: datetime | None = None,
    *,
    grace_minutes: int = GRACE_MINUTES,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    required = latest_required_slot(now, grace_minutes=grace_minutes)
    generated = parse_timestamp((analysis or {}).get("generated_at"))
    overdue = generated is None or generated < required
    age_hours = (
        round(max(0.0, (now - generated).total_seconds() / 3600.0), 3)
        if generated is not None
        else None
    )
    overdue_hours = (
        round(max(0.0, (now - required).total_seconds() / 3600.0), 3)
        if overdue
        else 0.0
    )
    return {
        "status": "ANALYSIS_OVERDUE" if overdue else "CURRENT",
        "overdue": overdue,
        "analysis_generated_at": generated.isoformat(timespec="seconds") if generated else None,
        "analysis_age_hours": age_hours,
        "latest_required_slot": required.isoformat(timespec="seconds"),
        "grace_minutes": int(grace_minutes),
        "overdue_hours": overdue_hours,
    }


def read_analysis(path: Path = ANALYSIS_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", type=Path, default=ANALYSIS_PATH)
    parser.add_argument("--now", help="ISO-8601 UTC timestamp for tests/manual diagnostics")
    parser.add_argument("--grace-minutes", type=int, default=GRACE_MINUTES)
    parser.add_argument("--github-output", action="store_true")
    parser.add_argument("--assert-current", action="store_true")
    args = parser.parse_args()

    now = parse_timestamp(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        raise SystemExit("Invalid --now timestamp")
    result = assess_analysis_liveness(
        read_analysis(args.analysis),
        now,
        grace_minutes=args.grace_minutes,
    )

    if args.github_output:
        for key, value in (
            ("overdue", str(bool(result["overdue"])).lower()),
            ("status", result["status"]),
            ("analysis_generated_at", result["analysis_generated_at"] or ""),
            ("latest_required_slot", result["latest_required_slot"]),
            ("analysis_age_hours", "" if result["analysis_age_hours"] is None else result["analysis_age_hours"]),
        ):
            print(f"{key}={value}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.assert_current and result["overdue"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
