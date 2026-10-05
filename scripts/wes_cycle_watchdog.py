#!/usr/bin/env python3
"""Independent liveness guard for the persisted Weekly Engine Star cycle."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Warsaw")
POLICY = ROOT / "data/investments/multi_instrument_exposure_policy.json"
WEEKLY_DIR = ROOT / "data/investments/weekly"
WES_REPORT = ROOT / "data/investments/wes_report.json"
EXPECTED_VERSION = "WES-1.3.1"


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


def week_id(now: datetime) -> str:
    iso = now.astimezone(TZ).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def evaluate(
    now: Optional[datetime] = None,
    *,
    policy_path: Path = POLICY,
    weekly_path: Optional[Path] = None,
    report_path: Path = WES_REPORT,
) -> Dict[str, Any]:
    now = (now or datetime.now(TZ)).astimezone(TZ)
    policy = read_json(policy_path)
    cfg = policy.get("wes_cycle_watchdog") if isinstance(policy.get("wes_cycle_watchdog"), dict) else {}
    max_age_minutes = max(1, int(cfg.get("max_age_minutes") or 25))
    result: Dict[str, Any] = {
        "checked_at": now.isoformat(timespec="seconds"),
        "week_id": week_id(now),
        "max_age_minutes": max_age_minutes,
        "status": "healthy",
        "reasons": [],
    }
    if cfg.get("enabled", True) is not True:
        result.update(status="disabled")
        return result

    path = weekly_path or (WEEKLY_DIR / f"{week_id(now)}.json")
    week = read_json(path)
    if not week:
        result.update(status="stale", reasons=["current_week_state_missing"])
        return result

    market_window = week.get("market_window") if isinstance(week.get("market_window"), dict) else {}
    start = parse_dt(market_window.get("entry_target_local"))
    end = parse_dt(market_window.get("exit_target_local"))
    if cfg.get("active_from_week_entry", True) and start is not None and now < start:
        result.update(status="inactive_window", reason="before_week_entry")
        return result
    if cfg.get("active_until_week_exit", True) and end is not None and now > end:
        result.update(status="inactive_window", reason="after_week_exit")
        return result

    wes_state = week.get("wes") if isinstance(week.get("wes"), dict) else {}
    preflight_at = parse_dt(wes_state.get("last_preflight_at"))
    report = read_json(report_path)
    postflight_at = parse_dt(report.get("checked_at")) if report.get("mode") == "postflight" and report.get("status") == "completed" else None

    if str(wes_state.get("version") or "") != EXPECTED_VERSION:
        result["reasons"].append("wes_state_version_mismatch")
    if str(report.get("version") or "") != EXPECTED_VERSION:
        result["reasons"].append("wes_report_version_mismatch")
    if preflight_at is None:
        result["reasons"].append("wes_preflight_timestamp_missing")
    if postflight_at is None:
        result["reasons"].append("wes_postflight_timestamp_missing")

    if preflight_at is not None:
        preflight_age = max(0.0, (now - preflight_at).total_seconds() / 60.0)
        result["preflight_at"] = preflight_at.isoformat(timespec="seconds")
        result["preflight_age_minutes"] = round(preflight_age, 2)
        if preflight_age > max_age_minutes:
            result["reasons"].append("wes_preflight_stale")
    if postflight_at is not None:
        postflight_age = max(0.0, (now - postflight_at).total_seconds() / 60.0)
        result["postflight_at"] = postflight_at.isoformat(timespec="seconds")
        result["postflight_age_minutes"] = round(postflight_age, 2)
        if postflight_age > max_age_minutes:
            result["reasons"].append("wes_postflight_stale")

    if result["reasons"]:
        result["status"] = "stale"
    result["auto_dispatch_recovery"] = bool(cfg.get("auto_dispatch_recovery", True))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["check", "print"], default="check")
    args = parser.parse_args()
    result = evaluate()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.mode == "check" and result.get("status") == "stale":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
