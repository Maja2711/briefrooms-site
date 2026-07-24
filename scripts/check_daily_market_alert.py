#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "investments" / "daily_market_alert.json"
NY = ZoneInfo("America/New_York")
UTC = timezone.utc


def now_utc() -> datetime:
    override = os.getenv("BR_ALERT_NOW", "").strip()
    if override:
        return datetime.fromisoformat(override.replace("Z", "+00:00")).astimezone(UTC)
    return datetime.now(UTC)


def load_payload(path: Path = OUT) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def session_targets(moment: datetime) -> tuple[datetime, datetime] | None:
    local_date = moment.astimezone(NY).date()
    schedule = mcal.get_calendar("NYSE").schedule(
        start_date=local_date, end_date=local_date
    )
    if schedule.empty:
        return None
    market_open = schedule.iloc[0]["market_open"].to_pydatetime().astimezone(UTC)
    market_close = schedule.iloc[0]["market_close"].to_pydatetime().astimezone(UTC)
    return market_open + timedelta(minutes=30), market_close - timedelta(minutes=30)


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def expected_mode(moment: datetime, requested: str, grace_minutes: int = 45) -> str:
    if requested in {"open", "preclose"}:
        return requested
    targets = session_targets(moment)
    if not targets:
        return "skip"
    open_target, preclose_target = targets
    grace = timedelta(minutes=grace_minutes)
    if moment >= preclose_target + grace:
        return "preclose"
    if moment >= open_target + grace:
        return "open"
    return "skip"


def slot_complete(payload: dict[str, Any], mode: str, moment: datetime) -> tuple[bool, str]:
    targets = session_targets(moment)
    if mode == "skip" or not targets:
        return True, "No NYSE slot is due"
    open_target, preclose_target = targets
    expected_date = moment.astimezone(NY).date().isoformat()
    if payload.get("schema_version") != "2.0":
        return False, "schema_version 2.0 is missing"
    if payload.get("session_date") != expected_date:
        return False, f"session_date is not {expected_date}"
    instruments = payload.get("instruments")
    if not isinstance(instruments, list) or {item.get("id") for item in instruments} != {"sp500", "brent", "us10y"}:
        return False, "the three governed instruments are missing"
    if mode == "open":
        published = parse_time(payload.get("updated_at"))
        if not published or published < open_target - timedelta(minutes=5):
            return False, "opening-slot publication timestamp is missing or stale"
        return True, "opening slot exists"
    if payload.get("edition") == "preclose":
        published = parse_time(payload.get("updated_at"))
        if published and published >= preclose_target - timedelta(minutes=5):
            return True, "material pre-close edition exists"
    checked = parse_time((payload.get("preclose_check") or {}).get("checked_at"))
    if checked and checked >= preclose_target - timedelta(minutes=5):
        return True, "pre-close no-change check exists"
    return False, "pre-close edition or no-change check is missing"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("auto", "open", "preclose"), default="auto")
    parser.add_argument("--grace-minutes", type=int, default=45)
    args = parser.parse_args()
    moment = now_utc()
    mode = expected_mode(moment, args.mode, args.grace_minutes)
    complete, reason = slot_complete(load_payload(), mode, moment)
    print(f"mode={mode}; complete={str(complete).lower()}; reason={reason}")
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
