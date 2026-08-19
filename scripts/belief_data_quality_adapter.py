#!/usr/bin/env python3
"""Decision-independent data-quality telemetry for BriefRooms Belief Core.

This module never creates Evidence and never changes a belief probability. It
only measures the quality, freshness, latency and coverage of persisted shadow
telemetry.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Mapping, Optional, Sequence

SCHEMA_VERSION = "belief-data-quality-v1"
STATUS_VALUES = ("ok", "unavailable", "stale", "invalid")


def parse_time(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_rate(numerator: int | float, denominator: int | float) -> Optional[float]:
    if not denominator:
        return None
    return round(float(numerator) / float(denominator), 6)


def percentile(values: Sequence[float], q: float) -> Optional[float]:
    rows = sorted(float(x) for x in values if math.isfinite(float(x)))
    if not rows:
        return None
    if len(rows) == 1:
        return round(rows[0], 6)
    pos = max(0.0, min(1.0, q)) * (len(rows) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return round(rows[lo], 6)
    w = pos - lo
    return round(rows[lo] * (1.0 - w) + rows[hi] * w, 6)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def read_jsonl_with_health(path: Path) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    parse_errors = 0
    if not path.exists():
        return {"rows": rows, "parse_errors": 0, "exists": False}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            parse_errors += 1
            continue
        if isinstance(item, dict):
            rows.append(item)
        else:
            parse_errors += 1
    return {"rows": rows, "parse_errors": parse_errors, "exists": True}


def _age_hours(when: Any, now: datetime) -> Optional[float]:
    dt = parse_time(when)
    if dt is None:
        return None
    return max(0.0, (now - dt).total_seconds() / 3600.0)


def _latency_hours(start: Any, end: Any) -> Optional[float]:
    a, b = parse_time(start), parse_time(end)
    if a is None or b is None or b < a:
        return None
    return (b - a).total_seconds() / 3600.0


def _group_health(rows: Sequence[Mapping[str, Any]], key: str, now: datetime) -> Dict[str, Any]:
    grouped: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key) or "unknown")].append(row)
    out: Dict[str, Any] = {}
    for label, group in sorted(grouped.items()):
        statuses = Counter(str(x.get("status") or "ok") for x in group)
        timestamps = [parse_time(x.get("observed_at")) for x in group]
        timestamps = [x for x in timestamps if x is not None]
        latest = max(timestamps) if timestamps else None
        latest_age = None if latest is None else max(0.0, (now - latest).total_seconds() / 3600.0)
        bad = statuses.get("stale", 0) + statuses.get("invalid", 0)
        unavailable = statuses.get("unavailable", 0)
        ok_rate = safe_rate(statuses.get("ok", 0), len(group))
        if latest is None:
            health = "unavailable"
        elif (ok_rate or 0.0) >= 0.95 and bad == 0:
            health = "healthy"
        elif (ok_rate or 0.0) >= 0.80:
            health = "watch"
        else:
            health = "degraded"
        out[label] = {
            "observations": len(group),
            "status_counts": {name: int(statuses.get(name, 0)) for name in STATUS_VALUES},
            "ok_rate": ok_rate,
            "stale_invalid_rate": safe_rate(bad, len(group)),
            "unavailable_rate": safe_rate(unavailable, len(group)),
            "latest_observed_at": None if latest is None else iso_z(latest),
            "latest_observation_age_hours": None if latest_age is None else round(latest_age, 6),
            "health": health,
        }
    return out


def observation_quality(rows: Sequence[Mapping[str, Any]], now: datetime) -> Dict[str, Any]:
    statuses = Counter(str(x.get("status") or "ok") for x in rows)
    ages = [x for x in (_age_hours(r.get("observed_at"), now) for r in rows) if x is not None]
    collection_latency = [
        x
        for x in (
            _latency_hours(
                r.get("observed_at"),
                r.get("collected_at") or (r.get("metadata") or {}).get("collected_at"),
            )
            for r in rows
        )
        if x is not None
    ]
    bad = statuses.get("stale", 0) + statuses.get("invalid", 0)
    return {
        "count": len(rows),
        "status_counts": {name: int(statuses.get(name, 0)) for name in STATUS_VALUES},
        "stale_invalid_rate": safe_rate(bad, len(rows)),
        "unavailable_rate": safe_rate(statuses.get("unavailable", 0), len(rows)),
        "freshness_age_hours": {
            "mean": None if not ages else round(mean(ages), 6),
            "p50": percentile(ages, 0.50),
            "p95": percentile(ages, 0.95),
            "max": None if not ages else round(max(ages), 6),
        },
        "collection_latency_hours": {
            "measured_count": len(collection_latency),
            "coverage_rate": safe_rate(len(collection_latency), len(rows)),
            "mean": None if not collection_latency else round(mean(collection_latency), 6),
            "p95": percentile(collection_latency, 0.95),
        },
        "by_adapter": _group_health(rows, "adapter", now),
        "by_source": _group_health(rows, "source", now),
    }


def forecast_quality(
    forecasts: Sequence[Mapping[str, Any]],
    verifications: Sequence[Mapping[str, Any]],
    now: datetime,
    *,
    evidence_time_field: str = "observed_at",
) -> Dict[str, Any]:
    verified_ids = {str(v.get("forecast_id")) for v in verifications if v.get("forecast_id")}
    due = [f for f in forecasts if parse_time(f.get("target_at")) and parse_time(f.get("target_at")) <= now]
    due_verified = [f for f in due if str(f.get("forecast_id")) in verified_ids]
    due_unresolved = [f for f in due if str(f.get("forecast_id")) not in verified_ids]

    complete = 0
    evidence_latencies: List[float] = []
    snapshot_evidence = 0
    fresh_snapshot_evidence = 0
    for forecast in forecasts:
        evidence = list(forecast.get("evidence_snapshot") or [])
        if (
            forecast.get("forecast_id")
            and forecast.get("forecast_at")
            and forecast.get("target_at")
            and forecast.get("predicted_probability") is not None
            and evidence
        ):
            complete += 1
        for item in evidence:
            snapshot_evidence += 1
            latency = _latency_hours(item.get(evidence_time_field), forecast.get("forecast_at"))
            if latency is not None:
                evidence_latencies.append(latency)
            freshness = item.get("freshness")
            if freshness is not None:
                try:
                    if float(freshness) >= 0.25:
                        fresh_snapshot_evidence += 1
                except (TypeError, ValueError):
                    pass

    eligible = sum(1 for v in verifications if bool(v.get("calibration_eligible", True)))
    return {
        "frozen_forecasts": len(forecasts),
        "snapshot_complete_count": complete,
        "frozen_forecast_coverage": safe_rate(complete, len(forecasts)),
        "verifications": len(verifications),
        "calibration_eligible_verifications": eligible,
        "calibration_eligible_rate": safe_rate(eligible, len(verifications)),
        "due_forecasts": len(due),
        "due_verified": len(due_verified),
        "due_unresolved": len(due_unresolved),
        "due_verification_coverage": safe_rate(len(due_verified), len(due)),
        "due_unresolved_rate": safe_rate(len(due_unresolved), len(due)),
        "forecast_evidence_latency_hours": {
            "measured_count": len(evidence_latencies),
            "mean": None if not evidence_latencies else round(mean(evidence_latencies), 6),
            "p50": percentile(evidence_latencies, 0.50),
            "p95": percentile(evidence_latencies, 0.95),
            "max": None if not evidence_latencies else round(max(evidence_latencies), 6),
        },
        "fresh_evidence_at_freeze_rate": safe_rate(fresh_snapshot_evidence, snapshot_evidence),
        "snapshot_evidence_count": snapshot_evidence,
    }


class DataQualityAdapter:
    """Diagnostic adapter. It cannot emit Belief Core Evidence."""

    name = "belief_data_quality"
    version = "1.0.0"
    emits_evidence = False
    decision_influence = False

    def build(
        self,
        *,
        observations: Sequence[Mapping[str, Any]],
        forecasts: Sequence[Mapping[str, Any]],
        verifications: Sequence[Mapping[str, Any]],
        scheduler: Mapping[str, Any],
        now: datetime,
        observation_parse_errors: int = 0,
        ledger_integrity: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        last_run = parse_time(scheduler.get("last_run_at"))
        last_external = parse_time(scheduler.get("last_external_run_at"))
        run_ages = [
            max(0.0, (now - item).total_seconds() / 3600.0)
            for item in (last_run, last_external)
            if item is not None
        ]
        gaps = list(scheduler.get("gaps") or [])
        recent_gaps = 0
        for gap in gaps:
            ts = parse_time(gap.get("timestamp"))
            if ts is not None and now - ts <= timedelta(hours=24):
                recent_gaps += 1
        observations_report = observation_quality(observations, now)
        coverage = forecast_quality(forecasts, verifications, now)
        stale_invalid = observations_report.get("stale_invalid_rate")
        if ledger_integrity and ledger_integrity.get("valid") is False:
            health = "critical"
        elif not observations:
            health = "awaiting_observations"
        elif observation_parse_errors or recent_gaps >= 3 or (stale_invalid is not None and stale_invalid > 0.10):
            health = "degraded"
        elif recent_gaps or (stale_invalid is not None and stale_invalid > 0.02):
            health = "watch"
        else:
            health = "healthy"
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": iso_z(now),
            "adapter": self.name,
            "adapter_version": self.version,
            "emits_evidence": False,
            "decision_influence": False,
            "health": health,
            "observations": observations_report,
            "forecast_coverage": coverage,
            "pipeline": {
                "latest_runtime_age_hours": None if not run_ages else round(min(run_ages), 6),
                "last_run_at": scheduler.get("last_run_at"),
                "last_external_run_at": scheduler.get("last_external_run_at"),
                "recent_gap_count_24h": recent_gaps,
                "stored_gap_count": len(gaps),
                "observation_jsonl_parse_errors": int(observation_parse_errors),
                "ledger_integrity": dict(ledger_integrity or {}),
                "last_market_status": dict(scheduler.get("last_status") or {}),
                "last_external_status": dict(scheduler.get("last_external_status") or {}),
            },
        }
