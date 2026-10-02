#!/usr/bin/env python3
"""Daily EUR/USD v1.8 — continuous autonomous contextual entry timing.

Direction remains owned by the Daily EUR/USD directional engine. This layer only
chooses how an already-admitted LONG/SHORT thesis is entered:
- CONTINUATION_NOW,
- PULLBACK_20/35/50_ATR,
- FLAT.

Authority is earned prospectively from context-similar counterfactual outcomes.
The learner never reverses direction and never executes a trade itself; verified
fills remain owned by EPE and the canonical Daily lifecycle.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from belief_market_data_adapter import Bar
from daily_engine_contract import DailyEngineOutput
import daily_eurusd_lifecycle as lifecycle
import daily_eurusd_macro_event_risk as macro_event_risk
import daily_eurusd_spot as base
import daily_eurusd_spot_v17 as v17  # installs the complete v1.7 production stack first
import daily_eurusd_contextual_policy_learning as contextual
import execution_price_engine as epe

ENGINE_VERSION = "eurusd-daily-spot-v1.8.0"
CONTEXTUAL_STATE_PATH = Path("data/investments/eurusd_contextual_entry_policy_learning.json")
FSE_PUBLIC_PATH = Path("data/investments/fse_public.json")

_original_build_output = base.build_output
_original_prepare_entry_candidate = base.prepare_entry_candidate
_original_create_position = base.create_position
_original_position_from_output = base.position_from_output
_original_evaluate_position = base.evaluate_position
_original_open_output = base._open_output
_original_closed_output = base._closed_output


def _clone(
    output: DailyEngineOutput,
    *,
    direction: str | None = None,
    entry: float | None | object = ...,
    stop: float | None | object = ...,
    target: float | None | object = ...,
    status: str | None = None,
    timestamp: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> DailyEngineOutput:
    new_direction = direction if direction is not None else output.direction
    new_entry = output.entry if entry is ... else entry
    new_stop = output.stop if stop is ... else stop
    new_target = output.target if target is ... else target
    return DailyEngineOutput(
        instrument=output.instrument,
        timestamp=timestamp or output.timestamp,
        direction=new_direction,
        score=float(output.score),
        confidence=float(output.confidence),
        entry=new_entry,
        stop=new_stop,
        target=new_target,
        horizon=output.horizon,
        engine_version=ENGINE_VERSION,
        status=status or output.status,
        decision_mode=output.decision_mode,
        metadata=dict(metadata if metadata is not None else output.metadata),
    ).validate()


def _load_mapping(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _belief_state() -> dict[str, Any]:
    raw = str(os.environ.get("BELIEF_CORE_STATE") or "").strip()
    return _load_mapping(Path(raw)) if raw else {}


def _contextual_state() -> dict[str, Any]:
    return contextual.normalize_state(_load_mapping(CONTEXTUAL_STATE_PATH))


def _policy_block(
    recommendation: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    status: str,
    pending: Mapping[str, Any] | None = None,
    cancellation_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "eurusd-contextual-entry-production-v1",
        "mode": "CONTINUOUS_AUTONOMOUS_PROMOTION",
        "status": status,
        "direction_owner": "Daily EURUSD direction engine",
        "execution_owner": "EPE + Daily EURUSD lifecycle",
        "direction_mutation_allowed": False,
        "research_reversal_authoritative": False,
        "automatic_promotion": True,
        "automatic_rollback": True,
        "authority_level": recommendation.get("authority_level", "SHADOW"),
        "authority_fraction": recommendation.get("authority_fraction", 0.0),
        "evidence_confidence": recommendation.get("evidence_confidence", 0.0),
        "recommended_policy_id": recommendation.get("recommended_policy_id"),
        "recommended_family": recommendation.get("recommended_family"),
        "expected_r": recommendation.get("expected_r"),
        "edge_vs_second_r": recommendation.get("edge_vs_second_r"),
        "application_edge_hurdle_r": recommendation.get("application_edge_hurdle_r"),
        "effective_policy_stats": recommendation.get("policy_stats"),
        "application_model": "deterministic_evidence_hurdle_not_random_mixing",
        "context": {
            "observed_at": context.get("observed_at"),
            "reference_mid": context.get("reference_mid"),
            "atr_30m": context.get("atr_30m"),
            "features": dict(context.get("features") or {}),
            "fse_regime": (context.get("fse") or {}).get("regime") if isinstance(context.get("fse"), Mapping) else None,
        },
        "pending_entry": dict(pending) if isinstance(pending, Mapping) else None,
        "cancellation_reason": cancellation_reason,
    }


def _live_recommendation(
    candidate: DailyEngineOutput,
    snapshot: Any,
    monitor_bars: Sequence[Bar],
    observed_at: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    reference_mid = float(monitor_bars[-1].close)
    context = contextual.build_context(
        spot=candidate.to_dict(),
        rows_30m=list((getattr(snapshot, "bars", {}) or {}).get(base.EURUSD) or []),
        when=observed_at,
        belief_state=_belief_state(),
        fse_public=_load_mapping(FSE_PUBLIC_PATH),
        reference_mid=reference_mid,
    )
    recommendation = contextual.recommend_policy(_contextual_state(), context)
    return recommendation, context


def _flat(
    candidate: DailyEngineOutput,
    recommendation: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    status: str,
    policy_status: str,
    pending: Mapping[str, Any] | None = None,
    cancellation_reason: str | None = None,
) -> DailyEngineOutput:
    metadata = dict(candidate.metadata)
    metadata["contextual_entry_policy"] = _policy_block(
        recommendation,
        context,
        status=policy_status,
        pending=pending,
        cancellation_reason=cancellation_reason,
    )
    return _clone(
        candidate,
        direction="FLAT",
        entry=None,
        stop=None,
        target=None,
        status=status,
        metadata=metadata,
    )


def _pullback_depth(policy_id: str) -> float | None:
    if not str(policy_id).startswith("PULLBACK_") or not str(policy_id).endswith("_ATR"):
        return None
    raw = str(policy_id)[len("PULLBACK_") : -len("_ATR")]
    try:
        return float(int(raw)) / 100.0
    except (TypeError, ValueError):
        return None


def _apply_recommendation(
    candidate: DailyEngineOutput,
    recommendation: Mapping[str, Any],
    context: Mapping[str, Any],
    observed_at: datetime,
) -> DailyEngineOutput:
    level = str(recommendation.get("authority_level") or "SHADOW").upper()
    policy_id = str(recommendation.get("recommended_policy_id") or "CONTINUATION_NOW").upper()

    metadata = dict(candidate.metadata)
    metadata["contextual_entry_policy"] = _policy_block(
        recommendation,
        context,
        status="SHADOW_OBSERVE" if level == "SHADOW" else "AUTHORITATIVE",
    )
    candidate = _clone(candidate, metadata=metadata)

    if level == "SHADOW" or recommendation.get("decision_influence") is not True:
        return candidate
    if policy_id == "CONTINUATION_NOW":
        metadata = dict(candidate.metadata)
        block = dict(metadata["contextual_entry_policy"])
        block["status"] = "APPLIED_NOW"
        metadata["contextual_entry_policy"] = block
        return _clone(candidate, metadata=metadata)
    if policy_id == "FLAT":
        return _flat(
            candidate,
            recommendation,
            context,
            status="NO_TRADE",
            policy_status="APPLIED_FLAT",
        )

    depth = _pullback_depth(policy_id)
    if depth is None:
        # Research-only reversal or any unknown policy can never mutate direction.
        return _flat(
            candidate,
            recommendation,
            context,
            status="NO_TRADE",
            policy_status="NON_DIRECTIONAL_FAIL_CLOSED",
            cancellation_reason="non_production_or_unknown_policy",
        )

    reference_mid = float(context["reference_mid"])
    atr_value = float(context["atr_30m"])
    trigger_mid = (
        reference_mid - depth * atr_value
        if candidate.direction == "LONG"
        else reference_mid + depth * atr_value
    )
    risk_distance = abs(float(candidate.entry) - float(candidate.stop))
    reward_risk = abs(float(candidate.target) - float(candidate.entry)) / max(risk_distance, 1e-12)
    pending = {
        "schema_version": "eurusd-contextual-pending-entry-v1",
        "policy_id": policy_id,
        "direction": candidate.direction,
        "created_at": contextual.iso_z(observed_at),
        "expires_at": contextual.iso_z(observed_at + timedelta(hours=contextual.HORIZON_HOURS)),
        "reference_mid": round(reference_mid, 8),
        "trigger_mid": round(trigger_mid, 8),
        "pullback_depth_atr": depth,
        "atr_30m": round(atr_value, 8),
        "risk_distance": round(risk_distance, 8),
        "reward_risk": round(reward_risk, 6),
        "entry_score": round(float(candidate.score), 4),
        "entry_confidence": round(float(candidate.confidence), 4),
        "decision_source": (candidate.metadata or {}).get("decision_source"),
        "authority_level": level,
        "authority_fraction": recommendation.get("authority_fraction"),
        "evidence_confidence": recommendation.get("evidence_confidence"),
        "expected_r": recommendation.get("expected_r"),
        "edge_vs_second_r": recommendation.get("edge_vs_second_r"),
        "application_edge_hurdle_r": recommendation.get("application_edge_hurdle_r"),
    }
    return _flat(
        candidate,
        recommendation,
        context,
        status="WAITING_ENTRY",
        policy_status="WAITING_PULLBACK",
        pending=pending,
    )


def _fresh_policy(
    candidate: DailyEngineOutput,
    snapshot: Any,
    monitor_bars: Sequence[Bar],
    observed_at: datetime,
) -> DailyEngineOutput:
    recommendation, context = _live_recommendation(candidate, snapshot, monitor_bars, observed_at)
    return _apply_recommendation(candidate, recommendation, context, observed_at)


def _mark_prior_pending_cancelled(
    output: DailyEngineOutput,
    pending: Mapping[str, Any],
    *,
    reason: str,
) -> DailyEngineOutput:
    metadata = dict(output.metadata)
    block = metadata.get("contextual_entry_policy")
    block = dict(block) if isinstance(block, Mapping) else {}
    block.update({
        "automatic_rollback_applied": reason.startswith("automatic_rollback_"),
        "prior_pending_cancelled": True,
        "cancelled_pending_policy_id": pending.get("policy_id"),
        "cancellation_reason": reason,
    })
    metadata["contextual_entry_policy"] = block
    return _clone(output, metadata=metadata)

def _pending_from_previous(previous: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(previous, Mapping):
        return None
    metadata = previous.get("metadata") if isinstance(previous.get("metadata"), Mapping) else {}
    policy = metadata.get("contextual_entry_policy") if isinstance(metadata.get("contextual_entry_policy"), Mapping) else {}
    pending = policy.get("pending_entry") if isinstance(policy.get("pending_entry"), Mapping) else None
    return dict(pending) if isinstance(pending, Mapping) else None


def _pending_triggered(pending: Mapping[str, Any], current_mid: float) -> bool:
    direction = str(pending.get("direction") or "").upper()
    trigger = float(pending.get("trigger_mid"))
    if direction == "LONG":
        return float(current_mid) <= trigger
    if direction == "SHORT":
        return float(current_mid) >= trigger
    return False


def _resume_pending(
    candidate: DailyEngineOutput,
    pending: Mapping[str, Any],
    snapshot: Any,
    monitor_bars: Sequence[Bar],
    observed_at: datetime,
) -> DailyEngineOutput:
    expires = contextual.parse_time(pending.get("expires_at"))
    pending_direction = str(pending.get("direction") or "").upper()

    if expires is None or observed_at >= expires:
        fresh = _fresh_policy(candidate, snapshot, monitor_bars, observed_at) if candidate.direction in {"LONG", "SHORT"} else candidate
        metadata = dict(fresh.metadata)
        prior = metadata.get("contextual_entry_policy") if isinstance(metadata.get("contextual_entry_policy"), Mapping) else {}
        metadata["contextual_entry_policy"] = {
            **dict(prior),
            "prior_pending_cancelled": True,
            "cancellation_reason": "pending_expired",
        }
        return _clone(fresh, metadata=metadata)

    if candidate.direction != pending_direction:
        if candidate.direction in {"LONG", "SHORT"}:
            fresh = _fresh_policy(candidate, snapshot, monitor_bars, observed_at)
        else:
            fresh = candidate
        metadata = dict(fresh.metadata)
        prior = metadata.get("contextual_entry_policy") if isinstance(metadata.get("contextual_entry_policy"), Mapping) else {}
        metadata["contextual_entry_policy"] = {
            **dict(prior),
            "prior_pending_cancelled": True,
            "cancellation_reason": "direction_or_admission_invalidated",
        }
        return _clone(fresh, metadata=metadata)

    current_mid = float(monitor_bars[-1].close)
    recommendation, context = _live_recommendation(candidate, snapshot, monitor_bars, observed_at)
    active_level = str(recommendation.get("authority_level") or "SHADOW").upper()
    active_policy = str(recommendation.get("recommended_policy_id") or "CONTINUATION_NOW").upper()
    pending_policy = str(pending.get("policy_id") or "").upper()

    # Automatic rollback is evaluated continuously, not only when a new policy
    # is created. A frozen pullback order must not survive loss of authority.
    if active_level == "SHADOW" or recommendation.get("decision_influence") is not True:
        fresh = _apply_recommendation(candidate, recommendation, context, observed_at)
        return _mark_prior_pending_cancelled(
            fresh,
            pending,
            reason="automatic_rollback_authority_lost",
        )

    # If the context-local winner rotates while a pullback is waiting, cancel
    # the old frozen policy and immediately apply the new authoritative policy.
    if active_policy != pending_policy:
        fresh = _apply_recommendation(candidate, recommendation, context, observed_at)
        return _mark_prior_pending_cancelled(
            fresh,
            pending,
            reason="automatic_rollback_policy_changed",
        )

    if not _pending_triggered(pending, current_mid):
        return _flat(
            candidate,
            recommendation,
            context,
            status="WAITING_ENTRY",
            policy_status="WAITING_PULLBACK",
            pending=pending,
        )

    trigger = float(pending["trigger_mid"])
    risk = float(pending["risk_distance"])
    reward_risk = float(pending.get("reward_risk") or 1.8)
    if pending_direction == "LONG":
        stop = trigger - risk
        target = trigger + reward_risk * risk
    else:
        stop = trigger + risk
        target = trigger - reward_risk * risk

    metadata = dict(candidate.metadata)
    block = _policy_block(recommendation, context, status="PULLBACK_TRIGGERED", pending=pending)
    block["triggered_at"] = contextual.iso_z(observed_at)
    block["observed_mid_at_trigger"] = round(current_mid, 8)
    metadata["contextual_entry_policy"] = block
    return DailyEngineOutput(
        instrument=candidate.instrument,
        timestamp=contextual.iso_z(observed_at),
        direction=pending_direction,
        score=float(pending.get("entry_score") or candidate.score),
        confidence=float(pending.get("entry_confidence") or candidate.confidence),
        entry=round(trigger, 5),
        stop=round(stop, 5),
        target=round(target, 5),
        horizon=candidate.horizon,
        engine_version=ENGINE_VERSION,
        status="SIGNAL",
        decision_mode=candidate.decision_mode,
        metadata=metadata,
    ).validate()


def _create_position(payload: Mapping[str, Any]) -> dict[str, Any]:
    position = dict(_original_create_position(payload))
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
    policy = metadata.get("contextual_entry_policy") if isinstance(metadata.get("contextual_entry_policy"), Mapping) else None
    if isinstance(policy, Mapping):
        position["contextual_entry_policy_at_entry"] = {
            key: policy.get(key)
            for key in (
                "mode",
                "status",
                "authority_level",
                "authority_fraction",
                "evidence_confidence",
                "recommended_policy_id",
                "recommended_family",
                "expected_r",
                "edge_vs_second_r",
                "direction_mutation_allowed",
            )
        }
    position["engine_version"] = ENGINE_VERSION
    return position


def _evaluate_position(position: Mapping[str, Any], bars: Sequence[Bar], observed_at: datetime) -> dict[str, Any] | None:
    trade = _original_evaluate_position(position, bars, observed_at)
    if trade is None:
        trade = macro_event_risk.maybe_close_position(position, bars, observed_at)
    if trade is None:
        return None
    enriched = dict(trade)
    policy = position.get("contextual_entry_policy_at_entry")
    if isinstance(policy, Mapping):
        enriched["contextual_entry_policy_at_entry"] = dict(policy)
    return enriched


def _open_output(candidate: DailyEngineOutput, position: Mapping[str, Any], mark_price: float) -> DailyEngineOutput:
    output = _original_open_output(candidate, position, mark_price)
    metadata = dict(output.metadata)
    if isinstance(position.get("contextual_entry_policy_at_entry"), Mapping):
        metadata["contextual_entry_policy_at_entry"] = dict(position["contextual_entry_policy_at_entry"])
    return _clone(output, metadata=metadata)


def _closed_output(candidate: DailyEngineOutput, trade: Mapping[str, Any], history: Mapping[str, Any]) -> DailyEngineOutput:
    output = _original_closed_output(candidate, trade, history)
    return _clone(output)


def run_cycle(output_path: Path, history_path: Path, client: Any | None = None) -> DailyEngineOutput:
    injected_client = client is not None
    client = client or base.YahooChartClient(timeout=15)
    snapshot = base.fetch_snapshot(client)
    monitor_bars = client.bars(base.EURUSD, "5d", "1m")
    yahoo_observed_at = monitor_bars[-1].timestamp.astimezone(timezone.utc)
    observed_at = yahoo_observed_at

    # Canonical TP/SL authority is Yahoo EURUSD=X 1m OHLC, matching the fast
    # watcher and public Daily price path. Stooq is used only when Yahoo itself
    # is materially stale, so a different point feed cannot override healthy
    # Yahoo high/low history.
    yahoo_age_seconds = (datetime.now(timezone.utc) - yahoo_observed_at).total_seconds()
    if yahoo_age_seconds > 120.0:
        try:
            if injected_client:
                raise RuntimeError("skip_external_stooq_for_injected_test_client")
            stooq = epe.fetch_stooq_eurusd_quote(timeout=5)
            stooq_at = stooq.timestamp.astimezone(timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - stooq_at).total_seconds()
            if -30.0 <= age_seconds <= epe.DEFAULT_QUOTE_MAX_AGE_SECONDS:
                monitor_bars = list(monitor_bars)
                monitor_bars.append(Bar(
                    timestamp=stooq_at,
                    open=float(stooq.price),
                    high=float(stooq.price),
                    low=float(stooq.price),
                    close=float(stooq.price),
                ))
                monitor_bars.sort(key=lambda bar: bar.timestamp)
                observed_at = max(observed_at, stooq_at)
        except Exception:
            # Yahoo remains canonical. If it is stale and Stooq is unavailable,
            # lifecycle persistence waits for the next healthy observation.
            pass
    history = lifecycle.load_history(history_path)
    previous = base._load_json(output_path)

    previous_metadata = previous.get("metadata") if isinstance(previous, Mapping) and isinstance(previous.get("metadata"), Mapping) else {}
    previous_trade = previous_metadata.get("last_trade") if isinstance(previous_metadata, Mapping) else None
    if isinstance(previous_trade, Mapping):
        history = lifecycle.append_trade(history, previous_trade)
    position = _original_position_from_output(previous)

    if position:
        # Refresh the decision stack before evaluating the existing position.
        # The raw refreshed candidate remains non-executable (allow_entry=False),
        # but it is valid thesis-management information for the open trade.
        candidate = _original_build_output(snapshot, history, allow_entry=False)
        management_position = dict(position)
        candidate_meta = candidate.metadata.get("candidate") if isinstance(candidate.metadata, Mapping) else None
        belief_meta = candidate.metadata.get("belief_macro") if isinstance(candidate.metadata, Mapping) else None
        if isinstance(candidate_meta, Mapping):
            management_position["_management_candidate"] = dict(candidate_meta)
        if isinstance(belief_meta, Mapping):
            management_position["_belief_macro_context"] = dict(belief_meta)

        trade = _evaluate_position(management_position, monitor_bars, observed_at)
        if trade:
            history = lifecycle.append_trade(history, trade)
            lifecycle.save_history(history_path, history, observed_at)
            output = _closed_output(candidate, trade, history)
        else:
            mark_price = lifecycle.execution_exit_price(position, float(monitor_bars[-1].close))
            output = _open_output(candidate, position, mark_price)
            lifecycle.save_history(history_path, history, observed_at)
    else:
        candidate = _original_build_output(snapshot, history)
        pending = _pending_from_previous(previous)
        if pending is not None:
            candidate = _resume_pending(candidate, pending, snapshot, monitor_bars, observed_at)
        elif candidate.direction in {"LONG", "SHORT"}:
            candidate = _fresh_policy(candidate, snapshot, monitor_bars, observed_at)

        if candidate.direction in {"LONG", "SHORT"}:
            candidate = _original_prepare_entry_candidate(candidate, monitor_bars, observed_at)
        if candidate.direction in {"LONG", "SHORT"}:
            position = _create_position(candidate.to_dict())
            execution = candidate.metadata.get("execution_price_engine") if isinstance(candidate.metadata, Mapping) else None
            execution_mid = execution.get("selected_mid_price") if isinstance(execution, Mapping) else None
            initial_mid = float(execution_mid) if execution_mid is not None else float(monitor_bars[-1].close)
            initial_mark = lifecycle.execution_exit_price(position, initial_mid)
            output = _open_output(candidate, position, initial_mark)
        else:
            output = _clone(candidate)
        lifecycle.save_history(history_path, history, observed_at)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def _install() -> None:
    base.ENGINE_VERSION = ENGINE_VERSION
    base.create_position = _create_position
    base.evaluate_position = _evaluate_position
    base._open_output = _open_output
    base._closed_output = _closed_output
    base.run_cycle = run_cycle


_install()


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
