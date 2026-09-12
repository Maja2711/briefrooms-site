#!/usr/bin/env python3
"""Shared, read-only primitives for BriefRooms Learning Loop v2.

Hard invariants: decision quality is ex-ante only; realized outcomes never
rewrite it; shadow observations are prospective; challenger has zero production
authority.
"""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "briefrooms-learning-loop-v2"
_FORBIDDEN_DQ = ("pnl", "profit", "return", "realized", "outcome", "exit", "mfe", "mae", "win", "loss")


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _probability(value: Any) -> float:
    number = _finite(value)
    if number is None:
        raise ValueError("probability must be finite")
    if 1.0 < number <= 100.0:
        number /= 100.0
    if not 0.0 <= number <= 1.0:
        raise ValueError("probability must be within [0,1] or [0,100]")
    return number


def sha256_payload(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def assess_decision_quality(components: Mapping[str, Any], weights: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Score process/evidence known at T0. Ex-post fields are rejected."""
    if not components:
        return {"status": "INSUFFICIENT_INPUT", "score": None, "outcome_independent": True}
    values: dict[str, float] = {}
    for key, raw in components.items():
        lowered = str(key).lower()
        if any(token in lowered for token in _FORBIDDEN_DQ):
            raise ValueError(f"decision quality cannot use ex-post field: {key}")
        value = _finite(raw)
        if value is None:
            continue
        if 1.0 < value <= 100.0:
            value /= 100.0
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"decision-quality component outside [0,1]: {key}")
        values[str(key)] = value
    if not values:
        return {"status": "INSUFFICIENT_INPUT", "score": None, "outcome_independent": True}
    raw_weights = dict(weights or {})
    effective = {key: float(raw_weights.get(key, 1.0)) for key in values}
    if any(weight < 0 for weight in effective.values()) or sum(effective.values()) <= 0:
        raise ValueError("decision-quality weights must be non-negative and sum to > 0")
    total = sum(effective.values())
    score = sum(values[key] * effective[key] for key in values) / total
    return {
        "status": "ASSESSED",
        "score": round(score, 6),
        "score_0_100": round(score * 100.0, 2),
        "components": {key: round(value, 6) for key, value in values.items()},
        "outcome_independent": True,
        "contract": "ex_ante_only",
    }


def assess_outcome_quality(realized_return: Any, benchmark_return: Any = 0.0) -> dict[str, Any]:
    realized, benchmark = _finite(realized_return), _finite(benchmark_return)
    if realized is None or benchmark is None:
        return {"status": "DATA_GAP"}
    excess = realized - benchmark
    return {
        "status": "ASSESSED",
        "realized_return": round(realized, 8),
        "benchmark_return": round(benchmark, 8),
        "excess_return": round(excess, 8),
        "positive_absolute": realized > 0,
        "positive_relative": excess > 0,
        "contract": "ex_post_only",
    }


def near_miss_analysis(score: Any, threshold: Any, band: float = 3.0) -> dict[str, Any]:
    score_f, threshold_f = _finite(score), _finite(threshold)
    if score_f is None or threshold_f is None or band < 0:
        return {"status": "NOT_EVALUABLE", "is_near_miss": False}
    delta = score_f - threshold_f
    distance = abs(delta)
    return {
        "status": "ASSESSED",
        "score": score_f,
        "threshold": threshold_f,
        "band": float(band),
        "signed_distance": round(delta, 8),
        "absolute_distance": round(distance, 8),
        "side": "AT" if delta == 0 else ("ABOVE" if delta > 0 else "BELOW"),
        "is_near_miss": distance <= band,
    }


def freeze_shadow_candidate(*, candidate_id: str, decision_at: str, status: str,
                            evidence: Mapping[str, Any], plan: Mapping[str, Any] | None = None,
                            score: Any = None, threshold: Any = None,
                            near_miss_band: float = 3.0,
                            model_outputs: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Create immutable ex-ante shadow record. No hindsight fields accepted."""
    for source in (evidence, plan or {}, model_outputs or {}):
        for key in source:
            lowered = str(key).lower()
            if any(token in lowered for token in ("realized", "outcome", "future_return", "exit_price")):
                raise ValueError(f"shadow candidate contains ex-post field: {key}")
    row = {
        "schema_version": SCHEMA_VERSION,
        "event_type": "SHADOW_CANDIDATE",
        "candidate_id": str(candidate_id),
        "decision_at": str(decision_at),
        "status": str(status).upper(),
        "score": _finite(score),
        "threshold": _finite(threshold),
        "near_miss": near_miss_analysis(score, threshold, near_miss_band),
        "evidence": deepcopy(dict(evidence)),
        "plan": deepcopy(dict(plan or {})),
        "model_outputs": deepcopy(dict(model_outputs or {})),
        "prospective_only": True,
        "immutable": True,
        "decision_influence": False,
    }
    row["state_sha256"] = sha256_payload(row)
    return row


def compute_mfe_mae(entry_price: Any, bars: Sequence[Mapping[str, Any]], side: str = "LONG") -> dict[str, Any]:
    entry = _finite(entry_price)
    if entry is None or entry <= 0:
        return {"status": "DATA_GAP", "reason": "invalid_entry_price"}
    side_u = side.upper()
    if side_u not in {"LONG", "SHORT"}:
        raise ValueError("side must be LONG or SHORT")
    observations = []
    for row in bars:
        high, low = _finite(row.get("high")), _finite(row.get("low"))
        if high is None or low is None or high <= 0 or low <= 0:
            continue
        stamp = row.get("timestamp") or row.get("date") or row.get("day")
        if side_u == "LONG":
            favorable, adverse = high / entry - 1.0, low / entry - 1.0
        else:
            favorable, adverse = entry / low - 1.0, entry / high - 1.0
        observations.append((favorable, adverse, stamp))
    if not observations:
        return {"status": "DATA_GAP", "reason": "no_valid_bars"}
    fav, adv = max(observations, key=lambda x: x[0]), min(observations, key=lambda x: x[1])
    mfe, mae = max(0.0, fav[0]), min(0.0, adv[1])
    return {
        "status": "ASSESSED", "side": side_u, "bars_observed": len(observations),
        "mfe": round(mfe, 8), "mae": round(mae, 8),
        "mfe_percent": round(mfe * 100.0, 4), "mae_percent": round(mae * 100.0, 4),
        "mfe_at": None if mfe == 0 else str(fav[2]), "mae_at": None if mae == 0 else str(adv[2]),
    }


def confidence_calibration(records: Sequence[Mapping[str, Any]], bins: int = 5, min_samples: int = 30) -> dict[str, Any]:
    pairs = []
    for row in records:
        if row.get("confidence") is None or row.get("outcome") is None:
            continue
        p, y = _probability(row["confidence"]), _finite(row["outcome"])
        if y not in (0.0, 1.0):
            raise ValueError("binary outcome must be 0 or 1")
        pairs.append((p, y))
    brier = None if not pairs else sum((p - y) ** 2 for p, y in pairs) / len(pairs)
    reliability = []
    for index in range(bins):
        low, high = index / bins, (index + 1) / bins
        values = [(p, y) for p, y in pairs if p >= low and (p < high or index == bins - 1)]
        if values:
            mean_p = sum(p for p, _ in values) / len(values)
            empirical = sum(y for _, y in values) / len(values)
            reliability.append({"low": low, "high": high, "n": len(values),
                                "mean_confidence": round(mean_p, 6), "empirical_rate": round(empirical, 6),
                                "calibration_gap": round(mean_p - empirical, 6)})
    return {
        "status": "ASSESSED" if len(pairs) >= min_samples else "INSUFFICIENT_SAMPLE",
        "n": len(pairs), "minimum_samples": min_samples,
        "brier_score": None if brier is None else round(brier, 8),
        "reliability_bins": reliability,
        "production_writeback_allowed": False,
    }


def model_marginal_contribution(model_scores: Mapping[str, Any], outcome: Any = None,
                                weights: Mapping[str, Any] | None = None, threshold: float = 0.5) -> dict[str, Any]:
    """Leave-one-model-out ablation. Do not pass heuristics disguised as models."""
    if len(model_scores) < 2:
        return {"status": "INSUFFICIENT_MODELS", "models": {}}
    scores = {str(key): _probability(value) for key, value in model_scores.items()}
    raw_weights = {key: float((weights or {}).get(key, 1.0)) for key in scores}
    if any(value < 0 for value in raw_weights.values()):
        raise ValueError("model weights cannot be negative")
    def aggregate(exclude: str | None = None) -> float:
        names = [key for key in scores if key != exclude]
        total = sum(raw_weights[key] for key in names)
        if total <= 0:
            raise ValueError("ablation leaves zero model weight")
        return sum(scores[key] * raw_weights[key] for key in names) / total
    full = aggregate()
    actual = None if outcome is None else _finite(outcome)
    if actual is not None and actual not in (0.0, 1.0):
        raise ValueError("binary outcome must be 0 or 1")
    rows = {}
    for name in scores:
        without = aggregate(name)
        row = {
            "model_score": round(scores[name], 8), "ensemble_full": round(full, 8),
            "ensemble_without_model": round(without, 8), "ensemble_score_delta": round(full - without, 8),
            "decision_flip_without_model": (full >= threshold) != (without >= threshold),
        }
        if actual is not None:
            row["brier_improvement_vs_without_model"] = round((without - actual) ** 2 - (full - actual) ** 2, 8)
        rows[name] = row
    return {"status": "ASSESSED", "ensemble_score": round(full, 8), "threshold": threshold, "models": rows}


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    if isinstance(value, Mapping):
        for key in sorted(value):
            child = f"{prefix}.{key}" if prefix else str(key)
            result.update(_flatten(value[key], child))
    elif isinstance(value, list):
        for index, child_value in enumerate(value):
            result.update(_flatten(child_value, f"{prefix}[{index}]"))
    else:
        result[prefix] = value
    return result


def evidence_delta(previous: Mapping[str, Any] | None, current: Mapping[str, Any],
                   ignore_paths: Sequence[str] = ("generated_at", "published_at", "captured_at", "updated_at")) -> dict[str, Any]:
    prev, curr = _flatten(previous or {}), _flatten(current)
    def keep(path: str) -> bool:
        return not any(path == key or path.endswith("." + key) for key in ignore_paths)
    prev, curr = ({k: v for k, v in prev.items() if keep(k)}, {k: v for k, v in curr.items() if keep(k)})
    added = {key: curr[key] for key in curr.keys() - prev.keys()}
    removed = {key: prev[key] for key in prev.keys() - curr.keys()}
    changed = {key: {"before": prev[key], "after": curr[key]} for key in curr.keys() & prev.keys() if prev[key] != curr[key]}
    return {
        "status": "ASSESSED", "previous_sha256": sha256_payload(previous or {}), "current_sha256": sha256_payload(current),
        "added": dict(sorted(added.items())), "removed": dict(sorted(removed.items())),
        "changed": dict(sorted(changed.items())), "change_count": len(added) + len(removed) + len(changed),
    }


def live_challenger_evaluation(*, input_hash_champion: str, input_hash_challenger: str,
                               champion: Mapping[str, Any], challenger: Mapping[str, Any], min_samples: int = 30,
                               min_excess_return_edge: float = 0.0, max_drawdown_worsening: float = 0.0,
                               max_brier_worsening: float = 0.0) -> dict[str, Any]:
    """Same-input live shadow comparison. Never promotes or writes production."""
    if not input_hash_champion or input_hash_champion != input_hash_challenger:
        return {"status": "INVALID_COMPARISON", "decision": "NO_CHANGE",
                "reason": "champion_and_challenger_inputs_differ", "production_writeback_allowed": False}
    n_c, n_h = int(_finite(champion.get("n")) or 0), int(_finite(challenger.get("n")) or 0)
    if min(n_c, n_h) < min_samples:
        return {"status": "INSUFFICIENT_SAMPLE", "decision": "NO_CHANGE", "n_champion": n_c, "n_challenger": n_h,
                "minimum_samples": min_samples, "production_writeback_allowed": False}
    required = ("excess_return", "max_drawdown", "brier_score")
    c = {key: _finite(champion.get(key)) for key in required}
    h = {key: _finite(challenger.get(key)) for key in required}
    if any(value is None for value in [*c.values(), *h.values()]):
        return {"status": "DATA_GAP", "decision": "NO_CHANGE", "reason": "required_live_shadow_metric_missing",
                "production_writeback_allowed": False}
    return_edge = h["excess_return"] - c["excess_return"]
    # Severity comparison handles both -10% and +10% drawdown conventions.
    drawdown_worsening = abs(h["max_drawdown"]) - abs(c["max_drawdown"])
    brier_worsening = h["brier_score"] - c["brier_score"]
    passes = (return_edge > min_excess_return_edge and drawdown_worsening <= max_drawdown_worsening
              and brier_worsening <= max_brier_worsening)
    return {
        "status": "ASSESSED", "decision": "PROMOTION_CANDIDATE" if passes else "NO_CHANGE",
        "return_edge": round(return_edge, 8), "drawdown_worsening": round(drawdown_worsening, 8),
        "brier_worsening": round(brier_worsening, 8), "production_writeback_allowed": False,
        "requires_existing_governed_promotion_gate": True,
    }
