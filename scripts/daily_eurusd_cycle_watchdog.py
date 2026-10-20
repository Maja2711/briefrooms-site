#!/usr/bin/env python3
"""Independent liveness guard for canonical Daily EUR/USD state."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, time
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from daily_eurusd_decision_record import ENGINE_VERSION as EXPECTED_ENGINE_VERSION

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Warsaw")
STATE = ROOT / "data/investments/eurusd_daily_spot.json"
DEFAULT_MAX_AGE_MINUTES = 15
DEFAULT_EPISTEMIC_MAX_AGE_MINUTES = 20
DEFAULT_BELIEF_MAX_AGE_MINUTES = 30


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



def _age_minutes(now: datetime, value: Any) -> Optional[float]:
    stamp = parse_dt(value)
    if stamp is None:
        return None
    return (now.astimezone(TZ) - stamp).total_seconds() / 60.0


def classify_daily_sla(
    now: datetime,
    daily_result: Dict[str, Any],
    *,
    recovery_stage: str,
    pipeline_status: str,
    workflow_health: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Independent hard 15-minute clock, even if upstream is stale.

    Source staleness and GitHub Actions infrastructure failure are separate
    incident classes; Shadow, recovery and this observer have no trade authority.
    """
    breach = daily_result.get("status") == "stale"
    age = daily_result.get("state_age_minutes")
    result = {
        "breached": breach,
        "max_age_minutes": daily_result.get("max_age_minutes", DEFAULT_MAX_AGE_MINUTES),
        "daily_age_minutes": age,
        "daily_reasons": list(daily_result.get("reasons") or []),
        "incident_class": "NONE",
        "source_stage": recovery_stage,
        "workflow_telemetry_available": isinstance(workflow_health, dict) and bool(workflow_health.get("workflows")),
        "workflow_failure": None,
    }
    if not breach:
        return result

    workflow_health = workflow_health or {}
    failures = []
    stuck = []
    for name, runs in (workflow_health.get("workflows") or {}).items():
        if not isinstance(runs, list):
            continue
        # A successful later run resolves earlier failures for this workflow.
        # Do not label an old, recovered failure as the cause of a new SLA breach.
        valid = sorted(
            (row for row in runs if isinstance(row, dict)),
            key=lambda row: str(row.get("created_at") or ""), reverse=True,
        )
        most_recent_completed = next(
            (row for row in valid if row.get("status") == "completed"), None
        )
        active = [row for row in valid if row.get("status") in {"in_progress", "queued"}]
        candidates = active + ([most_recent_completed] if most_recent_completed else [])
        for row in candidates:
            started = parse_dt(row.get("run_started_at") or row.get("created_at"))
            if started is None:
                continue
            elapsed = (now.astimezone(TZ) - started).total_seconds() / 60
            if elapsed < -2 or elapsed > 45:
                continue
            status = str(row.get("status") or "")
            conclusion = str(row.get("conclusion") or "")
            if conclusion in {"failure", "timed_out", "action_required"}:
                failures.append((elapsed, name, conclusion, row.get("id")))
            elif status in {"in_progress", "queued"} and elapsed > 15:
                stuck.append((elapsed, name, status, row.get("id")))
    if failures:
        incident = min(failures)
        result["incident_class"] = "WORKFLOW_FAILURE"
        result["workflow_failure"] = {
            "workflow": incident[1], "conclusion": incident[2], "run_id": incident[3]
        }
    elif stuck:
        incident = max(stuck)
        result["incident_class"] = "WORKFLOW_DELAY"
        result["workflow_failure"] = {
            "workflow": incident[1], "status": incident[2], "run_id": incident[3]
        }
    elif recovery_stage in {"BELIEF", "EPISTEMIC"}:
        result["incident_class"] = "UPSTREAM_DATA_STALE"
    elif pipeline_status == "upstream_unqualified":
        result["incident_class"] = "EPISTEMIC_UNQUALIFIED"
    elif not result["workflow_telemetry_available"]:
        result["incident_class"] = "WORKFLOW_TELEMETRY_UNAVAILABLE"
    else:
        result["incident_class"] = "DAILY_PUBLICATION_DELAY"
    return result


def evaluate_pipeline(
    now: Optional[datetime] = None,
    *,
    state_path: Path = STATE,
    daily_max_age_minutes: int = DEFAULT_MAX_AGE_MINUTES,
    belief_created_at: Any = None,
    belief_max_age_minutes: int = DEFAULT_BELIEF_MAX_AGE_MINUTES,
    epistemic_created_at: Any = None,
    epistemic_max_age_minutes: int = DEFAULT_EPISTEMIC_MAX_AGE_MINUTES,
    epistemic_readiness_path: Optional[Path] = None,
    workflow_health_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Evaluate TR-03 liveness and choose exactly one recovery stage.

    Recovery is deliberately ordered:
    BELIEF -> EPISTEMIC -> DAILY.
    A fresh Epistemic projection that explicitly says DAILY_EURUSD is not
    qualified is not an infrastructure failure and does not create a retry loop.
    """
    current = (now or datetime.now(TZ)).astimezone(TZ)
    result: Dict[str, Any] = {
        "checked_at": current.isoformat(timespec="seconds"),
        "market_active": fx_market_active(current),
        "status": "healthy",
        "reasons": [],
        "recovery_stage": "NONE",
        "auto_dispatch_recovery": False,
        "thresholds_minutes": {
            "belief": max(1, int(belief_max_age_minutes)),
            "epistemic": max(1, int(epistemic_max_age_minutes)),
            "daily": max(1, int(daily_max_age_minutes)),
        },
    }
    if not result["market_active"]:
        result["status"] = "inactive_window"
        return result

    belief_age = _age_minutes(current, belief_created_at)
    result["belief_created_at"] = belief_created_at
    result["belief_age_minutes"] = None if belief_age is None else round(belief_age, 2)
    if belief_age is None:
        result["status"] = "stale"
        result["reasons"].append("belief_runtime_missing")
        result["recovery_stage"] = "BELIEF"
    elif belief_age < -2.0 or belief_age > result["thresholds_minutes"]["belief"]:
        result["status"] = "stale"
        result["reasons"].append(
            "belief_runtime_timestamp_in_future" if belief_age < -2.0 else "belief_runtime_stale"
        )
        result["recovery_stage"] = "BELIEF"

    if result["recovery_stage"] == "NONE":
        epistemic_age = _age_minutes(current, epistemic_created_at)
        result["epistemic_created_at"] = epistemic_created_at
        result["epistemic_age_minutes"] = None if epistemic_age is None else round(epistemic_age, 2)
        if epistemic_age is None:
            result["status"] = "stale"
            result["reasons"].append("epistemic_runtime_missing")
            result["recovery_stage"] = "EPISTEMIC"
        elif epistemic_age < -2.0 or epistemic_age > result["thresholds_minutes"]["epistemic"]:
            result["status"] = "stale"
            result["reasons"].append(
                "epistemic_runtime_timestamp_in_future"
                if epistemic_age < -2.0
                else "epistemic_runtime_stale"
            )
            result["recovery_stage"] = "EPISTEMIC"

    readiness: Dict[str, Any] = {}
    if result["recovery_stage"] == "NONE":
        if epistemic_readiness_path is not None:
            readiness = read_json(epistemic_readiness_path)
        result["epistemic_daily_readiness"] = readiness
        if not readiness:
            result["status"] = "stale"
            result["reasons"].append("epistemic_daily_readiness_missing")
            result["recovery_stage"] = "EPISTEMIC"
        elif readiness.get("ready") is not True:
            result["status"] = "upstream_unqualified"
            result["reasons"].append(
                "epistemic_daily_not_ready:" + str(readiness.get("reason") or "unknown")
            )
            result["recovery_stage"] = "NONE"

    if result["recovery_stage"] == "NONE" and result["status"] != "upstream_unqualified":
        daily = evaluate(
            current,
            state_path=state_path,
            max_age_minutes=daily_max_age_minutes,
        )
        result["daily"] = daily
        if daily.get("status") == "stale":
            result["status"] = "stale"
            result["reasons"].extend(list(daily.get("reasons") or []))
            result["recovery_stage"] = "DAILY"
        else:
            result["status"] = daily.get("status") or "healthy"

    # The hard Daily SLA is independent of the upstream recovery stage.
    # The old pipeline short-circuited on Belief/Epistemic stale, hiding
    # Daily >15-minute breaches. Preserve staged recovery authority.
    independent_daily = result.get("daily")
    if independent_daily is None:
        independent_daily = evaluate(current, state_path=state_path, max_age_minutes=daily_max_age_minutes)
    result["daily_sla"] = classify_daily_sla(
        current, independent_daily,
        recovery_stage=result["recovery_stage"],
        pipeline_status=result["status"],
        workflow_health=read_json(workflow_health_path) if workflow_health_path else None,
    )
    result["auto_dispatch_recovery"] = result["recovery_stage"] in {"BELIEF", "EPISTEMIC", "DAILY"}
    return result

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["check", "print"], default="check")
    parser.add_argument("--max-age-minutes", type=int, default=DEFAULT_MAX_AGE_MINUTES)
    parser.add_argument("--pipeline", action="store_true")
    parser.add_argument("--belief-created-at")
    parser.add_argument("--belief-max-age-minutes", type=int, default=DEFAULT_BELIEF_MAX_AGE_MINUTES)
    parser.add_argument("--epistemic-created-at")
    parser.add_argument("--epistemic-max-age-minutes", type=int, default=DEFAULT_EPISTEMIC_MAX_AGE_MINUTES)
    parser.add_argument("--epistemic-readiness")
    parser.add_argument("--workflow-health", help="JSON summary of recent GitHub Actions runs, read-only")
    args = parser.parse_args()

    if args.pipeline:
        result = evaluate_pipeline(
            daily_max_age_minutes=args.max_age_minutes,
            belief_created_at=args.belief_created_at,
            belief_max_age_minutes=args.belief_max_age_minutes,
            epistemic_created_at=args.epistemic_created_at,
            epistemic_max_age_minutes=args.epistemic_max_age_minutes,
            epistemic_readiness_path=Path(args.epistemic_readiness) if args.epistemic_readiness else None,
            workflow_health_path=Path(args.workflow_health) if args.workflow_health else None,
        )
    else:
        result = evaluate(max_age_minutes=args.max_age_minutes)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.mode == "check" and result.get("status") == "stale":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
