#!/usr/bin/env python3
"""Fail-closed freshness guard for the public Weekly Trading view.

The public page must never silently substitute an older ISO week when the
expected weekly decision artifact is missing. A real generated decision and an
explicit MISSING_DECISION marker are two different states; NO_TRADE is only
valid when it comes from a real generated forecast.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WEEKLY_DIR = ROOT / "data" / "investments" / "weekly"
TZ = ZoneInfo("Europe/Warsaw")
MISSING_DECISION = "MISSING_DECISION"


def parse_now(value: Optional[str] = None) -> datetime:
    if not value:
        return datetime.now(TZ)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    return parsed.astimezone(TZ)


def week_id(value: datetime) -> str:
    iso = value.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def public_target_date(now: datetime) -> datetime:
    """Return the week the public page should select by default.

    Monday-Saturday show the current ISO week. On Sunday the next trading week
    becomes the public target because the governed Sunday forecast is due then.
    This deliberately avoids the old Friday/Saturday jump to a not-yet-generated
    week.
    """
    local = now.astimezone(TZ)
    return local + timedelta(days=1) if local.weekday() == 6 else local


def public_target_week(now: datetime) -> str:
    return week_id(public_target_date(now))


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def is_explicit_missing(data: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(data, dict):
        return False
    return (
        data.get("decision_state") == MISSING_DECISION
        or data.get("public_status") == MISSING_DECISION
        or data.get("forecast_status") == "missing_decision"
    )


def is_real_decision(data: Optional[Dict[str, Any]], expected_week: str) -> bool:
    """A generated neutral/NO_TRADE forecast is still a real decision.

    Freshness is proven by forecast metadata plus an instrument array, not by
    whether any instrument is directional. This prevents MISSING_DECISION from
    being conflated with a legitimate model NO_TRADE.
    """
    if not isinstance(data, dict) or is_explicit_missing(data):
        return False
    if str(data.get("week_id") or "") != expected_week:
        return False
    if not data.get("forecast_created_at"):
        return False
    instruments = data.get("instruments")
    return isinstance(instruments, list) and len(instruments) > 0


def missing_payload(expected_week: str, now: datetime, reason: str) -> Dict[str, Any]:
    target = public_target_date(now)
    monday = target - timedelta(days=target.weekday())
    friday = monday + timedelta(days=4)
    return {
        "week_id": expected_week,
        "decision_state": MISSING_DECISION,
        "public_status": MISSING_DECISION,
        "forecast_status": "missing_decision",
        "model_status": "weekly_decision_missing_fail_closed",
        "missing_decision": True,
        "timezone": "Europe/Warsaw",
        "expected_week_start": monday.date().isoformat(),
        "expected_week_end": friday.date().isoformat(),
        "missing_detected_at": now.isoformat(timespec="seconds"),
        "missing_reason": reason,
        "generated_by": "weekly_freshness_guard",
        "public_message_pl": "Brak wygenerowanej decyzji dla tego tygodnia. System nie zastępuje jej danymi z poprzedniego tygodnia.",
        "public_message_en": "The decision for this week has not been generated. The system does not substitute data from the previous week.",
        "instruments": [],
    }


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def state(weekly_dir: Path, now: datetime) -> Dict[str, Any]:
    expected = public_target_week(now)
    path = weekly_dir / f"{expected}.json"
    if not path.exists():
        return {"status": "ABSENT", "week_id": expected, "path": path}
    data = read_json(path)
    if data is None:
        return {"status": "INVALID_JSON", "week_id": expected, "path": path}
    if is_real_decision(data, expected):
        return {"status": "READY", "week_id": expected, "path": path, "data": data}
    if is_explicit_missing(data) and str(data.get("week_id") or "") == expected:
        return {"status": MISSING_DECISION, "week_id": expected, "path": path, "data": data}
    return {"status": "INDETERMINATE", "week_id": expected, "path": path, "data": data}


def ensure_formal_state(weekly_dir: Path, now: datetime) -> Dict[str, Any]:
    current = state(weekly_dir, now)
    if current["status"] in {"READY", MISSING_DECISION}:
        return current
    reason = {
        "ABSENT": "expected_week_file_absent",
        "INVALID_JSON": "expected_week_file_invalid_json",
        "INDETERMINATE": "expected_week_file_has_no_formal_decision_state",
    }.get(str(current["status"]), "expected_week_state_unknown")
    payload = missing_payload(str(current["week_id"]), now, reason)
    write_json(current["path"], payload)
    return {"status": MISSING_DECISION, "week_id": current["week_id"], "path": current["path"], "data": payload}


def clear_missing(weekly_dir: Path, now: datetime) -> bool:
    current = state(weekly_dir, now)
    if current["status"] != MISSING_DECISION:
        return False
    current["path"].unlink()
    return True


def printable(result: Dict[str, Any]) -> Dict[str, Any]:
    return {key: (str(value) if isinstance(value, Path) else value) for key, value in result.items() if key != "data"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["check", "ensure", "verify", "clear-missing"], default="verify")
    parser.add_argument("--now", default=None, help="ISO timestamp used by tests/recovery tooling")
    parser.add_argument("--weekly-dir", type=Path, default=DEFAULT_WEEKLY_DIR)
    args = parser.parse_args()
    now = parse_now(args.now)

    if args.mode == "ensure":
        result = ensure_formal_state(args.weekly_dir, now)
        print(json.dumps(printable(result), ensure_ascii=False))
        return 0

    if args.mode == "clear-missing":
        removed = clear_missing(args.weekly_dir, now)
        result = state(args.weekly_dir, now)
        print(json.dumps({"removed": removed, **printable(result)}, ensure_ascii=False))
        return 0

    result = state(args.weekly_dir, now)
    print(json.dumps(printable(result), ensure_ascii=False))
    if args.mode == "check":
        return 0 if result["status"] == "READY" else 2
    # verify accepts only a real decision or the explicit fail-closed marker.
    return 0 if result["status"] in {"READY", MISSING_DECISION} else 3


if __name__ == "__main__":
    raise SystemExit(main())
