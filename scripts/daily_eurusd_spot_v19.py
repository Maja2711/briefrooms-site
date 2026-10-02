#!/usr/bin/env python3
"""Daily EUR/USD v1.9 — Belief-first production architecture.

Direction is decided once, after upstream adapters/Evidence/Belief Core.
Legacy v1-v1.8 modules remain for lifecycle/history compatibility only.

Production order:
Belief Core -> one final LONG/SHORT/FLAT decision -> execution admission
-> EPE verified fill -> canonical lifecycle.

A/B/C, FSE, EURUSD X, Arm A fallback and low-edge exploration have zero
production direction/timing authority in v1.9. The legacy contextual entry
learner is reset to shadow observation until it earns evidence under the new
Belief-first decision semantics.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from daily_engine_contract import DailyEngineOutput
import daily_eurusd_belief_decision as belief_decision
import daily_eurusd_lifecycle as lifecycle
import daily_eurusd_spot as base
import daily_eurusd_spot_v14 as v14
import daily_eurusd_spot_v16 as v16
import daily_eurusd_spot_v18 as v18
import investments_wes_macro_belief as legacy_macro_context

ENGINE_VERSION = "eurusd-daily-spot-v1.9.0"
_epe_prepare = base.prepare_entry_candidate
_v18_open_output = v18._open_output
_v18_closed_output = v18._closed_output


def _clone(
    output: DailyEngineOutput,
    *,
    direction: str | None = None,
    entry: float | None | object = ...,
    stop: float | None | object = ...,
    target: float | None | object = ...,
    status: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> DailyEngineOutput:
    return DailyEngineOutput(
        instrument=output.instrument,
        timestamp=output.timestamp,
        direction=direction if direction is not None else output.direction,
        score=float(output.score),
        confidence=float(output.confidence),
        entry=output.entry if entry is ... else entry,
        stop=output.stop if stop is ... else stop,
        target=output.target if target is ... else target,
        horizon="intraday_to_27h",
        engine_version=ENGINE_VERSION,
        status=status or output.status,
        decision_mode="WITH",
        metadata=dict(metadata if metadata is not None else output.metadata),
    ).validate()


def _market_geometry(snapshot: Any, direction: str) -> tuple[float | None, float | None, float | None]:
    rows = list((getattr(snapshot, "bars", {}) or {}).get(base.EURUSD) or [])
    if not rows or direction not in {"LONG", "SHORT"}:
        return None, None, None
    atr = base._atr(rows)
    if atr is None or float(atr) <= 0:
        return None, None, None
    entry = float(rows[-1].close)
    risk = max(float(atr) * 1.35, entry * 0.0027)
    reward_risk = 1.8
    if direction == "LONG":
        return round(entry, 5), round(entry - risk, 5), round(entry + reward_risk * risk, 5)
    return round(entry, 5), round(entry + risk, 5), round(entry - reward_risk * risk, 5)


def fetch_snapshot(client: Any | None = None) -> Any:
    """Fetch only the direct EUR/USD market fact needed after final Belief synthesis.

    UUP/TLT and all other explanatory inputs belong upstream in the adapter /
    Evidence / Belief pipeline and are intentionally not fetched by the
    production decision runtime.
    """
    client = client or base.YahooChartClient(timeout=15)
    bars = {base.EURUSD: client.bars(base.EURUSD, "10d", "30m")}
    return base.MarketSnapshot(bars)


def _execution_admission(
    candidate: DailyEngineOutput,
    history: Mapping[str, Any] | None,
    *,
    observed_at: datetime,
) -> dict[str, Any]:
    reasons: list[str] = []
    details: dict[str, Any] = {
        "final_direction_preserved": candidate.direction,
        "direction_mutation_allowed": False,
    }

    calendar = belief_decision.calendar_safety(
        observed_at=observed_at,
        decision=(candidate.metadata or {}).get("final_decision") or {},
    )
    details["belief_calendar_safety"] = calendar
    if calendar.get("blocked") is True:
        reasons.append(str(calendar.get("reason") or "belief_calendar_safety_block"))

    # Re-use only the existing same-thesis risk governance. It is no longer a
    # direction engine: any veto is translated to execution_admission=false
    # while final_decision remains immutable in metadata.
    guarded = v16._apply_same_thesis_guard(candidate, history)
    guard_meta = (guarded.metadata or {}).get("same_thesis_reentry_guard") or {}
    details["same_thesis_reentry_guard"] = dict(guard_meta)
    if candidate.direction in {"LONG", "SHORT"} and guarded.direction == "FLAT":
        reasons.append(str(guard_meta.get("reason") or "same_thesis_reentry_blocked"))

    allowed = candidate.direction in {"LONG", "SHORT"} and not reasons
    return {
        "allowed": allowed,
        "status": "ALLOWED" if allowed else "BLOCKED",
        "reasons": reasons,
        **details,
    }


def build_output(
    snapshot: Any,
    history: Mapping[str, Any] | None = None,
    *,
    allow_entry: bool = True,
) -> DailyEngineOutput:
    rows = list((getattr(snapshot, "bars", {}) or {}).get(base.EURUSD) or [])
    if not rows:
        raise ValueError("EUR/USD market series is required for timestamp and risk geometry")
    observed_at = rows[-1].timestamp.astimezone(timezone.utc)

    state, consumer_meta = belief_decision.load_effective_state(observed_at=observed_at)
    decision = belief_decision.synthesize(state, observed_at=observed_at)
    decision["epistemic_consumer"] = dict(consumer_meta)
    direction = str(decision["direction"])
    entry, stop, target = _market_geometry(snapshot, direction)

    # If Belief direction is valid but current market geometry cannot be built,
    # fail closed. Geometry is an execution prerequisite, not a second direction.
    geometry_ok = direction == "FLAT" or all(value is not None for value in (entry, stop, target))
    if not geometry_ok:
        decision = {
            **decision,
            "direction": "FLAT",
            "reasons": [*list(decision.get("reasons") or []), "market_geometry_unavailable"],
        }
        direction = "FLAT"
        entry = stop = target = None

    legacy_learning = lifecycle.learning_state(list((history or {}).get("trades") or []))
    metadata: dict[str, Any] = {
        "market": "FX_SPOT",
        "currency_pair": "EUR/USD",
        "rollout_stage": "paper",
        "decision_source": "NATIVE",
        "belief": {
            "mode": "WITH",
            "decision_influence": True,
            "source": "CF-07 Epistemic Consumer Interface over BriefRooms Belief Core",
            "consumer": "DAILY_EURUSD",
            "trade_execution_authority": False,
        },
        "belief_pipeline": {
            "architecture": "BELIEF_FIRST_V1",
            "order": [*decision["routing"][:-1], "EPISTEMIC_CONSUMER_INTERFACE", decision["routing"][-1]],
            "consumer_contract": consumer_meta.get("contract"),
            "consumer_available": consumer_meta.get("available"),
            "raw_market_direction_before_belief": False,
            "final_decision_after_belief": True,
            "missing_data_is_neutral_vote": False,
        },
        "final_decision": dict(decision),
        "candidate": {
            "source": "NATIVE",
            "direction": direction,
            "score": float(decision["score"]),
            "confidence": float(decision["confidence"]),
            "accepted": bool(direction in {"LONG", "SHORT"} and allow_entry),
            "gate_reasons": (
                []
                if direction in {"LONG", "SHORT"} and allow_entry
                else ["entry_disabled_this_cycle"]
                if not allow_entry
                else list(decision.get("reasons") or [])
            ),
        },
        "direction_authority": {
            "owner": "NATIVE_DAILY_EURUSD_BELIEF_FIRST_DECISION_ENGINE",
            "single_owner": True,
            "source": "BELIEF_CORE",
            "direction": direction,
            "allowed": True,
            "rule": "Final LONG/SHORT/FLAT is synthesized only after Belief Core; no shadow/fallback may manufacture direction.",
        },
        "shadow_boundary": {
            "A_B_C": {"production_direction_authority": False, "production_timing_authority": False},
            "FSE": {"production_direction_authority": False, "production_timing_authority": False},
            "EURUSD_X": {"production_direction_authority": False, "production_timing_authority": False},
            "A_TECHNICAL_FALLBACK": {"production_direction_authority": False},
            "LOW_EDGE_LEARNING_EXPLORATION": {"production_direction_authority": False},
        },
        "contextual_entry_policy": {
            "mode": "LEGACY_V18_CONTEXTUAL_LEARNER",
            "status": "SHADOW_AFTER_BELIEF_FIRST_MIGRATION",
            "decision_influence": False,
            "direction_mutation_allowed": False,
            "timing_mutation_allowed": False,
            "reason": "Legacy learner was trained under pre-v1.9 decision semantics; production authority reset prospectively.",
            "FSE_production_influence": False,
        },
        "same_thesis_reentry_guard": {
            "enabled": True,
            "evaluated": False,
            "blocked": False,
            "reason": "no_directional_candidate" if direction == "FLAT" else "awaiting_execution_admission",
            "direction_mutation_allowed": False,
        },
        "learning": {
            **legacy_learning,
            "production_direction_learning_applied": False,
            "note": "Legacy local adaptive weights remain audit history only; v1.9 direction consumes Belief Core.",
        },
        "risk": {
            "atr_30m_window": 26,
            "atr_multiple": 1.35,
            "risk_floor_percent": 0.0027,
            "reward_risk": 1.8,
            "soft_horizon_hours": 24.0,
            "hard_horizon_hours": 27.0,
        },
        "data": {
            "market_geometry_source": "EURUSD 30m market snapshot",
            "directional_evidence_source": "BELIEF_CORE_STATE",
            "executable_bid_ask_available": False,
        },
    }

    # Preserve the macro fast lane only for open-position/risk-management
    # consumers. It is explicitly NOT a source of v1.9 final direction.
    try:
        risk_context = legacy_macro_context.context(observed_at)
    except Exception:
        risk_context = {"available": False, "reason": "risk_context_unavailable"}
    metadata["belief_macro"] = {
        **dict(risk_context),
        "direction_decision_influence": False,
        "role": "OPEN_POSITION_RISK_ONLY",
    }

    provisional = DailyEngineOutput(
        instrument="EUR/USD",
        timestamp=observed_at.isoformat().replace("+00:00", "Z"),
        direction=direction,
        score=float(decision["score"]),
        confidence=float(decision["confidence"]),
        entry=entry,
        stop=stop,
        target=target,
        horizon="intraday_to_27h",
        engine_version=ENGINE_VERSION,
        status="SIGNAL" if direction in {"LONG", "SHORT"} and allow_entry else "NO_TRADE",
        decision_mode="WITH",
        metadata=v14._with_dynamic_metadata(metadata),
    ).validate()

    if allow_entry and provisional.direction in {"LONG", "SHORT"}:
        md = dict(provisional.metadata)
        md["execution_admission"] = _execution_admission(
            provisional,
            history,
            observed_at=observed_at,
        )
        guard = (md["execution_admission"].get("same_thesis_reentry_guard") or {})
        if guard:
            md["same_thesis_reentry_guard"] = dict(guard)
        provisional = _clone(provisional, metadata=md)
    elif not allow_entry:
        md = dict(provisional.metadata)
        md["execution_admission"] = {
            "allowed": False,
            "status": "DISABLED",
            "reasons": ["entry_disabled_this_cycle"],
            "final_direction_preserved": provisional.direction,
            "direction_mutation_allowed": False,
        }
        provisional = _clone(provisional, metadata=md)

    return provisional


def _execution_blocked_output(candidate: DailyEngineOutput) -> DailyEngineOutput:
    metadata = dict(candidate.metadata)
    final = dict(metadata.get("final_decision") or {})
    metadata["execution_blocked_decision"] = {
        "direction": final.get("direction", candidate.direction),
        "score": final.get("score", candidate.score),
        "confidence": final.get("confidence", candidate.confidence),
        "preserved": True,
    }
    return _clone(
        candidate,
        direction="FLAT",
        entry=None,
        stop=None,
        target=None,
        status="NO_TRADE",
        metadata=metadata,
    )


def prepare_entry_candidate(
    candidate: DailyEngineOutput,
    monitor_bars: Any,
    observed_at: datetime,
) -> DailyEngineOutput:
    if candidate.direction not in {"LONG", "SHORT"}:
        return candidate
    admission = (candidate.metadata or {}).get("execution_admission") or {}
    if admission.get("allowed") is not True:
        return _execution_blocked_output(candidate)

    prepared = _epe_prepare(candidate, monitor_bars, observed_at)
    if prepared.direction not in {"LONG", "SHORT"}:
        metadata = dict(prepared.metadata)
        metadata.setdefault("execution_blocked_decision", {
            "direction": candidate.direction,
            "score": candidate.score,
            "confidence": candidate.confidence,
            "preserved": True,
        })
        return _clone(prepared, metadata=metadata)
    return _clone(prepared)


def _open_output_v19(
    candidate: DailyEngineOutput,
    position: Mapping[str, Any],
    mark_price: float,
) -> DailyEngineOutput:
    """Preserve legacy position facts while projecting them through v1.9 runtime."""
    output = _v18_open_output(candidate, position, mark_price)
    metadata = dict(output.metadata)
    metadata["runtime_projection"] = {
        "engine_version": ENGINE_VERSION,
        "decision_mode": "WITH",
        "historical_position_rewritten": False,
    }
    return _clone(output, metadata=metadata)


def _closed_output_v19(
    candidate: DailyEngineOutput,
    trade: Mapping[str, Any],
    history: Mapping[str, Any],
) -> DailyEngineOutput:
    output = _v18_closed_output(candidate, trade, history)
    metadata = dict(output.metadata)
    metadata["runtime_projection"] = {
        "engine_version": ENGINE_VERSION,
        "decision_mode": "WITH",
        "historical_trade_rewritten": False,
    }
    return _clone(output, metadata=metadata)


def _fresh_policy_shadow(
    candidate: DailyEngineOutput,
    snapshot: Any,
    monitor_bars: Any,
    observed_at: datetime,
) -> DailyEngineOutput:
    metadata = dict(candidate.metadata)
    metadata["contextual_entry_policy"] = {
        "mode": "LEGACY_V18_CONTEXTUAL_LEARNER",
        "status": "SHADOW_AFTER_BELIEF_FIRST_MIGRATION",
        "decision_influence": False,
        "direction_mutation_allowed": False,
        "timing_mutation_allowed": False,
        "reason": "Legacy learner was trained under pre-v1.9 decision semantics; production authority reset prospectively.",
        "FSE_production_influence": False,
    }
    return _clone(candidate, metadata=metadata)


def _resume_pending_v19(
    candidate: DailyEngineOutput,
    pending: Mapping[str, Any],
    snapshot: Any,
    monitor_bars: Any,
    observed_at: datetime,
) -> DailyEngineOutput:
    metadata = dict(candidate.metadata)
    metadata["contextual_entry_policy"] = {
        "mode": "LEGACY_V18_CONTEXTUAL_LEARNER",
        "status": "LEGACY_PENDING_CANCELLED_ON_V19_ACTIVATION",
        "decision_influence": False,
        "direction_mutation_allowed": False,
        "timing_mutation_allowed": False,
        "prior_pending_cancelled": True,
        "cancelled_policy_id": pending.get("policy_id"),
        "cancellation_reason": "belief_first_semantics_changed_prospectively",
        "historical_state_rewritten": False,
    }
    return _clone(candidate, metadata=metadata)


def _install() -> None:
    # v18 retains lifecycle/open-position management. Replace only the
    # new-entry decision/timing hooks with the Belief-first v1.9 contract.
    v18.build_output = build_output
    v18._fresh_policy = _fresh_policy_shadow
    v18._resume_pending = _resume_pending_v19
    v18._original_prepare_entry_candidate = prepare_entry_candidate
    v18._open_output = _open_output_v19
    v18._closed_output = _closed_output_v19

    base.ENGINE_VERSION = ENGINE_VERSION
    base.fetch_snapshot = fetch_snapshot
    base.build_output = build_output
    base.prepare_entry_candidate = prepare_entry_candidate
    base._open_output = _open_output_v19
    base._closed_output = _closed_output_v19
    base.run_cycle = v18.run_cycle


_install()


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
