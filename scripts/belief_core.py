#!/usr/bin/env python3
"""BriefRooms Belief Core v2.

Deterministic, provenance-aware belief state + frozen forecasts + verification
+ calibration memory. Shadow mode only: no policy, sizing or trade execution.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

from belief_calibration import build_calibration_report

SCHEMA_VERSION = 2
MODE = "shadow"
UPDATE_GAIN = 1.65


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


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(x) for x in parts)
    return f"{prefix}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def horizon_bucket(hours: float) -> str:
    h = float(hours)
    if h <= 24:
        return "<=1d"
    if h <= 72:
        return "1-3d"
    if h <= 168:
        return "3-7d"
    if h <= 720:
        return "1w-1m"
    if h <= 2160:
        return "1-3m"
    return ">3m"


@dataclass(frozen=True)
class Evidence:
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
    evidence_type: str = "general"
    note: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.evidence_id.strip() or not self.belief_id.strip():
            raise ValueError("evidence_id and belief_id must be non-empty")
        if int(self.direction) not in (-1, 1):
            raise ValueError("direction must be -1 or +1")
        if not 0.0 <= float(self.strength) <= 1.0:
            raise ValueError("strength must be in [0,1]")
        if not 0.0 <= float(self.reliability) <= 1.0:
            raise ValueError("reliability must be in [0,1]")
        if not self.independence_cluster.strip():
            raise ValueError("independence_cluster must be non-empty")
        if self.source_type not in {"primary", "secondary", "derived"}:
            raise ValueError("source_type must be primary, secondary or derived")
        if self.evidence_id in self.derived_from:
            raise ValueError("evidence cannot derive from itself")
        parse_time(self.observed_at)

    def freshness(self, as_of: str | datetime, half_life_hours: float) -> float:
        age_h = max(0.0, (parse_time(as_of) - parse_time(self.observed_at)).total_seconds() / 3600.0)
        return 0.5 ** (age_h / max(0.01, float(half_life_hours)))

    def effective_mass(self, as_of: str | datetime, half_life_hours: float) -> float:
        return clamp(self.strength) * clamp(self.reliability) * self.freshness(as_of, half_life_hours)

    def to_dict(self) -> Dict[str, Any]:
        p = asdict(self)
        p["derived_from"] = list(self.derived_from)
        p["metadata"] = dict(self.metadata)
        return p

    @classmethod
    def from_dict(cls, p: Mapping[str, Any]) -> "Evidence":
        return cls(
            evidence_id=str(p["evidence_id"]), belief_id=str(p["belief_id"]), source=str(p.get("source", "unknown")),
            observed_at=str(p["observed_at"]), direction=int(p["direction"]), strength=float(p["strength"]),
            reliability=float(p["reliability"]), independence_cluster=str(p["independence_cluster"]),
            source_type=str(p.get("source_type", "secondary")), source_ref=p.get("source_ref"),
            derived_from=tuple(p.get("derived_from") or ()), evidence_type=str(p.get("evidence_type", "general")),
            note=str(p.get("note", "")), metadata=dict(p.get("metadata") or {}),
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
    horizon_hours: float = 24.0
    outcome_rule: str = "manual_binary_resolution"

    def __post_init__(self) -> None:
        if not self.belief_id.strip() or not self.claim.strip():
            raise ValueError("belief_id and claim must be non-empty")
        if not 0.0 < float(self.prior_probability) < 1.0:
            raise ValueError("prior_probability must be strictly between 0 and 1")
        if float(self.half_life_hours) <= 0 or float(self.horizon_hours) <= 0:
            raise ValueError("half_life_hours and horizon_hours must be positive")

    def to_dict(self) -> Dict[str, Any]:
        p = asdict(self); p["tags"] = list(self.tags); return p

    @classmethod
    def from_dict(cls, p: Mapping[str, Any]) -> "BeliefDefinition":
        return cls(
            belief_id=str(p["belief_id"]), claim=str(p["claim"]), prior_probability=float(p.get("prior_probability", .5)),
            half_life_hours=float(p.get("half_life_hours", 72.0)), entity=str(p.get("entity", "GLOBAL")),
            domain=str(p.get("domain", "general")), alternative_group=p.get("alternative_group"),
            tags=tuple(p.get("tags") or ()), horizon_hours=float(p.get("horizon_hours", 24.0)),
            outcome_rule=str(p.get("outcome_rule", "manual_binary_resolution")),
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
    representative_evidence_ids: List[str]
    independent_clusters: int
    source_diversity: int
    contradiction_score: float
    cluster_conflict_score: float
    freshness_score: float
    last_updated: str
    entity: str
    domain: str
    alternative_group: Optional[str]
    tags: List[str]
    horizon_hours: float
    audit_status: str = "pending"

    def to_dict(self) -> Dict[str, Any]: return asdict(self)

    @classmethod
    def from_dict(cls, p: Mapping[str, Any]) -> "BeliefState":
        return cls(
            belief_id=str(p["belief_id"]), claim=str(p["claim"]), probability=float(p["probability"]),
            confidence=float(p["confidence"]), previous_probability=None if p.get("previous_probability") is None else float(p["previous_probability"]),
            support_evidence_ids=list(p.get("support_evidence_ids") or []), opposing_evidence_ids=list(p.get("opposing_evidence_ids") or []),
            representative_evidence_ids=list(p.get("representative_evidence_ids") or []),
            independent_clusters=int(p.get("independent_clusters", 0)), source_diversity=int(p.get("source_diversity", 0)),
            contradiction_score=float(p.get("contradiction_score", 0.0)), cluster_conflict_score=float(p.get("cluster_conflict_score", 0.0)),
            freshness_score=float(p.get("freshness_score", 0.0)),
            last_updated=str(p["last_updated"]), entity=str(p.get("entity", "GLOBAL")), domain=str(p.get("domain", "general")),
            alternative_group=p.get("alternative_group"), tags=list(p.get("tags") or []), horizon_hours=float(p.get("horizon_hours", 24.0)),
            audit_status=str(p.get("audit_status", "pending")),
        )


@dataclass(frozen=True)
class AuditFinding:
    code: str
    severity: str
    belief_id: str
    message: str
    evidence_ids: Tuple[str, ...] = ()
    def to_dict(self) -> Dict[str, Any]:
        p = asdict(self); p["evidence_ids"] = list(self.evidence_ids); return p


@dataclass(frozen=True)
class ForecastSnapshot:
    forecast_id: str
    forecast_set_id: str
    belief_id: str
    predicted_probability: float
    forecast_confidence: float
    forecast_at: str
    target_at: str
    horizon_hours: float
    domain: str
    entity: str
    regime: str
    alternative_group: Optional[str]
    outcome_rule: str
    representative_evidence_ids: Tuple[str, ...]
    evidence_snapshot: Tuple[Mapping[str, Any], ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        p = asdict(self)
        p["representative_evidence_ids"] = list(self.representative_evidence_ids)
        p["evidence_snapshot"] = [dict(x) for x in self.evidence_snapshot]
        p["metadata"] = dict(self.metadata)
        return p

    @classmethod
    def from_dict(cls, p: Mapping[str, Any]) -> "ForecastSnapshot":
        return cls(
            forecast_id=str(p["forecast_id"]), forecast_set_id=str(p.get("forecast_set_id") or _stable_id("forecastset", p.get("belief_id"), p.get("forecast_at"), p.get("target_at"))),
            belief_id=str(p["belief_id"]), predicted_probability=float(p["predicted_probability"]),
            forecast_confidence=float(p.get("forecast_confidence", 0.0)), forecast_at=str(p["forecast_at"]), target_at=str(p["target_at"]),
            horizon_hours=float(p.get("horizon_hours", 24.0)), domain=str(p.get("domain", "general")), entity=str(p.get("entity", "GLOBAL")),
            regime=str(p.get("regime", "unknown")), alternative_group=p.get("alternative_group"),
            outcome_rule=str(p.get("outcome_rule", "manual_binary_resolution")),
            representative_evidence_ids=tuple(p.get("representative_evidence_ids") or ()),
            evidence_snapshot=tuple(dict(x) for x in (p.get("evidence_snapshot") or ())), metadata=dict(p.get("metadata") or {}),
        )


@dataclass(frozen=True)
class Verification:
    verification_id: str
    forecast_id: Optional[str]
    forecast_set_id: Optional[str]
    belief_id: str
    predicted_probability: float
    forecast_confidence: float
    outcome: bool
    forecast_at: str
    target_at: str
    verified_at: str
    horizon_hours: float
    horizon_bucket: str
    domain: str
    entity: str
    regime: str
    alternative_group: Optional[str]
    outcome_rule: str
    outcome_source: str
    outcome_ref: Optional[str]
    brier_score: float
    log_loss: float
    evidence_snapshot: Tuple[Mapping[str, Any], ...]
    calibration_eligible: bool = True
    legacy: bool = False
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        p = asdict(self); p["evidence_snapshot"] = [dict(x) for x in self.evidence_snapshot]; return p

    @classmethod
    def from_dict(cls, p: Mapping[str, Any]) -> "Verification":
        prob = float(p["predicted_probability"])
        outcome = bool(p["outcome"])
        forecast_at = str(p.get("forecast_at") or p.get("verified_at"))
        target_at = str(p.get("target_at") or p.get("verified_at"))
        h = float(p.get("horizon_hours", 0.0))
        return cls(
            verification_id=str(p.get("verification_id") or _stable_id("legacy-v", p.get("belief_id"), p.get("verified_at"), prob, outcome)),
            forecast_id=p.get("forecast_id"), forecast_set_id=p.get("forecast_set_id"), belief_id=str(p["belief_id"]), predicted_probability=prob,
            forecast_confidence=float(p.get("forecast_confidence", 0.0)), outcome=outcome, forecast_at=forecast_at,
            target_at=target_at, verified_at=str(p["verified_at"]), horizon_hours=h, horizon_bucket=str(p.get("horizon_bucket") or horizon_bucket(h)),
            domain=str(p.get("domain", "general")), entity=str(p.get("entity", "GLOBAL")), regime=str(p.get("regime", "unknown")),
            alternative_group=p.get("alternative_group"), outcome_rule=str(p.get("outcome_rule", "manual_binary_resolution")),
            outcome_source=str(p.get("outcome_source", "legacy" if p.get("forecast_id") is None else "manual")), outcome_ref=p.get("outcome_ref"),
            brier_score=float(p.get("brier_score", (prob - (1.0 if outcome else 0.0)) ** 2)),
            log_loss=float(p.get("log_loss", _log_loss(prob, outcome))),
            evidence_snapshot=tuple(dict(x) for x in (p.get("evidence_snapshot") or ())),
            calibration_eligible=bool(p.get("calibration_eligible", False if p.get("forecast_id") is None else True)),
            legacy=bool(p.get("legacy", p.get("forecast_id") is None)), note=str(p.get("note", "")),
        )


def _log_loss(p: float, outcome: bool) -> float:
    p = clamp(p, 1e-9, 1.0 - 1e-9)
    y = 1.0 if outcome else 0.0
    return -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))


class BeliefAuditor:
    def audit(self, definition: BeliefDefinition, state: BeliefState, evidence: Sequence[Evidence], as_of: str | datetime) -> List[AuditFinding]:
        as_dt = parse_time(as_of)
        findings: List[AuditFinding] = []
        future = tuple(e.evidence_id for e in evidence if parse_time(e.observed_at) > as_dt)
        if future:
            findings.append(AuditFinding("future_dated_evidence", "critical", definition.belief_id,
                "Evidence observed after as_of is excluded to prevent look-ahead leakage.", future))
        active = [e for e in evidence if parse_time(e.observed_at) <= as_dt]
        clusters: Dict[str, List[Evidence]] = {}
        for item in active: clusters.setdefault(item.independence_cluster, []).append(item)
        # Provenance graph safety: known derived_from edges must remain acyclic.
        known = {e.evidence_id: e for e in active}
        visiting: set[str] = set(); visited: set[str] = set(); cycle_ids: set[str] = set()
        def walk(eid: str) -> None:
            if eid in visiting:
                cycle_ids.update(visiting); return
            if eid in visited: return
            visiting.add(eid)
            for parent in known[eid].derived_from:
                if parent in known: walk(parent)
            visiting.remove(eid); visited.add(eid)
        for eid in known: walk(eid)
        if cycle_ids:
            findings.append(AuditFinding("provenance_cycle", "critical", definition.belief_id,
                "Known evidence lineage contains a cycle.", tuple(sorted(cycle_ids))))
        missing_lineage = tuple(e.evidence_id for e in active if e.source_type == "derived" and not e.derived_from)
        if missing_lineage:
            findings.append(AuditFinding("derived_without_lineage", "warning", definition.belief_id,
                "Derived evidence should identify its upstream lineage.", missing_lineage))
        refs: Dict[str, set[str]] = {}
        for e in active:
            if e.source_ref: refs.setdefault(str(e.source_ref), set()).add(e.independence_cluster)
        collision = tuple(sorted(ref for ref, cs in refs.items() if len(cs) > 1))
        if collision:
            findings.append(AuditFinding("provenance_cluster_collision", "warning", definition.belief_id,
                "The same source_ref appears in multiple independence clusters; review double-counting risk."))
        conflict_ids = tuple(e.evidence_id for items in clusters.values()
                             if len({x.direction for x in items}) > 1 for e in items)
        if conflict_ids:
            findings.append(AuditFinding("cluster_direction_conflict", "warning", definition.belief_id,
                "One independence cluster contains both supporting and opposing interpretations.", conflict_ids))
        dup = tuple(e.evidence_id for items in clusters.values() if len(items) > 1 for e in items)
        if dup:
            findings.append(AuditFinding("duplicate_provenance_cluster", "info", definition.belief_id,
                "Correlated observations are retained for provenance but count as one independence cluster.", dup))
        if state.independent_clusters < 2:
            findings.append(AuditFinding("thin_evidence", "warning", definition.belief_id, "Belief uses fewer than two independent evidence clusters."))
        stale = tuple(e.evidence_id for e in active if e.freshness(as_dt, definition.half_life_hours) < .25)
        if stale:
            findings.append(AuditFinding("stale_evidence", "info", definition.belief_id, "Some evidence freshness is below 25%.", stale))
        if state.contradiction_score >= .55:
            findings.append(AuditFinding("material_contradiction", "warning", definition.belief_id,
                "Strong supporting and opposing evidence coexist.", tuple(state.support_evidence_ids + state.opposing_evidence_ids)))
        if active and not any(e.source_type == "primary" for e in active):
            findings.append(AuditFinding("no_primary_source", "info", definition.belief_id, "No primary-source evidence is present."))
        weak = tuple(e.evidence_id for e in active if not e.source.strip() or e.source.strip().lower() == "unknown")
        if weak:
            findings.append(AuditFinding("weak_provenance", "warning", definition.belief_id, "Evidence without a clear source weakens auditability.", weak))
        if state.probability >= .75 and state.confidence < .50:
            findings.append(AuditFinding("high_belief_low_confidence", "warning", definition.belief_id,
                "High belief probability is not backed by high evidence confidence."))
        if definition.outcome_rule == "manual_binary_resolution":
            findings.append(AuditFinding("manual_outcome_rule", "info", definition.belief_id,
                "Outcome resolution is manual; real-data adapters should define an explicit deterministic outcome rule."))
        reps = [e for e in active if e.evidence_id in state.representative_evidence_ids]
        if len(reps) >= 3:
            counts: Dict[str, int] = {}
            for e in reps: counts[e.source] = counts.get(e.source, 0) + 1
            if max(counts.values()) / len(reps) >= .80:
                findings.append(AuditFinding("source_concentration", "warning", definition.belief_id,
                    "At least 80% of independent representatives come from one source."))
        return findings


class BeliefCore:
    """Belief engine + frozen forecast registry + verification/calibration memory."""
    def __init__(self, state_dir: str | os.PathLike[str]) -> None:
        self.state_dir = Path(state_dir)
        self.state_path = self.state_dir / "state.json"
        self.ledger_path = self.state_dir / "ledger.jsonl"
        self.dashboard_path = self.state_dir / "dashboard.json"
        self.definitions: Dict[str, BeliefDefinition] = {}
        self.evidence: Dict[str, Evidence] = {}
        self.beliefs: Dict[str, BeliefState] = {}
        self.history: Dict[str, List[Dict[str, Any]]] = {}
        self.forecasts: Dict[str, ForecastSnapshot] = {}
        self.verifications: Dict[str, Verification] = {}
        self.auditor = BeliefAuditor()
        self.load()

    def load(self) -> None:
        if not self.state_path.exists(): return
        p = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.definitions = {x["belief_id"]: BeliefDefinition.from_dict(x) for x in p.get("definitions", [])}
        self.evidence = {x["evidence_id"]: Evidence.from_dict(x) for x in p.get("evidence", [])}
        self.beliefs = {x["belief_id"]: BeliefState.from_dict(x) for x in p.get("beliefs", [])}
        self.history = {str(k): list(v) for k, v in (p.get("history") or {}).items()}
        self.forecasts = {x["forecast_id"]: ForecastSnapshot.from_dict(x) for x in p.get("forecasts", [])}
        raw_vs = p.get("verifications", [])
        for x in raw_vs:
            v = Verification.from_dict(x)
            self.verifications[v.verification_id] = v

    def register_beliefs(self, definitions: Iterable[BeliefDefinition]) -> None:
        for d in definitions:
            existing = self.definitions.get(d.belief_id)
            if existing is not None and existing.to_dict() != d.to_dict():
                if any(f.belief_id == d.belief_id for f in self.forecasts.values()):
                    raise ValueError(f"belief definition {d.belief_id} is immutable after forecasts exist")
            self.definitions[d.belief_id] = d
            self.history.setdefault(d.belief_id, [])

    def ingest(self, evidence: Iterable[Evidence]) -> None:
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
        return max(items, key=lambda e: (rank[e.source_type], e.effective_mass(as_of, half_life_hours), parse_time(e.observed_at).timestamp()))

    def _representatives(self, definition: BeliefDefinition, as_of: str | datetime) -> List[Evidence]:
        as_dt = parse_time(as_of)
        items = [e for e in self.evidence.values() if e.belief_id == definition.belief_id and parse_time(e.observed_at) <= as_dt]
        clusters: Dict[str, List[Evidence]] = {}
        for e in items: clusters.setdefault(e.independence_cluster, []).append(e)
        return [self._representative(v, as_dt, definition.half_life_hours) for v in clusters.values()]

    def _compute_raw_state(self, d: BeliefDefinition, as_of: str | datetime) -> BeliefState:
        as_dt = parse_time(as_of)
        all_items = [e for e in self.evidence.values() if e.belief_id == d.belief_id and parse_time(e.observed_at) <= as_dt]
        reps = self._representatives(d, as_dt)
        cluster_rows: Dict[str, List[Evidence]] = {}
        for e in all_items: cluster_rows.setdefault(e.independence_cluster, []).append(e)
        conflicting_clusters = sum(1 for rows in cluster_rows.values() if len({x.direction for x in rows}) > 1)
        cluster_conflict = 0.0 if not cluster_rows else conflicting_clusters / len(cluster_rows)
        support = oppose = signed = 0.0
        freshness: List[float] = []; reliabilities: List[float] = []
        for e in reps:
            mass = e.effective_mass(as_dt, d.half_life_hours)
            signed += e.direction * mass
            support += mass if e.direction > 0 else 0.0
            oppose += mass if e.direction < 0 else 0.0
            freshness.append(e.freshness(as_dt, d.half_life_hours)); reliabilities.append(e.reliability)
        prob = logistic(logit(d.prior_probability) + UPDATE_GAIN * signed)
        total = support + oppose
        contradiction = 0.0 if total <= 1e-12 else clamp(2.0 * min(support, oppose) / total)
        count = len(reps)
        coverage = 1.0 - math.exp(-count / 3.0) if count else 0.0
        fresh_score = sum(freshness) / len(freshness) if freshness else 0.0
        quality = sum(reliabilities) / len(reliabilities) if reliabilities else 0.0
        diversity_n = len({e.source for e in reps}); diversity = min(1.0, diversity_n / 3.0)
        confidence = clamp((.35 * quality + .25 * fresh_score + .20 * coverage + .20 * diversity)
                           * (1.0 - .45 * contradiction) * (1.0 - .25 * cluster_conflict))
        prev = self.beliefs.get(d.belief_id)
        return BeliefState(
            belief_id=d.belief_id, claim=d.claim, probability=round(prob, 6), confidence=round(confidence, 6),
            previous_probability=None if prev is None else round(prev.probability, 6),
            support_evidence_ids=sorted(e.evidence_id for e in all_items if e.direction > 0),
            opposing_evidence_ids=sorted(e.evidence_id for e in all_items if e.direction < 0),
            representative_evidence_ids=sorted(e.evidence_id for e in reps), independent_clusters=count,
            source_diversity=diversity_n, contradiction_score=round(contradiction, 6),
            cluster_conflict_score=round(cluster_conflict, 6), freshness_score=round(fresh_score, 6),
            last_updated=iso_z(as_dt), entity=d.entity, domain=d.domain, alternative_group=d.alternative_group,
            tags=list(d.tags), horizon_hours=d.horizon_hours,
        )

    @staticmethod
    def _normalize_alternative_groups(states: MutableMapping[str, BeliefState]) -> None:
        groups: Dict[str, List[BeliefState]] = {}
        for state in states.values():
            if state.alternative_group: groups.setdefault(state.alternative_group, []).append(state)
        for rows in groups.values():
            if len(rows) < 2: continue
            total = sum(max(1e-9, x.probability) for x in rows)
            for x in rows: x.probability = round(x.probability / total, 6)

    def recompute(self, as_of: str | datetime | None = None) -> Dict[str, BeliefState]:
        as_dt = parse_time(as_of or utc_now())
        states = {bid: self._compute_raw_state(d, as_dt) for bid, d in self.definitions.items()}
        self._normalize_alternative_groups(states)
        for bid, state in states.items():
            d = self.definitions[bid]
            items = [e for e in self.evidence.values() if e.belief_id == bid]
            findings = self.auditor.audit(d, state, items, as_dt)
            sev = {f.severity for f in findings}
            state.audit_status = "critical" if "critical" in sev else "warning" if "warning" in sev else "ok"
        for bid, state in states.items():
            prev = self.beliefs.get(bid)
            changed = prev is None or abs(prev.probability - state.probability) >= 1e-6 or abs(prev.confidence - state.confidence) >= 1e-6
            if changed:
                event = {"timestamp": state.last_updated, "belief_id": bid,
                         "previous_probability": None if prev is None else prev.probability,
                         "probability": state.probability, "confidence": state.confidence,
                         "contradiction_score": state.contradiction_score,
                         "cluster_conflict_score": state.cluster_conflict_score,
                         "independent_clusters": state.independent_clusters, "mode": MODE}
                self.history.setdefault(bid, []).append(event)
                self._append_ledger({"event_type": "belief_transition", **event})
        self.beliefs = states
        self.save(); self.write_dashboard(as_dt)
        return dict(self.beliefs)

    def _forecast_evidence_snapshot(self, belief_id: str, as_of: str | datetime) -> Tuple[Mapping[str, Any], ...]:
        d = self.definitions[belief_id]
        rows = []
        for e in self._representatives(d, as_of):
            rows.append({"evidence_id": e.evidence_id, "source": e.source, "source_type": e.source_type,
                         "evidence_type": e.evidence_type, "direction": e.direction, "strength": e.strength,
                         "reliability": e.reliability, "independence_cluster": e.independence_cluster,
                         "observed_at": e.observed_at, "freshness": round(e.freshness(as_of, d.half_life_hours), 6),
                         "effective_mass": round(e.effective_mass(as_of, d.half_life_hours), 6), "source_ref": e.source_ref})
        return tuple(rows)

    def capture_forecast(self, belief_id: str, as_of: str | datetime | None = None, target_at: str | datetime | None = None,
                         regime: str = "unknown", forecast_id: Optional[str] = None,
                         metadata: Optional[Mapping[str, Any]] = None) -> ForecastSnapshot:
        if belief_id not in self.beliefs:
            raise KeyError(f"Unknown or not-yet-computed belief: {belief_id}")
        state = self.beliefs[belief_id]; d = self.definitions[belief_id]
        as_dt = parse_time(as_of or state.last_updated)
        if as_dt != parse_time(state.last_updated):
            raise ValueError("capture_forecast as_of must equal the currently computed belief timestamp; recompute first")
        target_dt = parse_time(target_at) if target_at is not None else as_dt + timedelta(hours=d.horizon_hours)
        if target_dt <= as_dt:
            raise ValueError("target_at must be after forecast_at")
        h = (target_dt - as_dt).total_seconds() / 3600.0
        fid = forecast_id or _stable_id("forecast", belief_id, iso_z(as_dt), iso_z(target_dt), regime)
        set_scope = f"group:{state.alternative_group}" if state.alternative_group else f"belief:{belief_id}"
        set_id = _stable_id("forecastset", set_scope, iso_z(as_dt), iso_z(target_dt), regime)
        snap = ForecastSnapshot(
            forecast_id=fid, forecast_set_id=set_id, belief_id=belief_id, predicted_probability=state.probability,
            forecast_confidence=state.confidence, forecast_at=iso_z(as_dt), target_at=iso_z(target_dt), horizon_hours=round(h, 6),
            domain=state.domain, entity=state.entity, regime=str(regime or "unknown"), alternative_group=state.alternative_group,
            outcome_rule=d.outcome_rule, representative_evidence_ids=tuple(state.representative_evidence_ids),
            evidence_snapshot=self._forecast_evidence_snapshot(belief_id, as_dt), metadata=dict(metadata or {}),
        )
        existing = self.forecasts.get(fid)
        if existing is not None:
            if existing.to_dict() != snap.to_dict():
                raise ValueError(f"forecast_id {fid} already exists with different content")
            return existing
        self.forecasts[fid] = snap
        self._append_ledger({"event_type": "forecast_frozen", **snap.to_dict(), "mode": MODE})
        self.save(); self.write_dashboard(as_dt)
        return snap

    def capture_all_forecasts(self, as_of: str | datetime | None = None, regime: str = "unknown") -> List[ForecastSnapshot]:
        as_dt = parse_time(as_of or utc_now())
        if any(parse_time(s.last_updated) != as_dt for s in self.beliefs.values()):
            self.recompute(as_dt)
        return [self.capture_forecast(bid, as_dt, regime=regime) for bid in sorted(self.beliefs)]

    def verify_forecast(self, forecast_id: str, outcome: bool, verified_at: str | datetime | None = None,
                        note: str = "", verification_id: Optional[str] = None,
                        allow_early: bool = False, outcome_source: str = "manual",
                        outcome_ref: Optional[str] = None) -> Verification:
        if forecast_id not in self.forecasts:
            raise KeyError(f"Unknown forecast_id: {forecast_id}")
        f = self.forecasts[forecast_id]
        verified_dt = parse_time(verified_at or utc_now())
        if not allow_early and verified_dt < parse_time(f.target_at):
            raise ValueError("cannot verify a forecast before target_at")
        vid = verification_id or _stable_id("verify", forecast_id)
        p = f.predicted_probability; y = 1.0 if outcome else 0.0
        v = Verification(
            verification_id=vid, forecast_id=f.forecast_id, forecast_set_id=f.forecast_set_id,
            belief_id=f.belief_id, predicted_probability=p,
            forecast_confidence=f.forecast_confidence, outcome=bool(outcome), forecast_at=f.forecast_at, target_at=f.target_at,
            verified_at=iso_z(verified_dt), horizon_hours=f.horizon_hours, horizon_bucket=horizon_bucket(f.horizon_hours),
            domain=f.domain, entity=f.entity, regime=f.regime, alternative_group=f.alternative_group,
            outcome_rule=f.outcome_rule, outcome_source=str(outcome_source or "manual"), outcome_ref=outcome_ref,
            brier_score=round((p-y)**2, 6),
            log_loss=round(_log_loss(p, bool(outcome)), 6), evidence_snapshot=f.evidence_snapshot,
            calibration_eligible=not allow_early, legacy=False, note=note,
        )
        existing = self.verifications.get(vid)
        if existing is not None:
            if existing.to_dict() != v.to_dict():
                raise ValueError(f"verification_id {vid} already exists with different content")
            return existing
        if any(x.forecast_id == forecast_id for x in self.verifications.values()):
            raise ValueError(f"forecast {forecast_id} is already verified")
        self.verifications[vid] = v
        self._append_ledger({"event_type": "verification", **v.to_dict(), "mode": MODE})
        self.save(); self.write_dashboard(verified_dt)
        return v

    def verify(self, belief_id: str, outcome: bool, verified_at: str | datetime | None = None, note: str = "") -> Verification:
        """Backward-compatible convenience method.

        It verifies the latest unresolved *frozen* forecast. If no frozen forecast
        exists, a legacy verification is recorded but excluded from calibration so
        that post-outcome state cannot leak into calibration metrics.
        """
        when = parse_time(verified_at or utc_now())
        candidates = [f for f in self.forecasts.values() if f.belief_id == belief_id and parse_time(f.target_at) <= when
                      and not any(v.forecast_id == f.forecast_id for v in self.verifications.values())]
        if candidates:
            f = max(candidates, key=lambda x: parse_time(x.forecast_at))
            return self.verify_forecast(f.forecast_id, outcome, when, note, outcome_source="manual")
        if belief_id not in self.beliefs:
            raise KeyError(f"Unknown or not-yet-computed belief: {belief_id}")
        s = self.beliefs[belief_id]; d = self.definitions[belief_id]
        vid = _stable_id("legacy-v", belief_id, s.last_updated, iso_z(when), bool(outcome))
        p = s.probability
        v = Verification(
            verification_id=vid, forecast_id=None, forecast_set_id=None, belief_id=belief_id, predicted_probability=p,
            forecast_confidence=s.confidence, outcome=bool(outcome), forecast_at=s.last_updated, target_at=iso_z(when),
            verified_at=iso_z(when), horizon_hours=0.0, horizon_bucket="legacy", domain=d.domain, entity=d.entity,
            regime="unknown", alternative_group=d.alternative_group, outcome_rule=d.outcome_rule,
            outcome_source="legacy", outcome_ref=None,
            brier_score=round((p-(1.0 if outcome else 0.0))**2,6), log_loss=round(_log_loss(p, bool(outcome)),6),
            evidence_snapshot=(), calibration_eligible=False, legacy=True,
            note=(note + " | legacy verification excluded from calibration").strip(" |"),
        )
        existing = self.verifications.get(vid)
        if existing: return existing
        self.verifications[vid] = v
        self._append_ledger({"event_type": "verification_legacy", **v.to_dict(), "mode": MODE})
        self.save(); self.write_dashboard(when)
        return v

    def verify_alternative_group(self, alternative_group: str, winning_belief_id: str,
                                 verified_at: str | datetime | None = None, note: str = "",
                                 outcome_source: str = "manual", outcome_ref: Optional[str] = None) -> List[Verification]:
        when = parse_time(verified_at or utc_now())
        verified_forecasts = {v.forecast_id for v in self.verifications.values() if v.forecast_id}
        candidates = [f for f in self.forecasts.values() if f.alternative_group == alternative_group
                      and parse_time(f.target_at) <= when and f.forecast_id not in verified_forecasts]
        set_ids = {f.forecast_set_id for f in candidates if f.belief_id == winning_belief_id}
        if not set_ids:
            raise KeyError("No unresolved alternative forecast set contains the winning belief")
        chosen = max(set_ids, key=lambda sid: max(parse_time(f.forecast_at) for f in candidates if f.forecast_set_id == sid))
        rows = sorted((f for f in candidates if f.forecast_set_id == chosen), key=lambda f: f.belief_id)
        if len(rows) < 2:
            raise ValueError("alternative group verification requires at least two frozen hypotheses")
        return [self.verify_forecast(f.forecast_id, f.belief_id == winning_belief_id, when, note,
                                     outcome_source=outcome_source, outcome_ref=outcome_ref) for f in rows]

    def calibration_summary(self) -> Dict[str, Any]:
        return build_calibration_report([v.to_dict() for v in self.verifications.values()])

    def trajectory_diagnostics(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for v in self.verifications.values():
            if not v.calibration_eligible: continue
            events = [e for e in self.history.get(v.belief_id, []) if parse_time(v.forecast_at) <= parse_time(e["timestamp"]) <= parse_time(v.target_at)]
            if not events:
                out[v.verification_id] = {"belief_id": v.belief_id, "flip_count": 0, "first_correct_side_at": None, "lead_time_hours": None}
                continue
            correct_side = lambda p: (float(p) >= .5) == bool(v.outcome)
            sides = [correct_side(e["probability"]) for e in events]
            flips = sum(1 for a,b in zip(sides, sides[1:]) if a != b)
            first = next((e for e in events if correct_side(e["probability"])), None)
            lead = None if first is None else (parse_time(v.target_at) - parse_time(first["timestamp"])).total_seconds()/3600.0
            out[v.verification_id] = {"belief_id": v.belief_id, "flip_count": flips,
                "first_correct_side_at": None if first is None else first["timestamp"],
                "lead_time_hours": None if lead is None else round(lead, 6)}
        return out

    def unresolved_forecasts(self, as_of: str | datetime | None = None) -> List[Dict[str, Any]]:
        verified = {v.forecast_id for v in self.verifications.values() if v.forecast_id}
        rows = [f.to_dict() for f in self.forecasts.values() if f.forecast_id not in verified]
        if as_of is not None:
            now = parse_time(as_of)
            for r in rows: r["due"] = parse_time(r["target_at"]) <= now
        return sorted(rows, key=lambda r: (r["target_at"], r["forecast_id"]))

    def save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        p = {"schema_version": SCHEMA_VERSION, "mode": MODE,
             "definitions": [self.definitions[k].to_dict() for k in sorted(self.definitions)],
             "evidence": [self.evidence[k].to_dict() for k in sorted(self.evidence)],
             "beliefs": [self.beliefs[k].to_dict() for k in sorted(self.beliefs)],
             "history": self.history,
             "forecasts": [self.forecasts[k].to_dict() for k in sorted(self.forecasts)],
             "verifications": [self.verifications[k].to_dict() for k in sorted(self.verifications)]}
        self._atomic_json_write(self.state_path, p)

    def dashboard_snapshot(self, as_of: str | datetime | None = None) -> Dict[str, Any]:
        as_dt = parse_time(as_of or utc_now()); findings: List[Dict[str, Any]] = []
        for bid, state in sorted(self.beliefs.items()):
            d = self.definitions[bid]; items = [e for e in self.evidence.values() if e.belief_id == bid]
            findings.extend(x.to_dict() for x in self.auditor.audit(d, state, items, as_dt))
        return {"schema_version": SCHEMA_VERSION, "mode": MODE, "status": "ready" if self.beliefs else "awaiting_evidence",
                "generated_at": iso_z(as_dt),
                "controls": {"decision_engine_connected": False, "trade_execution_enabled": False,
                             "policy_output_enabled": False, "automatic_tuning_enabled": False,
                             "message": "Belief Core v2 is shadow-only and cannot control BRACE/WES/BRACE-SPX."},
                "summary": {"belief_count": len(self.beliefs), "evidence_count": len(self.evidence),
                            "forecast_count": len(self.forecasts), "verification_count": len(self.verifications),
                            "unresolved_forecast_count": len(self.unresolved_forecasts()),
                            "independent_cluster_count": len({e.independence_cluster for e in self.evidence.values()}),
                            "audit_warning_count": sum(1 for x in findings if x["severity"] in {"warning","critical"})},
                "world_state": [self.beliefs[k].to_dict() for k in sorted(self.beliefs)],
                "audit_findings": findings, "history": self.history,
                "unresolved_forecasts": self.unresolved_forecasts(as_dt),
                "calibration": self.calibration_summary(), "trajectory": self.trajectory_diagnostics()}

    def write_dashboard(self, as_of: str | datetime | None = None) -> None:
        self._atomic_json_write(self.dashboard_path, self.dashboard_snapshot(as_of))

    def _append_ledger(self, record: Mapping[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        prev_hash = self._last_ledger_hash()
        payload = dict(record); payload["ledger_version"] = 2; payload["prev_hash"] = prev_hash
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload["record_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self.ledger_path.open("a", encoding="utf-8") as h:
            h.write(json.dumps(payload, ensure_ascii=False, sort_keys=True)); h.write("\n")

    def _last_ledger_hash(self) -> str:
        if not self.ledger_path.exists(): return "GENESIS"
        lines = [x for x in self.ledger_path.read_text(encoding="utf-8").splitlines() if x.strip()]
        if not lines: return "GENESIS"
        try:
            last = json.loads(lines[-1])
            if last.get("record_hash"): return str(last["record_hash"])
        except Exception:
            pass
        return hashlib.sha256(lines[-1].encode("utf-8")).hexdigest()

    def verify_ledger_integrity(self) -> Dict[str, Any]:
        if not self.ledger_path.exists(): return {"valid": True, "records": 0, "legacy_prefix_records": 0}
        lines = [x for x in self.ledger_path.read_text(encoding="utf-8").splitlines() if x.strip()]
        prev: Optional[str] = None; legacy = 0; checked = 0
        for idx, line in enumerate(lines):
            obj = json.loads(line)
            if not obj.get("record_hash"):
                legacy += 1; prev = hashlib.sha256(line.encode("utf-8")).hexdigest(); continue
            claimed = str(obj["record_hash"]); copy = dict(obj); copy.pop("record_hash", None)
            canonical = json.dumps(copy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            actual = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if claimed != actual:
                return {"valid": False, "records": len(lines), "failed_line": idx+1, "reason": "record_hash_mismatch"}
            expected_prev = "GENESIS" if idx == 0 else prev
            if str(obj.get("prev_hash")) != str(expected_prev):
                return {"valid": False, "records": len(lines), "failed_line": idx+1, "reason": "prev_hash_mismatch"}
            prev = claimed; checked += 1
        return {"valid": True, "records": len(lines), "legacy_prefix_records": legacy, "hashed_records_checked": checked}

    @staticmethod
    def _atomic_json_write(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as h:
                json.dump(payload, h, ensure_ascii=False, indent=2, sort_keys=True); h.write("\n")
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name): os.unlink(tmp_name)


def load_input(path: str | os.PathLike[str]) -> Tuple[List[BeliefDefinition], List[Evidence], Optional[str]]:
    p = json.loads(Path(path).read_text(encoding="utf-8"))
    return ([BeliefDefinition.from_dict(x) for x in p.get("beliefs", [])],
            [Evidence.from_dict(x) for x in p.get("evidence", [])], p.get("as_of"))
