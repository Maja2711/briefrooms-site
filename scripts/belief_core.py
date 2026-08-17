#!/usr/bin/env python3
"""BriefRooms Belief Core v1.

A deterministic, auditable belief-state layer for BriefRooms.

Design goals
------------
* Beliefs are separate from actions/policies.
* Probability and evidence confidence are separate quantities.
* Evidence is provenance-aware and de-duplicated by independence cluster.
* Old evidence decays deterministically.
* Supporting and contradicting evidence are preserved.
* Belief transitions are append-only in a decision-independent ledger.
* Verification records calibration outcomes without changing trading policy.

This module intentionally contains NO trade execution, position sizing, or
BUY/HOLD/SELL policy code. It is suitable for shadow-mode operation.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

SCHEMA_VERSION = 1
MODE = "shadow"


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def logit(probability: float) -> float:
    p = clamp(probability, 1e-6, 1.0 - 1e-6)
    return math.log(p / (1.0 - p))


def logistic(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


@dataclass(frozen=True)
class Evidence:
    """One observable item that bears on one belief.

    independence_cluster is the anti-double-counting key. Multiple news
    articles derived from the same filing/event should share one cluster.
    They remain in the evidence store for provenance, but only one
    representative contributes statistical weight to the belief update.
    """
    evidence_id: str
    belief_id: str
    source: str
    observed_at: str
    direction: int
    strength: float
    reliability: float
    independence_cluster: str
    source_type: str = "secondary"
    source_ref: Optional[str] = None
    derived_from: Tuple[str, ...] = ()
    note: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.evidence_id.strip():
            raise ValueError("evidence_id must be non-empty")
        if not self.belief_id.strip():
            raise ValueError("belief_id must be non-empty")
        if int(self.direction) not in (-1, 1):
            raise ValueError("direction must be -1 (oppose) or +1 (support)")
        if not 0.0 <= float(self.strength) <= 1.0:
            raise ValueError("strength must be in [0, 1]")
        if not 0.0 <= float(self.reliability) <= 1.0:
            raise ValueError("reliability must be in [0, 1]")
        if not self.independence_cluster.strip():
            raise ValueError("independence_cluster must be non-empty")
        if self.source_type not in {"primary", "secondary", "derived"}:
            raise ValueError("source_type must be primary, secondary, or derived")
        parse_time(self.observed_at)

    def freshness(self, as_of: str | datetime, half_life_hours: float) -> float:
        as_dt = parse_time(as_of)
        observed = parse_time(self.observed_at)
        age_hours = max(0.0, (as_dt - observed).total_seconds() / 3600.0)
        half_life = max(0.01, float(half_life_hours))
        return 0.5 ** (age_hours / half_life)

    def effective_mass(self, as_of: str | datetime, half_life_hours: float) -> float:
        return clamp(self.strength) * clamp(self.reliability) * self.freshness(as_of, half_life_hours)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["derived_from"] = list(self.derived_from)
        payload["metadata"] = dict(self.metadata)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Evidence":
        return cls(
            evidence_id=str(payload["evidence_id"]), belief_id=str(payload["belief_id"]),
            source=str(payload.get("source", "unknown")), observed_at=str(payload["observed_at"]),
            direction=int(payload["direction"]), strength=float(payload["strength"]),
            reliability=float(payload["reliability"]), independence_cluster=str(payload["independence_cluster"]),
            source_type=str(payload.get("source_type", "secondary")), source_ref=payload.get("source_ref"),
            derived_from=tuple(payload.get("derived_from") or ()), note=str(payload.get("note", "")),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True)
class BeliefDefinition:
    belief_id: str
    claim: str
    prior_probability: float = 0.5
    half_life_hours: float = 72.0
    entity: str = "GLOBAL"
    domain: str = "general"
    alternative_group: Optional[str] = None
    tags: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.belief_id.strip(): raise ValueError("belief_id must be non-empty")
        if not self.claim.strip(): raise ValueError("claim must be non-empty")
        if not 0.0 < float(self.prior_probability) < 1.0: raise ValueError("prior_probability must be strictly between 0 and 1")
        if float(self.half_life_hours) <= 0: raise ValueError("half_life_hours must be positive")

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self); payload["tags"] = list(self.tags); return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BeliefDefinition":
        return cls(
            belief_id=str(payload["belief_id"]), claim=str(payload["claim"]),
            prior_probability=float(payload.get("prior_probability", 0.5)),
            half_life_hours=float(payload.get("half_life_hours", 72.0)),
            entity=str(payload.get("entity", "GLOBAL")), domain=str(payload.get("domain", "general")),
            alternative_group=payload.get("alternative_group"), tags=tuple(payload.get("tags") or ()),
        )


@dataclass
class BeliefState:
    belief_id: str
    claim: str
    probability: float
    confidence: float
    previous_probability: Optional[float]
    support_evidence_ids: List[str]
    opposing_evidence_ids: List[str]
    independent_clusters: int
    source_diversity: int
    contradiction_score: float
    freshness_score: float
    last_updated: str
    entity: str
    domain: str
    alternative_group: Optional[str]
    tags: List[str]
    audit_status: str = "pending"

    def to_dict(self) -> Dict[str, Any]: return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BeliefState":
        return cls(
            belief_id=str(payload["belief_id"]), claim=str(payload["claim"]),
            probability=float(payload["probability"]), confidence=float(payload["confidence"]),
            previous_probability=None if payload.get("previous_probability") is None else float(payload["previous_probability"]),
            support_evidence_ids=list(payload.get("support_evidence_ids") or []),
            opposing_evidence_ids=list(payload.get("opposing_evidence_ids") or []),
            independent_clusters=int(payload.get("independent_clusters", 0)), source_diversity=int(payload.get("source_diversity", 0)),
            contradiction_score=float(payload.get("contradiction_score", 0.0)), freshness_score=float(payload.get("freshness_score", 0.0)),
            last_updated=str(payload["last_updated"]), entity=str(payload.get("entity", "GLOBAL")),
            domain=str(payload.get("domain", "general")), alternative_group=payload.get("alternative_group"),
            tags=list(payload.get("tags") or []), audit_status=str(payload.get("audit_status", "pending")),
        )


@dataclass(frozen=True)
class AuditFinding:
    code: str
    severity: str
    belief_id: str
    message: str
    evidence_ids: Tuple[str, ...] = ()
    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self); payload["evidence_ids"] = list(self.evidence_ids); return payload


@dataclass(frozen=True)
class Verification:
    belief_id: str
    predicted_probability: float
    outcome: bool
    verified_at: str
    brier_score: float
    note: str = ""
    def to_dict(self) -> Dict[str, Any]: return asdict(self)


class BeliefAuditor:
    def audit(self, definition: BeliefDefinition, state: BeliefState, evidence: Sequence[Evidence], as_of: str | datetime) -> List[AuditFinding]:
        findings: List[AuditFinding] = []
        clusters: Dict[str, List[Evidence]] = {}
        for item in evidence: clusters.setdefault(item.independence_cluster, []).append(item)
        duplicate_ids = tuple(item.evidence_id for items in clusters.values() if len(items) > 1 for item in items)
        if duplicate_ids:
            findings.append(AuditFinding("duplicate_provenance_cluster", "info", definition.belief_id,
                "Powielone obserwacje zostały zachowane dla provenance, ale liczone jako jeden niezależny klaster.", duplicate_ids))
        if state.independent_clusters < 2:
            findings.append(AuditFinding("thin_evidence", "warning", definition.belief_id,
                "Belief opiera się na mniej niż dwóch niezależnych klastrach evidence."))
        stale_ids = tuple(item.evidence_id for item in evidence if item.freshness(as_of, definition.half_life_hours) < 0.25)
        if stale_ids:
            findings.append(AuditFinding("stale_evidence", "info", definition.belief_id,
                "Część evidence ma freshness poniżej 25% i szybko traci wagę.", stale_ids))
        if state.contradiction_score >= 0.55:
            findings.append(AuditFinding("material_contradiction", "warning", definition.belief_id,
                "Silne evidence wspierające i przeciwne występują jednocześnie.", tuple(state.support_evidence_ids + state.opposing_evidence_ids)))
        if evidence and not any(item.source_type == "primary" for item in evidence):
            findings.append(AuditFinding("no_primary_source", "info", definition.belief_id, "Brak primary-source evidence dla tego beliefu."))
        missing_source = tuple(item.evidence_id for item in evidence if not item.source.strip() or item.source.strip().lower() == "unknown")
        if missing_source:
            findings.append(AuditFinding("weak_provenance", "warning", definition.belief_id,
                "Evidence bez jednoznacznego źródła obniża audytowalność.", missing_source))
        if state.probability >= 0.75 and state.confidence < 0.50:
            findings.append(AuditFinding("high_belief_low_confidence", "warning", definition.belief_id,
                "Wysokie probability nie jest poparte wysokim evidence confidence."))
        return findings


class BeliefCore:
    """Deterministic belief updater with persistence and audit trail.

    This class deliberately has no policy/action API.
    """
    def __init__(self, state_dir: str | os.PathLike[str]) -> None:
        self.state_dir = Path(state_dir)
        self.state_path = self.state_dir / "state.json"
        self.ledger_path = self.state_dir / "ledger.jsonl"
        self.dashboard_path = self.state_dir / "dashboard.json"
        self.definitions: Dict[str, BeliefDefinition] = {}
        self.evidence: Dict[str, Evidence] = {}
        self.beliefs: Dict[str, BeliefState] = {}
        self.history: Dict[str, List[Dict[str, Any]]] = {}
        self.verifications: List[Verification] = []
        self.auditor = BeliefAuditor()
        self.load()

    def load(self) -> None:
        if not self.state_path.exists(): return
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.definitions = {item["belief_id"]: BeliefDefinition.from_dict(item) for item in payload.get("definitions", [])}
        self.evidence = {item["evidence_id"]: Evidence.from_dict(item) for item in payload.get("evidence", [])}
        self.beliefs = {item["belief_id"]: BeliefState.from_dict(item) for item in payload.get("beliefs", [])}
        self.history = {str(key): list(value) for key, value in (payload.get("history") or {}).items()}
        self.verifications = [Verification(
            belief_id=str(item["belief_id"]), predicted_probability=float(item["predicted_probability"]),
            outcome=bool(item["outcome"]), verified_at=str(item["verified_at"]), brier_score=float(item["brier_score"]),
            note=str(item.get("note", ""))) for item in payload.get("verifications", [])]

    def register_beliefs(self, definitions: Iterable[BeliefDefinition]) -> None:
        for definition in definitions:
            self.definitions[definition.belief_id] = definition
            self.history.setdefault(definition.belief_id, [])

    def ingest(self, evidence: Iterable[Evidence]) -> None:
        """Idempotent evidence upsert with immutable evidence IDs."""
        for item in evidence:
            if item.belief_id not in self.definitions:
                raise KeyError(f"Evidence {item.evidence_id} targets unknown belief {item.belief_id}")
            existing = self.evidence.get(item.evidence_id)
            if existing is not None and existing.to_dict() != item.to_dict():
                raise ValueError(f"evidence_id {item.evidence_id} already exists with different content")
            self.evidence[item.evidence_id] = item

    @staticmethod
    def _representative(items: Sequence[Evidence], as_of: str | datetime, half_life_hours: float) -> Evidence:
        rank = {"primary": 2, "secondary": 1, "derived": 0}
        return max(items, key=lambda item: (rank[item.source_type], item.effective_mass(as_of, half_life_hours), parse_time(item.observed_at).timestamp()))

    def _compute_raw_state(self, definition: BeliefDefinition, as_of: str | datetime) -> BeliefState:
        items = [item for item in self.evidence.values() if item.belief_id == definition.belief_id]
        clusters: Dict[str, List[Evidence]] = {}
        for item in items: clusters.setdefault(item.independence_cluster, []).append(item)
        representatives = [self._representative(group, as_of, definition.half_life_hours) for group in clusters.values()]
        support_mass = oppose_mass = signed_mass = 0.0
        fresh_values: List[float] = []; reliability_values: List[float] = []
        for item in representatives:
            freshness = item.freshness(as_of, definition.half_life_hours)
            mass = item.effective_mass(as_of, definition.half_life_hours)
            signed_mass += item.direction * mass
            if item.direction > 0: support_mass += mass
            else: oppose_mass += mass
            fresh_values.append(freshness); reliability_values.append(item.reliability)
        probability = logistic(logit(definition.prior_probability) + 1.65 * signed_mass)
        total_mass = support_mass + oppose_mass
        contradiction = 0.0 if total_mass <= 1e-12 else clamp(2.0 * min(support_mass, oppose_mass) / total_mass)
        cluster_count = len(representatives)
        coverage = 1.0 - math.exp(-cluster_count / 3.0) if cluster_count else 0.0
        freshness_score = sum(fresh_values) / len(fresh_values) if fresh_values else 0.0
        quality = sum(reliability_values) / len(reliability_values) if reliability_values else 0.0
        source_diversity = len({item.source for item in representatives})
        diversity = min(1.0, source_diversity / 3.0)
        confidence = (0.35 * quality + 0.25 * freshness_score + 0.20 * coverage + 0.20 * diversity)
        confidence *= 1.0 - 0.45 * contradiction
        confidence = clamp(confidence)
        previous = self.beliefs.get(definition.belief_id)
        return BeliefState(
            belief_id=definition.belief_id, claim=definition.claim, probability=round(probability, 6), confidence=round(confidence, 6),
            previous_probability=None if previous is None else round(previous.probability, 6),
            support_evidence_ids=sorted(item.evidence_id for item in items if item.direction > 0),
            opposing_evidence_ids=sorted(item.evidence_id for item in items if item.direction < 0),
            independent_clusters=cluster_count, source_diversity=source_diversity, contradiction_score=round(contradiction, 6),
            freshness_score=round(freshness_score, 6), last_updated=iso_z(parse_time(as_of)), entity=definition.entity,
            domain=definition.domain, alternative_group=definition.alternative_group, tags=list(definition.tags))

    @staticmethod
    def _normalize_alternative_groups(states: MutableMapping[str, BeliefState]) -> None:
        groups: Dict[str, List[BeliefState]] = {}
        for state in states.values():
            if state.alternative_group: groups.setdefault(state.alternative_group, []).append(state)
        for group_states in groups.values():
            if len(group_states) < 2: continue
            total = sum(max(1e-9, state.probability) for state in group_states)
            for state in group_states: state.probability = round(state.probability / total, 6)

    def recompute(self, as_of: str | datetime | None = None) -> Dict[str, BeliefState]:
        as_of = as_of or utc_now()
        states = {belief_id: self._compute_raw_state(definition, as_of) for belief_id, definition in self.definitions.items()}
        self._normalize_alternative_groups(states)
        for belief_id, state in states.items():
            definition = self.definitions[belief_id]
            items = [item for item in self.evidence.values() if item.belief_id == belief_id]
            findings = self.auditor.audit(definition, state, items, as_of)
            severities = {finding.severity for finding in findings}
            state.audit_status = "critical" if "critical" in severities else "warning" if "warning" in severities else "ok"
        for belief_id, state in states.items():
            previous = self.beliefs.get(belief_id)
            changed = previous is None or abs(previous.probability - state.probability) >= 1e-6 or abs(previous.confidence - state.confidence) >= 1e-6
            if changed:
                event = {"timestamp": state.last_updated, "belief_id": belief_id,
                    "previous_probability": None if previous is None else previous.probability,
                    "probability": state.probability, "confidence": state.confidence,
                    "contradiction_score": state.contradiction_score, "independent_clusters": state.independent_clusters, "mode": MODE}
                self.history.setdefault(belief_id, []).append(event)
                self._append_ledger({"event_type": "belief_transition", **event})
        self.beliefs = states
        self.save(); self.write_dashboard(as_of)
        return dict(self.beliefs)

    def verify(self, belief_id: str, outcome: bool, verified_at: str | datetime | None = None, note: str = "") -> Verification:
        if belief_id not in self.beliefs: raise KeyError(f"Unknown or not-yet-computed belief: {belief_id}")
        state = self.beliefs[belief_id]; y = 1.0 if outcome else 0.0; brier = (state.probability - y) ** 2
        verification = Verification(belief_id=belief_id, predicted_probability=state.probability, outcome=bool(outcome),
            verified_at=iso_z(parse_time(verified_at or utc_now())), brier_score=round(brier, 6), note=note)
        self.verifications.append(verification)
        self._append_ledger({"event_type": "verification", **verification.to_dict(), "mode": MODE})
        self.save(); self.write_dashboard(verified_at or utc_now())
        return verification

    def calibration_summary(self) -> Dict[str, Any]:
        if not self.verifications: return {"count": 0, "mean_brier_score": None, "status": "awaiting_outcomes"}
        mean_brier = sum(v.brier_score for v in self.verifications) / len(self.verifications)
        return {"count": len(self.verifications), "mean_brier_score": round(mean_brier, 6), "status": "observed"}

    def save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": SCHEMA_VERSION, "mode": MODE,
            "definitions": [self.definitions[key].to_dict() for key in sorted(self.definitions)],
            "evidence": [self.evidence[key].to_dict() for key in sorted(self.evidence)],
            "beliefs": [self.beliefs[key].to_dict() for key in sorted(self.beliefs)],
            "history": self.history, "verifications": [item.to_dict() for item in self.verifications]}
        self._atomic_json_write(self.state_path, payload)

    def dashboard_snapshot(self, as_of: str | datetime | None = None) -> Dict[str, Any]:
        as_dt = parse_time(as_of or utc_now()); findings: List[Dict[str, Any]] = []
        for belief_id in sorted(self.beliefs):
            definition = self.definitions[belief_id]; state = self.beliefs[belief_id]
            items = [item for item in self.evidence.values() if item.belief_id == belief_id]
            findings.extend(finding.to_dict() for finding in self.auditor.audit(definition, state, items, as_dt))
        evidence_detail: Dict[str, Dict[str, Any]] = {}
        for item in self.evidence.values():
            definition = self.definitions[item.belief_id]; detail = item.to_dict()
            detail["freshness"] = round(item.freshness(as_dt, definition.half_life_hours), 6); evidence_detail[item.evidence_id] = detail
        contradictions = [{"belief_id": state.belief_id, "claim": state.claim, "score": state.contradiction_score,
            "probability": state.probability, "confidence": state.confidence} for state in self.beliefs.values() if state.contradiction_score > 0.0]
        contradictions.sort(key=lambda item: item["score"], reverse=True)
        status = "ready" if self.beliefs else "awaiting_evidence"
        return {"schema_version": SCHEMA_VERSION, "mode": MODE, "status": status, "generated_at": iso_z(as_dt),
            "controls": {"decision_engine_connected": False, "trade_execution_enabled": False, "policy_output_enabled": False,
                "message": "Belief Core działa wyłącznie w shadow mode i nie steruje BRACE/WES/BRACE-SPX."},
            "summary": {"belief_count": len(self.beliefs), "evidence_count": len(self.evidence),
                "independent_cluster_count": len({item.independence_cluster for item in self.evidence.values()}),
                "audit_warning_count": sum(1 for item in findings if item["severity"] in {"warning", "critical"})},
            "world_state": [self.beliefs[key].to_dict() for key in sorted(self.beliefs)], "evidence": evidence_detail,
            "audit_findings": findings, "active_contradictions": contradictions, "history": self.history,
            "calibration": self.calibration_summary()}

    def write_dashboard(self, as_of: str | datetime | None = None) -> None:
        self._atomic_json_write(self.dashboard_path, self.dashboard_snapshot(as_of))

    def _append_ledger(self, record: Mapping[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(record), ensure_ascii=False, sort_keys=True)); handle.write("\n")

    @staticmethod
    def _atomic_json_write(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True); handle.write("\n")
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name): os.unlink(tmp_name)


def load_input(path: str | os.PathLike[str]) -> Tuple[List[BeliefDefinition], List[Evidence], Optional[str]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    definitions = [BeliefDefinition.from_dict(item) for item in payload.get("beliefs", [])]
    evidence = [Evidence.from_dict(item) for item in payload.get("evidence", [])]
    return definitions, evidence, payload.get("as_of")
