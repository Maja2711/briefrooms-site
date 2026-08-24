#!/usr/bin/env python3
"""Shared read-only consumer interface for BriefRooms EpistemicState.

Consumers receive a bounded, authoritative projection and may request drill-down,
but may never override the aggregate or write back into Belief Core.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

CONTRACT_VERSION = "epistemic-consumer-interface-v1"
EPISTEMIC_CONTRACT = "belief-epistemic-state-v1"

SPX_BELIEF_IDS: Tuple[str, ...] = (
    "spx.trend.bullish",
    "spx.breadth.healthy",
    "spx.volatility.benign",
    "spx.liquidity.supportive",
    "spx.financial_conditions.supportive",
)

CONSUMER_PROFILES: Dict[str, Tuple[str, ...]] = {
    "BRACE_SPX": SPX_BELIEF_IDS,
    "WES_SPX": SPX_BELIEF_IDS,
}


def _sha(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ConsumerAuthority:
    aggregate_authoritative: bool = True
    consumer_may_inspect: bool = True
    consumer_may_request_drilldown: bool = True
    consumer_may_challenge: bool = True
    consumer_may_request_recalculation: bool = True
    consumer_may_override_probability: bool = False
    consumer_may_override_confidence: bool = False
    consumer_may_ignore_aggregate: bool = False
    belief_core_writeback_enabled: bool = False
    decision_writeback_enabled: bool = False
    automatic_tuning_enabled: bool = False


@dataclass(frozen=True)
class ConsumerEnvelope:
    consumer: str
    available: bool
    reason: str
    stance: str
    aggregate_probability: Optional[float]
    aggregate_confidence: Optional[float]
    max_contradiction: Optional[float]
    max_abs_delta_probability: Optional[float]
    drilldown_required: bool
    drilldown_reasons: Tuple[str, ...]
    states: Tuple[Mapping[str, Any], ...]
    source_contract_version: str
    source_sha256: str
    authority: ConsumerAuthority = ConsumerAuthority()
    contract_version: str = CONTRACT_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EpistemicConsumerInterface:
    def __init__(self, epistemic_payload: Mapping[str, Any], drilldown_index: Optional[Mapping[str, Any]] = None) -> None:
        self.payload = dict(epistemic_payload)
        self.index = dict(drilldown_index or {})
        if self.payload.get("contract_version") != EPISTEMIC_CONTRACT:
            raise ValueError("unsupported EpistemicState contract")
        authority = self.payload.get("authority") or {}
        if authority.get("llm_may_ignore_aggregate") is not False:
            raise ValueError("aggregate authority invariant missing")
        if authority.get("llm_may_override_probability") is not False:
            raise ValueError("probability override invariant missing")
        controls = self.payload.get("controls") or {}
        if controls.get("belief_core_writeback_enabled") is not False:
            raise ValueError("Belief Core writeback must remain disabled")

    @classmethod
    def from_state_dir(cls, state_dir: Path) -> "EpistemicConsumerInterface":
        payload = json.loads((state_dir / "epistemic_state.json").read_text(encoding="utf-8"))
        index_path = state_dir / "epistemic_drilldown_index.json"
        index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
        return cls(payload, index)

    def _states(self) -> Dict[str, Mapping[str, Any]]:
        states = self.payload.get("states") or {}
        return {str(k): v for k, v in states.items() if isinstance(v, Mapping)}

    def envelope(self, consumer: str) -> ConsumerEnvelope:
        if consumer not in CONSUMER_PROFILES:
            raise KeyError(f"unknown consumer profile: {consumer}")
        required = CONSUMER_PROFILES[consumer]
        states_by_id = self._states()
        selected = [states_by_id[x] for x in required if x in states_by_id]
        if len(selected) != len(required):
            return ConsumerEnvelope(
                consumer=consumer, available=False, reason="required_epistemic_states_missing",
                stance="unavailable", aggregate_probability=None, aggregate_confidence=None,
                max_contradiction=None, max_abs_delta_probability=None, drilldown_required=False,
                drilldown_reasons=(), states=tuple(selected), source_contract_version=EPISTEMIC_CONTRACT,
                source_sha256=_sha(self.payload),
            )
        probs = [float(x.get("probability", 0.5)) for x in selected]
        confs = [float(x.get("confidence", 0.0)) for x in selected]
        contradictions = [float(x.get("contradiction", 0.0)) for x in selected]
        deltas = [abs(float(x.get("delta_probability"))) for x in selected if x.get("delta_probability") is not None]
        p = fmean(probs)
        if p >= 0.60:
            stance = "risk_on"
        elif p <= 0.40:
            stance = "defensive"
        else:
            stance = "neutral"
        reasons = sorted({reason for x in selected for reason in (x.get("drilldown_reasons") or [])})
        drilldown_required = any(bool(x.get("drilldown_required")) for x in selected)
        compact_states = tuple({
            "state_id": x.get("state_id"),
            "belief_id": (x.get("member_belief_ids") or [None])[0],
            "topic": x.get("topic"),
            "probability": x.get("probability"),
            "confidence": x.get("confidence"),
            "delta_probability": x.get("delta_probability"),
            "contradiction": x.get("contradiction"),
            "freshness": x.get("freshness"),
            "audit_status": x.get("audit_status"),
            "drilldown_required": x.get("drilldown_required"),
            "drilldown_reasons": list(x.get("drilldown_reasons") or []),
            "dominant_support_evidence_ids": list(x.get("dominant_support_evidence_ids") or []),
            "dominant_opposition_evidence_ids": list(x.get("dominant_opposition_evidence_ids") or []),
        } for x in selected)
        return ConsumerEnvelope(
            consumer=consumer, available=True, reason="authoritative_epistemic_state_projection",
            stance=stance, aggregate_probability=round(p, 6), aggregate_confidence=round(fmean(confs), 6),
            max_contradiction=round(max(contradictions), 6),
            max_abs_delta_probability=round(max(deltas), 6) if deltas else None,
            drilldown_required=drilldown_required, drilldown_reasons=tuple(reasons),
            states=compact_states, source_contract_version=EPISTEMIC_CONTRACT, source_sha256=_sha(self.payload),
        )

    def drilldown_request(self, consumer: str, belief_id: str, *, depth: int = 4) -> Dict[str, Any]:
        if consumer not in CONSUMER_PROFILES or belief_id not in CONSUMER_PROFILES[consumer]:
            raise PermissionError("consumer is not authorized for this belief")
        if depth < 1 or depth > 4:
            raise ValueError("depth must be in [1,4]")
        states = self._states()
        state = states.get(belief_id)
        if state is None:
            raise KeyError(belief_id)
        return {
            "consumer": consumer,
            "belief_id": belief_id,
            "state_id": state.get("state_id"),
            "requested_depth": depth,
            "max_depth": 4,
            "max_evidence_per_side": 3,
            "max_sources": 6,
            "max_reinspection_cycles": 1,
            "purpose": "inspect_explain_or_challenge_only",
            "aggregate_remains_authoritative": True,
            "consumer_may_override": False,
            "recalculation_required_for_state_change": True,
            "provenance": self.index.get(belief_id) or self.index.get(str(state.get("state_id"))) or {},
        }


def build_consumer_bundle(state_dir: Path) -> Dict[str, Any]:
    interface = EpistemicConsumerInterface.from_state_dir(state_dir)
    envelopes = {name: interface.envelope(name).to_dict() for name in sorted(CONSUMER_PROFILES)}
    payload = {
        "contract_version": CONTRACT_VERSION,
        "source_contract_version": EPISTEMIC_CONTRACT,
        "authority": asdict(ConsumerAuthority()),
        "consumers": envelopes,
    }
    (state_dir / "epistemic_consumer_bundle.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload
