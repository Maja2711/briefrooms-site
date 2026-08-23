#!/usr/bin/env python3
"""Authoritative, epistemically reversible state projection for Belief Core.

The layer is deliberately read-only with respect to Belief Core.  It compresses
BeliefState into a bounded machine-readable view while preserving a deterministic
path back to contributing beliefs, evidence, observations and source references.

Aggregate Authority Principle:
- EpistemicState is the authoritative compressed view exposed to reasoning clients.
- reasoning clients may inspect/explain/challenge/request recalculation;
- they may not override probability/confidence/reliability or mutate Belief Core.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

UPDATE_GAIN = 1.65
CONTRACT_VERSION = "belief-epistemic-state-v1"
AUTHORITY_POLICY = "aggregate-authority-v1"
DRILLDOWN_POLICY = "bounded-drilldown-v1"


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _logit(probability: float) -> float:
    p = _clamp(probability, 1e-6, 1.0 - 1e-6)
    return math.log(p / (1.0 - p))


def _logistic(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def _parse_time(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(x) for x in parts)
    return f"{prefix}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


@dataclass(frozen=True)
class AggregateAuthority:
    policy: str = AUTHORITY_POLICY
    authoritative_state: str = "epistemic_state"
    llm_may_inspect: bool = True
    llm_may_explain: bool = True
    llm_may_challenge: bool = True
    llm_may_request_recalculation: bool = True
    llm_may_override_probability: bool = False
    llm_may_override_confidence: bool = False
    llm_may_override_reliability: bool = False
    llm_may_ignore_aggregate: bool = False
    belief_core_writeback_enabled: bool = False


@dataclass(frozen=True)
class DrilldownPolicy:
    policy: str = DRILLDOWN_POLICY
    confidence_below: float = 0.50
    contradiction_above: float = 0.55
    absolute_probability_delta_above: float = 0.15
    high_impact_always: bool = True
    audit_nonclean_always: bool = True
    max_depth: int = 4
    max_evidence_per_side: int = 3
    max_sources: int = 6
    max_reinspection_cycles: int = 1


@dataclass(frozen=True)
class Contribution:
    contributor_type: str
    contributor_id: str
    signed_probability_delta: float
    direction: int
    source_ref: Optional[str] = None
    observation_id: Optional[str] = None


@dataclass(frozen=True)
class EpistemicState:
    state_id: str
    topic: str
    entity: str
    domain: str
    probability: float
    confidence: float
    previous_probability: Optional[float]
    delta_probability: Optional[float]
    contradiction: float
    freshness: float
    audit_status: str
    member_belief_ids: Tuple[str, ...]
    contributions: Tuple[Contribution, ...]
    dominant_support_evidence_ids: Tuple[str, ...]
    dominant_opposition_evidence_ids: Tuple[str, ...]
    provenance_root: Mapping[str, Any]
    drilldown_required: bool
    drilldown_reasons: Tuple[str, ...]
    authority: AggregateAuthority = field(default_factory=AggregateAuthority)
    drilldown_policy: DrilldownPolicy = field(default_factory=DrilldownPolicy)
    contract_version: str = CONTRACT_VERSION

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        return payload


class EpistemicStateBuilder:
    """Read-only projection over an already-computed Belief Core state payload."""

    def __init__(self, state_payload: Mapping[str, Any], observations: Iterable[Mapping[str, Any]] = ()) -> None:
        self.state_payload = dict(state_payload)
        self.definitions = {str(x["belief_id"]): dict(x) for x in state_payload.get("definitions", [])}
        self.beliefs = {str(x["belief_id"]): dict(x) for x in state_payload.get("beliefs", [])}
        self.evidence = {str(x["evidence_id"]): dict(x) for x in state_payload.get("evidence", [])}
        self.observations = {str(x["observation_id"]): dict(x) for x in observations if x.get("observation_id")}

    @staticmethod
    def load_observations(path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not row.get("observation_id"):
                raise ValueError(f"observations.jsonl line {line_no} lacks observation_id")
            rows.append(row)
        return rows

    def _effective_mass(self, evidence: Mapping[str, Any], definition: Mapping[str, Any], as_of: str) -> float:
        observed_at = _parse_time(str(evidence["observed_at"]))
        as_dt = _parse_time(as_of)
        age_hours = max(0.0, (as_dt - observed_at).total_seconds() / 3600.0)
        half_life = max(0.01, float(definition.get("half_life_hours", 72.0)))
        freshness = 0.5 ** (age_hours / half_life)
        return _clamp(float(evidence.get("strength", 0.0))) * _clamp(float(evidence.get("reliability", 0.0))) * freshness

    def _loo_contributions(self, belief: Mapping[str, Any]) -> List[Contribution]:
        belief_id = str(belief["belief_id"])
        definition = self.definitions[belief_id]
        reps = [self.evidence[eid] for eid in belief.get("representative_evidence_ids", []) if eid in self.evidence]
        as_of = str(belief["last_updated"])
        prior = float(definition.get("prior_probability", 0.5))
        masses = [(row, int(row.get("direction", 1)) * self._effective_mass(row, definition, as_of)) for row in reps]
        total_signed = sum(value for _, value in masses)
        full_p = _logistic(_logit(prior) + UPDATE_GAIN * total_signed)
        out: List[Contribution] = []
        for row, signed_mass in masses:
            without_p = _logistic(_logit(prior) + UPDATE_GAIN * (total_signed - signed_mass))
            metadata = dict(row.get("metadata") or {})
            delta = full_p - without_p
            out.append(Contribution(
                contributor_type="evidence",
                contributor_id=str(row["evidence_id"]),
                signed_probability_delta=round(delta, 6),
                direction=1 if delta >= 0 else -1,
                source_ref=row.get("source_ref"),
                observation_id=metadata.get("observation_id"),
            ))
        out.sort(key=lambda x: abs(x.signed_probability_delta), reverse=True)
        return out

    def build_for_belief(
        self,
        belief_id: str,
        *,
        previous_probability: Optional[float] = None,
        high_impact: bool = False,
        policy: Optional[DrilldownPolicy] = None,
    ) -> EpistemicState:
        policy = policy or DrilldownPolicy()
        belief = self.beliefs[belief_id]
        definition = self.definitions[belief_id]
        probability = float(belief["probability"])
        previous = previous_probability
        if previous is None and belief.get("previous_probability") is not None:
            previous = float(belief["previous_probability"])
        delta = None if previous is None else round(probability - previous, 6)
        reasons: List[str] = []
        if float(belief.get("confidence", 0.0)) < policy.confidence_below:
            reasons.append("low_confidence")
        if float(belief.get("contradiction_score", 0.0)) > policy.contradiction_above:
            reasons.append("high_contradiction")
        if delta is not None and abs(delta) > policy.absolute_probability_delta_above:
            reasons.append("large_probability_delta")
        if high_impact and policy.high_impact_always:
            reasons.append("high_impact")
        audit_status = str(belief.get("audit_status", "pending"))
        if audit_status not in {"clean", "ok"} and policy.audit_nonclean_always:
            reasons.append("audit_nonclean")

        contributions = self._loo_contributions(belief)
        support = tuple(x.contributor_id for x in contributions if x.direction > 0)[:policy.max_evidence_per_side]
        opposition = tuple(x.contributor_id for x in contributions if x.direction < 0)[:policy.max_evidence_per_side]
        state_id = _stable_id("estate", belief_id, belief.get("last_updated"), probability, belief.get("confidence"))
        return EpistemicState(
            state_id=state_id,
            topic=str(definition.get("claim", belief.get("claim", belief_id))),
            entity=str(belief.get("entity", definition.get("entity", "GLOBAL"))),
            domain=str(belief.get("domain", definition.get("domain", "general"))),
            probability=round(probability, 6),
            confidence=round(float(belief.get("confidence", 0.0)), 6),
            previous_probability=None if previous is None else round(previous, 6),
            delta_probability=delta,
            contradiction=round(float(belief.get("contradiction_score", 0.0)), 6),
            freshness=round(float(belief.get("freshness_score", 0.0)), 6),
            audit_status=audit_status,
            member_belief_ids=(belief_id,),
            contributions=tuple(contributions),
            dominant_support_evidence_ids=support,
            dominant_opposition_evidence_ids=opposition,
            provenance_root={
                "belief_ids": [belief_id],
                "representative_evidence_ids": list(belief.get("representative_evidence_ids", [])),
                "path": "state->belief->evidence->observation->source",
            },
            drilldown_required=bool(reasons),
            drilldown_reasons=tuple(reasons),
            drilldown_policy=policy,
        )

    def build_all(self, previous_by_belief: Optional[Mapping[str, float]] = None) -> Dict[str, EpistemicState]:
        previous_by_belief = previous_by_belief or {}
        return {
            belief_id: self.build_for_belief(belief_id, previous_probability=previous_by_belief.get(belief_id))
            for belief_id in sorted(self.beliefs)
            if belief_id in self.definitions
        }

    def drilldown(self, state: EpistemicState, *, depth: int = 4) -> Dict[str, Any]:
        """Bounded authoritative inspection; never mutates or recalculates Belief Core."""
        if depth < 1 or depth > state.drilldown_policy.max_depth:
            raise ValueError(f"depth must be in [1,{state.drilldown_policy.max_depth}]")
        result: Dict[str, Any] = {
            "state": {
                "state_id": state.state_id,
                "probability": state.probability,
                "confidence": state.confidence,
                "delta_probability": state.delta_probability,
                "contradiction": state.contradiction,
                "authority": asdict(state.authority),
            }
        }
        if depth == 1:
            return result
        beliefs: List[Dict[str, Any]] = []
        for belief_id in state.member_belief_ids:
            if belief_id in self.beliefs:
                b = self.beliefs[belief_id]
                beliefs.append({
                    "belief_id": belief_id,
                    "claim": b.get("claim"),
                    "probability": b.get("probability"),
                    "confidence": b.get("confidence"),
                    "representative_evidence_ids": list(b.get("representative_evidence_ids", [])),
                })
        result["beliefs"] = beliefs
        if depth == 2:
            return result

        evidence_ids: List[str] = []
        for eid in state.dominant_support_evidence_ids + state.dominant_opposition_evidence_ids:
            if eid not in evidence_ids:
                evidence_ids.append(eid)
        evidence_rows: List[Dict[str, Any]] = []
        for eid in evidence_ids[: 2 * state.drilldown_policy.max_evidence_per_side]:
            if eid not in self.evidence:
                continue
            row = self.evidence[eid]
            metadata = dict(row.get("metadata") or {})
            evidence_rows.append({
                "evidence_id": eid,
                "direction": row.get("direction"),
                "strength": row.get("strength"),
                "reliability": row.get("reliability"),
                "source": row.get("source"),
                "source_ref": row.get("source_ref"),
                "observed_at": row.get("observed_at"),
                "observation_id": metadata.get("observation_id"),
            })
        result["evidence"] = evidence_rows
        if depth == 3:
            return result

        observations: List[Dict[str, Any]] = []
        sources: List[Dict[str, Any]] = []
        seen_sources: set[Tuple[str, str]] = set()
        for row in evidence_rows:
            oid = row.get("observation_id")
            if oid and oid in self.observations:
                obs = self.observations[oid]
                observations.append({
                    "observation_id": oid,
                    "metric": obs.get("metric"),
                    "entity": obs.get("entity"),
                    "value": obs.get("value"),
                    "unit": obs.get("unit"),
                    "observed_at": obs.get("observed_at"),
                    "source": obs.get("source"),
                    "source_ref": obs.get("source_ref"),
                })
            key = (str(row.get("source") or ""), str(row.get("source_ref") or ""))
            if key not in seen_sources and len(sources) < state.drilldown_policy.max_sources:
                seen_sources.add(key)
                sources.append({"source": row.get("source"), "source_ref": row.get("source_ref")})
        result["observations"] = observations
        result["sources"] = sources
        result["stop_reason"] = "bounded_drilldown_max_depth_reached"
        return result


def load_previous_probabilities(history_path: Path) -> Dict[str, float]:
    if not history_path.exists():
        return {}
    latest: Dict[str, Tuple[str, float]] = {}
    for line in history_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        ts = str(row.get("created_at", ""))
        for belief_id, state in (row.get("states") or {}).items():
            candidate = (ts, float(state["probability"]))
            if belief_id not in latest or candidate[0] > latest[belief_id][0]:
                latest[belief_id] = candidate
    return {belief_id: value for belief_id, (_, value) in latest.items()}


def build_runtime_snapshot(state_dir: Path) -> Dict[str, Any]:
    state_path = state_dir / "state.json"
    if not state_path.exists():
        raise FileNotFoundError(state_path)
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    observations = EpistemicStateBuilder.load_observations(state_dir / "observations.jsonl")
    history_path = state_dir / "epistemic_state_history.jsonl"
    previous = load_previous_probabilities(history_path)
    builder = EpistemicStateBuilder(payload, observations)
    states = builder.build_all(previous)
    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    result = {
        "contract_version": CONTRACT_VERSION,
        "created_at": created_at,
        "authority": asdict(AggregateAuthority()),
        "drilldown_policy": asdict(DrilldownPolicy()),
        "states": {belief_id: state.to_dict() for belief_id, state in states.items()},
        "controls": {
            "decision_writeback_enabled": False,
            "belief_core_writeback_enabled": False,
            "llm_override_enabled": False,
            "automatic_tuning_enabled": False,
        },
    }
    return result


def persist_runtime_snapshot(state_dir: Path, snapshot: Mapping[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    current = state_dir / "epistemic_state.json"
    history = state_dir / "epistemic_state_history.jsonl"
    drilldown_index = state_dir / "epistemic_drilldown_index.json"
    current.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with history.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot, sort_keys=True, separators=(",", ":")) + "\n")
    index = {
        belief_id: {
            "state_id": state["state_id"],
            "belief_ids": state["member_belief_ids"],
            "evidence_ids": state["provenance_root"]["representative_evidence_ids"],
            "path": state["provenance_root"]["path"],
        }
        for belief_id, state in (snapshot.get("states") or {}).items()
    }
    drilldown_index.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
