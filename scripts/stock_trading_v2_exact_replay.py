#!/usr/bin/env python3
"""Exact replay adapters for deployable Stock Trading v2 Challenger artifacts."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable, Mapping

ENTRY_BLOCKERS = {
    "entry_score_below_threshold",
    "score",
    "entry_score",
    "minimum_composite_score",
}


def parse_dt(value: Any) -> datetime:
    text = str(value or "").replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError("timestamp must be timezone aware")
    return dt


def source_group(event: Mapping[str, Any]) -> str:
    return str(((event.get("source") or {}).get("payload_sha256")) or event.get("event_id") or "")


def composite_score(event: Mapping[str, Any]) -> float | None:
    score_state = ((event.get("candidate_state") or {}).get("score_state") or {})
    for key in ("opening_adjusted_score", "score", "composite_score", "legacy_composite_score", "quant_pre_score"):
        value = score_state.get(key)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            return numeric
    return None


def failed_gate_names(event: Mapping[str, Any]) -> list[str]:
    state = event.get("candidate_state") or {}
    path = state.get("decision_path") or {}
    names: list[str] = []
    for gate in path.get("gates") or []:
        if not isinstance(gate, Mapping) or gate.get("passed") is not False:
            continue
        name = str(gate.get("name") or "").strip()
        if name:
            names.append(name)
    if names:
        return names
    blocker = path.get("first_blocking_gate") or state.get("first_blocking_gate")
    if isinstance(blocker, Mapping):
        name = str(blocker.get("name") or "").strip()
        return [name] if name else []
    return [str(blocker)] if blocker else []


def is_single_entry_blocker(event: Mapping[str, Any]) -> bool:
    names = failed_gate_names(event)
    return len(names) == 1 and names[0] in ENTRY_BLOCKERS


def outcome_net_percent(row: Mapping[str, Any]) -> float | None:
    replay = row.get("replay") or {}
    status = replay.get("status")
    if status == "NOT_ACTIVATED":
        return 0.0
    if status != "SETTLED":
        return None
    try:
        value = float(replay.get("net_return_percent"))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def entry_thresholds(candidate: Mapping[str, Any]) -> tuple[float, float]:
    replay = candidate.get("replay_contract") or {}
    if replay.get("adapter") != "entry_threshold_v1":
        raise ValueError("unsupported exact replay adapter")
    champion = float(replay.get("champion_threshold"))
    challenger = float(replay.get("challenger_threshold"))
    if not (challenger < champion):
        raise ValueError("entry threshold challenger must be a bounded relaxation")
    spec = candidate.get("deployment_spec") or {}
    values: list[float] = []
    for target, patches in (spec.get("targets") or {}).items():
        if target not in {"gpw_daily_config", "stock_trading_policy"}:
            continue
        for patch in patches or []:
            path = tuple(str(part) for part in (patch.get("path") or []))
            if path in {("minimum_composite_score",), ("markets", "GPW", "minimum_entry_score")}:
                values.append(float(patch.get("value")))
    if len(values) != 2 or any(abs(value - challenger) > 1e-9 for value in values):
        raise ValueError("deployment spec does not match exact replay threshold")
    return champion, challenger


def collect_entry_threshold_samples(
    *,
    candidate: Mapping[str, Any],
    events: Iterable[Mapping[str, Any]],
    admissions: Iterable[Mapping[str, Any]],
    outcomes: Iterable[Mapping[str, Any]],
    after: datetime | None = None,
    before_or_at: datetime | None = None,
) -> list[dict[str, Any]]:
    champion_threshold, challenger_threshold = entry_thresholds(candidate)
    horizon = int(candidate.get("horizon_sessions") or 0)
    event_map: dict[str, dict[str, Any]] = {}
    groups: dict[str, list[str]] = defaultdict(list)
    for raw in events:
        event = dict(raw)
        if str(event.get("market") or "").upper() != "GPW":
            continue
        try:
            decision_dt = parse_dt(event.get("decision_at"))
        except Exception:
            continue
        if after is not None and decision_dt <= after:
            continue
        if before_or_at is not None and decision_dt > before_or_at:
            continue
        event_id = str(event.get("event_id") or "")
        if not event_id:
            continue
        event_map[event_id] = event
        groups[source_group(event)].append(event_id)

    admission_map: dict[str, dict[str, Any]] = {}
    for row in admissions:
        event_id = str(row.get("source_event_id") or "")
        if event_id:
            admission_map[event_id] = dict(row)

    outcome_map: dict[str, dict[str, Any]] = {}
    for row in outcomes:
        try:
            row_horizon = int(row.get("horizon_sessions") or 0)
        except (TypeError, ValueError):
            continue
        if row_horizon == horizon:
            outcome_map[str(row.get("source_event_id") or "")] = dict(row)

    samples: list[dict[str, Any]] = []
    for group, event_ids in groups.items():
        if any(((admission_map.get(event_id, {}).get("champion") or {}).get("action")) == "LONG" for event_id in event_ids):
            continue
        eligible: list[tuple[float, dict[str, Any], float]] = []
        for event_id in event_ids:
            event = event_map[event_id]
            if event.get("selected") is True or not is_single_entry_blocker(event):
                continue
            score = composite_score(event)
            if score is None or not (challenger_threshold <= score < champion_threshold):
                continue
            outcome = outcome_map.get(event_id)
            if not outcome:
                continue
            net = outcome_net_percent(outcome)
            if net is None:
                continue
            eligible.append((score, event, net))
        if not eligible:
            continue
        eligible.sort(key=lambda item: (-item[0], str(item[1].get("symbol") or "")))
        score, event, net = eligible[0]
        samples.append({
            "source_group": group,
            "decision_at": event.get("decision_at"),
            "session_date": event.get("session_date"),
            "symbol": event.get("symbol"),
            "source_event_id": event.get("event_id"),
            "champion_action": "CASH",
            "challenger_action": "LONG",
            "champion_score_threshold": champion_threshold,
            "challenger_score_threshold": challenger_threshold,
            "candidate_score": round(score, 8),
            "champion_net_return_percent": 0.0,
            "challenger_net_return_percent": round(net, 8),
            "incremental_net_return_percent": round(net, 8),
        })
    samples.sort(key=lambda row: (str(row.get("decision_at")), str(row.get("source_group"))))
    return samples
