#!/usr/bin/env python3
"""P2.1 audit-only watchdog: canonical forecast -> Shadow freeze -> real Verification -> OOS.

Fail closed. A missed retrospective freeze cannot be repaired by backdating;
an *open*, eligible baseline may be safely frozen by the collector bridge.
Market-window alarms use the actual BriefRooms Belief collector (US cash slots),
not 24/7 BTC trading or weekends inferred as collector failures.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, time, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from forecast_event_identity import identity
from hypothesis_challenger_engine import (
    SCHEMA as CHALLENGER_SCHEMA, _settle, _gate,
    canon, sha, trusted_market_verification, utc, ts,
)

SCHEMA = "briefrooms-p2-shadow-watchdog-v1"
NY = ZoneInfo("America/New_York")
SLOTS = ("1000", "1300", "1600")
SLOT_GRACE_MINUTES = 55  # collection grace (45 min) + workflow start buffer
COLLECTOR_MAX_AGE_MINUTES = 105
CALIBRATION_MAX_AGE_MINUTES = 185
SETTLEMENT_GRACE_MINUTES = 180
CLOCK_FUTURE_TOLERANCE_MINUTES = 5
REQUIRED_BASELINE_PROBABILITY = (0, 1)
RECOVERY_ACTIONS = {
    "SHADOW_FREEZE_GAP_OPEN": "rerun_p2_bridge",
    "SETTLEMENT_BACKLOG": "rerun_p2_bridge",
    "COLLECTOR_STALE_DURING_MARKET": "dispatch_belief_collector",
    "BASELINE_FORECAST_MISSING_AFTER_SLOT": "dispatch_belief_collector",
    "SOURCE_FORECASTS_MISSING_AFTER_CONFIRMED_SLOT": "dispatch_belief_collector",
}


def _parse(value: Any) -> datetime | None:
    try:
        return utc(value)
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


def _age_min(now: datetime, value: Any) -> float | None:
    then = _parse(value)
    return None if then is None else round((now - then).total_seconds() / 60, 2)


def _market(now: datetime) -> dict[str, Any]:
    local = now.astimezone(NY)
    session_day = local.weekday() < 5
    active = session_day and time(9, 30) <= local.timetz().replace(tzinfo=None) <= time(16, 20)
    # A completed slot is expected only AFTER the full collection grace.
    completed_due_slots = []
    if session_day:
        for label in SLOTS:
            minute_clock = datetime.combine(local.date(), time(int(label[:2]), int(label[2:])), tzinfo=NY)
            if local >= minute_clock + timedelta(minutes=SLOT_GRACE_MINUTES):
                completed_due_slots.append(label)
    return {
        "session_date_ny": local.date().isoformat(),
        "weekday_ny": local.weekday(),
        "session_window": "US_EQUITY_SCHEDULED_COLLECTION",
        "us_weekday": session_day,
        "market_window_active": active,
        "due_slots": completed_due_slots,
        "holiday_proof": "UNAVAILABLE_does_not_assume_weekday_equals_open_market",
    }


def _alert(code: str, severity: str, candidate_id: str | None, reason: str,
           *, count: int = 0) -> dict[str, Any]:
    return {
        "code": code, "severity": severity, "candidate_id": candidate_id,
        "reason": reason, "count": count,
        "recovery": RECOVERY_ACTIONS.get(code, "investigate_no_retroactive_mutation"),
    }


def _scope_match(candidate: Mapping[str, Any], f: Mapping[str, Any]) -> bool:
    ids = identity(f)
    if (ids["hypothesis_id"] != candidate.get("hypothesis_id") or
        ids["hypothesis_version"] != candidate.get("hypothesis_version")):
        return False
    horizon = str((f.get("metadata") or {}).get("calibration_horizon_bucket") or "unknown")
    return candidate.get("horizon_bucket") in (horizon, "__ALL_HORIZONS__")


def assess(state: Mapping[str, Any], p2: Mapping[str, Any] | None,
           scheduler: Mapping[str, Any] | None, *,
           now: str | None = None, previous: Mapping[str, Any] | None = None,
           monitor: bool = False) -> dict[str, Any]:
    current = utc(now) if now else datetime.now(timezone.utc)
    session = _market(current)
    alerts: list[dict[str, Any]] = []
    raw = p2 or {}
    clock_tolerance = CLOCK_FUTURE_TOLERANCE_MINUTES
    if raw.get("schema_version") != CHALLENGER_SCHEMA:
        alerts.append(_alert("P2_STATE_MISSING", "CRITICAL", None,
                             "canonical P2 challenger artifact missing or invalid"))
        candidates = {}
    else:
        candidates = raw.get("candidates") or {}
        if not isinstance(candidates, dict):
            candidates = {}
            alerts.append(_alert("P2_CANDIDATE_REGISTRY_INVALID", "CRITICAL", None,
                                 "challenger candidate map is not a dictionary"))

    calibration_age = _age_min(current, raw.get("generated_at"))
    if raw.get("schema_version") == CHALLENGER_SCHEMA:
        if calibration_age is None or calibration_age < -clock_tolerance:
            alerts.append(_alert("P2_REPORT_TIMESTAMP_INVALID", "CRITICAL", None,
                                 "P2 generated_at absent or in the future"))
        elif session["market_window_active"] and calibration_age > CALIBRATION_MAX_AGE_MINUTES:
            alerts.append(_alert("P2_CALIBRATION_STALE", "CRITICAL", None,
                                 "P2 report has not updated for >185m during US market window"))
        elif calibration_age > 36 * 60:
            alerts.append(_alert("P2_CALIBRATION_STALE", "WARNING", None,
                                 "P2 report older than 36h, market closed or inactive"))

    scheduler = scheduler or {}
    collector_age = _age_min(current, scheduler.get("last_run_at"))
    if collector_age is not None and collector_age < -clock_tolerance:
        alerts.append(_alert("COLLECTOR_CLOCK_IN_FUTURE", "CRITICAL", None,
                             "collector timestamp is later than watchdog clock"))
    if (session["market_window_active"] and session["due_slots"] and
        (collector_age is None or collector_age > COLLECTOR_MAX_AGE_MINUTES)):
        alerts.append(_alert("COLLECTOR_STALE_DURING_MARKET", "CRITICAL", None,
                             "no recent Belief collector heartbeat after scheduled market slot"))

    # The calendar is not a market holiday source. Trust actual completed slots,
    # and classify missing market snapshots independently from missing baselines.
    completed = scheduler.get("completed_slots") or {}
    if not isinstance(completed, dict):
        completed = {}
    collector_observations = (scheduler.get("last_status") or {}).get("observations_collected")
    collector_gaps = (scheduler.get("gaps") or [])[-8:]
    if (session["market_window_active"] and session["due_slots"] and
        collector_age is not None and collector_age <= COLLECTOR_MAX_AGE_MINUTES and
        not any(str(k).startswith("wes-assets:"+session["session_date_ny"]) for k in completed)):
        alerts.append(_alert("NO_CONFIRMED_MARKET_COLLECTION", "WARNING", None,
                             "market slot due, but collector has no successful market slot; check source/holiday",
                             count=len(session["due_slots"])))

    forecasts = [f for f in state.get("forecasts", []) if isinstance(f, Mapping)]
    forecast_map = {str(f.get("forecast_id")): f for f in forecasts if f.get("forecast_id")}

    # Independent SOURCE forecast liveness, even without an eligible P2 candidate.
    # A completed, mature and confirmed WES-ASSET slot must leave at least one
    # canonical source forecast. This check cannot be masked by candidate
    # discovery status or a recently refreshed scheduler heartbeat.
    source_missing_slots = []
    # Also audit the 16:00 NY close slot after the 16:20 collection window:
    # its 55-minute maturation occurs at 16:55, when the cash session is over.
    if session["us_weekday"]:
        for slot in session["due_slots"]:
            key = "wes-assets:" + session["session_date_ny"] + ":" + slot
            completed_at = _parse(completed.get(key))
            if (completed_at is not None and completed_at <= current and
                not any(
                    isinstance(f.get("metadata"), Mapping) and
                    str(f["metadata"].get("slot_key") or "") == key
                    for f in forecasts
                )):
                source_missing_slots.append(key)
    if source_missing_slots:
        alerts.append(_alert("SOURCE_FORECASTS_MISSING_AFTER_CONFIRMED_SLOT",
                             "CRITICAL", None,
                             "collector completed an actual market slot with zero canonical asset forecasts",
                             count=len(source_missing_slots)))

    verifications = [v for v in state.get("verifications", []) if isinstance(v, Mapping)]
    verification_by_event: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for v in verifications:
        f = forecast_map.get(str(v.get("forecast_id") or ""))
        if f:
            verification_by_event[identity(f)["event_id"]].append(v)

    # Proof that canonical market source produces real Verifications is
    # separate from proof that a particular *P2* candidate settled OOS.
    source_verification_count = 0
    source_last_verified = None
    for v in verifications:
        f = forecast_map.get(str(v.get("forecast_id") or ""))
        if not f or not isinstance(v.get("outcome"), bool):
            continue
        stamp = _parse(v.get("verified_at"))
        forecast_at = _parse(f.get("forecast_at"))
        target_at = _parse(f.get("target_at"))
        if (not stamp or not forecast_at or not target_at or
            not forecast_at < target_at <= stamp <= current or
            str(v.get("belief_id") or "") != str(f.get("belief_id") or "") or
            not bool(v.get("calibration_eligible", False)) or
            not trusted_market_verification(v, f.get("target_at"), f)):
            continue
        source_verification_count += 1
        if source_last_verified is None or stamp > source_last_verified:
            source_last_verified = stamp
    previous_checkpoints = ((previous or {}).get("integrity_checkpoint") or {})
    new_checkpoints: dict[str, Any] = {}
    monitored: list[dict[str, Any]] = []
    for cid, c in sorted(candidates.items()):
        if not isinstance(c, Mapping) or c.get("status") not in {
            "OOS_RUNNING", "PROMOTION_ELIGIBLE", "HOLD", "GATE_FAILED",
        }:
            continue
        start = _parse(c.get("activation_boundary"))
        if not start or start > current + timedelta(minutes=clock_tolerance):
            alerts.append(_alert("CANDIDATE_ACTIVATION_INVALID", "CRITICAL", str(cid),
                                 "activation boundary absent or in the future"))
            continue
        commitments = c.get("shadow_forecasts") or {}
        settled = c.get("settlements") or {}
        if not isinstance(commitments, dict) or not isinstance(settled, dict):
            alerts.append(_alert("SHADOW_STATE_INVALID", "CRITICAL", str(cid),
                                 "commitments or settlements are not maps"))
            continue
        prior_hashes = previous_checkpoints.get(str(cid)) or {}
        frozen_hashes = {str(k): sha(v) for k,v in commitments.items()}
        settled_hashes = {str(k): sha(v) for k,v in settled.items()}
        changed = sum(current.get(eid) != digest
                      for name,current in (("frozen", frozen_hashes), ("settled", settled_hashes))
                      for eid,digest in (prior_hashes.get(name) or {}).items())
        if changed:
            alerts.append(_alert("SHADOW_APPEND_ONLY_INTEGRITY_FAILURE", "CRITICAL", str(cid),
                                 "previous frozen Shadow or settlement content changed/disappeared",
                                 count=changed))
        new_checkpoints[str(cid)] = {
            "frozen": {**frozen_hashes, **(prior_hashes.get("frozen") or {})},
            "settled": {**settled_hashes, **(prior_hashes.get("settled") or {})},
        }
        active_corruption = 0
        for event_id, commit in commitments.items():
            source = forecast_map.get(str(commit.get("forecast_id") or ""))
            try:
                if source is None or identity(source)["event_id"] != event_id:
                    raise ValueError("source missing or event drift")
                at, frozen_at, target = (
                    utc(commit["forecast_at"]), utc(commit["frozen_at"]), utc(commit["target_at"]))
                if not start < at <= frozen_at < target or frozen_at > current:
                    raise ValueError("invalid freeze lineage")
                expected = sha({
                    "forecast_id": commit["forecast_id"], "event_id": event_id,
                    "forecast_at": commit["forecast_at"],
                    "target_at": commit["target_at"],
                    "raw_probability": round(float(source["predicted_probability"]), 12),
                    "outcome_spec_sha256": sha((source.get("metadata") or {}).get("outcome_spec")),
                })
                if expected != commit["source_snapshot_sha256"]:
                    raise ValueError("frozen source mutated")
                if sha((c.get("proposed_change") or {}).get("transform")) != commit["transform_sha256"]:
                    raise ValueError("challenger transform mutated")
            except (ValueError, TypeError, KeyError, OverflowError):
                active_corruption += 1
        if active_corruption:
            alerts.append(_alert("SHADOW_FROZEN_SOURCE_INTEGRITY_FAILURE", "CRITICAL", str(cid),
                                 "committed source or transform no longer matches immutably frozen data",
                                 count=active_corruption))
        baseline: dict[str, Mapping[str, Any]] = {}
        first_baseline_at = None
        last_baseline_at = None
        for f in forecasts:
            if not _scope_match(c, f):
                continue
            forecast_at = _parse(f.get("forecast_at"))
            target = _parse(f.get("target_at"))
            try:
                p = float(f["predicted_probability"])
            except (ValueError, TypeError, KeyError):
                continue
            if (not forecast_at or not target or not start < forecast_at <= current
                or not forecast_at < target or not 0 < p < 1):
                continue
            eid = identity(f)["event_id"]
            existing = baseline.get(eid)
            if (existing is None or str(f["forecast_at"]) < str(existing["forecast_at"])):
                baseline[eid] = f
            if last_baseline_at is None or forecast_at > last_baseline_at:
                last_baseline_at = forecast_at
            if first_baseline_at is None or forecast_at < first_baseline_at:
                first_baseline_at = forecast_at

        missed_open = [eid for eid, f in baseline.items() if eid not in commitments
                       and _parse(f.get("target_at")) > current]
        missed_expired = [eid for eid, f in baseline.items() if eid not in commitments
                          and _parse(f.get("target_at")) <= current]
        if missed_open:
            alerts.append(_alert("SHADOW_FREEZE_GAP_OPEN", "CRITICAL", str(cid),
                                 "source forecast exists and target is still open, but no Shadow freeze",
                                 count=len(missed_open)))
        if missed_expired:
            alerts.append(_alert("SHADOW_FREEZE_MISSED_IRRECOVERABLE", "CRITICAL", str(cid),
                                 "target passed before any Shadow freeze; NEVER backdate this forecast",
                                 count=len(missed_expired)))

        # An actually completed source slot is authoritative evidence of the
        # collection opportunity; do not equate a US weekday with a live market.
        due_completed_slots = []
        for slot in session["due_slots"]:
            key = "wes-assets:" + session["session_date_ny"] + ":" + slot
            completed_at = _parse(completed.get(key))
            if completed_at and completed_at > start and completed_at <= current:
                due_completed_slots.append((slot, completed_at))
        # Check *every* already completed, mature Belief collection slot
        # since candidate activation, not just the current day. Source slot
        # identity is embedded in immutable forecast.metadata.slot_key.
        # A slot whose planned time precedes activation is NOT expected.
        missing_slots = []
        for key, completed_raw in completed.items():
            if not str(key).startswith("wes-assets:"):
                continue
            parts = str(key).split(":")
            if len(parts) != 3 or parts[2] not in SLOTS:
                continue
            try:
                planned = datetime.combine(
                    datetime.fromisoformat(parts[1]).date(),
                    time(int(parts[2][:2]), int(parts[2][2:])), tzinfo=NY)
                complete_at = utc(completed_raw)
            except (ValueError, TypeError):
                continue
            if not (planned.astimezone(timezone.utc) > start and
                    complete_at > start and complete_at <= current and
                    planned + timedelta(minutes=SLOT_GRACE_MINUTES) <= current):
                continue
            if not any(str(f.get("metadata", {}).get("slot_key") or "") == key
                       for f in baseline.values()):
                missing_slots.append(key)
        if missing_slots:
            alerts.append(_alert("BASELINE_FORECAST_MISSING_AFTER_SLOT", "CRITICAL", str(cid),
                                 "Belief collector completed scheduled market slot(s), but forecast is missing for this hypothesis",
                                 count=len(missing_slots)))

        # Settlements absent despite real verification are catch-up candidates.
        overdue = []
        waiting_real = []
        backlog = []
        genuinely_verified = 0
        conflicts = []
        copy_candidate = json.loads(canon(c))
        newly_settleable, settlement_conflicts = _settle(copy_candidate, state, current)
        if settlement_conflicts:
            conflicts = settlement_conflicts
            alerts.append(_alert("SHADOW_SETTLEMENT_INTEGRITY_FAILURE", "CRITICAL", str(cid),
                                 "source snapshot, verification or previous settled outcome conflicts",
                                 count=len(conflicts)))
        if newly_settleable:
            backlog = [eid for eid in copy_candidate.get("settlements", {})
                       if eid not in settled]
            alerts.append(_alert("SETTLEMENT_BACKLOG", "CRITICAL", str(cid),
                                 "real Verification exists but candidate state has not settled it",
                                 count=len(backlog)))
        for eid, commit in commitments.items():
            if eid in settled and eid not in conflicts:
                genuinely_verified += 1
                continue
            target = _parse(commit.get("target_at"))
            if not target or target > current:
                continue
            real = verification_by_event.get(eid) or []
            if real:
                if eid not in backlog and eid not in conflicts:
                    overdue.append(eid)  # an unexplained unresolved state
                continue
            # After hours are not a source of false "market active" alarms.
            if current >= target + timedelta(minutes=SETTLEMENT_GRACE_MINUTES):
                if session["market_window_active"]:
                    overdue.append(eid)
                else:
                    waiting_real.append(eid)
        if overdue:
            alerts.append(_alert("SHADOW_VERIFICATION_OVERDUE", "CRITICAL", str(cid),
                                 "target and grace elapsed during active collection, no real usable settlement",
                                 count=len(overdue)))

        latest_commitment_at = max(
            (_parse(x.get("frozen_at")) for x in commitments.values()
             if _parse(x.get("frozen_at")) is not None), default=None)
        last_progress = max((x for x in (latest_commitment_at,
                             max((_parse(x.get("verified_at")) for x in settled.values()
                                  if _parse(x.get("verified_at"))), default=None))
                             if x is not None), default=None)

        # Explicit stagnation after two distinct *completed* slots. This
        # distinguishes "no scheduled baseline" from off-session inactivity.
        if (len(due_completed_slots) >= 2 and not commitments and
            session["market_window_active"] and
            not any(a["code"] == "BASELINE_FORECAST_MISSING_AFTER_SLOT" and
                    a["candidate_id"] == cid for a in alerts)):
            alerts.append(_alert("SHADOW_NO_PROGRESS_TWO_SLOTS", "CRITICAL", str(cid),
                                 "two confirmed market collection slots elapsed without Shadow freeze"))

        monitored.append({
            "candidate_id": str(cid),
            "hypothesis_id": c.get("hypothesis_id"),
            "status": c.get("status"),
            "activation_boundary": c.get("activation_boundary"),
            "source_baseline_events_since_activation": len(baseline),
            "source_latest_forecast_at": ts(last_baseline_at) if last_baseline_at else None,
            "frozen_shadow_forecasts": len(commitments),
            "settled_oos_events": len(settled),
            "verified_settlement_links": genuinely_verified,
            "real_settlement_probe": ("VERIFIED" if genuinely_verified else "WAITING_REAL_VERIFICATION"),
            "missed_open": len(missed_open),
            "missed_irrecoverable": len(missed_expired),
            "settlement_backlog": len(backlog),
            "unverified_after_target": len(waiting_real),
            "overdue_verification": len(overdue),
            "completed_due_market_slots": len(due_completed_slots),
            "missing_completed_market_slots": len(missing_slots),
            "last_progress_at": ts(last_progress) if last_progress else None,
        })

    alert_codes = sorted(set(x["code"] for x in alerts))
    severity = ("FAIL" if any(x["severity"] == "CRITICAL" for x in alerts) else
                "WARN" if alerts else "PASS")
    # No active P2 candidates is a non-failure, separately reported and NOT
    # presented as a successful end-to-end Verification proof.
    if not candidates:
        readiness = "NO_ACTIVE_CHALLENGER"
    elif any(x["verified_settlement_links"] > 0 for x in monitored):
        readiness = "REAL_SETTLEMENT_VERIFIED"
    elif any(x["frozen_shadow_forecasts"] > 0 for x in monitored):
        readiness = "WAITING_REAL_SETTLEMENT"
    else:
        readiness = "WAITING_FIRST_SHADOW_FREEZE"
    prev_state = previous or {}
    if not isinstance(prev_state, Mapping):
        prev_state = {}
    return {
        "schema_version": SCHEMA,
        "generated_at": ts(current),
        "status": severity,
        "mode": "external_read_only" if monitor else "canonical_calibration_watchdog",
        "readiness": readiness,
        "session": session,
        "source": {
            "canonical_market_verification_count": source_verification_count,
            "canonical_market_last_verified_at": ts(source_last_verified) if source_last_verified else None,
            "collector_age_minutes": collector_age,
            "calibration_age_minutes": calibration_age,
            "collector_last_run_at": scheduler.get("last_run_at"),
            "calibration_last_run_at": raw.get("generated_at"),
            "collector_observations_last_run": collector_observations,
            "market_gaps_last_8": [
                {"at": x.get("timestamp"), "reason": x.get("reason")}
                for x in collector_gaps if isinstance(x, Mapping)
            ],
        },
        "summary": {
            "active_challengers": len(monitored),
            "confirmed_slots_without_any_forecast": len(source_missing_slots),
            "source_baseline_events_since_activation": sum(x["source_baseline_events_since_activation"] for x in monitored),
            "frozen_shadow_forecasts": sum(x["frozen_shadow_forecasts"] for x in monitored),
            "real_settled_events": sum(x["verified_settlement_links"] for x in monitored),
            "recoverable_freeze_gaps": sum(x["missed_open"] for x in monitored),
            "irrecoverable_missed_freezes": sum(x["missed_irrecoverable"] for x in monitored),
            "real_settlement_backlog": sum(x["settlement_backlog"] for x in monitored),
            "alerts": len(alerts),
            "critical_alerts": sum(a["severity"] == "CRITICAL" for a in alerts),
        },
        "candidates": monitored,
        "integrity_checkpoint": new_checkpoints,
        "alerts": alerts,
        "alert_codes": alert_codes,
        "recovery_dispatch_at": prev_state.get("recovery_dispatch_at"),
        "authority": {
            "trading": False, "production_writeback": False,
            "source_evidence_mutation": False,
            "frozen_forecast_rewrite": False,
            "retrospective_oos_backfill": False,
        },
    }


def public_view(report: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(report, Mapping) or report.get("schema_version") != SCHEMA:
        return {"schema_version": SCHEMA, "status": "NOT_AVAILABLE",
                "summary": {"active_challengers": 0}, "alerts": [],
                "authority": {"production_writeback": False}}
    return {
        "schema_version": SCHEMA, "generated_at": report.get("generated_at"),
        "status": report.get("status"), "readiness": report.get("readiness"),
        "session": report.get("session"),
        "summary": report.get("summary") or {},
        "canonical_market_verification_probe": {
            "verified": bool((report.get("source") or {}).get("canonical_market_verification_count")),
            "verified_count": (report.get("source") or {}).get("canonical_market_verification_count", 0),
            "last_verified_at": (report.get("source") or {}).get("canonical_market_last_verified_at"),
            "not_p2_oos_proof": True,
        },
        "alerts": [
            {"code": a.get("code"), "severity": a.get("severity"),
             "candidate_id": a.get("candidate_id"), "count": a.get("count")}
            for a in (report.get("alerts") or [])
        ],
        "candidates": [
            {key: c.get(key) for key in (
                "candidate_id", "hypothesis_id", "status",
                "frozen_shadow_forecasts", "settled_oos_events",
                "verified_settlement_links", "real_settlement_probe",
                "missed_open", "missed_irrecoverable", "last_progress_at")}
            for c in (report.get("candidates") or [])
        ],
        "authority": {"production_writeback": False,
                      "frozen_forecast_rewrite": False,
                      "retrospective_oos_backfill": False},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit P2 actual forecast->Shadow->Verification->OOS")
    ap.add_argument("--state-dir", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--now")
    ap.add_argument("--monitor", action="store_true", help="External read-only monitor")
    ap.add_argument("--strict", action="store_true", help="Nonzero on critical alert; write report first")
    args = ap.parse_args()
    root = args.state_dir
    def load(path: Path, default: Any) -> Any:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    state = load(root / "state.json", {})
    p2 = load(root / "HYPOTHESIS_CHALLENGERS_STATE.json", {})
    scheduler = load(root / "scheduler.json", {})
    prev = load(args.output, load(root / "P2_SHADOW_WATCHDOG.json", {}))
    result = assess(state, p2, scheduler, now=args.now, previous=prev,
                    monitor=args.monitor)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, args.output)
    print("P2_SHADOW_WATCHDOG", json.dumps({
        "status": result["status"], "readiness": result["readiness"],
        "summary": result["summary"], "alert_codes": result["alert_codes"],
    }, sort_keys=True))
    for a in result["alerts"]:
        print("::" + ("error" if a["severity"] == "CRITICAL" else "warning") +
              " title=P2 Shadow " + a["code"] + "::" + a["reason"] +
              " candidate=" + str(a["candidate_id"]) + " count=" + str(a["count"]))
    return 2 if args.strict and result["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
