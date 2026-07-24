#!/usr/bin/env python3
"""Point-in-time Macro & Event Intelligence primitives for BRACE-SPX.

The module intentionally does not scrape or label news.  It defines the strict,
auditable contract that future data adapters must satisfy before event features
can enter a backtest.  Later summaries, revised observations and timestamps
that post-date a signal are rejected.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping, Sequence

import pandas as pd

ALLOWED_EVENT_TYPES = {
    "macro_release",
    "central_bank",
    "election",
    "policy_announcement",
    "sanctions",
    "geopolitical_shock",
    "armed_conflict",
    "natural_disaster",
    "infrastructure_disruption",
}
ALLOWED_SCHEDULE_TYPES = {"scheduled", "unscheduled"}
ALLOWED_DIRECTIONS = {"negative", "neutral", "positive", "mixed", "unknown"}


@dataclass(frozen=True)
class PointInTimeEvent:
    event_id: str
    event_type: str
    schedule_type: str
    occurred_at_utc: str
    first_public_at_utc: str
    ingested_at_utc: str
    source_url: str
    source_name: str
    source_tier: int
    headline: str
    direction: str = "unknown"
    intensity: float = 0.0
    novelty: float = 0.0
    confidence: float = 0.0
    affected_assets: tuple[str, ...] = ()
    actual_value: float | None = None
    consensus_value: float | None = None
    prior_value_as_known: float | None = None
    revision_vintage: str | None = None

    def validate(self) -> None:
        if self.event_type not in ALLOWED_EVENT_TYPES:
            raise ValueError(f"Unsupported event_type: {self.event_type}")
        if self.schedule_type not in ALLOWED_SCHEDULE_TYPES:
            raise ValueError(f"Unsupported schedule_type: {self.schedule_type}")
        if self.direction not in ALLOWED_DIRECTIONS:
            raise ValueError(f"Unsupported direction: {self.direction}")
        for name, value in (("intensity", self.intensity), ("novelty", self.novelty), ("confidence", self.confidence)):
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name} must be within [0, 1]")
        if not 1 <= int(self.source_tier) <= 5:
            raise ValueError("source_tier must be within [1, 5]")
        occurred = _parse_utc(self.occurred_at_utc)
        public = _parse_utc(self.first_public_at_utc)
        ingested = _parse_utc(self.ingested_at_utc)
        if public < occurred and self.schedule_type == "unscheduled":
            raise ValueError("An unscheduled event cannot be public before it occurred")
        if ingested < public:
            raise ValueError("ingested_at_utc cannot precede first_public_at_utc")
        if self.event_type == "macro_release" and self.actual_value is not None and self.consensus_value is None:
            raise ValueError("Macro surprise requires the pre-release consensus")

    def stable_hash(self) -> str:
        payload = asdict(self)
        payload["affected_assets"] = sorted(self.affected_assets)
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamps must be timezone-aware UTC")
    return parsed.astimezone(timezone.utc)


def available_before(events: Iterable[PointInTimeEvent], signal_time_utc: str) -> list[PointInTimeEvent]:
    """Return only records publicly available no later than signal time."""
    signal_time = _parse_utc(signal_time_utc)
    selected: list[PointInTimeEvent] = []
    for event in events:
        event.validate()
        if _parse_utc(event.first_public_at_utc) <= signal_time:
            selected.append(event)
    return sorted(selected, key=lambda item: (item.first_public_at_utc, item.event_id))


def macro_surprise(event: PointInTimeEvent, scale: float | None = None) -> float | None:
    """Standardized first-release surprise without later revisions."""
    event.validate()
    if event.event_type != "macro_release" or event.actual_value is None or event.consensus_value is None:
        return None
    denominator = abs(float(scale)) if scale not in (None, 0.0) else max(abs(float(event.consensus_value)), 1.0)
    return (float(event.actual_value) - float(event.consensus_value)) / denominator


def monthly_event_features(
    events: Sequence[PointInTimeEvent],
    signal_times: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Build simple auditable event-window features known at each signal time.

    This is deliberately conservative: no free-form sentiment and no hindsight
    categories. Features summarize the preceding 31 days using first-public
    timestamps only.
    """
    rows: list[Mapping[str, float]] = []
    for signal in signal_times:
        signal_utc = signal.tz_localize("UTC") if signal.tzinfo is None else signal.tz_convert("UTC")
        start = signal_utc - pd.Timedelta(days=31)
        known = [
            event for event in available_before(events, signal_utc.isoformat())
            if _parse_utc(event.first_public_at_utc) > start.to_pydatetime()
        ]
        macro = [event for event in known if event.event_type == "macro_release"]
        unscheduled = [event for event in known if event.schedule_type == "unscheduled"]
        negative_pressure = sum(
            event.intensity * event.confidence
            for event in known
            if event.direction == "negative"
        )
        positive_pressure = sum(
            event.intensity * event.confidence
            for event in known
            if event.direction == "positive"
        )
        surprises = [value for event in macro if (value := macro_surprise(event)) is not None]
        rows.append({
            "event_count_31d": float(len(known)),
            "unscheduled_event_count_31d": float(len(unscheduled)),
            "negative_event_pressure_31d": float(negative_pressure),
            "positive_event_pressure_31d": float(positive_pressure),
            "macro_surprise_mean_31d": float(sum(surprises) / len(surprises)) if surprises else 0.0,
            "macro_release_count_31d": float(len(macro)),
        })
    return pd.DataFrame(rows, index=signal_times)
