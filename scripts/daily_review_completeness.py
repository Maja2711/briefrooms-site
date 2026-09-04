#!/usr/bin/env python3
"""Audit and enforce one scheduled daily review per open weekly position/session.

A missing review is a DATA_GAP, never an implicit HOLD. Historical gaps are audit
facts only: this module does not synthesize or backfill decisions. Strict
fail-closed enforcement is prospective from the date declared by policy.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "data" / "investments" / "daily_review_policy.json"
WEEKLY_DIR = ROOT / "data" / "investments" / "weekly"
REPORT_PATH = ROOT / "data" / "investments" / "daily_review_completeness.json"
SCHEMA_VERSION = "briefrooms-daily-review-completeness-v1"
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    policy = read_json(path, {})
    if not isinstance(policy, dict) or policy.get("enabled") is not True:
        raise ValueError("daily review policy missing or disabled")
    return policy


def _tz(policy: Mapping[str, Any]) -> ZoneInfo:
    return ZoneInfo(str(policy.get("timezone") or "Europe/Warsaw"))


def parse_dt(value: Any, policy: Mapping[str, Any]) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    tz = _tz(policy)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def review_time(policy: Mapping[str, Any]) -> time:
    raw = str(policy.get("review_time_local") or "23:00")
    hour, minute = raw.split(":", 1)
    return time(int(hour), int(minute))


def review_cutoff(session_date: date, policy: Mapping[str, Any]) -> datetime:
    return datetime.combine(session_date, review_time(policy), tzinfo=_tz(policy))


def is_review_day(session_date: date, policy: Mapping[str, Any]) -> bool:
    allowed = {str(x) for x in (policy.get("review_days") or [])}
    return DAY_NAMES[session_date.weekday()] in allowed


def position_key(week: Mapping[str, Any], item: Mapping[str, Any]) -> str:
    week_id = str(week.get("week_id") or "unknown-week")
    instrument_id = str(item.get("instrument_id") or "unknown-instrument")
    entry = str(item.get("entry_captured_at") or item.get("entry_price") or "unknown-entry")
    return f"{week_id}:{instrument_id}:{entry}"


def _fallback_entry(week: Mapping[str, Any], policy: Mapping[str, Any]) -> datetime | None:
    raw = week.get("forecast_for_week_start")
    if not raw:
        return None
    try:
        day = date.fromisoformat(str(raw)[:10])
    except Exception:
        return None
    return datetime.combine(day, time(0, 0), tzinfo=_tz(policy))


def scheduled_reviews(item: Mapping[str, Any], session_date: date) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in item.get("daily_reviews") or []:
        if not isinstance(row, dict) or str(row.get("review_date") or "") != session_date.isoformat():
            continue
        trigger = str(row.get("review_trigger") or "scheduled_daily_model_review")
        # Material-event reviews are separate observations and never masquerade as
        # the once-per-session scheduled review.
        if trigger == "scheduled_daily_model_review":
            out.append(row)
    return out


def open_at_review_cutoff(
    week: Mapping[str, Any],
    item: Mapping[str, Any],
    session_date: date,
    policy: Mapping[str, Any],
) -> bool:
    side = str(item.get("direction") or "neutral")
    if side not in {"long", "short"} or item.get("entry_price") is None:
        return False
    cutoff = review_cutoff(session_date, policy)
    entry = parse_dt(item.get("entry_captured_at"), policy) or _fallback_entry(week, policy)
    if entry is None or entry > cutoff:
        return False
    exit_dt = parse_dt(item.get("exit_captured_at"), policy)
    if exit_dt is not None and exit_dt <= cutoff:
        return False
    return True


def evaluate_session(
    week: Mapping[str, Any],
    session_date: date,
    policy: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    tz = _tz(policy)
    now = (now or datetime.now(tz)).astimezone(tz)
    due_day = is_review_day(session_date, policy)
    due_time = session_date < now.date() or (session_date == now.date() and now >= review_cutoff(session_date, policy))
    if not due_day or not due_time:
        return {
            "schema_version": SCHEMA_VERSION,
            "session_date": session_date.isoformat(),
            "status": "NOT_DUE",
            "expected_count": 0,
            "reviewed_count": 0,
            "missing": [],
            "duplicates": [],
            "formal_learning_eligible": False,
            "missing_review_is_hold": False,
        }

    expected: list[dict[str, Any]] = []
    reviewed_count = 0
    missing: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    reconstructed = False

    for item in week.get("instruments") or []:
        if not isinstance(item, dict):
            continue
        rows = scheduled_reviews(item, session_date)
        # A persisted scheduled review proves the position was reviewable that
        # session even if its close-price timestamp is at the review cutoff.
        required = bool(rows) or open_at_review_cutoff(week, item, session_date, policy)
        if not required:
            continue
        key = position_key(week, item)
        instrument_id = str(item.get("instrument_id") or "")
        expected.append({"position_key": key, "instrument_id": instrument_id})
        reviewed_count += len(rows)
        if not rows:
            missing.append({
                "position_key": key,
                "instrument_id": instrument_id,
                "session_date": session_date.isoformat(),
                "interpretation": "DATA_GAP_NOT_HOLD",
            })
        elif len(rows) > 1:
            duplicates.append({
                "position_key": key,
                "instrument_id": instrument_id,
                "session_date": session_date.isoformat(),
                "review_count": len(rows),
            })
        for row in rows:
            if str(row.get("observation_mode") or "LEGACY_LIVE") == "RECONSTRUCTED":
                reconstructed = True

    status = "PASS" if not missing and not duplicates else "DATA_GAP"
    return {
        "schema_version": SCHEMA_VERSION,
        "week_id": str(week.get("week_id") or ""),
        "session_date": session_date.isoformat(),
        "status": status,
        "expected_count": len(expected),
        "reviewed_count": reviewed_count,
        "expected_positions": expected,
        "missing": missing,
        "duplicates": duplicates,
        "missing_review_is_hold": False,
        "historical_backfill_performed": False,
        "formal_learning_eligible": status == "PASS" and not reconstructed,
        "reconstructed_review_present": reconstructed,
    }


def _date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def audit_week(
    week: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    tz = _tz(policy)
    as_of = (as_of or datetime.now(tz)).astimezone(tz)
    try:
        start = date.fromisoformat(str(week.get("forecast_for_week_start"))[:10])
        end = date.fromisoformat(str(week.get("forecast_for_week_end"))[:10])
    except Exception as exc:
        raise ValueError("week start/end missing") from exc
    end = min(end, as_of.date())
    sessions: list[dict[str, Any]] = []
    for day in _date_range(start, end):
        result = evaluate_session(week, day, policy, now=as_of)
        if result["status"] != "NOT_DUE" and (result["expected_count"] > 0 or result["reviewed_count"] > 0):
            sessions.append(result)
    gaps = [row for row in sessions if row["status"] == "DATA_GAP"]
    return {
        "schema_version": SCHEMA_VERSION,
        "week_id": str(week.get("week_id") or ""),
        "audit_mode": "HISTORICAL_OBSERVATION_ONLY_NO_BACKFILL",
        "status": "DATA_GAP" if gaps else "PASS",
        "sessions": sessions,
        "gap_sessions": [row["session_date"] for row in gaps],
        "missing_reviews": [item for row in gaps for item in row["missing"]],
        "duplicate_reviews": [item for row in gaps for item in row["duplicates"]],
        "historical_backfill_performed": False,
        "formal_learning_eligible": not gaps and all(row["formal_learning_eligible"] for row in sessions),
    }


def upsert_session_history(state: Mapping[str, Any], session: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(state)
    history = [dict(row) for row in (state.get("session_history") or []) if isinstance(row, dict)]
    history = [row for row in history if str(row.get("session_date") or "") != str(session.get("session_date") or "")]
    history.append(dict(session))
    history.sort(key=lambda row: str(row.get("session_date") or ""))
    out["session_history"] = history[-30:]
    return out


def annotate_current_reviews(week: dict[str, Any], session_date: date, session_ok: bool) -> None:
    for item in week.get("instruments") or []:
        if not isinstance(item, dict):
            continue
        key = position_key(week, item)
        for row in scheduled_reviews(item, session_date):
            row.setdefault("position_review_key", key)
            row.setdefault("observation_mode", "LIVE")
            row["formal_learning_eligible"] = bool(session_ok and row.get("observation_mode") != "RECONSTRUCTED")
            row["missing_review_is_hold"] = False


def apply_current_state(
    week: dict[str, Any],
    policy: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    tz = _tz(policy)
    now = (now or datetime.now(tz)).astimezone(tz)
    session = evaluate_session(week, now.date(), policy, now=now)
    state = week.get("daily_position_review") if isinstance(week.get("daily_position_review"), dict) else {}
    state = upsert_session_history(state, session)
    state["enabled"] = True
    state["completeness_contract"] = "exactly_one_scheduled_review_per_open_position_session"
    state["missing_review_is_hold"] = False
    state["historical_backfill_allowed"] = False
    state["last_completeness_checked_at"] = now.isoformat(timespec="seconds")
    state["last_completeness_status"] = session["status"]

    if session["status"] == "PASS":
        state["last_review_date"] = now.date().isoformat()
        state["last_reviewed_at"] = state.get("last_reviewed_at") or now.isoformat(timespec="seconds")
    elif session["status"] == "DATA_GAP":
        state["last_attempt_date"] = now.date().isoformat()
        state["last_attempted_at"] = now.isoformat(timespec="seconds")
        # daily_position_review.py marks the day complete before a completeness
        # check. Clear only today's marker so the next hourly run can retry.
        if state.get("last_review_date") == now.date().isoformat():
            state.pop("last_review_date", None)
            state.pop("last_reviewed_at", None)

    annotate_current_reviews(week, now.date(), session["status"] == "PASS")
    week["daily_position_review"] = state
    # Re-evaluate after annotations; structure is unchanged, but formal eligibility
    # now reflects explicit LIVE/RECONSTRUCTED metadata.
    session = evaluate_session(week, now.date(), policy, now=now)
    return session


def enforcement_active(session_date: date, policy: Mapping[str, Any]) -> bool:
    cfg = policy.get("completeness") if isinstance(policy.get("completeness"), dict) else {}
    raw = str(cfg.get("strict_enforcement_from") or "9999-12-31")
    return session_date >= date.fromisoformat(raw)


def current_week_path(now: datetime) -> Path:
    year, week, _ = now.isocalendar()
    return WEEKLY_DIR / f"{year}-W{week:02d}.json"


def run_current(*, enforce: bool = False, write: bool = True, now: datetime | None = None) -> dict[str, Any]:
    policy = load_policy()
    tz = _tz(policy)
    now = (now or datetime.now(tz)).astimezone(tz)
    path = current_week_path(now)
    week = read_json(path, {})
    if not isinstance(week, dict) or not week:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "checked_at": now.isoformat(timespec="seconds"),
            "status": "NO_CURRENT_WEEK",
            "formal_learning_eligible": False,
            "missing_review_is_hold": False,
        }
    else:
        session = apply_current_state(week, policy, now=now)
        historical = audit_week(week, policy, as_of=now)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "checked_at": now.isoformat(timespec="seconds"),
            "week_id": week.get("week_id"),
            "current_session": session,
            "historical_audit": historical,
            "status": session["status"],
            "strict_enforcement_active": enforcement_active(now.date(), policy),
            "formal_learning_eligible": session["formal_learning_eligible"],
            "missing_review_is_hold": False,
            "historical_backfill_performed": False,
        }
        if write:
            write_json(path, week)
    if write:
        write_json(REPORT_PATH, payload)
    if enforce and payload.get("status") == "DATA_GAP" and enforcement_active(now.date(), policy):
        raise SystemExit(2)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", action="store_true", help="audit current weekly position review state")
    parser.add_argument("--audit-week", type=Path, help="historically audit one weekly JSON without backfill")
    parser.add_argument("--enforce", action="store_true", help="fail closed for a prospective current-session gap")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    policy = load_policy()
    if args.audit_week:
        week = read_json(args.audit_week, {})
        payload = audit_week(week, policy)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    payload = run_current(enforce=args.enforce, write=not args.no_write)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
