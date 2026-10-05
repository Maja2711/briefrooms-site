#!/usr/bin/env python3
"""Independent liveness guard for canonical Daily EUR/USD state."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, time
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Warsaw")
STATE = ROOT / "data/investments/eurusd_daily_spot.json"
EXPECTED_ENGINE_VERSION = "eurusd-daily-spot-v1.9.0"
DEFAULT_MAX_AGE_MINUTES = 15


def read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    return parsed.astimezone(TZ)


def fx_market_active(now: datetime) -> bool:
    """Approximate the normal EUR/USD 24x5 window in Europe/Warsaw.

    Sunday evening is treated as active from 23:00 local time and Friday
    closes at 23:00 local time. Saturday is inactive. This guard is only for
    liveness/recovery; it never determines trade direction or execution.
    """
    local = now.astimezone(TZ)
    weekday = local.weekday()  # Monday=0 ... Sunday=6
    clock = local.time().replace(tzinfo=None)

    if weekday == 5:  # Saturday
        return False
    if weekday == 6:  # Sunday
        return clock >= time(23, 0)
    if weekday == 4:  # Friday
        return clock < time(23, 0)
    return True


def evaluate(
    now: Optional[datetime] = None,
    *,
    state_path: Path = STATE,
    max_age_minutes: int = DEFAULT_MAX_AGE_MINUTES,
) -> Dict[str, Any]:
    now = (now or datetime.now(TZ)).astimezone(TZ)
    max_age_minutes = max(1, int(max_age_minutes))
    result: Dict[str, Any] = {
        "checked_at": now.isoformat(timespec="seconds"),
        "market_active": fx_market_active(now),
        "max_age_minutes": max_age_minutes,
        "expected_engine_version": EXPECTED_ENGINE_VERSION,
        "status": "healthy",
        "reasons": [],
        "auto_dispatch_recovery": True,
    }

    if not result["market_active"]:
        result["status"] = "inactive_window"
        return result

    state = read_json(state_path)
    if not state:
        result["status"] = "stale"
        result["reasons"].append("daily_state_missing_or_invalid")
        return result

    timestamp = parse_dt(state.get("timestamp"))
    engine_version = str(state.get("engine_version") or "")
    result.update({
        "instrument": state.get("instrument"),
        "direction": state.get("direction"),
        "trade_status": state.get("status"),
        "engine_version": engine_version,
        "state_timestamp": timestamp.isoformat(timespec="seconds") if timestamp else None,
    })

    if engine_version != EXPECTED_ENGINE_VERSION:
        result["reasons"].append("daily_engine_version_mismatch")
    if timestamp is None:
        result["reasons"].append("daily_state_timestamp_missing_or_invalid")
    else:
        age_minutes = (now - timestamp).total_seconds() / 60.0
        result["state_age_minutes"] = round(age_minutes, 2)
        if age_minutes < -2.0:
            result["reasons"].append("daily_state_timestamp_in_future")
        elif age_minutes > max_age_minutes:
            result["reasons"].append("daily_state_stale")

    if result["reasons"]:
        result["status"] = "stale"
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["check", "print"], default="check")
    parser.add_argument("--max-age-minutes", type=int, default=DEFAULT_MAX_AGE_MINUTES)
    args = parser.parse_args()

    result = evaluate(max_age_minutes=args.max_age_minutes)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.mode == "check" and result.get("status") == "stale":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
