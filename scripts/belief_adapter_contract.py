from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, Sequence, Tuple

from belief_core import Evidence, parse_time


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(x) for x in parts)
    return f"{prefix}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def strength_from_return(value: float, full_scale: float, floor: float = 0.08) -> float:
    if not math.isfinite(value):
        return 0.0
    return clamp(abs(value) / max(1e-9, full_scale), floor, 1.0)


@dataclass(frozen=True)
class Observation:
    """Source- or feature-level fact before it becomes Belief Core evidence.

    `status="unavailable"` is explicit: adapters must not fabricate missing fields
    such as a bid/ask spread when the upstream source only provides OHLCV.
    """

    observation_id: str
    adapter: str
    metric: str
    entity: str
    observed_at: str
    value: Any
    unit: str
    source: str
    source_type: str
    source_ref: str
    reliability: float
    independence_cluster: str
    status: str = "ok"
    tags: Tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.observation_id.strip() or not self.adapter.strip() or not self.metric.strip():
            raise ValueError("observation_id, adapter and metric must be non-empty")
        if self.status not in {"ok", "unavailable", "stale", "invalid"}:
            raise ValueError("unsupported observation status")
        if self.source_type not in {"primary", "secondary", "derived"}:
            raise ValueError("source_type must be primary, secondary or derived")
        if not 0.0 <= float(self.reliability) <= 1.0:
            raise ValueError("reliability must be in [0,1]")
        if not self.independence_cluster.strip():
            raise ValueError("independence_cluster must be non-empty")
        parse_time(self.observed_at)

    @classmethod
    def make(
        cls,
        *,
        adapter: str,
        metric: str,
        entity: str,
        observed_at: str,
        value: Any,
        unit: str,
        source: str,
        source_type: str,
        source_ref: str,
        reliability: float,
        independence_cluster: str,
        status: str = "ok",
        tags: Sequence[str] = (),
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> "Observation":
        oid = stable_id("obs", adapter, metric, entity, observed_at, source_ref, status)
        return cls(
            observation_id=oid,
            adapter=adapter,
            metric=metric,
            entity=entity,
            observed_at=observed_at,
            value=value,
            unit=unit,
            source=source,
            source_type=source_type,
            source_ref=source_ref,
            reliability=clamp(reliability),
            independence_cluster=independence_cluster,
            status=status,
            tags=tuple(tags),
            metadata=dict(metadata or {}),
        )


@dataclass(frozen=True)
class EvidenceAssessment:
    belief_id: str
    direction: int
    strength: float
    evidence_type: str
    note: str
    independence_cluster: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.direction not in (-1, 1):
            raise ValueError("direction must be -1 or +1")
        if not 0.0 <= float(self.strength) <= 1.0:
            raise ValueError("strength must be in [0,1]")


def observation_to_evidence(observation: Observation, assessment: EvidenceAssessment) -> Evidence:
    """Single canonical Observation -> Evidence boundary used by all adapters.

    Derived Evidence records its originating Observation as the immediate lineage
    node. This keeps the provenance graph explicit even when raw observations are
    intentionally not promoted into belief-weighting Evidence of their own.
    """
    if observation.status != "ok":
        raise ValueError(f"observation {observation.observation_id} is not evidence-eligible: {observation.status}")
    cluster = assessment.independence_cluster or observation.independence_cluster
    evidence_id = stable_id(
        "ev",
        assessment.belief_id,
        observation.observation_id,
        assessment.direction,
        round(float(assessment.strength), 6),
        cluster,
    )
    metadata = {
        "adapter": observation.adapter,
        "observation_id": observation.observation_id,
        "observation_metric": observation.metric,
        "observation_value": observation.value,
        "observation_unit": observation.unit,
        "lineage_node_type": "observation" if observation.source_type == "derived" else "source",
        **dict(observation.metadata),
        **dict(assessment.metadata),
    }
    return Evidence(
        evidence_id=evidence_id,
        belief_id=assessment.belief_id,
        source=observation.source,
        observed_at=observation.observed_at,
        direction=assessment.direction,
        strength=clamp(assessment.strength),
        reliability=clamp(observation.reliability),
        independence_cluster=cluster,
        source_type=observation.source_type,
        source_ref=observation.source_ref,
        derived_from=(observation.observation_id,) if observation.source_type == "derived" else (),
        evidence_type=assessment.evidence_type,
        note=assessment.note,
        metadata=metadata,
    )


@dataclass(frozen=True)
class AdapterResult:
    adapter: str
    observations: Tuple[Observation, ...]
    evidence: Tuple[Evidence, ...]


class EvidenceAdapter(Protocol):
    name: str
    version: str

    def run(self, snapshot: Any) -> AdapterResult: ...
