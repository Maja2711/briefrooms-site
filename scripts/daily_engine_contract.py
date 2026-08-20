#!/usr/bin/env python3
"""Canonical output contract for BriefRooms Daily Trading engines."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

SCHEMA_VERSION = "daily-engine-output-v1"
DIRECTIONS = {"LONG", "SHORT", "FLAT"}
DECISION_MODES = {"WITHOUT", "WITH"}


@dataclass(frozen=True)
class DailyEngineOutput:
    instrument: str
    timestamp: str
    direction: str
    score: float
    confidence: float
    entry: float | None
    stop: float | None
    target: float | None
    horizon: str
    engine_version: str
    status: str = "SHADOW"
    decision_mode: str = "WITHOUT"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> "DailyEngineOutput":
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported daily engine schema")
        if self.direction not in DIRECTIONS:
            raise ValueError("direction must be LONG, SHORT or FLAT")
        if self.decision_mode not in DECISION_MODES:
            raise ValueError("decision_mode must be WITHOUT or WITH")
        if not self.instrument or not self.timestamp or not self.engine_version or not self.horizon:
            raise ValueError("instrument, timestamp, horizon and engine_version are required")
        if not _finite(self.score) or not 0.0 <= float(self.score) <= 100.0:
            raise ValueError("score must be in [0,100]")
        if not _finite(self.confidence) or not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be in [0,1]")

        if self.direction == "FLAT":
            if any(x is not None for x in (self.entry, self.stop, self.target)):
                raise ValueError("FLAT output cannot expose directional entry/stop/target")
            return self

        if not all(_finite(x) for x in (self.entry, self.stop, self.target)):
            raise ValueError("directional output requires finite entry/stop/target")
        entry, stop, target = float(self.entry), float(self.stop), float(self.target)
        if self.direction == "LONG" and not (stop < entry < target):
            raise ValueError("LONG geometry must satisfy stop < entry < target")
        if self.direction == "SHORT" and not (target < entry < stop):
            raise ValueError("SHORT geometry must satisfy target < entry < stop")
        return self

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


def _finite(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def validate_daily_engine_output(payload: Mapping[str, Any]) -> None:
    DailyEngineOutput(
        instrument=str(payload.get("instrument") or ""),
        timestamp=str(payload.get("timestamp") or ""),
        direction=str(payload.get("direction") or ""),
        score=float(payload.get("score")),
        confidence=float(payload.get("confidence")),
        entry=payload.get("entry"),
        stop=payload.get("stop"),
        target=payload.get("target"),
        horizon=str(payload.get("horizon") or ""),
        engine_version=str(payload.get("engine_version") or ""),
        status=str(payload.get("status") or "SHADOW"),
        decision_mode=str(payload.get("decision_mode") or "WITHOUT"),
        metadata=payload.get("metadata") or {},
        schema_version=str(payload.get("schema_version") or ""),
    ).validate()
