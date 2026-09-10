#!/usr/bin/env python3
"""ECB-aware Daily EUR/USD engine v1.7.

v1.7 keeps the v1.6 technical/lifecycle stack and adds an explicit ECB
monetary-policy event layer for fresh entries. During an ECB decision window
the engine fails closed until the official decision is available and a
post-decision EUR/USD reaction can be observed. A strong policy impulse that
conflicts with the technical direction vetoes the new entry.

The layer is conservative: it does not fabricate market consensus,
forward-guidance sentiment, OIS repricing or a rate differential.
"""
from __future__ import annotations

from typing import Any, Mapping

from daily_engine_contract import DailyEngineOutput
import daily_eurusd_spot as base
import daily_eurusd_spot_v16 as v16  # installs v1.6 stack first
from daily_eurusd_policy import build_ecb_policy_context, policy_conflicts

ENGINE_VERSION = "eurusd-daily-spot-v1.7.0"

_original_build_output = base.build_output
_original_open_output = base._open_output
_original_closed_output = base._closed_output


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
    return apply_ecb_policy_gate(candidate, context, allow_entry=allow_entry)


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
        "note": "v1.7 policy veto applies to fresh entries; existing positions remain owned by lifecycle/risk exits.",
    }
    return _clone(output, metadata=metadata)


def _closed_output(candidate: DailyEngineOutput, trade: Mapping[str, Any], history: Mapping[str, Any]) -> DailyEngineOutput:
    return _clone(_original_closed_output(candidate, trade, history))


def _install() -> None:
    base.ENGINE_VERSION = ENGINE_VERSION
    base.build_output = build_output
    base._open_output = _open_output
    base._closed_output = _closed_output


_install()


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
