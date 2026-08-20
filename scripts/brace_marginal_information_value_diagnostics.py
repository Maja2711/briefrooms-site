#!/usr/bin/env python3
"""PR #18.1 — Marginal Information Value Diagnostics.

Research-shadow diagnostics only.

PR17 supplies prospective paired WITHOUT/WITH economic outcomes. PR18 freezes
what BRACE knew, what Entity Belief knew and the disagreement topology before
those outcomes mature. PR18.1 asks the harder question:

    did the Belief add useful information beyond what BRACE already knew?

The answer is deliberately a diagnostic vector, not a single MIV score. No
composite alpha score, trust score, promotion threshold or decision authority
is defined here.

Redundancy/orthogonality are descriptive dependence proxies only. They use
equal-weighted unique prospectively-frozen Belief states, so repeated engine
decisions against the same forecast state do not pretend to be independent
Belief information.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import statistics
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

MODE = "research_shadow"
SCHEMA_VERSION = "brace-marginal-information-value-diagnostics-v1"
REPORT_VERSION = "brace-marginal-information-value-diagnostics-report-v1"
CONTRACT_VERSION = "brace-miv-diagnostics-contract-v1"

PR18_CONTRACT_VERSION = "brace-information-disagreement-capture-contract-v1"
PR17_CONTRACT_VERSION = "brace-entity-belief-shadow-bridge-contract-v1"

STATE_FILENAME = "BRACE_MIV_DIAGNOSTICS_STATE.json"
REPORT_FILENAME = "BRACE_MIV_DIAGNOSTICS_REPORT.json"

# Engineering availability minima only. Neither is a promotion threshold.
MIN_UNIQUE_BELIEF_STATES_FOR_DEPENDENCE = 4
MIN_SERIAL_N = 4
BOOTSTRAP_ITERATIONS = 1000

# Frozen ex-ante dependence screen; not selected from PnL.
REDUNDANCY_FEATURES: Tuple[Tuple[str, str], ...] = (
    ("feature_scores", "quality_score"),
    ("feature_scores", "valuation_score"),
    ("feature_scores", "momentum_score"),
    ("feature_scores", "risk_score"),
    ("feature_scores", "diversification_score"),
    ("feature_scores", "thesis_score"),
    ("feature_scores", "final_score"),
    ("feature_scores", "risk_adjusted_score"),
    ("expectations", "expected_return_base"),
    ("expectations", "expected_drawdown"),
    ("expectations", "probability_of_reaching_target"),
)


def safety_controls() -> Dict[str, bool]:
    return {
        "active_decision_influence": False,
        "engine_score_writeback": False,
        "belief_probability_writeback": False,
        "candidate_ranking_change": False,
        "optimizer_change": False,
        "target_exposure_change": False,
        "sizing_change": False,
        "veto": False,
        "forced_exit": False,
        "direction_reversal": False,
        "trade_execution": False,
        "policy_output": False,
        "automatic_tuning": False,
        "historical_information_backfill": False,
        "retroactive_source_reconstruction": False,
        "composite_miv_score_output": False,
        "engine_specific_trust_output": False,
        "causal_belief_graph_output": False,
        "automatic_promotion": False,
    }


def capabilities() -> Dict[str, bool]:
    return {
        "prospective_miv_diagnostics_enabled": True,
        "economic_incremental_value_diagnostics_enabled": True,
        "redundancy_proxy_enabled": True,
        "orthogonality_proxy_enabled": True,
        "disagreement_regime_slices_enabled": True,
        "dependence_diagnostics_enabled": True,
        "concentration_diagnostics_enabled": True,
        "deterministic_bootstrap_ci_enabled": True,
        "composite_miv_score_enabled": False,
        "engine_specific_trust_enabled": False,
        "causal_belief_graph_enabled": False,
        "promotion_gate_enabled": False,
    }


def _assert_safety() -> None:
    bad = [key for key, value in safety_controls().items() if value is not False]
    if bad:
        raise RuntimeError("PR18.1 zero-authority invariant violated: " + ",".join(bad))


def parse_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("empty timestamp")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(default)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _sha(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, payload: Any) -> str:
    return f"{prefix}-{_sha(payload)[:20]}"


def _float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _validate_inputs(
    pr18_state: Mapping[str, Any],
    pr18_report: Mapping[str, Any],
    pr17_state: Mapping[str, Any],
    pr17_report: Mapping[str, Any],
) -> None:
    if str(pr18_state.get("contract_version") or "") != PR18_CONTRACT_VERSION:
        raise ValueError("PR18.1 requires reviewed PR18 state contract")
    if str(pr18_report.get("contract_version") or "") != PR18_CONTRACT_VERSION:
        raise ValueError("PR18.1 requires reviewed PR18 report contract")
    if str(pr18_report.get("mode") or "") != MODE:
        raise ValueError("PR18.1 requires PR18 research_shadow mode")
    if pr18_report.get("active_decision_influence") is not False:
        raise ValueError("PR18.1 refuses PR18 input with active decision influence")
    info = pr18_report.get("information_contracts") or {}
    if info.get("source_snapshot_sha_parity_required") is not True:
        raise ValueError("PR18.1 requires PR18 source snapshot SHA parity")
    if info.get("historical_information_backfill") is not False:
        raise ValueError("PR18.1 refuses PR18 historical information backfill")
    if info.get("retroactive_source_reconstruction") is not False:
        raise ValueError("PR18.1 refuses retroactive PR18 source reconstruction")

    if str(pr17_state.get("contract_version") or "") != PR17_CONTRACT_VERSION:
        raise ValueError("PR18.1 requires reviewed PR17 state contract")
    if str(pr17_report.get("contract_version") or "") != PR17_CONTRACT_VERSION:
        raise ValueError("PR18.1 requires reviewed PR17 report contract")
    if str(pr17_report.get("mode") or "") != MODE:
        raise ValueError("PR18.1 requires PR17 research_shadow mode")
    if pr17_report.get("active_decision_influence") is not False:
        raise ValueError("PR18.1 refuses PR17 input with active decision influence")


def empty_state() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "contract_version": CONTRACT_VERSION,
        "first_run_at": None,
        "last_run_at": None,
        "seen_source_fingerprints": [],
        "diagnostic_snapshots": {},
    }


def _assert_append_only(previous: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    before = previous.get("diagnostic_snapshots") or {}
    after = current.get("diagnostic_snapshots") or {}
    for key, value in before.items():
        if key not in after or after[key] != value:
            raise RuntimeError(f"PR18.1 append-only diagnostic snapshot mutation detected: {key}")


def _capture_valid(capture: Mapping[str, Any]) -> bool:
    return (
        capture.get("prospective_to_economic_outcome") is True
        and capture.get("historical_information_backfill") is False
        and capture.get("source_reconstruction") is False
        and capture.get("promotion_authority") is False
    )


def _pair_valid(pair: Mapping[str, Any]) -> bool:
    return (
        pair.get("engine_consumed_belief") is False
        and pair.get("hypothetical_only") is True
        and pair.get("historical_backfill") is False
        and pair.get("promotion_authority") is False
    )


def _belief_state_key(belief: Mapping[str, Any]) -> str:
    forecasts = []
    for raw in belief.get("forecasts") or []:
        if not isinstance(raw, Mapping):
            continue
        forecasts.append({
            "forecast_id": raw.get("forecast_id"),
            "belief_id": raw.get("belief_id"),
            "dimension": raw.get("dimension"),
            "predicted_probability": _float(raw.get("predicted_probability")),
            "forecast_confidence": _float(raw.get("forecast_confidence")),
            "forecast_at": raw.get("forecast_at"),
            "target_at": raw.get("target_at"),
        })
    forecasts.sort(key=lambda x: (str(x.get("belief_id") or ""), str(x.get("forecast_id") or "")))
    return _stable_id("belief-state", forecasts)


def _belief_signature(belief: Mapping[str, Any]) -> str:
    dims = sorted(str(x) for x in (belief.get("dimensions") or []) if x)
    return "+".join(dims) if dims else "UNAVAILABLE"


def _regime_signature(topology: Mapping[str, Any]) -> str:
    return "__".join([
        f"MARKET_{topology.get('market_stance') or 'UNAVAILABLE'}",
        f"SECTOR_{topology.get('sector_stance') or 'UNAVAILABLE'}",
        f"FACTOR_{topology.get('factor_stance') or 'UNAVAILABLE'}",
        f"TOPDOWN_{topology.get('top_down_state') or 'UNAVAILABLE'}",
    ])


def _engine_scalar_features(engine: Mapping[str, Any]) -> Dict[str, Optional[float]]:
    result: Dict[str, Optional[float]] = {}
    for block, field in REDUNDANCY_FEATURES:
        source = engine.get(block) or {}
        value = source.get(field) if isinstance(source, Mapping) else None
        result[f"{block}.{field}"] = _float(value)
    return result


def _find_outcome_item(
    outcome: Mapping[str, Any], instrument: str, item_count: int
) -> Tuple[Optional[Mapping[str, Any]], Optional[str]]:
    rows = [
        row for row in (outcome.get("items") or [])
        if isinstance(row, Mapping) and str(row.get("instrument") or "").lower() == instrument.lower()
    ]
    if len(rows) == 1:
        return rows[0], None
    if len(rows) > 1:
        return None, "duplicate_outcome_instrument_rows"
    if item_count == 1:
        # For a single-item pair, pair aggregate and item contribution are identical.
        return {
            "instrument": instrument,
            "without_contribution_return": outcome.get("without_return"),
            "with_contribution_return": outcome.get("with_return"),
            "without_turnover": outcome.get("without_turnover"),
            "with_turnover": outcome.get("with_turnover"),
            "without_cost_return": outcome.get("without_cost_return"),
            "with_cost_return": outcome.get("with_cost_return"),
        }, None
    return None, "multi_item_pair_missing_item_outcomes"


def build_observations(
    pr18_state: Mapping[str, Any],
    pr17_state: Mapping[str, Any],
    *,
    as_of: datetime,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    pairs = {
        str(k): v for k, v in (pr17_state.get("pair_sets") or {}).items()
        if isinstance(v, Mapping)
    }
    outcomes = {
        str(k): v for k, v in (pr17_state.get("economic_outcomes") or {}).items()
        if isinstance(v, Mapping)
    }
    observations: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []

    for pair_id, capture in sorted((pr18_state.get("captures") or {}).items()):
        if not isinstance(capture, Mapping):
            continue
        if not _capture_valid(capture):
            issues.append({"pair_set_id": pair_id, "code": "invalid_pr18_capture_governance", "critical": True})
            continue
        pair = pairs.get(str(pair_id))
        if not pair or not _pair_valid(pair):
            issues.append({"pair_set_id": pair_id, "code": "missing_or_invalid_pr17_pair", "critical": True})
            continue
        if str(capture.get("decision_set_id") or "") != str(pair.get("decision_set_id") or ""):
            issues.append({"pair_set_id": pair_id, "code": "decision_set_join_mismatch", "critical": True})
            continue
        try:
            decision_at = parse_time(str(capture.get("decision_at") or pair.get("decision_at") or ""))
        except Exception:
            issues.append({"pair_set_id": pair_id, "code": "invalid_decision_timestamp", "critical": True})
            continue

        outcome = outcomes.get(str(pair_id)) or {}
        matured = outcome.get("status") == "matured" and outcome.get("calibration_eligible") is True
        outcome_valid = False
        closed_at: Optional[datetime] = None
        if matured:
            try:
                closed_at = parse_time(str(outcome.get("closed_at") or ""))
                target_at = parse_time(str(pair.get("target_at") or outcome.get("target_at") or ""))
                if closed_at < target_at or closed_at > as_of:
                    raise ValueError("outcome temporal boundary invalid")
                outcome_valid = True
            except Exception:
                issues.append({
                    "pair_set_id": pair_id,
                    "code": "invalid_matured_outcome_temporal_boundary",
                    "critical": True,
                })
                matured = False

        capture_items = [x for x in (capture.get("items") or []) if isinstance(x, Mapping)]
        for item in capture_items:
            instrument = str(item.get("instrument") or "").lower()
            engine = item.get("engine_information") or {}
            belief = item.get("belief_information") or {}
            topology = item.get("disagreement_topology") or {}
            world = item.get("world_context") or {}
            row: Dict[str, Any] = {
                "pair_set_id": str(pair_id),
                "decision_set_id": capture.get("decision_set_id"),
                "decision_at": iso_z(decision_at),
                "instrument": instrument,
                "engine_methodology_version": capture.get("engine_methodology_version"),
                "belief_state_key": _belief_state_key(belief),
                "belief_signature": _belief_signature(belief),
                "belief_signal": _float(belief.get("aggregate_confidence_weighted_signed_signal")),
                "belief_modifier_score_points": _float(belief.get("primary_modifier_score_points"), 0.0),
                "modifier_nonzero": bool(belief.get("modifier_nonzero")),
                "decision_changed": bool(belief.get("decision_changed")),
                "engine_features": _engine_scalar_features(engine),
                "engine_stance": topology.get("engine_stance"),
                "entity_stance": topology.get("entity_stance"),
                "market_stance": topology.get("market_stance"),
                "sector_stance": topology.get("sector_stance"),
                "factor_stance": topology.get("factor_stance"),
                "engine_entity_relation": topology.get("engine_entity_relation"),
                "top_down_state": topology.get("top_down_state"),
                "pattern_code": topology.get("pattern_code"),
                "regime_signature": _regime_signature(topology),
                "world_state_id": world.get("world_state_id") or capture.get("decision_world_state_id"),
                "matured": False,
                "economic_eligible": False,
                "without_contribution_return": None,
                "with_contribution_return": None,
                "delta_contribution_return": None,
                "without_turnover": None,
                "with_turnover": None,
                "delta_turnover": None,
                "without_cost_return": None,
                "with_cost_return": None,
                "delta_cost_return": None,
                "delta_pnl_pln": None,
                "outcome_closed_at": None,
            }
            if matured and outcome_valid:
                out_item, item_issue = _find_outcome_item(outcome, instrument, len(capture_items))
                if item_issue:
                    issues.append({
                        "pair_set_id": pair_id,
                        "instrument": instrument,
                        "code": item_issue,
                        "critical": True,
                    })
                elif out_item is not None:
                    without_ret = _float(out_item.get("without_contribution_return"))
                    with_ret = _float(out_item.get("with_contribution_return"))
                    if without_ret is None or with_ret is None:
                        issues.append({
                            "pair_set_id": pair_id,
                            "instrument": instrument,
                            "code": "matured_item_return_missing",
                            "critical": True,
                        })
                    else:
                        without_turnover = _float(out_item.get("without_turnover"), 0.0) or 0.0
                        with_turnover = _float(out_item.get("with_turnover"), 0.0) or 0.0
                        without_cost = _float(out_item.get("without_cost_return"), 0.0) or 0.0
                        with_cost = _float(out_item.get("with_cost_return"), 0.0) or 0.0
                        delta = with_ret - without_ret
                        notional = _float(pair.get("portfolio_notional_pln"))
                        row.update({
                            "matured": True,
                            "economic_eligible": True,
                            "without_contribution_return": without_ret,
                            "with_contribution_return": with_ret,
                            "delta_contribution_return": delta,
                            "without_turnover": without_turnover,
                            "with_turnover": with_turnover,
                            "delta_turnover": with_turnover - without_turnover,
                            "without_cost_return": without_cost,
                            "with_cost_return": with_cost,
                            "delta_cost_return": with_cost - without_cost,
                            "delta_pnl_pln": None if notional is None else notional * delta,
                            "outcome_closed_at": iso_z(closed_at) if closed_at else None,
                        })
            observations.append(row)
    return observations, issues


def _mean(values: Sequence[float]) -> Optional[float]:
    return statistics.fmean(values) if values else None


def _median(values: Sequence[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def _cvar(values: Sequence[float], fraction: float = 0.10) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(float(x) for x in values)
    count = max(1, int(math.ceil(len(ordered) * fraction)))
    return statistics.fmean(ordered[:count])


def _max_drawdown(returns: Sequence[float]) -> Optional[float]:
    if not returns:
        return None
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for ret in returns:
        equity *= 1.0 + float(ret)
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1.0)
    return max_dd


def _bootstrap_mean_ci(values: Sequence[float], *, seed_key: Any) -> Dict[str, Any]:
    vals = [float(x) for x in values]
    if len(vals) < 2:
        return {
            "available": False,
            "lower_95": None,
            "upper_95": None,
            "iterations": BOOTSTRAP_ITERATIONS,
            "method": "deterministic_nonparametric_percentile",
        }
    rng = random.Random(int(_sha(seed_key)[:16], 16))
    means: List[float] = []
    n = len(vals)
    for _ in range(BOOTSTRAP_ITERATIONS):
        sample = [vals[rng.randrange(n)] for _ in range(n)]
        means.append(statistics.fmean(sample))
    means.sort()
    lo_idx = max(0, int(math.floor(0.025 * (len(means) - 1))))
    hi_idx = min(len(means) - 1, int(math.ceil(0.975 * (len(means) - 1))))
    return {
        "available": True,
        "lower_95": means[lo_idx],
        "upper_95": means[hi_idx],
        "iterations": BOOTSTRAP_ITERATIONS,
        "method": "deterministic_nonparametric_percentile",
    }


def economic_incremental_value(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    matured = [
        row for row in rows
        if row.get("economic_eligible") is True and row.get("delta_contribution_return") is not None
    ]
    matured.sort(key=lambda r: (str(r.get("decision_at")), str(r.get("pair_set_id")), str(r.get("instrument"))))
    deltas = [float(row["delta_contribution_return"]) for row in matured]
    without = [float(row["without_contribution_return"]) for row in matured]
    with_side = [float(row["with_contribution_return"]) for row in matured]
    pnl = [float(row["delta_pnl_pln"]) for row in matured if row.get("delta_pnl_pln") is not None]
    cost_delta = [float(row["delta_cost_return"]) for row in matured if row.get("delta_cost_return") is not None]
    turnover_delta = [float(row["delta_turnover"]) for row in matured if row.get("delta_turnover") is not None]
    return {
        "matured_item_n": len(matured),
        "unique_pair_n": len({str(row.get("pair_set_id")) for row in matured}),
        "unique_instrument_n": len({str(row.get("instrument")) for row in matured}),
        "unique_belief_state_n": len({str(row.get("belief_state_key")) for row in matured}),
        "mean_without_contribution_return": _mean(without),
        "mean_with_contribution_return": _mean(with_side),
        "mean_delta_contribution_return": _mean(deltas),
        "median_delta_contribution_return": _median(deltas),
        "cumulative_event_delta_return": math.prod(1.0 + x for x in deltas) - 1.0 if deltas else None,
        "delta_event_max_drawdown": _max_drawdown(deltas),
        "positive_uplift_rate": sum(1 for x in deltas if x > 0) / len(deltas) if deltas else None,
        "negative_uplift_rate": sum(1 for x in deltas if x < 0) / len(deltas) if deltas else None,
        "zero_uplift_rate": sum(1 for x in deltas if abs(x) <= 1e-15) / len(deltas) if deltas else None,
        "mean_delta_turnover": _mean(turnover_delta),
        "mean_delta_cost_return": _mean(cost_delta),
        "delta_pnl_pln_sum": sum(pnl) if pnl else (0.0 if deltas else None),
        "worst_delta_event_return": min(deltas) if deltas else None,
        "empirical_delta_cvar_10pct": _cvar(deltas),
        "decision_change_rate": (
            sum(1 for row in matured if row.get("decision_changed")) / len(matured) if matured else None
        ),
        "nonzero_modifier_rate": (
            sum(1 for row in matured if row.get("modifier_nonzero")) / len(matured) if matured else None
        ),
        "mean_delta_bootstrap_ci_95": _bootstrap_mean_ci(
            deltas,
            seed_key=[
                (row.get("pair_set_id"), row.get("instrument"), row.get("delta_contribution_return"))
                for row in matured
            ],
        ),
        "interpretation": "DESCRIPTIVE_INCREMENTAL_ECONOMIC_VALUE_ONLY",
    }


def _ranks(values: Sequence[float]) -> List[float]:
    indexed = sorted(enumerate(float(x) for x in values), key=lambda x: x[1])
    ranks = [0.0] * len(indexed)
    i = 0
    while i < len(indexed):
        j = i + 1
        while j < len(indexed) and indexed[j][1] == indexed[i][1]:
            j += 1
        rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[indexed[k][0]] = rank
        i = j
    return ranks


def _pearson(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    if len(x) != len(y) or len(x) < 2:
        return None
    mx = statistics.fmean(x)
    my = statistics.fmean(y)
    dx = [float(v) - mx for v in x]
    dy = [float(v) - my for v in y]
    sx = math.sqrt(sum(v * v for v in dx))
    sy = math.sqrt(sum(v * v for v in dy))
    if sx <= 1e-15 or sy <= 1e-15:
        return None
    return max(-1.0, min(1.0, sum(a * b for a, b in zip(dx, dy)) / (sx * sy)))


def _spearman(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    return _pearson(_ranks(x), _ranks(y))


def _collapse_unique_belief_states(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    grouped: MutableMapping[str, List[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        signal = _float(row.get("belief_signal"))
        key = str(row.get("belief_state_key") or "")
        if key and signal is not None:
            grouped[key].append(row)
    collapsed: List[Dict[str, Any]] = []
    for key, items in sorted(grouped.items()):
        signals = [float(row["belief_signal"]) for row in items if _float(row.get("belief_signal")) is not None]
        if not signals:
            continue
        feature_values: MutableMapping[str, List[float]] = defaultdict(list)
        for row in items:
            for name, value in (row.get("engine_features") or {}).items():
                number = _float(value)
                if number is not None:
                    feature_values[str(name)].append(number)
        collapsed.append({
            "belief_state_key": key,
            "belief_signal": statistics.fmean(signals),
            "occurrences": len(items),
            "engine_features": {
                name: statistics.fmean(vals) for name, vals in feature_values.items() if vals
            },
        })
    return collapsed


def redundancy_orthogonality(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    collapsed = _collapse_unique_belief_states(rows)
    unique_n = len(collapsed)
    base = {
        "method": "max_absolute_spearman_across_frozen_engine_feature_screen",
        "feature_screen": [f"{block}.{field}" for block, field in REDUNDANCY_FEATURES],
        "pnl_tuned": False,
        "uses_economic_outcome": False,
        "equal_weight_unique_belief_states": True,
        "raw_observation_n": len(rows),
        "unique_belief_state_n": unique_n,
        "engineering_min_unique_states": MIN_UNIQUE_BELIEF_STATES_FOR_DEPENDENCE,
        "engineering_min_is_promotion_threshold": False,
        "redundancy_proxy": None,
        "orthogonality_proxy": None,
        "max_correlated_feature": None,
        "feature_correlations": [],
    }
    if unique_n < MIN_UNIQUE_BELIEF_STATES_FOR_DEPENDENCE:
        return {**base, "status": "NOT_YET_ESTIMABLE", "reason": "insufficient_unique_belief_states"}
    signals = [float(row["belief_signal"]) for row in collapsed]
    if max(signals) - min(signals) <= 1e-15:
        return {**base, "status": "NOT_YET_ESTIMABLE", "reason": "constant_belief_signal_across_unique_states"}

    correlations = []
    for block, field in REDUNDANCY_FEATURES:
        name = f"{block}.{field}"
        xs: List[float] = []
        ys: List[float] = []
        for row in collapsed:
            value = _float((row.get("engine_features") or {}).get(name))
            signal = _float(row.get("belief_signal"))
            if value is not None and signal is not None:
                xs.append(signal)
                ys.append(value)
        rho = _spearman(xs, ys) if len(xs) >= MIN_UNIQUE_BELIEF_STATES_FOR_DEPENDENCE else None
        correlations.append({
            "feature": name,
            "n": len(xs),
            "spearman_rho": rho,
            "absolute_rho": None if rho is None else abs(rho),
        })
    estimable = [row for row in correlations if row["absolute_rho"] is not None]
    if not estimable:
        return {
            **base,
            "status": "NOT_YET_ESTIMABLE",
            "reason": "no_variable_engine_feature_with_sufficient_unique_states",
            "feature_correlations": correlations,
        }
    best = max(estimable, key=lambda row: (float(row["absolute_rho"]), str(row["feature"])))
    redundancy = float(best["absolute_rho"])
    return {
        **base,
        "status": "DESCRIPTIVE_PROXY_AVAILABLE",
        "reason": None,
        "redundancy_proxy": redundancy,
        "orthogonality_proxy": 1.0 - redundancy,
        "max_correlated_feature": best["feature"],
        "max_signed_spearman_rho": best["spearman_rho"],
        "feature_correlations": correlations,
        "warning": "Correlation is a dependence screen, not proof of informational redundancy or causal orthogonality.",
    }


def _serial_effective_n(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    matured = [
        row for row in rows
        if row.get("economic_eligible") is True and row.get("delta_contribution_return") is not None
    ]
    matured.sort(key=lambda r: (str(r.get("decision_at")), str(r.get("pair_set_id")), str(r.get("instrument"))))
    values = [float(row["delta_contribution_return"]) for row in matured]
    n = len(values)
    if n < MIN_SERIAL_N:
        return {
            "raw_n": n,
            "lag1_rho": None,
            "serial_effective_n": None,
            "status": "NOT_ESTIMABLE",
            "engineering_min_n": MIN_SERIAL_N,
            "engineering_min_is_promotion_threshold": False,
        }
    rho = _pearson(values[:-1], values[1:])
    if rho is None:
        return {
            "raw_n": n,
            "lag1_rho": None,
            "serial_effective_n": None,
            "status": "NOT_ESTIMABLE_CONSTANT_SERIES",
            "engineering_min_n": MIN_SERIAL_N,
            "engineering_min_is_promotion_threshold": False,
        }
    clipped = max(-0.8, min(0.8, rho))
    eff = n * (1.0 - clipped) / (1.0 + clipped)
    eff = max(1.0, min(float(n), eff))
    return {
        "raw_n": n,
        "lag1_rho": rho,
        "lag1_rho_clipped": clipped,
        "serial_effective_n": eff,
        "status": "DESCRIPTIVE_ESTIMABLE",
        "engineering_min_n": MIN_SERIAL_N,
        "engineering_min_is_promotion_threshold": False,
    }


def dependence_diagnostics(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    matured = [row for row in rows if row.get("economic_eligible") is True]
    raw_n = len(matured)
    pair_n = len({str(row.get("pair_set_id")) for row in matured})
    belief_state_n = len({str(row.get("belief_state_key")) for row in matured})
    serial = _serial_effective_n(rows)
    caps = [float(raw_n), float(pair_n), float(belief_state_n)] if raw_n else []
    if serial.get("serial_effective_n") is not None:
        caps.append(float(serial["serial_effective_n"]))
    floor = min(caps) if caps else None
    return {
        "raw_matured_item_n": raw_n,
        "unique_pair_n": pair_n,
        "unique_belief_state_n": belief_state_n,
        "serial": serial,
        "descriptive_effective_n_floor": floor,
        "floor_components": [
            "raw_matured_item_n",
            "unique_pair_n",
            "unique_belief_state_n",
            *(["serial_effective_n"] if serial.get("serial_effective_n") is not None else []),
        ],
        "promotion_grade_effective_n": None,
        "effective_n_threshold_defined_here": False,
        "interpretation": "DESCRIPTIVE_DEPENDENCE_CONTROL_ONLY",
    }


def _concentration(rows: Sequence[Mapping[str, Any]], field: str) -> Dict[str, Any]:
    matured = [row for row in rows if row.get("economic_eligible") is True]
    counts = Counter(str(row.get(field) or "UNAVAILABLE") for row in matured)
    total = sum(counts.values())
    if not total:
        return {
            "field": field,
            "n": 0,
            "max_share": None,
            "hhi": None,
            "effective_category_count": None,
            "top_categories": [],
        }
    shares = {key: count / total for key, count in counts.items()}
    hhi = sum(share * share for share in shares.values())
    return {
        "field": field,
        "n": total,
        "max_share": max(shares.values()),
        "hhi": hhi,
        "effective_category_count": 1.0 / hhi if hhi > 0 else None,
        "top_categories": [
            {"category": key, "count": counts[key], "share": shares[key]}
            for key in sorted(counts, key=lambda k: (-counts[k], k))[:10]
        ],
    }


def concentration_diagnostics(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    return {
        "instrument": _concentration(rows, "instrument"),
        "belief_signature": _concentration(rows, "belief_signature"),
        "belief_state_key": _concentration(rows, "belief_state_key"),
        "pattern_code": _concentration(rows, "pattern_code"),
        "world_state_id": _concentration(rows, "world_state_id"),
        "promotion_cutoff_defined_here": False,
    }


def _slice(rows: Sequence[Mapping[str, Any]], field: str) -> List[Dict[str, Any]]:
    groups: MutableMapping[str, List[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(field) or "UNAVAILABLE")].append(row)
    result: List[Dict[str, Any]] = []
    for key, items in sorted(groups.items()):
        matured = [
            row for row in items
            if row.get("economic_eligible") is True and row.get("delta_contribution_return") is not None
        ]
        deltas = [float(row["delta_contribution_return"]) for row in matured]
        result.append({
            field: key,
            "captured_n": len(items),
            "matured_n": len(matured),
            "unique_pair_n": len({str(row.get("pair_set_id")) for row in matured}),
            "unique_belief_state_n": len({str(row.get("belief_state_key")) for row in matured}),
            "mean_delta_contribution_return": _mean(deltas),
            "median_delta_contribution_return": _median(deltas),
            "positive_uplift_rate": sum(1 for x in deltas if x > 0) / len(deltas) if deltas else None,
            "decision_change_rate": (
                sum(1 for row in matured if row.get("decision_changed")) / len(matured) if matured else None
            ),
            "mean_delta_bootstrap_ci_95": _bootstrap_mean_ci(
                deltas,
                seed_key=(field, key, [(row.get("pair_set_id"), row.get("instrument")) for row in matured]),
            ),
            "interpretation": "DESCRIPTIVE_SLICE_ONLY",
        })
    return result


def disagreement_regime_diagnostics(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    cross_groups: MutableMapping[Tuple[str, str], List[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        cross_groups[(
            str(row.get("engine_entity_relation") or "UNAVAILABLE"),
            str(row.get("regime_signature") or "UNAVAILABLE"),
        )].append(row)
    cross = []
    for (relation, regime), items in sorted(cross_groups.items()):
        matured = [
            row for row in items
            if row.get("economic_eligible") is True and row.get("delta_contribution_return") is not None
        ]
        deltas = [float(row["delta_contribution_return"]) for row in matured]
        cross.append({
            "engine_entity_relation": relation,
            "regime_signature": regime,
            "captured_n": len(items),
            "matured_n": len(matured),
            "unique_belief_state_n": len({str(row.get("belief_state_key")) for row in matured}),
            "mean_delta_contribution_return": _mean(deltas),
            "positive_uplift_rate": sum(1 for x in deltas if x > 0) / len(deltas) if deltas else None,
            "interpretation": "DESCRIPTIVE_DISAGREEMENT_X_REGIME_ONLY",
        })
    return {
        "by_pattern": _slice(rows, "pattern_code"),
        "by_engine_entity_relation": _slice(rows, "engine_entity_relation"),
        "by_top_down_state": _slice(rows, "top_down_state"),
        "by_regime_signature": _slice(rows, "regime_signature"),
        "by_belief_signature": _slice(rows, "belief_signature"),
        "by_instrument": _slice(rows, "instrument"),
        "engine_entity_x_regime": cross,
        "alpha_threshold_defined_here": False,
    }


def source_fingerprint(pr18_state: Mapping[str, Any], pr17_state: Mapping[str, Any]) -> str:
    return _sha({
        "pr18_contract": pr18_state.get("contract_version"),
        "captures": pr18_state.get("captures") or {},
        "terminal_uncaptured": pr18_state.get("terminal_uncaptured") or {},
        "pr17_contract": pr17_state.get("contract_version"),
        "pair_sets": pr17_state.get("pair_sets") or {},
        "economic_outcomes": pr17_state.get("economic_outcomes") or {},
    })


def build_report(
    *,
    pr18_state: Mapping[str, Any],
    pr18_report: Mapping[str, Any],
    pr17_state: Mapping[str, Any],
    pr17_report: Mapping[str, Any],
    as_of: datetime,
) -> Dict[str, Any]:
    observations, issues = build_observations(pr18_state, pr17_state, as_of=as_of)
    economic = economic_incremental_value(observations)
    redundancy = redundancy_orthogonality(observations)
    dependence = dependence_diagnostics(observations)
    concentration = concentration_diagnostics(observations)
    disagreement = disagreement_regime_diagnostics(observations)
    matured_n = int(economic["matured_item_n"])
    status = "COLLECTING_NO_MATURED_DATA" if matured_n == 0 else "DESCRIPTIVE_DIAGNOSTICS_AVAILABLE"
    critical = sum(1 for row in issues if row.get("critical") is True)
    return {
        "schema_version": SCHEMA_VERSION,
        "report_version": REPORT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "mode": MODE,
        "generated_at": iso_z(as_of),
        "purpose": "Measure whether prospective Entity Belief information adds economic value beyond the frozen BRACE information set.",
        "active_decision_influence": False,
        "miv": {
            "status": status,
            "composite_miv_score": None,
            "composite_miv_score_contract_exists": False,
            "edge_claim_allowed": False,
            "economic_incremental_value": economic,
            "information_redundancy": redundancy,
            "information_orthogonality": {
                "status": redundancy.get("status"),
                "orthogonality_proxy": redundancy.get("orthogonality_proxy"),
                "method": "1 - max_absolute_spearman_redundancy_proxy",
                "warning": "Proxy only; not proof of causal or conditional independence.",
            },
            "dependence": dependence,
            "concentration": concentration,
            "disagreement_x_regime": disagreement,
        },
        "sample": {
            "pr18_captures_total": len(pr18_state.get("captures") or {}),
            "pr18_terminal_uncaptured_total": len(pr18_state.get("terminal_uncaptured") or {}),
            "observation_rows_total": len(observations),
            "matured_economic_rows": matured_n,
            "pending_rows": sum(1 for row in observations if row.get("economic_eligible") is not True),
            "unique_belief_states_all_captures": len({str(row.get("belief_state_key")) for row in observations}),
            "critical_data_quality_issues": critical,
            "data_quality_issues_total": len(issues),
        },
        "data_quality_issues": issues,
        "methodology": {
            "unit_of_economic_analysis": "instrument contribution inside a prospectively captured PR17 pair",
            "multi_item_pair_delta_not_duplicated": True,
            "redundancy_uses_outcomes": False,
            "redundancy_equal_weights_unique_belief_states": True,
            "redundancy_engineering_min_unique_states": MIN_UNIQUE_BELIEF_STATES_FOR_DEPENDENCE,
            "redundancy_engineering_min_is_promotion_threshold": False,
            "serial_engineering_min_n": MIN_SERIAL_N,
            "serial_engineering_min_is_promotion_threshold": False,
            "bootstrap_iterations": BOOTSTRAP_ITERATIONS,
            "effective_n_threshold_defined_here": False,
            "promotion_grade_effective_n_defined_here": False,
        },
        "anti_hindsight": {
            "only_pr18_prospectively_frozen_captures_are_consumed": True,
            "historical_information_backfill": False,
            "retroactive_source_reconstruction": False,
            "pr18_existing_prospective_captures_may_bootstrap_diagnostics": True,
            "bootstrap_is_not_historical_forecast_backfill": True,
            "outcomes_must_be_matured_and_calibration_eligible": True,
            "outcome_must_close_at_or_after_frozen_target": True,
            "future_outcomes_are_rejected": True,
        },
        "research_boundary": {
            "correct_belief_is_not_assumed_useful": True,
            "economic_uplift_is_not_assumed_orthogonal": True,
            "redundancy_proxy_is_not_causal_proof": True,
            "disagreement_slice_is_not_alpha": True,
            "composite_miv_score_enabled": False,
            "engine_specific_trust_enabled": False,
            "causal_belief_graph_enabled": False,
        },
        "promotion": {
            "status": "NOT_ELIGIBLE_FOR_PROMOTION_REVIEW",
            "eligible_for_promotion_review": False,
            "automatic_promotion": False,
            "effective_n_threshold_defined_here": False,
            "requires_future_promotion_gate": True,
        },
        "source_contracts": {"pr18": PR18_CONTRACT_VERSION, "pr17": PR17_CONTRACT_VERSION},
        "source_economics": deepcopy(pr17_report.get("with_without_economics")),
        "capabilities": capabilities(),
        "safety_controls": safety_controls(),
    }


def run(
    state_dir: Path,
    *,
    pr18_state_path: Path,
    pr18_report_path: Path,
    pr17_state_path: Path,
    pr17_report_path: Path,
    as_of: Optional[datetime] = None,
) -> Dict[str, Any]:
    _assert_safety()
    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    state_dir = Path(state_dir)
    state_path = state_dir / STATE_FILENAME
    report_path = state_dir / REPORT_FILENAME

    pr18_state = _read_json(pr18_state_path, {})
    pr18_report = _read_json(pr18_report_path, {})
    pr17_state = _read_json(pr17_state_path, {})
    pr17_report = _read_json(pr17_report_path, {})
    _validate_inputs(pr18_state, pr18_report, pr17_state, pr17_report)

    previous = _read_json(state_path, empty_state())
    state = deepcopy(previous)
    if str(state.get("schema_version") or "") != SCHEMA_VERSION:
        raise ValueError("PR18.1 state schema mismatch")
    if str(state.get("contract_version") or "") != CONTRACT_VERSION:
        raise ValueError("PR18.1 state contract mismatch")
    if not state.get("first_run_at"):
        state["first_run_at"] = iso_z(now)

    fingerprint = source_fingerprint(pr18_state, pr17_state)
    seen = set(str(x) for x in state.get("seen_source_fingerprints") or [])
    report = build_report(
        pr18_state=pr18_state,
        pr18_report=pr18_report,
        pr17_state=pr17_state,
        pr17_report=pr17_report,
        as_of=now,
    )

    new_snapshot = fingerprint not in seen
    if new_snapshot:
        snapshot = {
            "snapshot_id": _stable_id("miv-diagnostic", {
                "source_fingerprint": fingerprint,
                "contract_version": CONTRACT_VERSION,
            }),
            "source_fingerprint": fingerprint,
            "created_at": iso_z(now),
            "pr18_captures_total": report["sample"]["pr18_captures_total"],
            "observation_rows_total": report["sample"]["observation_rows_total"],
            "matured_economic_rows": report["sample"]["matured_economic_rows"],
            "critical_data_quality_issues": report["sample"]["critical_data_quality_issues"],
            "miv_status": report["miv"]["status"],
            "redundancy_status": report["miv"]["information_redundancy"]["status"],
            "composite_miv_score": None,
            "historical_backfill": False,
            "promotion_authority": False,
        }
        snapshot["immutable_sha256"] = _sha(snapshot)
        state.setdefault("diagnostic_snapshots", {})[fingerprint] = snapshot
        seen.add(fingerprint)

    state["seen_source_fingerprints"] = sorted(seen)
    state["last_run_at"] = iso_z(now)
    _assert_append_only(previous, state)
    _write_json(state_path, state)

    report["runtime"] = {
        "source_fingerprint": fingerprint,
        "new_diagnostic_snapshot_this_run": new_snapshot,
        "diagnostic_snapshots_total": len(state.get("diagnostic_snapshots") or {}),
    }
    _write_json(report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="BRACE Marginal Information Value Diagnostics")
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--pr18-state", required=True, type=Path)
    parser.add_argument("--pr18-report", required=True, type=Path)
    parser.add_argument("--pr17-state", required=True, type=Path)
    parser.add_argument("--pr17-report", required=True, type=Path)
    args = parser.parse_args()
    report = run(
        args.state_dir,
        pr18_state_path=args.pr18_state,
        pr18_report_path=args.pr18_report,
        pr17_state_path=args.pr17_state,
        pr17_report_path=args.pr17_report,
    )
    print(json.dumps({
        "status": report["promotion"]["status"],
        "miv_status": report["miv"]["status"],
        "matured_rows": report["sample"]["matured_economic_rows"],
        "redundancy_status": report["miv"]["information_redundancy"]["status"],
        "composite_miv_score": report["miv"]["composite_miv_score"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
