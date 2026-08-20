#!/usr/bin/env python3
"""PR #17 — BRACE ↔ Entity Belief Prospective Shadow Bridge.

Research-shadow bridge only. It freezes prospective paired WITHOUT/WITH BELIEF
records for existing BRACE portfolio-position recommendations using only PR15
Entity forecasts that already have valid PR16.1 World State bindings.

Hard boundaries:
- first run is activation-only for the currently visible BRACE recommendation set;
- no historical decision backfill;
- no candidate ranking change, optimizer rewrite, sizing invention, veto, forced
  exit, execution, policy change, automatic tuning or promotion authority;
- primary hypothetical Belief modifier is frozen ex ante at ±2 score points;
- WITH reuses the same local BRACE position thresholds and only existing
  current/proposed/target weights; it never creates a new sizing instruction;
- EXIT cannot be created or cancelled by Belief (no forced exit / no veto);
- paired economics use the same instrument, entry price, evaluation price,
  horizon and cost assumption for WITHOUT and WITH;
- reports always expose WITH/WITHOUT economics, even while observations are
  pending and metrics are null.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

MODE = "research_shadow"
SCHEMA_VERSION = "brace-entity-belief-shadow-bridge-v1"
REPORT_VERSION = "brace-entity-belief-shadow-bridge-report-v1"
CONTRACT_VERSION = "brace-entity-belief-shadow-bridge-contract-v1"
PRIMARY_MODIFIER_CONTRACT_VERSION = "entity-belief-score-modifier-v1"
ECONOMIC_CONTRACT_VERSION = "entity-belief-paired-economics-v1"

PR15_CONTRACT_VERSION = "entity-belief-forecast-contract-v1"
PR16_1_CONTRACT_VERSION = "investment-semantics-world-state-contract-v1"
FORECAST_BINDING_CONTRACT_VERSION = "entity-forecast-world-state-binding-v1"
WORLD_STATE_CONTRACT_VERSION = "investment-world-state-v1"

STATE_FILENAME = "BRACE_ENTITY_BELIEF_SHADOW_BRIDGE_STATE.json"
REPORT_FILENAME = "BRACE_ENTITY_BELIEF_WITH_WITHOUT_REPORT.json"

PRIMARY_MODIFIER_SCORE_POINTS = 2.0
SENSITIVITY_CEILINGS = (1.0, 2.0, 3.0)
ECONOMIC_HORIZON_DAYS = 7
MAX_EVALUATION_LAG_DAYS = 14
TRANSACTION_COST_BPS = 5.0
TRANSACTION_COST_RATE = TRANSACTION_COST_BPS / 10_000.0


def safety_controls() -> Dict[str, bool]:
    return {
        "active_decision_influence": False,
        "engine_score_writeback": False,
        "candidate_ranking_change": False,
        "optimizer_change": False,
        "target_exposure_writeback": False,
        "sizing_instruction": False,
        "veto": False,
        "forced_exit": False,
        "direction_reversal": False,
        "trade_execution": False,
        "policy_output": False,
        "automatic_tuning": False,
        "historical_decision_backfill": False,
        "historical_belief_backfill": False,
        "retroactive_world_state_binding": False,
        "automatic_promotion": False,
    }


def capabilities() -> Dict[str, bool]:
    return {
        "brace_entity_shadow_bridge_enabled": True,
        "prospective_paired_without_with_capture_enabled": True,
        "primary_bounded_modifier_computed_enabled": True,
        "primary_modifier_consumed_by_brace_enabled": False,
        "paired_economic_outcome_resolution_enabled": True,
        "with_without_report_enabled": True,
        "marginal_information_value_telemetry_seed_enabled": True,
        "candidate_ranking_override_enabled": False,
        "engine_sizing_override_enabled": False,
        "promotion_gate_enabled": False,
    }


def promotion_evidence_standard() -> Dict[str, Any]:
    return {
        "with_without_required": True,
        "paired_prospective_counterfactual_required": True,
        "primary_modifier_frozen_ex_ante": True,
        "primary_modifier_score_points": PRIMARY_MODIFIER_SCORE_POINTS,
        "sensitivity_only_score_point_ceilings": list(SENSITIVITY_CEILINGS),
        "effective_n_required": True,
        "effective_n_threshold_defined_here": False,
        "stable_uplift_required": True,
        "paired_uplift_uncertainty_required": True,
        "multi_regime_robustness_required": True,
        "concentration_check_required": True,
        "drawdown_not_materially_worse_required": True,
        "tail_risk_not_materially_worse_required": True,
        "belief_calibration_required": True,
        "drift_check_required": True,
        "data_quality_and_provenance_required": True,
        "anti_hindsight_required": True,
        "shadow_runtime_stability_required": True,
        "automatic_promotion": False,
        "review_output_only": "ELIGIBLE_FOR_PROMOTION_REVIEW",
    }


def _assert_safety() -> None:
    bad = [key for key, value in safety_controls().items() if value is not False]
    if bad:
        raise RuntimeError("PR17 shadow-only invariant violated: " + ",".join(bad))


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


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _date(value: Any) -> Optional[date]:
    if value in {None, ""}:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def empty_state() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "contract_version": CONTRACT_VERSION,
        "activated_at": None,
        "last_run_at": None,
        "seen_decision_set_ids": [],
        "pre_activation_decision_set_ids": [],
        "pair_sets": {},
        "terminal_unpaired_sets": {},
        "economic_outcomes": {},
    }


def _validate_pr15(core: Mapping[str, Any], runtime: Mapping[str, Any]) -> None:
    if not core:
        raise ValueError("PR17 requires PR15 BeliefCore state")
    if str(runtime.get("contract_version") or "") != PR15_CONTRACT_VERSION:
        raise ValueError("PR17 requires reviewed PR15 runtime contract")


def _validate_world_state(state: Mapping[str, Any], report: Mapping[str, Any]) -> None:
    if str(state.get("contract_version") or "") != PR16_1_CONTRACT_VERSION:
        raise ValueError("PR17 requires reviewed PR16.1 state contract")
    if str(report.get("contract_version") or "") != PR16_1_CONTRACT_VERSION:
        raise ValueError("PR17 requires reviewed PR16.1 report contract")
    if report.get("active_decision_influence") is not False:
        raise ValueError("PR17 refuses PR16.1 input with active decision influence")


def _analysis_positions(analysis: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(row.get("instrument_id") or row.get("id") or "").lower(): dict(row)
        for row in (analysis.get("positions") or [])
        if isinstance(row, Mapping) and (row.get("instrument_id") or row.get("id"))
    }


def _recommendations(pending: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(row.get("instrument") or "").lower(): dict(row)
        for row in (pending.get("recommendations") or [])
        if isinstance(row, Mapping) and row.get("instrument")
    }


def _decision_snapshot(
    pending: Mapping[str, Any], analysis: Mapping[str, Any], portfolio: Mapping[str, Any]
) -> Dict[str, Any]:
    positions = _analysis_positions(analysis)
    recommendations = _recommendations(pending)
    rows: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []
    for instrument in sorted(set(positions) & set(recommendations)):
        pos = positions[instrument]
        rec = recommendations[instrument]
        final_score = _float(pos.get("final_score"), float("nan"))
        rec_score = _float(rec.get("final_score"), float("nan"))
        signal_price = _float(rec.get("signal_price"), float("nan"))
        current_price = _float(pos.get("current_price"), float("nan"))
        if not math.isfinite(final_score) or not math.isfinite(rec_score) or abs(final_score - rec_score) > 1e-6:
            issues.append({"code": "recommendation_analysis_score_mismatch", "instrument": instrument})
            continue
        if not math.isfinite(signal_price) or not math.isfinite(current_price) or signal_price <= 0 or abs(signal_price - current_price) > max(1e-6, abs(current_price) * 1e-6):
            issues.append({"code": "recommendation_analysis_price_mismatch", "instrument": instrument})
            continue
        rows.append({
            "instrument": instrument,
            "broker_symbol": rec.get("broker_symbol") or pos.get("broker_symbol"),
            "source_action": str(rec.get("action") or "").upper(),
            "final_score": final_score,
            "risk_score": _float(pos.get("risk_score"), 0.0),
            "data_quality_confidence": _float(pos.get("confidence_score"), _float(rec.get("confidence"), 0.0)),
            "current_weight": _float(rec.get("current_weight"), _float(pos.get("current_weight"), 0.0)),
            "proposed_weight": _float(rec.get("proposed_weight"), _float(pos.get("current_weight"), 0.0)),
            "target_weight": _float(pos.get("target_weight"), _float(rec.get("current_weight"), 0.0)),
            "signal_price": signal_price,
            "signal_fx_to_pln": rec.get("signal_fx_to_pln") or pos.get("current_fx_to_pln"),
        })
    decision_at = parse_time(str(analysis.get("generated_at") or pending.get("generated_at") or ""))
    payload = {
        "engine_methodology_version": analysis.get("methodology_version") or pending.get("methodology_version"),
        "analysis_generated_at": iso_z(decision_at),
        "rows": rows,
    }
    return {
        "decision_set_id": _stable_id("brace-entity-decision-set", payload),
        "decision_at": iso_z(decision_at),
        "engine_methodology_version": str(analysis.get("methodology_version") or pending.get("methodology_version") or "unknown"),
        "safe_mode": bool(pending.get("safe_mode")),
        "portfolio_notional_pln": _float(portfolio.get("total_value_pln"), 0.0),
        "rows": rows,
        "issues": issues,
        "source_sha256": {
            "pending_decisions": _sha(pending),
            "analysis": _sha(analysis),
            "portfolio": _sha(portfolio),
        },
    }


def _belief_parts(belief_id: str) -> Tuple[str, str]:
    text = str(belief_id or "")
    if not text.startswith("entity."):
        return "", ""
    body = text[len("entity."):]
    if "." not in body:
        return "", ""
    entity, dimension = body.rsplit(".", 1)
    return entity.lower(), dimension


def _world_snapshots(world_state: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    return {
        str(row.get("world_state_id")): row
        for row in (world_state.get("snapshots") or [])
        if isinstance(row, Mapping) and row.get("world_state_id")
    }


def _eligible_decision_world_state(world_state: Mapping[str, Any], decision_at: datetime) -> Optional[Mapping[str, Any]]:
    eligible: List[Mapping[str, Any]] = []
    for row in world_state.get("snapshots") or []:
        if not isinstance(row, Mapping):
            continue
        try:
            created = parse_time(str(row.get("created_at") or ""))
            cutoff = parse_time(str(row.get("source_cutoff_at") or row.get("context_as_of") or ""))
        except Exception:
            continue
        if created <= decision_at and cutoff <= decision_at:
            eligible.append(row)
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda row: (
            parse_time(str(row.get("source_cutoff_at") or row.get("context_as_of"))),
            parse_time(str(row.get("created_at"))),
            str(row.get("world_state_id")),
        ),
    )


def _eligible_entity_forecasts(
    core: Mapping[str, Any],
    world_state: Mapping[str, Any],
    *,
    entity: str,
    decision_at: datetime,
) -> List[Dict[str, Any]]:
    bindings = world_state.get("forecast_context_bindings") or {}
    snapshots = _world_snapshots(world_state)
    rows: List[Dict[str, Any]] = []
    seen_dimensions: set[str] = set()
    candidates: List[Dict[str, Any]] = []
    for raw in core.get("forecasts") or []:
        if not isinstance(raw, Mapping):
            continue
        belief_id = str(raw.get("belief_id") or "")
        forecast_entity, dimension = _belief_parts(belief_id)
        if forecast_entity != entity.lower() or not dimension:
            continue
        forecast_id = str(raw.get("forecast_id") or "")
        binding = bindings.get(forecast_id)
        if not isinstance(binding, Mapping):
            continue
        if str(binding.get("contract_version") or "") != FORECAST_BINDING_CONTRACT_VERSION:
            continue
        if binding.get("prospective") is not True or binding.get("retroactive") is not False:
            continue
        try:
            forecast_at = parse_time(str(raw.get("forecast_at") or ""))
            target_at = parse_time(str(raw.get("target_at") or ""))
        except Exception:
            continue
        if not (forecast_at <= decision_at < target_at):
            continue
        world_id = str(binding.get("world_state_id") or "")
        snap = snapshots.get(world_id)
        if not snap:
            continue
        try:
            if parse_time(str(snap.get("created_at"))) > forecast_at:
                continue
            if parse_time(str(snap.get("source_cutoff_at") or snap.get("context_as_of"))) > forecast_at:
                continue
        except Exception:
            continue
        candidates.append({
            "forecast_id": forecast_id,
            "belief_id": belief_id,
            "dimension": dimension,
            "predicted_probability": _clip(_float(raw.get("predicted_probability"), 0.5), 0.0, 1.0),
            "forecast_confidence": _clip(_float(raw.get("forecast_confidence"), 0.0), 0.0, 1.0),
            "forecast_at": iso_z(forecast_at),
            "target_at": iso_z(target_at),
            "forecast_world_state_id": world_id,
            "binding_id": binding.get("binding_id"),
            "forecast_contract_version": (raw.get("metadata") or {}).get("contract_version"),
        })
    candidates.sort(key=lambda row: (row["dimension"], row["forecast_at"], row["forecast_id"]), reverse=True)
    for row in candidates:
        if row["dimension"] in seen_dimensions:
            continue
        seen_dimensions.add(row["dimension"])
        rows.append(row)
    rows.sort(key=lambda row: (row["dimension"], row["forecast_id"]))
    return rows


def _aggregate_belief_signal(forecasts: Sequence[Mapping[str, Any]]) -> float:
    if not forecasts:
        return 0.0
    contributions = []
    for row in forecasts:
        p = _clip(_float(row.get("predicted_probability"), 0.5), 0.0, 1.0)
        confidence = _clip(_float(row.get("forecast_confidence"), 0.0), 0.0, 1.0)
        contributions.append((2.0 * p - 1.0) * confidence)
    return _clip(sum(contributions) / len(contributions), -1.0, 1.0)


def modifier_for_forecasts(forecasts: Sequence[Mapping[str, Any]], ceiling: float = PRIMARY_MODIFIER_SCORE_POINTS) -> float:
    return round(_clip(float(ceiling) * _aggregate_belief_signal(forecasts), -float(ceiling), float(ceiling)), 6)


def local_brace_action(
    *,
    score: float,
    risk_score: float,
    data_quality_confidence: float,
    current_weight: float,
    target_weight: float,
    safe_mode: bool,
) -> str:
    """Frozen local BRACE position-threshold parity contract for PR17 v1."""
    if safe_mode:
        return "WATCH"
    if data_quality_confidence < 0.5:
        return "WATCH"
    if risk_score < 25.0 or score < 30.0:
        return "EXIT"
    if score < 43.0:
        return "REDUCE"
    if score < 56.0:
        return "WATCH"
    if score >= 74.0 and current_weight + 0.015 < target_weight:
        return "ADD"
    return "HOLD"


def governed_with_action(with_action_raw: str, without_action: str) -> Tuple[str, Optional[str]]:
    """Belief cannot create or cancel EXIT; this prevents forced exit/veto."""
    if without_action == "EXIT":
        return "EXIT", "belief_cannot_veto_existing_exit"
    if with_action_raw == "EXIT":
        return "REDUCE", "belief_cannot_force_exit"
    return with_action_raw, None


def _target_weight(action: str, current_weight: float, proposed_weight: float) -> float:
    current = max(0.0, current_weight)
    proposed = max(0.0, proposed_weight)
    action = str(action or "").upper()
    if action == "EXIT":
        return 0.0
    if action == "REDUCE":
        return min(current, proposed)
    if action == "ADD":
        return max(current, proposed)
    return current


def _pair_set(
    snapshot: Mapping[str, Any],
    core: Mapping[str, Any],
    world_state: Mapping[str, Any],
    *,
    now: datetime,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    decision_at = parse_time(str(snapshot["decision_at"]))
    if snapshot.get("safe_mode"):
        return None, {
            "decision_set_id": snapshot["decision_set_id"],
            "status": "engine_safe_mode",
            "recorded_at": iso_z(now),
            "terminal_no_backfill": True,
        }
    if snapshot.get("issues"):
        return None, {
            "decision_set_id": snapshot["decision_set_id"],
            "status": "source_parity_failure",
            "issues": list(snapshot.get("issues") or []),
            "recorded_at": iso_z(now),
            "terminal_no_backfill": True,
        }
    decision_world = _eligible_decision_world_state(world_state, decision_at)
    if decision_world is None:
        return None, {
            "decision_set_id": snapshot["decision_set_id"],
            "status": "no_pre_decision_world_state",
            "recorded_at": iso_z(now),
            "terminal_no_backfill": True,
        }

    items: List[Dict[str, Any]] = []
    for row in snapshot.get("rows") or []:
        instrument = str(row.get("instrument") or "").lower()
        forecasts = _eligible_entity_forecasts(core, world_state, entity=instrument, decision_at=decision_at)
        if not forecasts:
            continue
        without_action = local_brace_action(
            score=_float(row.get("final_score")),
            risk_score=_float(row.get("risk_score")),
            data_quality_confidence=_float(row.get("data_quality_confidence")),
            current_weight=_float(row.get("current_weight")),
            target_weight=_float(row.get("target_weight")),
            safe_mode=False,
        )
        source_action = str(row.get("source_action") or "").upper()
        if source_action != without_action:
            # Material overlays can legitimately change the published action, but
            # PR17 v1 is defined against the deterministic local BRACE threshold
            # contract. Mismatches fail closed instead of silently changing the
            # counterfactual definition.
            continue
        modifier = modifier_for_forecasts(forecasts)
        with_score = _clip(_float(row.get("final_score")) + modifier, 0.0, 100.0)
        with_action_raw = local_brace_action(
            score=with_score,
            risk_score=_float(row.get("risk_score")),
            data_quality_confidence=_float(row.get("data_quality_confidence")),
            current_weight=_float(row.get("current_weight")),
            target_weight=_float(row.get("target_weight")),
            safe_mode=False,
        )
        with_action, governance_clamp = governed_with_action(with_action_raw, without_action)
        current_weight = _float(row.get("current_weight"))
        proposed_weight = _float(row.get("proposed_weight"), current_weight)
        without_weight = _target_weight(without_action, current_weight, proposed_weight)
        with_weight = _target_weight(with_action, current_weight, proposed_weight)
        items.append({
            "instrument": instrument,
            "broker_symbol": row.get("broker_symbol"),
            "signal_price": _float(row.get("signal_price")),
            "signal_fx_to_pln": row.get("signal_fx_to_pln"),
            "original_score": _float(row.get("final_score")),
            "belief_modifier_score_points": modifier,
            "with_score": with_score,
            "source_action": source_action,
            "without_action": without_action,
            "with_action_raw": with_action_raw,
            "with_action": with_action,
            "governance_clamp": governance_clamp,
            "data_quality_confidence": _float(row.get("data_quality_confidence")),
            "risk_score": _float(row.get("risk_score")),
            "current_weight": current_weight,
            "proposed_weight": proposed_weight,
            "target_weight": _float(row.get("target_weight")),
            "without_weight": without_weight,
            "with_weight": with_weight,
            "without_turnover": abs(without_weight - current_weight),
            "with_turnover": abs(with_weight - current_weight),
            "forecasts": forecasts,
            "sensitivity_modifier_score_points": {
                str(int(x)): modifier_for_forecasts(forecasts, x) for x in SENSITIVITY_CEILINGS
            },
        })
    if not items:
        return None, {
            "decision_set_id": snapshot["decision_set_id"],
            "status": "no_parity_eligible_entity_forecasts",
            "recorded_at": iso_z(now),
            "terminal_no_backfill": True,
        }

    payload = {
        "contract_version": CONTRACT_VERSION,
        "decision_set_id": snapshot["decision_set_id"],
        "decision_at": snapshot["decision_at"],
        "engine_methodology_version": snapshot["engine_methodology_version"],
        "decision_world_state_id": decision_world["world_state_id"],
        "items": items,
    }
    pair_set_id = _stable_id("brace-entity-pair", payload)
    target_at = decision_at + timedelta(days=ECONOMIC_HORIZON_DAYS)
    record = {
        "pair_set_id": pair_set_id,
        "decision_set_id": snapshot["decision_set_id"],
        "captured_at": iso_z(now),
        "decision_at": snapshot["decision_at"],
        "target_at": iso_z(target_at),
        "engine_methodology_version": snapshot["engine_methodology_version"],
        "bridge_contract_version": CONTRACT_VERSION,
        "modifier_contract_version": PRIMARY_MODIFIER_CONTRACT_VERSION,
        "economic_contract_version": ECONOMIC_CONTRACT_VERSION,
        "decision_world_state_id": decision_world["world_state_id"],
        "decision_world_state_context_as_of": decision_world.get("context_as_of"),
        "portfolio_notional_pln": snapshot.get("portfolio_notional_pln"),
        "transaction_cost_bps": TRANSACTION_COST_BPS,
        "economic_horizon_days": ECONOMIC_HORIZON_DAYS,
        "items": items,
        "source_sha256": dict(snapshot.get("source_sha256") or {}),
        "paired_on_same_market_state": True,
        "engine_consumed_belief": False,
        "hypothetical_only": True,
        "historical_backfill": False,
        "promotion_authority": False,
    }
    record["immutable_sha256"] = _sha(record)
    return record, None


def _current_prices(analysis: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    for raw in analysis.get("positions") or []:
        if not isinstance(raw, Mapping):
            continue
        instrument = str(raw.get("instrument_id") or raw.get("id") or "").lower()
        if not instrument:
            continue
        rows[instrument] = {
            "price": _float(raw.get("current_price"), 0.0),
            "market_date": str(raw.get("market_date") or raw.get("latest_price_date") or "")[:10],
            "price_updated_at": raw.get("current_price_updated_at"),
        }
    return rows


def _resolve_pair_outcome(pair: Mapping[str, Any], analysis: Mapping[str, Any], *, now: datetime) -> Optional[Dict[str, Any]]:
    target_at = parse_time(str(pair.get("target_at")))
    if now < target_at:
        return None
    current = _current_prices(analysis)
    target_date = target_at.date()
    max_date = target_date + timedelta(days=MAX_EVALUATION_LAG_DAYS)
    rows: List[Dict[str, Any]] = []
    for item in pair.get("items") or []:
        instrument = str(item.get("instrument") or "").lower()
        price_row = current.get(instrument)
        if not price_row:
            if now.date() > max_date:
                return {
                    "pair_set_id": pair["pair_set_id"],
                    "status": "incomplete_missing_instrument_after_max_lag",
                    "closed_at": iso_z(now),
                    "calibration_eligible": False,
                    "missing_instrument": instrument,
                }
            return None
        market_date = _date(price_row.get("market_date"))
        price = _float(price_row.get("price"), 0.0)
        if market_date is None or market_date < target_date or price <= 0:
            if now.date() > max_date:
                return {
                    "pair_set_id": pair["pair_set_id"],
                    "status": "incomplete_no_post_target_price_after_max_lag",
                    "closed_at": iso_z(now),
                    "calibration_eligible": False,
                    "instrument": instrument,
                }
            return None
        signal_price = _float(item.get("signal_price"), 0.0)
        if signal_price <= 0:
            return {
                "pair_set_id": pair["pair_set_id"],
                "status": "invalid_signal_price",
                "closed_at": iso_z(now),
                "calibration_eligible": False,
                "instrument": instrument,
            }
        instrument_return = price / signal_price - 1.0
        without_weight = _float(item.get("without_weight"))
        with_weight = _float(item.get("with_weight"))
        without_turnover = _float(item.get("without_turnover"))
        with_turnover = _float(item.get("with_turnover"))
        without_cost = without_turnover * TRANSACTION_COST_RATE
        with_cost = with_turnover * TRANSACTION_COST_RATE
        rows.append({
            "instrument": instrument,
            "signal_price": signal_price,
            "evaluation_price": price,
            "evaluation_market_date": market_date.isoformat(),
            "instrument_return": instrument_return,
            "without_weight": without_weight,
            "with_weight": with_weight,
            "without_turnover": without_turnover,
            "with_turnover": with_turnover,
            "without_cost_return": without_cost,
            "with_cost_return": with_cost,
            "without_contribution_return": without_weight * instrument_return - without_cost,
            "with_contribution_return": with_weight * instrument_return - with_cost,
        })
    without_return = sum(row["without_contribution_return"] for row in rows)
    with_return = sum(row["with_contribution_return"] for row in rows)
    notional = _float(pair.get("portfolio_notional_pln"), 0.0)
    return {
        "pair_set_id": pair["pair_set_id"],
        "status": "matured",
        "closed_at": iso_z(now),
        "calibration_eligible": True,
        "decision_at": pair.get("decision_at"),
        "target_at": pair.get("target_at"),
        "evaluation_analysis_generated_at": analysis.get("generated_at"),
        "without_return": without_return,
        "with_return": with_return,
        "delta_return": with_return - without_return,
        "without_pnl_pln": None if notional <= 0 else notional * without_return,
        "with_pnl_pln": None if notional <= 0 else notional * with_return,
        "delta_pnl_pln": None if notional <= 0 else notional * (with_return - without_return),
        "without_turnover": sum(row["without_turnover"] for row in rows),
        "with_turnover": sum(row["with_turnover"] for row in rows),
        "without_cost_return": sum(row["without_cost_return"] for row in rows),
        "with_cost_return": sum(row["with_cost_return"] for row in rows),
        "items": rows,
        "same_instrument_entry_evaluation_and_cost_contract": True,
        "historical_backfill": False,
    }


def _max_drawdown(returns: Sequence[float]) -> Optional[float]:
    if not returns:
        return None
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for ret in returns:
        equity *= 1.0 + float(ret)
        peak = max(peak, equity)
        dd = equity / peak - 1.0
        max_dd = min(max_dd, dd)
    return max_dd


def _sharpe(returns: Sequence[float]) -> Optional[float]:
    if len(returns) < 2:
        return None
    sd = statistics.stdev(float(x) for x in returns)
    if sd <= 1e-12:
        return None
    return statistics.mean(float(x) for x in returns) / sd * math.sqrt(52.0)


def _tail_mean(returns: Sequence[float], fraction: float = 0.10) -> Optional[float]:
    if not returns:
        return None
    ordered = sorted(float(x) for x in returns)
    count = max(1, int(math.ceil(len(ordered) * fraction)))
    return sum(ordered[:count]) / count


def _economic_side(rows: Sequence[Mapping[str, Any]], side: str) -> Dict[str, Any]:
    returns = [float(row[f"{side}_return"]) for row in rows if row.get("calibration_eligible") is True]
    pnl_values = [row.get(f"{side}_pnl_pln") for row in rows if row.get("calibration_eligible") is True]
    pnl_numeric = [float(x) for x in pnl_values if x is not None]
    turnover = [float(row.get(f"{side}_turnover") or 0.0) for row in rows if row.get("calibration_eligible") is True]
    costs = [float(row.get(f"{side}_cost_return") or 0.0) for row in rows if row.get("calibration_eligible") is True]
    return {
        "paired_n": len(returns),
        "pnl_pln": sum(pnl_numeric) if pnl_numeric else (0.0 if returns else None),
        "cumulative_return": math.prod(1.0 + x for x in returns) - 1.0 if returns else None,
        "max_drawdown": _max_drawdown(returns),
        "sharpe_annualized_event_basis": _sharpe(returns),
        "hit_rate": sum(1 for x in returns if x > 0.0) / len(returns) if returns else None,
        "turnover": sum(turnover) if returns else None,
        "cost_return": sum(costs) if returns else None,
        "worst_event_return": min(returns) if returns else None,
        "empirical_cvar_10pct": _tail_mean(returns, 0.10),
    }


def _economics_report(outcomes: Sequence[Mapping[str, Any]], pair_sets: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    matured = [row for row in outcomes if row.get("status") == "matured" and row.get("calibration_eligible") is True]
    without = _economic_side(matured, "without")
    with_side = _economic_side(matured, "with")
    dd_without = without.get("max_drawdown")
    dd_with = with_side.get("max_drawdown")
    delta_pnl = None
    if without.get("pnl_pln") is not None and with_side.get("pnl_pln") is not None:
        delta_pnl = float(with_side["pnl_pln"]) - float(without["pnl_pln"])
    decision_items = [item for pair in pair_sets for item in (pair.get("items") or [])]
    changed = sum(1 for item in decision_items if item.get("with_action") != item.get("without_action"))
    nonzero = sum(1 for item in decision_items if abs(_float(item.get("belief_modifier_score_points"))) > 1e-12)
    return {
        "scope": "paired_entity_position_contribution_only; unchanged non-Entity portfolio exposures cancel in the delta",
        "economic_contract_version": ECONOMIC_CONTRACT_VERSION,
        "horizon_days": ECONOMIC_HORIZON_DAYS,
        "transaction_cost_bps": TRANSACTION_COST_BPS,
        "matured_pair_sets": len(matured),
        "pending_pair_sets": max(0, len(pair_sets) - len([x for x in outcomes if x.get("pair_set_id")])),
        "ENGINE_ORIGINAL_WITHOUT_BELIEF": without,
        "ENGINE_PLUS_HYPOTHETICAL_BELIEF_WITH_BELIEF": with_side,
        "DELTA": {
            "pnl_pln": delta_pnl,
            "cumulative_return": None if without.get("cumulative_return") is None else float(with_side["cumulative_return"]) - float(without["cumulative_return"]),
            "sharpe": None if without.get("sharpe_annualized_event_basis") is None or with_side.get("sharpe_annualized_event_basis") is None else float(with_side["sharpe_annualized_event_basis"]) - float(without["sharpe_annualized_event_basis"]),
            "hit_rate": None if without.get("hit_rate") is None or with_side.get("hit_rate") is None else float(with_side["hit_rate"]) - float(without["hit_rate"]),
            "turnover": None if without.get("turnover") is None or with_side.get("turnover") is None else float(with_side["turnover"]) - float(without["turnover"]),
            "cost_return": None if without.get("cost_return") is None or with_side.get("cost_return") is None else float(with_side["cost_return"]) - float(without["cost_return"]),
            "drawdown_change": None if dd_without is None or dd_with is None else float(dd_with) - float(dd_without),
            "drawdown_improvement": None if dd_without is None or dd_with is None else abs(float(dd_without)) - abs(float(dd_with)),
            "worst_event_return": None if without.get("worst_event_return") is None or with_side.get("worst_event_return") is None else float(with_side["worst_event_return"]) - float(without["worst_event_return"]),
            "empirical_cvar_10pct": None if without.get("empirical_cvar_10pct") is None or with_side.get("empirical_cvar_10pct") is None else float(with_side["empirical_cvar_10pct"]) - float(without["empirical_cvar_10pct"]),
        },
        "required_drawdown_fields": {
            "max_drawdown_without": dd_without,
            "max_drawdown_with": dd_with,
            "drawdown_change": None if dd_without is None or dd_with is None else float(dd_with) - float(dd_without),
            "drawdown_improvement": None if dd_without is None or dd_with is None else abs(float(dd_without)) - abs(float(dd_with)),
        },
        "information_value_seed": {
            "paired_decision_items": len(decision_items),
            "nonzero_modifier_items": nonzero,
            "decision_changed_items": changed,
            "decision_change_rate": changed / len(decision_items) if decision_items else None,
            "mean_delta_return": statistics.mean(float(x["delta_return"]) for x in matured) if matured else None,
            "interpretation": "descriptive seed only; this is not yet the Marginal Information Value model",
        },
    }


def _assert_append_only(previous: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    for field in ("pair_sets", "terminal_unpaired_sets", "economic_outcomes"):
        before = previous.get(field) or {}
        after = current.get(field) or {}
        for key, value in before.items():
            if key not in after or after[key] != value:
                raise RuntimeError(f"PR17 append-only mutation detected in {field}: {key}")


def run(
    state_dir: Path,
    *,
    pr15_core_state_path: Path,
    pr15_runtime_state_path: Path,
    world_state_path: Path,
    world_report_path: Path,
    pending_decisions_path: Path,
    analysis_path: Path,
    portfolio_path: Path,
    as_of: Optional[datetime] = None,
) -> Dict[str, Any]:
    _assert_safety()
    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_z = iso_z(now)
    state_dir = Path(state_dir)
    state_path = state_dir / STATE_FILENAME
    report_path = state_dir / REPORT_FILENAME

    core = _read_json(pr15_core_state_path, {})
    pr15_runtime = _read_json(pr15_runtime_state_path, {})
    world_state = _read_json(world_state_path, {})
    world_report = _read_json(world_report_path, {})
    pending = _read_json(pending_decisions_path, {})
    analysis = _read_json(analysis_path, {})
    portfolio = _read_json(portfolio_path, {})
    _validate_pr15(core, pr15_runtime)
    _validate_world_state(world_state, world_report)

    decision_snapshot = _decision_snapshot(pending, analysis, portfolio)
    decision_at = parse_time(str(decision_snapshot["decision_at"]))
    if decision_at > now:
        raise ValueError("future-dated BRACE decision snapshot rejected")

    previous = _read_json(state_path, empty_state())
    state = deepcopy(previous)
    if str(state.get("schema_version") or "") != SCHEMA_VERSION:
        raise ValueError("PR17 state schema mismatch")
    if str(state.get("contract_version") or "") != CONTRACT_VERSION:
        raise ValueError("PR17 state contract mismatch")
    first_run = not bool(state.get("activated_at"))
    if first_run:
        state["activated_at"] = now_z

    seen = set(str(x) for x in state.get("seen_decision_set_ids") or [])
    pre_activation = set(str(x) for x in state.get("pre_activation_decision_set_ids") or [])
    decision_set_id = str(decision_snapshot["decision_set_id"])
    new_pair_this_run = False
    new_terminal_this_run = False

    if first_run:
        seen.add(decision_set_id)
        pre_activation.add(decision_set_id)
    elif decision_set_id not in seen:
        seen.add(decision_set_id)
        activated_at = parse_time(str(state.get("activated_at")))
        if decision_at < activated_at:
            state.setdefault("terminal_unpaired_sets", {})[decision_set_id] = {
                "decision_set_id": decision_set_id,
                "status": "decision_predates_pr17_activation",
                "recorded_at": now_z,
                "terminal_no_backfill": True,
            }
            new_terminal_this_run = True
        else:
            pair, terminal = _pair_set(decision_snapshot, core, world_state, now=now)
            if pair is not None:
                state.setdefault("pair_sets", {})[pair["pair_set_id"]] = pair
                new_pair_this_run = True
            elif terminal is not None:
                state.setdefault("terminal_unpaired_sets", {})[decision_set_id] = terminal
                new_terminal_this_run = True

    state["seen_decision_set_ids"] = sorted(seen)
    state["pre_activation_decision_set_ids"] = sorted(pre_activation)

    outcomes: MutableMapping[str, Any] = state.setdefault("economic_outcomes", {})
    for pair_id, pair in sorted((state.get("pair_sets") or {}).items()):
        if pair_id in outcomes:
            continue
        resolved = _resolve_pair_outcome(pair, analysis, now=now)
        if resolved is not None:
            outcomes[pair_id] = resolved

    state["last_run_at"] = now_z
    _assert_append_only(previous, state)
    _write_json(state_path, state)

    pair_sets = [state["pair_sets"][key] for key in sorted(state.get("pair_sets") or {})]
    outcome_rows = [state["economic_outcomes"][key] for key in sorted(state.get("economic_outcomes") or {})]
    economics = _economics_report(outcome_rows, pair_sets)
    matured_n = economics["matured_pair_sets"]

    readiness_reasons = [
        "PR17 is Phase A computed-only; BRACE does not consume the Entity Belief modifier",
        "promotion-grade effective N is not defined or assessed in PR17",
        "paired uplift uncertainty/stability/regime/concentration gates are not yet complete",
    ]
    if matured_n == 0:
        readiness_reasons.append("no matured prospective paired economic observations yet")

    report = {
        "schema_version": SCHEMA_VERSION,
        "report_version": REPORT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "mode": MODE,
        "generated_at": now_z,
        "purpose": "Prospective BRACE↔Entity Belief paired shadow bridge and WITH/WITHOUT economics only.",
        "active_decision_influence": False,
        "bridge_phase": "A_COMPUTED_NOT_CONSUMED",
        "primary_modifier_contract": {
            "contract_version": PRIMARY_MODIFIER_CONTRACT_VERSION,
            "score_point_ceiling": PRIMARY_MODIFIER_SCORE_POINTS,
            "formula": "ceiling * mean((2*predicted_probability-1)*forecast_confidence) across one active forecast per Entity dimension",
            "pnl_tuned": False,
            "sensitivity_only_score_point_ceilings": list(SENSITIVITY_CEILINGS),
            "candidate_ranking_override": False,
            "new_sizing_instruction": False,
            "veto": False,
            "forced_exit": False,
            "engine_consumes_modifier": False,
        },
        "source_contract": {
            "pr15_contract_version": PR15_CONTRACT_VERSION,
            "pr16_1_contract_version": PR16_1_CONTRACT_VERSION,
            "forecast_binding_contract_version": FORECAST_BINDING_CONTRACT_VERSION,
            "world_state_contract_version": WORLD_STATE_CONTRACT_VERSION,
            "engine_methodology_version": decision_snapshot.get("engine_methodology_version"),
            "decision_set_id": decision_set_id,
            "decision_at": decision_snapshot.get("decision_at"),
            "same_state_pairing": True,
        },
        "sample": {
            "activation_only_this_run": first_run,
            "decision_sets_seen_total": len(seen),
            "pre_activation_decision_sets_total": len(pre_activation),
            "pair_sets_total": len(pair_sets),
            "terminal_unpaired_sets_total": len(state.get("terminal_unpaired_sets") or {}),
            "new_pair_set_this_run": new_pair_this_run,
            "new_terminal_unpaired_this_run": new_terminal_this_run,
            "economic_outcomes_total": len(outcome_rows),
            "matured_economic_outcomes": matured_n,
        },
        "paired_sets": pair_sets,
        "terminal_unpaired_sets": [state["terminal_unpaired_sets"][key] for key in sorted(state.get("terminal_unpaired_sets") or {})],
        "economic_outcomes": outcome_rows,
        "with_without_economics": economics,
        "promotion_readiness": {
            "eligible_for_promotion_review": False,
            "status": "NOT_ELIGIBLE_FOR_PROMOTION_REVIEW",
            "reasons": readiness_reasons,
            "effective_n_sufficient": None,
            "uplift_positive": None if matured_n == 0 else economics["DELTA"]["pnl_pln"] is not None and economics["DELTA"]["pnl_pln"] > 0,
            "uplift_ci_acceptable": None,
            "uplift_stable_over_time": None,
            "regime_robust": None,
            "concentration_ok": None,
            "drawdown_not_worse": None if matured_n == 0 else economics["required_drawdown_fields"]["drawdown_change"] is not None and economics["required_drawdown_fields"]["drawdown_change"] >= 0,
            "tail_risk_not_worse": None,
            "belief_calibration_ok": None,
            "drift_ok": None,
            "data_quality_ok": not bool(decision_snapshot.get("issues")),
            "provenance_ok": True,
            "anti_hindsight_ok": True,
            "shadow_runtime_stable": None,
            "automatic_promotion": False,
        },
        "anti_hindsight": {
            "first_run_activation_only": True,
            "historical_decision_backfill": False,
            "historical_belief_backfill": False,
            "forecast_must_exist_at_or_before_decision": True,
            "forecast_must_have_prospective_pr16_1_binding": True,
            "decision_world_state_must_preexist_decision": True,
            "same_entry_evaluation_horizon_costs_for_pair": True,
        },
        "research_next": {
            "marginal_information_value_enabled": False,
            "information_value_seed_present": True,
            "causal_belief_graph_enabled": False,
            "engine_specific_trust_enabled": False,
            "disagreement_topology_enabled": False,
            "note": "PR17 collects the prospective paired evidence needed before a separate Marginal Information Value model is justified.",
        },
        "capabilities": capabilities(),
        "promotion_evidence_standard": promotion_evidence_standard(),
        "safety_controls": safety_controls(),
    }
    _write_json(report_path, report)
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--pr15-core-state", type=Path, required=True)
    parser.add_argument("--pr15-runtime-state", type=Path, required=True)
    parser.add_argument("--world-state", type=Path, required=True)
    parser.add_argument("--world-report", type=Path, required=True)
    parser.add_argument("--pending-decisions", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--portfolio", type=Path, required=True)
    parser.add_argument("--now")
    args = parser.parse_args(argv)
    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    report = run(
        args.state_dir,
        pr15_core_state_path=args.pr15_core_state,
        pr15_runtime_state_path=args.pr15_runtime_state,
        world_state_path=args.world_state,
        world_report_path=args.world_report,
        pending_decisions_path=args.pending_decisions,
        analysis_path=args.analysis,
        portfolio_path=args.portfolio,
        as_of=now,
    )
    print(json.dumps({
        "mode": report["mode"],
        "phase": report["bridge_phase"],
        "sample": report["sample"],
        "economics": report["with_without_economics"],
        "promotion": report["promotion_readiness"]["status"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
