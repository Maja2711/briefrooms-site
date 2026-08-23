#!/usr/bin/env python3
"""ARIS-inspired epistemic optimization research layer for Belief Core.

This module transfers three mathematical principles only:
1. Model + Residual
2. Competing representations
3. ROI-based search / pruning

It does NOT import ARIS code, does NOT modify Belief Core, and has zero decision authority.
The canonical BeliefState probability remains authoritative.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

CONTRACT_VERSION = "belief-aris-shadow-v1"
MODE = "research_shadow"
UPDATE_GAIN = 1.65


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def logit(probability: float) -> float:
    p = clamp(probability, 1e-6, 1.0 - 1e-6)
    return math.log(p / (1.0 - p))


def logistic(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def parse_time(value: str):
    from datetime import datetime, timezone
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class RepresentationScore:
    name: str
    probability: float
    evidence_ids: Tuple[str, ...]
    residual_evidence_ids: Tuple[str, ...]
    retained_effective_mass: float
    residual_effective_mass: float
    information_retention: float
    complexity_units: int
    contradiction: float
    stability_distance_from_authority: float
    roi_score: float
    pruned: bool
    prune_reason: Optional[str]


@dataclass(frozen=True)
class ModelResidualState:
    belief_id: str
    authoritative_probability: float
    selected_representation: str
    selected_probability: float
    residual_probability_gap: float
    residual_evidence_ids: Tuple[str, ...]
    representation_disagreement: float
    representations: Tuple[RepresentationScore, ...]
    authority_preserved: bool = True
    decision_influence: bool = False
    belief_core_writeback_enabled: bool = False
    automatic_tuning_enabled: bool = False
    contract_version: str = CONTRACT_VERSION
    mode: str = MODE

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ARISBeliefShadow:
    """Read-only competing-representation evaluator over persisted Belief Core state."""

    def __init__(self, state_payload: Mapping[str, Any]) -> None:
        self.definitions = {str(x["belief_id"]): dict(x) for x in state_payload.get("definitions", [])}
        self.beliefs = {str(x["belief_id"]): dict(x) for x in state_payload.get("beliefs", [])}
        self.evidence = {str(x["evidence_id"]): dict(x) for x in state_payload.get("evidence", [])}

    def _mass(self, row: Mapping[str, Any], definition: Mapping[str, Any], as_of: str) -> float:
        age_h = max(0.0, (parse_time(as_of) - parse_time(str(row["observed_at"]))).total_seconds() / 3600.0)
        half_life = max(0.01, float(definition.get("half_life_hours", 72.0)))
        freshness = 0.5 ** (age_h / half_life)
        return clamp(row.get("strength", 0.0)) * clamp(row.get("reliability", 0.0)) * freshness

    def _probability(self, prior: float, rows: Sequence[Mapping[str, Any]], definition: Mapping[str, Any], as_of: str) -> float:
        signed = sum(int(x.get("direction", 1)) * self._mass(x, definition, as_of) for x in rows)
        return logistic(logit(prior) + UPDATE_GAIN * signed)

    @staticmethod
    def _contradiction(rows: Sequence[Mapping[str, Any]], masses: Sequence[float]) -> float:
        support = sum(m for row, m in zip(rows, masses) if int(row.get("direction", 1)) > 0)
        oppose = sum(m for row, m in zip(rows, masses) if int(row.get("direction", 1)) < 0)
        total = support + oppose
        return 0.0 if total <= 1e-12 else clamp(2.0 * min(support, oppose) / total)

    def _representatives(self, belief: Mapping[str, Any]) -> List[Dict[str, Any]]:
        return [self.evidence[eid] for eid in belief.get("representative_evidence_ids", []) if eid in self.evidence]

    def _candidates(self, belief: Mapping[str, Any], definition: Mapping[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        reps = self._representatives(belief)
        as_of = str(belief["last_updated"])
        fresh = [x for x in reps if self._mass(x, definition, as_of) >= 0.20]
        high_reliability = [x for x in reps if float(x.get("reliability", 0.0)) >= 0.70]
        primary_preferred = [x for x in reps if x.get("source_type") == "primary"]
        if not primary_preferred:
            primary_preferred = [x for x in reps if x.get("source_type") != "derived"]
        return {
            "full_representatives": reps,
            "fresh_signal": fresh,
            "high_reliability": high_reliability,
            "primary_preferred": primary_preferred,
        }

    def evaluate_belief(self, belief_id: str, *, complexity_penalty: float = 0.035, min_roi: float = 0.05) -> ModelResidualState:
        belief = self.beliefs[belief_id]
        definition = self.definitions[belief_id]
        as_of = str(belief["last_updated"])
        prior = float(definition.get("prior_probability", 0.5))
        authoritative = float(belief["probability"])
        all_reps = self._representatives(belief)
        all_ids = {str(x["evidence_id"]) for x in all_reps}
        total_mass = sum(self._mass(x, definition, as_of) for x in all_reps)

        scores: List[RepresentationScore] = []
        for name, rows in self._candidates(belief, definition).items():
            ids = tuple(str(x["evidence_id"]) for x in rows)
            residual_ids = tuple(sorted(all_ids.difference(ids)))
            retained_mass = sum(self._mass(x, definition, as_of) for x in rows)
            residual_mass = max(0.0, total_mass - retained_mass)
            retention = 1.0 if total_mass <= 1e-12 else retained_mass / total_mass
            probability = self._probability(prior, rows, definition, as_of)
            masses = [self._mass(x, definition, as_of) for x in rows]
            contradiction = self._contradiction(rows, masses)
            distance = abs(probability - authoritative)
            complexity = len(rows)
            # Research ROI: retain effective information while penalising model size,
            # disagreement with the authoritative aggregate and internal contradiction.
            utility = retention - 0.50 * distance - 0.20 * contradiction
            roi = utility - complexity_penalty * complexity
            pruned = not rows or (name != "full_representatives" and roi < min_roi)
            reason = "empty_representation" if not rows else ("roi_below_threshold" if pruned else None)
            scores.append(RepresentationScore(
                name=name,
                probability=round(probability, 6),
                evidence_ids=ids,
                residual_evidence_ids=residual_ids,
                retained_effective_mass=round(retained_mass, 6),
                residual_effective_mass=round(residual_mass, 6),
                information_retention=round(retention, 6),
                complexity_units=complexity,
                contradiction=round(contradiction, 6),
                stability_distance_from_authority=round(distance, 6),
                roi_score=round(roi, 6),
                pruned=pruned,
                prune_reason=reason,
            ))

        viable = [x for x in scores if not x.pruned]
        selected = max(viable or scores, key=lambda x: x.roi_score)
        probs = [x.probability for x in viable]
        disagreement = (max(probs) - min(probs)) if len(probs) >= 2 else 0.0
        return ModelResidualState(
            belief_id=belief_id,
            authoritative_probability=round(authoritative, 6),
            selected_representation=selected.name,
            selected_probability=selected.probability,
            residual_probability_gap=round(authoritative - selected.probability, 6),
            residual_evidence_ids=selected.residual_evidence_ids,
            representation_disagreement=round(disagreement, 6),
            representations=tuple(sorted(scores, key=lambda x: x.roi_score, reverse=True)),
        )

    def evaluate_all(self) -> Dict[str, ModelResidualState]:
        return {
            belief_id: self.evaluate_belief(belief_id)
            for belief_id in sorted(self.beliefs)
            if belief_id in self.definitions
        }


def build_shadow_report(state_dir: Path) -> Dict[str, Any]:
    state_path = state_dir / "state.json"
    if not state_path.exists():
        raise FileNotFoundError(state_path)
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    engine = ARISBeliefShadow(payload)
    rows = engine.evaluate_all()
    report = {
        "contract_version": CONTRACT_VERSION,
        "mode": MODE,
        "principles": ["model_plus_residual", "competing_representations", "roi_search_pruning"],
        "authority": {
            "belief_core_probability_remains_authoritative": True,
            "selected_representation_is_diagnostic_only": True,
            "decision_influence": False,
            "belief_core_writeback_enabled": False,
            "automatic_tuning_enabled": False,
        },
        "beliefs": {belief_id: state.to_dict() for belief_id, state in rows.items()},
    }
    out = state_dir / "aris_belief_shadow.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
