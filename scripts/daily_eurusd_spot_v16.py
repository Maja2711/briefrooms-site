#!/usr/bin/env python3
"""Post-trade intelligence for the active Daily EUR/USD engine.

v1.6 extends the existing single EUR/USD decision loop. It does NOT create a
second shadow trader or a second position stream.

The layer adds three tightly scoped capabilities:
1. Post-Trade Intelligence: every newly closed trade receives an auditable
   review based on the observed price path and the entry thesis.
2. Error Attribution: losses are classified into deterministic diagnostic
   patterns without pretending that price-path evidence proves causality.
3. Same-Thesis Re-entry Guard: after a recent meaningful loss, another entry
   in the same direction and thesis family is blocked unless the market/model
   state changed materially.

The guard is deliberately NOT a generic cooldown. A different thesis family is
free to act, and the same thesis may re-enter immediately when score,
confidence, or its component state changed materially. Existing one-open-
position-at-a-time, SL/TP, dynamic exit, A fallback and low-edge exploration
semantics remain owned by v1.5 and earlier layers.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from belief_market_data_adapter import Bar
from daily_engine_contract import DailyEngineOutput
import daily_eurusd_lifecycle as lifecycle
import daily_eurusd_spot as base
import daily_eurusd_spot_v15 as v15  # installs v1.5 + v1.4 + v1.3 + v1.2 first

ENGINE_VERSION = "eurusd-daily-spot-v1.6.0"
ENTRY_THESIS_SCHEMA = "eurusd-entry-thesis-v1"
POST_TRADE_REVIEW_SCHEMA = "eurusd-post-trade-review-v1"
SAME_THESIS_GUARD_WINDOW_HOURS = 12.0
SAME_THESIS_GUARD_MIN_LOSS_R = -0.15
MATERIAL_SCORE_STRENGTH_IMPROVEMENT = 3.0
MATERIAL_CONFIDENCE_IMPROVEMENT = 0.08
MATERIAL_COMPONENT_L1_CHANGE = 0.20

_original_build_output = base.build_output
_original_create_position = base.create_position
_original_position_from_output = base.position_from_output
_original_evaluate_position = base.evaluate_position
_original_open_output = base._open_output
_original_closed_output = base._closed_output


def _parse(value: str | None) -> datetime | None:
    return lifecycle.parse_iso(value)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _clone(output: DailyEngineOutput, *, metadata: Mapping[str, Any] | None = None) -> DailyEngineOutput:
    return DailyEngineOutput(
        instrument=output.instrument,
        timestamp=output.timestamp,
        direction=output.direction,
        score=float(output.score),
        confidence=float(output.confidence),
        entry=output.entry,
        stop=output.stop,
        target=output.target,
        horizon=output.horizon,
        engine_version=ENGINE_VERSION,
        status=output.status,
        decision_mode=output.decision_mode,
        metadata=dict(metadata if metadata is not None else output.metadata),
    ).validate()


def _numeric_map(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): float(item)
        for key, item in value.items()
        if isinstance(item, (int, float)) and not isinstance(item, bool)
    }


def _decision_source(metadata: Mapping[str, Any] | None) -> str:
    md = dict(metadata or {})
    candidate = md.get("candidate") if isinstance(md.get("candidate"), Mapping) else {}
    return str(md.get("decision_source") or candidate.get("source") or "NATIVE").upper()


def _source_family(source: str | None) -> str:
    normalized = str(source or "NATIVE").upper()
    if normalized in {"NATIVE", "NATIVE_LEGACY", "LOW_EDGE_LEARNING_EXPLORATION"}:
        return "NATIVE_COMPONENTS"
    if normalized == "A_TECHNICAL_FALLBACK":
        return "A_TECHNICAL_FALLBACK"
    return normalized


def _directional_strength(direction: str, score: float) -> float:
    normalized = str(direction).upper()
    if normalized == "LONG":
        return float(score) - 50.0
    if normalized == "SHORT":
        return 50.0 - float(score)
    return 0.0


def _entry_thesis_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
    source = _decision_source(metadata)
    direction = str(payload.get("direction") or "FLAT").upper()
    score = float(payload.get("score") or 50.0)
    confidence = float(payload.get("confidence") or 0.0)
    thesis: dict[str, Any] = {
        "schema_version": ENTRY_THESIS_SCHEMA,
        "source": source,
        "source_family": _source_family(source),
        "direction": direction,
        "score": round(score, 4),
        "directional_strength": round(_directional_strength(direction, score), 4),
        "confidence": round(confidence, 4),
        "components": {key: round(value, 6) for key, value in _numeric_map(metadata.get("components")).items()},
        "weights": {key: round(value, 6) for key, value in _numeric_map(metadata.get("weights")).items()},
    }
    if isinstance(metadata.get("a_fallback"), Mapping):
        a = dict(metadata.get("a_fallback") or {})
        thesis["a_fallback"] = {
            key: a.get(key)
            for key in ("method", "direction", "score", "confidence", "market_observed_at")
            if key in a
        }
    if isinstance(metadata.get("exploration"), Mapping):
        exploration = dict(metadata.get("exploration") or {})
        thesis["exploration"] = {
            key: exploration.get(key)
            for key in (
                "edge_points",
                "supporting_components",
                "supporting_component_count",
                "directional_dominance",
            )
            if key in exploration
        }
    return thesis


def _legacy_entry_thesis(row: Mapping[str, Any]) -> dict[str, Any]:
    source = str(row.get("decision_source") or "NATIVE_LEGACY").upper()
    direction = str(row.get("direction") or "FLAT").upper()
    score = float(row.get("entry_score") if row.get("entry_score") is not None else row.get("score") or 50.0)
    confidence = float(
        row.get("entry_confidence")
        if row.get("entry_confidence") is not None
        else row.get("confidence") or 0.0
    )
    return {
        "schema_version": ENTRY_THESIS_SCHEMA,
        "source": source,
        "source_family": _source_family(source),
        "direction": direction,
        "score": round(score, 4),
        "directional_strength": round(_directional_strength(direction, score), 4),
        "confidence": round(confidence, 4),
        "components": {
            key: round(value, 6)
            for key, value in _numeric_map(row.get("entry_components") or row.get("components")).items()
        },
        "weights": {
            key: round(value, 6)
            for key, value in _numeric_map(row.get("entry_weights") or row.get("weights")).items()
        },
        "legacy_reconstructed": True,
    }


def _entry_thesis_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    thesis = row.get("entry_thesis")
    if isinstance(thesis, Mapping):
        result = dict(thesis)
        source = str(result.get("source") or row.get("decision_source") or "NATIVE_LEGACY").upper()
        result.setdefault("source", source)
        result.setdefault("source_family", _source_family(source))
        result.setdefault("direction", str(row.get("direction") or "FLAT").upper())
        return result
    return _legacy_entry_thesis(row)


def _post_trade_review(trade: Mapping[str, Any]) -> dict[str, Any]:
    """Classify an observed outcome without making an unsupported causal claim."""
    r_multiple = float(trade.get("r_multiple") or 0.0)
    mfe_r = float(trade.get("mfe_r") or 0.0)
    mae_r = float(trade.get("mae_r") or 0.0)
    exit_reason = str(trade.get("exit_reason") or "UNKNOWN").upper()
    thesis = _entry_thesis_from_row(trade)

    if r_multiple > 0.0:
        primary_pattern = "THESIS_CONFIRMED_PATH"
    elif r_multiple == 0.0:
        primary_pattern = "FLAT_OUTCOME_NO_ATTRIBUTION"
    elif mfe_r < 0.25 and mae_r <= -0.75:
        primary_pattern = "DIRECTION_OR_ENTRY_TIMING_FAILURE"
    elif mfe_r >= 0.50:
        primary_pattern = "EDGE_GIVEBACK_OR_EXIT_FAILURE"
    elif exit_reason == "STOP_LOSS":
        primary_pattern = "THESIS_INVALIDATED_AT_RISK_BOUNDARY"
    elif mfe_r < 0.25:
        primary_pattern = "WEAK_FOLLOW_THROUGH_OR_STALLED_THESIS"
    else:
        primary_pattern = "UNRESOLVED_LOSS_PATTERN"

    direction_timing_status = (
        "supported_by_path"
        if primary_pattern == "DIRECTION_OR_ENTRY_TIMING_FAILURE"
        else "possible" if r_multiple < 0.0 else "not_indicated"
    )
    exit_status = (
        "possible_giveback"
        if r_multiple < 0.0 and mfe_r >= 0.50
        else "not_indicated_by_path"
    )
    follow_through_status = (
        "weak"
        if r_multiple < 0.0 and mfe_r < 0.25
        else "material_favorable_excursion_observed" if mfe_r >= 0.50 else "mixed"
    )

    return {
        "schema_version": POST_TRADE_REVIEW_SCHEMA,
        "review_mode": "DETERMINISTIC_OBSERVED_PATH",
        "classification_basis": "observed_price_path_not_causal_proof",
        "causal_claim": False,
        "primary_pattern": primary_pattern,
        "evidence": {
            "exit_reason": exit_reason,
            "r_multiple": round(r_multiple, 4),
            "mfe_r": round(mfe_r, 4),
            "mae_r": round(mae_r, 4),
            "reached_0_25r_favorable": bool(mfe_r >= 0.25),
            "reached_0_50r_favorable": bool(mfe_r >= 0.50),
            "reached_0_75r_adverse": bool(mae_r <= -0.75),
            "reached_1r_adverse": bool(mae_r <= -1.0),
            "decision_source": str(trade.get("decision_source") or thesis.get("source") or "NATIVE_LEGACY"),
        },
        "error_attribution": {
            "direction_or_entry_timing": direction_timing_status,
            "follow_through": follow_through_status,
            "exit_management": exit_status,
            "risk_geometry": "unresolved_from_single_trade",
            "macro_or_news_event": "not_evaluated_by_this_layer",
            "data_quality": "not_inferred_from_outcome",
        },
        "entry_thesis": thesis,
        "learning_action": (
            "require_material_thesis_change_before_same_family_reentry"
            if r_multiple <= SAME_THESIS_GUARD_MIN_LOSS_R
            else "observe_without_reentry_restriction"
        ),
        "policy_mutation_allowed_from_single_trade": False,
    }


def _recent_directional_losses(
    history: Mapping[str, Any] | None,
    *,
    direction: str,
    observed_at: datetime,
) -> list[Mapping[str, Any]]:
    if not history:
        return []
    cutoff = observed_at - timedelta(hours=SAME_THESIS_GUARD_WINDOW_HOURS)
    rows: list[tuple[datetime, Mapping[str, Any]]] = []
    for trade in history.get("trades") or []:
        if not isinstance(trade, Mapping):
            continue
        if str(trade.get("direction") or "").upper() != str(direction).upper():
            continue
        if float(trade.get("r_multiple") or 0.0) > SAME_THESIS_GUARD_MIN_LOSS_R:
            continue
        closed_at = _parse(str(trade.get("closed_at") or ""))
        if closed_at is None or closed_at < cutoff or closed_at > observed_at:
            continue
        rows.append((closed_at, trade))
    rows.sort(key=lambda item: item[0], reverse=True)
    return [trade for _, trade in rows]


def _thesis_change_diagnostics(current: Mapping[str, Any], prior: Mapping[str, Any]) -> dict[str, Any]:
    direction = str(current.get("direction") or "FLAT").upper()
    current_strength = _directional_strength(direction, float(current.get("score") or 50.0))
    prior_strength = _directional_strength(direction, float(prior.get("score") or 50.0))
    score_improvement = current_strength - prior_strength
    confidence_improvement = float(current.get("confidence") or 0.0) - float(prior.get("confidence") or 0.0)

    current_components = _numeric_map(current.get("components"))
    prior_components = _numeric_map(prior.get("components"))
    shared = sorted(set(current_components) & set(prior_components))
    component_l1_change = None
    if shared:
        component_l1_change = sum(abs(current_components[key] - prior_components[key]) for key in shared)

    triggers: list[str] = []
    if score_improvement >= MATERIAL_SCORE_STRENGTH_IMPROVEMENT:
        triggers.append("directional_score_strength_improved")
    if confidence_improvement >= MATERIAL_CONFIDENCE_IMPROVEMENT:
        triggers.append("confidence_improved")
    if component_l1_change is not None and component_l1_change >= MATERIAL_COMPONENT_L1_CHANGE:
        triggers.append("component_state_changed")

    return {
        "material_change": bool(triggers),
        "material_change_triggers": triggers,
        "directional_score_strength_improvement": round(score_improvement, 4),
        "confidence_improvement": round(confidence_improvement, 4),
        "component_l1_change": None if component_l1_change is None else round(component_l1_change, 4),
        "shared_components": shared,
        "thresholds": {
            "score_strength_improvement": MATERIAL_SCORE_STRENGTH_IMPROVEMENT,
            "confidence_improvement": MATERIAL_CONFIDENCE_IMPROVEMENT,
            "component_l1_change": MATERIAL_COMPONENT_L1_CHANGE,
        },
    }


def _guard_metadata_base() -> dict[str, Any]:
    return {
        "enabled": True,
        "mode": "SAME_THESIS_REENTRY_GUARD",
        "generic_loss_cooldown": False,
        "window_hours": SAME_THESIS_GUARD_WINDOW_HOURS,
        "min_loss_r": SAME_THESIS_GUARD_MIN_LOSS_R,
    }


def _apply_same_thesis_guard(
    candidate: DailyEngineOutput,
    history: Mapping[str, Any] | None,
) -> DailyEngineOutput:
    metadata = dict(candidate.metadata)
    guard = _guard_metadata_base()

    if candidate.direction not in {"LONG", "SHORT"}:
        guard.update({"evaluated": False, "blocked": False, "reason": "no_directional_candidate"})
        metadata["same_thesis_reentry_guard"] = guard
        return _clone(candidate, metadata=metadata)

    observed_at = _parse(candidate.timestamp)
    if observed_at is None:
        guard.update({"evaluated": False, "blocked": False, "reason": "missing_candidate_timestamp"})
        metadata["same_thesis_reentry_guard"] = guard
        return _clone(candidate, metadata=metadata)

    current_thesis = _entry_thesis_from_payload(candidate.to_dict())
    losses = _recent_directional_losses(
        history,
        direction=candidate.direction,
        observed_at=observed_at,
    )
    if not losses:
        guard.update({
            "evaluated": True,
            "blocked": False,
            "reason": "no_recent_meaningful_same_direction_loss",
            "current_thesis": current_thesis,
        })
        metadata["same_thesis_reentry_guard"] = guard
        return _clone(candidate, metadata=metadata)

    current_family = str(current_thesis.get("source_family") or "")
    matching = [
        trade for trade in losses
        if str(_entry_thesis_from_row(trade).get("source_family") or "") == current_family
    ]
    if not matching:
        latest = losses[0]
        prior_thesis = _entry_thesis_from_row(latest)
        guard.update({
            "evaluated": True,
            "blocked": False,
            "reason": "recent_loss_belongs_to_different_thesis_family",
            "current_thesis": current_thesis,
            "latest_directional_loss": {
                "trade_id": latest.get("trade_id"),
                "closed_at": latest.get("closed_at"),
                "r_multiple": latest.get("r_multiple"),
                "source_family": prior_thesis.get("source_family"),
            },
        })
        metadata["same_thesis_reentry_guard"] = guard
        return _clone(candidate, metadata=metadata)

    prior_trade = matching[0]
    prior_thesis = _entry_thesis_from_row(prior_trade)
    change = _thesis_change_diagnostics(current_thesis, prior_thesis)
    guard.update({
        "evaluated": True,
        "blocked": not bool(change["material_change"]),
        "reason": (
            "material_new_evidence_detected"
            if change["material_change"]
            else "recent_same_thesis_loss_without_material_new_evidence"
        ),
        "prior_trade_id": prior_trade.get("trade_id"),
        "prior_closed_at": prior_trade.get("closed_at"),
        "prior_r_multiple": prior_trade.get("r_multiple"),
        "source_family": current_family,
        "current_thesis": current_thesis,
        "prior_thesis": prior_thesis,
        "change": change,
    })

    if change["material_change"]:
        metadata["same_thesis_reentry_guard"] = guard
        return _clone(candidate, metadata=metadata)

    guarded_candidate = dict(metadata.get("candidate") or {})
    metadata["guarded_candidate"] = guarded_candidate
    candidate_meta = dict(guarded_candidate)
    reasons = list(candidate_meta.get("gate_reasons") or [])
    if "same_thesis_reentry_blocked" not in reasons:
        reasons.append("same_thesis_reentry_blocked")
    candidate_meta.update({"accepted": False, "gate_reasons": reasons})
    metadata["candidate"] = candidate_meta
    metadata["same_thesis_reentry_guard"] = guard
    return DailyEngineOutput(
        instrument=candidate.instrument,
        timestamp=candidate.timestamp,
        direction="FLAT",
        score=float(candidate.score),
        confidence=float(candidate.confidence),
        entry=None,
        stop=None,
        target=None,
        horizon=candidate.horizon,
        engine_version=ENGINE_VERSION,
        status="NO_TRADE",
        decision_mode=candidate.decision_mode,
        metadata=metadata,
    ).validate()


def build_output(snapshot: Any, history: Mapping[str, Any] | None = None, *, allow_entry: bool = True) -> DailyEngineOutput:
    candidate = _original_build_output(snapshot, history, allow_entry=allow_entry)
    if not allow_entry:
        metadata = dict(candidate.metadata)
        guard = _guard_metadata_base()
        guard.update({"evaluated": False, "blocked": False, "reason": "entry_disabled_this_cycle"})
        metadata["same_thesis_reentry_guard"] = guard
        return _clone(candidate, metadata=metadata)
    return _apply_same_thesis_guard(candidate, history)


def create_position(payload: Mapping[str, Any]) -> dict[str, Any]:
    position = dict(_original_create_position(payload))
    position["entry_thesis"] = _entry_thesis_from_payload(payload)
    return position


def position_from_output(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    position = _original_position_from_output(payload)
    if not position:
        return None
    result = dict(position)
    if not isinstance(result.get("entry_thesis"), Mapping):
        result["entry_thesis"] = _legacy_entry_thesis(result)
    return result


def evaluate_position(position: Mapping[str, Any], bars: Sequence[Bar], observed_at: datetime) -> dict[str, Any] | None:
    trade = _original_evaluate_position(position, bars, observed_at)
    if trade is None:
        return None
    enriched = dict(trade)
    enriched["entry_thesis"] = dict(
        position.get("entry_thesis")
        if isinstance(position.get("entry_thesis"), Mapping)
        else _legacy_entry_thesis(position)
    )
    enriched["post_trade_review"] = _post_trade_review(enriched)
    return enriched


def _open_output(candidate: DailyEngineOutput, position: Mapping[str, Any], mark_price: float) -> DailyEngineOutput:
    output = _original_open_output(candidate, position, mark_price)
    return _clone(output)


def _closed_output(candidate: DailyEngineOutput, trade: Mapping[str, Any], history: Mapping[str, Any]) -> DailyEngineOutput:
    output = _original_closed_output(candidate, trade, history)
    metadata = dict(output.metadata)
    metadata["post_trade_review"] = dict(trade.get("post_trade_review") or {})
    return _clone(output, metadata=metadata)


def _install() -> None:
    base.ENGINE_VERSION = ENGINE_VERSION
    base.build_output = build_output
    base.create_position = create_position
    base.position_from_output = position_from_output
    base.evaluate_position = evaluate_position
    base._open_output = _open_output
    base._closed_output = _closed_output


_install()


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
