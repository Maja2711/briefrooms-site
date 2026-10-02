#!/usr/bin/env python3
from __future__ import annotations

"""Low-latency macro-event context for Daily EUR/USD.

The fast lane is deliberately narrower than the full Belief Core collection:
official macro calendar + BLS data + sourced expectations + EURUSD-only Gemini.
It creates no trades. Daily EUR/USD consumes the compact context for risk
management and post-release entry gating.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from belief_adapter_contract import Observation
from belief_core import iso_z, parse_time
from belief_llm_interpreter import EURUSD_ALLOWED_BELIEFS, GeminiEvidenceInterpreter
from belief_macro_calendar_adapter import MacroEventCalendarAdapter
from belief_macro_data_adapter import MacroDataAdapter
from belief_macro_expectations_adapter import MacroExpectationsAdapter

EURUSD_EVENT_TERMS = (
    "employment situation",
    "consumer price index",
    "cpi",
    "personal income and outlays",
    "pce",
    "gross domestic product",
    "gdp",
    "fomc",
    "federal open market committee",
    "ecb",
    "monetary policy",
    "hicp",
    "inflation",
)
ACTIVE_BEFORE_HOURS = 2.0
ACTIVE_AFTER_HOURS = 1.0


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _high_impact_events(observations: Sequence[Observation], now: datetime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for obs in observations:
        if obs.adapter != "macro_event_calendar" or obs.metric != "scheduled_macro_event":
            continue
        meta = obs.metadata if isinstance(obs.metadata, Mapping) else {}
        if str(meta.get("importance") or "") != "high":
            continue
        title = str(meta.get("title") or "")
        if not any(term in title.lower() for term in EURUSD_EVENT_TERMS):
            continue
        event_at = _dt(meta.get("event_at"))
        if event_at is None:
            continue
        hours_until = (event_at - now.astimezone(timezone.utc)).total_seconds() / 3600.0
        if -ACTIVE_AFTER_HOURS <= hours_until <= ACTIVE_BEFORE_HOURS:
            rows.append({
                "uid": meta.get("uid"),
                "title": title,
                "event_at": iso_z(event_at),
                "hours_until_at_check": round(hours_until, 4),
                "source": obs.source,
                "source_ref": obs.source_ref,
                "importance": "high",
            })
    rows.sort(key=lambda row: abs(float(row["hours_until_at_check"])))
    return rows


def _previous_month(event_at: datetime) -> str:
    year = event_at.year
    month = event_at.month - 1
    if month == 0:
        year -= 1
        month = 12
    return f"{year:04d}-{month:02d}"


def _expectation_for_event(
    observations: Sequence[Observation],
    event: Mapping[str, Any],
) -> Observation | None:
    event_at = _dt(event.get("event_at"))
    if event_at is None:
        return None
    candidates = []
    for obs in observations:
        if obs.adapter != "macro_expectations" or obs.metric != "macro_expectation_distribution":
            continue
        meta = obs.metadata if isinstance(obs.metadata, Mapping) else {}
        obs_event = _dt(meta.get("event_at"))
        if obs_event is None:
            continue
        delta = abs((obs_event - event_at).total_seconds())
        if delta <= 2 * 3600:
            candidates.append((delta, obs))
    return min(candidates, key=lambda pair: pair[0])[1] if candidates else None


def _macro_actuals_for_event(
    observations: Sequence[Observation],
    event: Mapping[str, Any],
) -> list[Observation]:
    title = str(event.get("title") or "").lower()
    event_at = _dt(event.get("event_at"))
    if event_at is None:
        return []
    expected_period = _previous_month(event_at)
    wanted: set[str] = set()
    if "employment situation" in title or "unemployment" in title:
        wanted = {"total_nonfarm_payroll_level", "unemployment_rate"}
    elif "consumer price index" in title or title.strip() == "cpi":
        wanted = {"cpi_index_sa"}
    if not wanted:
        return []

    rows = []
    for obs in observations:
        if obs.adapter != "macro_data" or obs.metric not in wanted or obs.status != "ok":
            continue
        meta = obs.metadata if isinstance(obs.metadata, Mapping) else {}
        if str(meta.get("data_period") or "") != expected_period:
            continue
        rows.append(obs)
    return rows


def _composite_observation(
    *,
    now: datetime,
    event: Mapping[str, Any],
    actuals: Sequence[Observation],
    expectation: Observation | None,
) -> Observation | None:
    if not actuals and expectation is None:
        return None

    actual_payload = [
        {
            "metric": obs.metric,
            "value": obs.value,
            "unit": obs.unit,
            "metadata": dict(obs.metadata),
            "source": obs.source,
            "source_ref": obs.source_ref,
        }
        for obs in actuals
    ]
    expectation_payload = None
    if expectation is not None:
        expectation_payload = {
            "value": expectation.value,
            "unit": expectation.unit,
            "metadata": dict(expectation.metadata),
            "source": expectation.source,
            "source_ref": expectation.source_ref,
        }

    phase = "POST_RELEASE" if float(event.get("hours_until_at_check") or 0.0) < 0 else "PRE_RELEASE"
    document = {
        "task_context": "EUR/USD high-impact macro event",
        "phase": phase,
        "event": dict(event),
        "official_actuals": actual_payload,
        "sourced_expectations": expectation_payload,
        "instruction": (
            "Assess only the supplied numbers and sources. Do not invent consensus, forecasts, revisions, "
            "or unstated bank views. For PRE_RELEASE, treat expectations as uncertainty/scenario evidence. "
            "For POST_RELEASE, compare official actuals with any supplied expectations and recent trend data."
        ),
    }

    refs = [str(row["source_ref"]) for row in actual_payload if row.get("source_ref")]
    if expectation_payload and expectation_payload.get("source_ref"):
        refs.append(str(expectation_payload["source_ref"]))
    if event.get("source_ref"):
        refs.append(str(event["source_ref"]))
    source_ref = ";".join(sorted(set(refs))) or "derived:daily-eurusd-macro-fast"

    return Observation.make(
        adapter="daily_eurusd_macro_fast",
        metric="eurusd_macro_event_package",
        entity="EURUSD",
        observed_at=iso_z(now),
        value=document,
        unit="structured_macro_event",
        source="BriefRooms sourced macro fast lane",
        source_type="derived",
        source_ref=source_ref,
        reliability=0.90 if actuals else 0.76,
        independence_cluster=f"eurusd_macro_fast:{event.get('uid') or event.get('event_at')}",
        tags=("EURUSD", "macro", "fast_lane", phase.lower()),
        metadata={
            "document_text": json.dumps(document, ensure_ascii=False, sort_keys=True, default=str),
            "event_uid": event.get("uid"),
            "event_at": event.get("event_at"),
            "phase": phase,
            "actual_count": len(actuals),
            "expectations_available": expectation is not None,
        },
    )


def _llm_payload(result) -> dict[str, Any] | None:
    if result is None:
        return None
    row = result.interpretation
    score = float(row.direction or 0) * float(row.strength) * float(row.confidence) * float(row.materiality) * 10.0
    return {
        "belief_id": row.belief_id,
        "direction": row.direction,
        "strength": round(float(row.strength), 6),
        "confidence": round(float(row.confidence), 6),
        "materiality": round(float(row.materiality), 6),
        "horizon_hours": row.horizon_hours,
        "summary": row.summary,
        "alternative_hypothesis": row.alternative_hypothesis,
        "model": row.model,
        "eurusd_score": round(score, 4),
    }


def _semantic(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in {"generated_at", "checked_at"}}


def build_context(
    now: datetime,
    *,
    calendar: MacroEventCalendarAdapter,
    macro_data: MacroDataAdapter,
    expectations: MacroExpectationsAdapter,
    interpreter: GeminiEvidenceInterpreter,
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    now = now.astimezone(timezone.utc)
    calendar_result = calendar.run(now)
    events = _high_impact_events(calendar_result.observations, now)
    if not events:
        return {
            "schema_version": "daily-eurusd-macro-fast-v1",
            "status": "CLEAR",
            "event": None,
            "expectations": None,
            "actuals": [],
            "llm": None,
            "eurusd_score": 0.0,
            "decision_influence": False,
        }

    event = events[0]
    event_at = _dt(event["event_at"])
    assert event_at is not None
    hours_until = (event_at - now).total_seconds() / 3600.0
    phase = "PRE_RELEASE" if hours_until >= 0 else "POST_RELEASE"

    expectation_result = expectations.run(now)
    expectation = _expectation_for_event(expectation_result.observations, event)
    macro_result = macro_data.run(now)
    actuals = _macro_actuals_for_event(macro_result.observations, event) if phase == "POST_RELEASE" else []

    package = _composite_observation(
        now=now,
        event={**event, "hours_until_at_check": round(hours_until, 4)},
        actuals=actuals,
        expectation=expectation,
    )

    expectation_summary = None
    if expectation is not None:
        expectation_summary = {
            "value": expectation.value,
            "metadata": {
                "provider": expectation.metadata.get("provider"),
                "forecast_count": expectation.metadata.get("forecast_count"),
                "market_consensus": expectation.metadata.get("market_consensus"),
            },
        }

    actual_summary = [
        {
            "metric": obs.metric,
            "value": obs.value,
            "unit": obs.unit,
            "metadata": dict(obs.metadata),
            "source_ref": obs.source_ref,
        }
        for obs in actuals
    ]

    data_key = json.dumps({
        "event_uid": event.get("uid"),
        "phase": phase,
        "expectations": expectation_summary,
        "actuals": actual_summary,
    }, ensure_ascii=False, sort_keys=True, default=str)

    previous_llm = None
    if previous and str(previous.get("llm_key") or "") == data_key:
        previous_llm = previous.get("llm") if isinstance(previous.get("llm"), Mapping) else None

    llm = previous_llm
    if llm is None and package is not None and interpreter.available:
        llm = _llm_payload(interpreter.interpret(package))

    score = float((llm or {}).get("eurusd_score") or 0.0)
    post_release_ready = phase == "POST_RELEASE" and bool(actuals) and llm is not None
    return {
        "schema_version": "daily-eurusd-macro-fast-v1",
        "status": phase,
        "event": {**event, "hours_until_at_check": round(hours_until, 4)},
        "expectations": expectation_summary,
        "actuals": actual_summary,
        "llm_key": data_key,
        "llm": llm,
        "eurusd_score": round(score, 4),
        "decision_influence": bool(llm),
        "post_release_ready": post_release_ready,
        "rules": {
            "no_invented_consensus": True,
            "eurusd_only_llm_beliefs": list(EURUSD_ALLOWED_BELIEFS),
            "pre_event_expectations_require_sourced_bundle": True,
            "post_release_requires_official_actuals": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/investments/eurusd_macro_fast_context.json")
    parser.add_argument("--now")
    args = parser.parse_args()

    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    output = Path(args.output)
    previous: dict[str, Any] = {}
    try:
        previous = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        previous = {}

    context = build_context(
        now,
        calendar=MacroEventCalendarAdapter(),
        macro_data=MacroDataAdapter(),
        expectations=MacroExpectationsAdapter(),
        interpreter=GeminiEvidenceInterpreter(allowed_beliefs=EURUSD_ALLOWED_BELIEFS),
        previous=previous,
    )

    if _semantic(previous) == _semantic(context):
        print(json.dumps({"changed": False, "status": context.get("status")}, sort_keys=True))
        return 0

    context["generated_at"] = iso_z(now)
    context["checked_at"] = iso_z(now)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"changed": True, "status": context.get("status"), "output": str(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
