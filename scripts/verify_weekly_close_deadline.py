#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail closed when a governed weekly position reaches its saved deadline.

This verifier is intentionally independent from rendering and broad historical
audits. It checks the current lifecycle plus the same eight-week recovery window
used by the settlement engine. A placeholder exit value is not a close.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo


TZ = ZoneInfo("Europe/Warsaw")
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WEEKLY_DIR = ROOT / "data" / "investments" / "weekly"
SCAN_LIMIT = 8
DIRECTIONAL = {"long", "short"}
OPEN_STATUSES = {
    "open",
    "opened",
    "planned",
    "pending",
    "in_progress",
    "week_in_progress",
    "w_trakcie",
}


def finite_number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


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


def week_id_for(now: datetime) -> str:
    local = now.astimezone(TZ)
    iso = local.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def deadline_from_week_id(week_id: str) -> Optional[datetime]:
    try:
        year_text, week_text = week_id.split("-W", 1)
        friday = datetime.fromisocalendar(int(year_text), int(week_text), 5)
    except (TypeError, ValueError):
        return None
    return friday.replace(hour=22, minute=0, second=0, microsecond=0, tzinfo=TZ)


def saved_deadline(week: Dict[str, Any]) -> Optional[datetime]:
    explicit = parse_dt((week.get("market_window") or {}).get("exit_target_local"))
    if explicit is not None:
        return explicit
    return deadline_from_week_id(str(week.get("week_id") or ""))


def pending_direction(item: Dict[str, Any]) -> Optional[str]:
    pending = item.get("pending_entry_decision")
    if not isinstance(pending, dict):
        return None
    decision = pending.get("decision")
    if not isinstance(decision, dict):
        return None
    direction = str(decision.get("direction") or "").lower()
    return direction if direction in DIRECTIONAL else None


def lifecycle_errors(week: Dict[str, Any], now: datetime) -> List[str]:
    week_id = str(week.get("week_id") or "unknown-week")
    deadline = saved_deadline(week)
    if deadline is None:
        return [f"{week_id}: missing or invalid Friday 22:00 close deadline"]
    if now.astimezone(TZ) < deadline:
        return []

    errors: List[str] = []
    for item in week.get("instruments") or []:
        if not isinstance(item, dict):
            errors.append(f"{week_id}: malformed instrument row")
            continue
        instrument_id = str(item.get("instrument_id") or "unknown-instrument")
        prefix = f"{week_id}/{instrument_id}"
        direction = str(item.get("direction") or "").lower()
        entry = finite_number(item.get("entry_price"))
        exit_price = finite_number(item.get("exit_price"))
        status = str(item.get("trade_status") or "").strip().lower().replace(" ", "_")
        exposure_status = (
            str(item.get("continuous_exposure_status") or "")
            .strip()
            .lower()
            .replace(" ", "_")
        )

        if direction in DIRECTIONAL and entry is not None and exit_price is None:
            errors.append(f"{prefix}: directional position has no numeric exit after {deadline.isoformat()}")
        if exit_price is not None and parse_dt(item.get("exit_captured_at")) is None:
            errors.append(f"{prefix}: numeric exit has no valid capture timestamp")
        if pending_direction(item) is not None:
            errors.append(f"{prefix}: pending directional entry remains after the close deadline")
        if status in OPEN_STATUSES:
            errors.append(f"{prefix}: trade_status remains {status!r} after the close deadline")
        if item.get("continuous_exposure_active") is True:
            errors.append(f"{prefix}: continuous_exposure_active remains true after the close deadline")
        if exposure_status in OPEN_STATUSES:
            errors.append(
                f"{prefix}: continuous_exposure_status remains {exposure_status!r} after the close deadline"
            )

    return errors


def load_weeks(paths: Iterable[Path]) -> List[Dict[str, Any]]:
    weeks: List[Dict[str, Any]] = []
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            weeks.append({"week_id": path.stem, "_load_error": str(exc)})
            continue
        if isinstance(value, dict):
            weeks.append(value)
        else:
            weeks.append({"week_id": path.stem, "_load_error": "root is not an object"})
    return weeks


def verify_directory(weekly_dir: Path, now: datetime) -> List[str]:
    paths = sorted(weekly_dir.glob("*.json"))[-SCAN_LIMIT:]
    current_week_id = week_id_for(now)
    current_path = weekly_dir / f"{current_week_id}.json"
    current_deadline = deadline_from_week_id(current_week_id)
    errors: List[str] = []

    if current_deadline is not None and now.astimezone(TZ) >= current_deadline and not current_path.exists():
        errors.append(f"{current_week_id}: current weekly ledger is missing after the close deadline")

    for week in load_weeks(paths):
        if week.get("_load_error"):
            errors.append(f"{week.get('week_id')}: unreadable weekly ledger: {week['_load_error']}")
            continue
        errors.extend(lifecycle_errors(week, now))
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--weekly-dir", type=Path, default=DEFAULT_WEEKLY_DIR)
    parser.add_argument("--now", help="ISO timestamp used by deterministic tests")
    args = parser.parse_args()

    now = parse_dt(args.now) if args.now else datetime.now(TZ)
    if now is None:
        raise SystemExit("--now must be a valid ISO timestamp")

    errors = verify_directory(args.weekly_dir, now)
    if errors:
        raise SystemExit("\n".join(dict.fromkeys(errors)))
    print("Weekly close deadline invariant passed.")


if __name__ == "__main__":
    main()
