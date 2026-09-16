#!/usr/bin/env python3
"""ECB-aware Daily EUR/USD engine v1.7 with production Event Intelligence.

v1.7 keeps the v1.6 technical/lifecycle stack and adds two bounded event layers:
- ECB official monetary-policy context,
- shared production Event Intelligence interpreted with the DAILY engine profile.

A fresh adverse event may veto a new entry or close an existing position through the
canonical Daily lifecycle record. Missing/stale Event Intelligence is fail-neutral.
"""
from __future__ import annotations

from typing import Any, Mapping

from daily_engine_contract import DailyEngineOutput
import daily_eurusd_event_overlay as daily_event
import daily_eurusd_spot as base
import daily_eurusd_spot_v16 as v16  # installs v1.6 stack first
from daily_eurusd_policy import build_ecb_policy_context, policy_conflicts
import investment_event_engine_profiles as event_profiles

ENGINE_VERSION = "eurusd-daily-spot-v1.7.0"

_original_build_output = base.build_output
_original_open_output = base._open_output
_original_closed_output = base._closed_output
_original_evaluate_position = base.evaluate_position


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


def _flat_with_policy_block(candidate: DailyEngineOutput, metadata: Mapping[str, Any], reason: str) -> DailyEngineOutput:
    md = dict(metadata)
    original_candidate = dict(md.get("candidate") or {})
    md["policy_guarded_candidate"] = {
        **original_candidate,
        "effective_direction_before_policy": candidate.direction,
    }
    candidate_meta = dict(original_candidate)
    reasons = list(candidate_meta.get("gate_reasons") or [])
    if reason not in reasons:
        reasons.append(reason)
    candidate_meta.update({"accepted": False, "gate_reasons": reasons})
    md["candidate"] = candidate_meta
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
        metadata=md,
    ).validate()


def _flat_with_event_block(candidate: DailyEngineOutput, metadata: Mapping[str, Any], reason: str) -> DailyEngineOutput:
    md = dict(metadata)
    original_candidate = dict(md.get("candidate") or {})
    md["event_guarded_candidate"] = {
        **original_candidate,
        "effective_direction_before_event": candidate.direction,
    }
    candidate_meta = dict(original_candidate)
    reasons = list(candidate_meta.get("gate_reasons") or [])
    if reason not in reasons:
        reasons.append(reason)
    candidate_meta.update({"accepted": False, "gate_reasons": reasons})
    md["candidate"] = candidate_meta
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
        metadata=md,
    ).validate()


def apply_ecb_policy_gate(candidate: DailyEngineOutput, context: Mapping[str, Any], *, allow_entry: bool = True) -> DailyEngineOutput:
    metadata = dict(candidate.metadata)
    policy = dict(context)
    metadata["ecb_policy"] = policy
    metadata["central_bank_policy"] = {
        "mode": "ECB_OFFICIAL_DECISION_PLUS_MARKET_REACTION_V1",
        "decision_influence": bool(policy.get("event_active")),
        "new_entry_veto_enabled": True,
        "fail_closed_during_event_window": True,
    }

    data = dict(metadata.get("data") or {})
    data.update({
        "ecb_policy_coverage": bool(policy.get("coverage")),
        "ecb_policy_event_active": bool(policy.get("event_active")),
        "ecb_policy_status": str(policy.get("status") or "UNKNOWN"),
        "ecb_policy_consensus_claimed": False,
        "ecb_ois_repricing_claimed": False,
    })
    metadata["data"] = data

    if not allow_entry:
        policy["gate_evaluated"] = False
        policy["gate_reason"] = "entry_disabled_this_cycle"
        metadata["ecb_policy"] = policy
        return _clone(candidate, metadata=metadata)

    if candidate.direction not in {"LONG", "SHORT"}:
        policy["gate_evaluated"] = False
        policy["gate_reason"] = "no_directional_candidate"
        metadata["ecb_policy"] = policy
        return _clone(candidate, metadata=metadata)

    policy["gate_evaluated"] = True

    if policy.get("event_active") and policy.get("must_block_entry"):
        reason = str(policy.get("block_reason") or "ecb_policy_event_data_unavailable")
        policy["gate_blocked"] = True
        policy["gate_reason"] = reason
        metadata["ecb_policy"] = policy
        return _flat_with_policy_block(candidate, metadata, reason)

    if policy_conflicts(candidate.direction, policy):
        reason = "ecb_policy_conflict_veto"
        policy["gate_blocked"] = True
        policy["gate_reason"] = reason
        metadata["ecb_policy"] = policy
        return _flat_with_policy_block(candidate, metadata, reason)

    policy["gate_blocked"] = False
    policy["gate_reason"] = "ecb_policy_aligned_or_neutral"
    metadata["ecb_policy"] = policy
    return _clone(candidate, metadata=metadata)


def apply_event_intelligence_gate(candidate: DailyEngineOutput, context: Mapping[str, Any], *, allow_entry: bool = True) -> DailyEngineOutput:
    metadata = dict(candidate.metadata)
    overlay = dict(context)
    metadata["event_intelligence"] = overlay
    metadata["event_intelligence_policy"] = {
        "profile_version": event_profiles.PROFILE_VERSION,
        "engine_profile": event_profiles.DAILY,
        "role": event_profiles.profile(event_profiles.DAILY)["role"],
        "new_entry_veto_enabled": True,
        "existing_position_close_enabled": True,
        "missing_or_stale_snapshot": "FAIL_NEUTRAL",
    }

    if not allow_entry or candidate.direction not in {"LONG", "SHORT"}:
        overlay["gate_evaluated"] = False
        overlay["gate_reason"] = "entry_disabled_or_no_directional_candidate"
        metadata["event_intelligence"] = overlay
        return _clone(candidate, metadata=metadata)

    overlay["gate_evaluated"] = True
    if daily_event.entry_blocked(overlay, candidate.direction):
        reason = "event_intelligence_entry_block"
        overlay["gate_blocked"] = True
        overlay["gate_reason"] = reason
        metadata["event_intelligence"] = overlay
        return _flat_with_event_block(candidate, metadata, reason)

    overlay["gate_blocked"] = False
    overlay["gate_reason"] = "event_intelligence_aligned_neutral_or_below_threshold"
    metadata["event_intelligence"] = overlay
    return _clone(candidate, metadata=metadata)


def build_output(snapshot: Any, history: Mapping[str, Any] | None = None, *, allow_entry: bool = True) -> DailyEngineOutput:
    candidate = _original_build_output(snapshot, history, allow_entry=allow_entry)
    fx_rows = list((getattr(snapshot, "bars", {}) or {}).get(base.EURUSD) or [])
    observed_at = v16._parse(candidate.timestamp)
    if observed_at is None:
        context = {
            "schema_version": "eurusd-ecb-policy-context-v1",
            "enabled": True,
            "central_bank": "ECB",
            "event_active": False,
            "coverage": False,
            "must_block_entry": False,
            "status": "CANDIDATE_TIMESTAMP_UNAVAILABLE",
        }
    else:
        context = build_ecb_policy_context(observed_at, fx_rows)
    candidate = apply_ecb_policy_gate(candidate, context, allow_entry=allow_entry)

    event_context = (
        daily_event.build_context(observed_at)
        if observed_at is not None
        else {
            "schema_version": "daily-eurusd-event-overlay-v1",
            "enabled": True,
            "engine_profile": event_profiles.DAILY,
            "coverage": False,
            "decision_influence": False,
            "fail_neutral": True,
            "status": "CANDIDATE_TIMESTAMP_UNAVAILABLE",
            "score": None,
        }
    )
    return apply_event_intelligence_gate(candidate, event_context, allow_entry=allow_entry)


def _evaluate_position(position: Mapping[str, Any], bars: Any, observed_at: Any) -> dict[str, Any] | None:
    normal_exit = _original_evaluate_position(position, bars, observed_at)
    if normal_exit is not None:
        return normal_exit
    return daily_event.maybe_close_position(position, bars, observed_at)


def _open_output(candidate: DailyEngineOutput, position: Mapping[str, Any], mark_price: float) -> DailyEngineOutput:
    output = _original_open_output(candidate, position, mark_price)
    metadata = dict(output.metadata)
    policy = metadata.get("ecb_policy") if isinstance(metadata.get("ecb_policy"), Mapping) else {}
    position_direction = str(position.get("direction") or "").upper()
    metadata["open_position_policy_check"] = {
        "evaluated": bool(policy.get("event_active") and policy.get("coverage")),
        "direction": position_direction or None,
        "conflict": bool(policy_conflicts(position_direction, dict(policy))),
        "advisory_only": True,
        "note": "ECB entry veto stays advisory for existing positions; lifecycle/risk exits remain authoritative.",
    }
    event_context = metadata.get("event_intelligence") if isinstance(metadata.get("event_intelligence"), Mapping) else {}
    metadata["open_position_event_check"] = {
        "evaluated": bool(event_context.get("coverage")),
        "direction": position_direction or None,
        "decision": daily_event.position_decision(event_context, position_direction),
        "engine_profile": event_profiles.DAILY,
        "close_authority": True,
        "note": "Fresh Event Intelligence can close through the canonical Daily lifecycle record.",
    }
    return _clone(output, metadata=metadata)


def _closed_output(candidate: DailyEngineOutput, trade: Mapping[str, Any], history: Mapping[str, Any]) -> DailyEngineOutput:
    return _clone(_original_closed_output(candidate, trade, history))


def _install() -> None:
    base.ENGINE_VERSION = ENGINE_VERSION
    base.build_output = build_output
    base._open_output = _open_output
    base._closed_output = _closed_output
    base.evaluate_position = _evaluate_position


_install()


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
