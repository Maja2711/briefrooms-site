#!/usr/bin/env python3
"""Engine-owned US Daily Stock risk policy for PR-C.

No shared module owns these limits. They remain sourced from the existing US
Daily Stock configuration and are merely emitted through the common assessment
contract for lineage and auditability.
"""
from __future__ import annotations

from typing import Any, Mapping

try:
    from risk_policy_contract import RiskAssessment, RiskCheck, build_assessment
except ModuleNotFoundError:
    from scripts.risk_policy_contract import RiskAssessment, RiskCheck, build_assessment

ENGINE_ID = "us_daily_stock"
POLICY_ID = "us-daily-stock-risk-policy-v1"


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _limits(config: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "minimum_reward_risk": float(config.get("minimum_reward_risk", 1.5)),
        "maximum_risk_percent": float(config.get("maximum_risk_percent", 0.07)),
        "direction": "LONG_ONLY",
        "horizon": "1-2 US regular sessions",
        "one_open_position": True,
    }


def evaluate(
    payload: Mapping[str, Any],
    *,
    assessed_at: str,
    config: Mapping[str, Any],
) -> RiskAssessment:
    decision = str(payload.get("decision") or "").upper()
    action = "LONG" if decision == "TRADE" else "FLAT" if decision == "NO_TRADE" else decision
    limits = _limits(config)
    policy_version = f"{config.get('policy_version') or 'us-policy-unknown'}|risk-v1"
    if action == "FLAT":
        return build_assessment(
            engine_id=ENGINE_ID,
            policy_id=POLICY_ID,
            policy_version=policy_version,
            policy_inputs=limits,
            assessed_at=assessed_at,
            action=action,
            checks=(),
        )

    selection = payload.get("selection") if isinstance(payload.get("selection"), Mapping) else {}
    ref = _float(selection.get("reference_price"))
    stop = _float(selection.get("stop"))
    target = _float(selection.get("target"))
    rr = _float(selection.get("reward_risk"))
    risk = _float(selection.get("risk_percent"))
    if risk is None and ref and stop is not None:
        risk = (ref - stop) / ref
    entry_zone = selection.get("entry_zone") if isinstance(selection.get("entry_zone"), (list, tuple)) else None
    entry_low = _float(entry_zone[0]) if entry_zone and len(entry_zone) == 2 else None
    entry_high = _float(entry_zone[1]) if entry_zone and len(entry_zone) == 2 else None
    skip_above = _float(selection.get("skip_above"))

    checks = (
        RiskCheck("reference_price_positive", bool(ref and ref > 0), ref, 0.0, ">"),
        RiskCheck(
            "entry_zone_ordered",
            bool(entry_low and entry_high and 0 < entry_low <= entry_high),
            [entry_low, entry_high],
            "0 < low <= high",
            "geometry",
        ),
        RiskCheck("stop_below_reference", bool(ref and stop and 0 < stop < ref), stop, ref, "<"),
        RiskCheck("target_above_reference", bool(ref and target and target > ref), target, ref, ">"),
        RiskCheck(
            "minimum_reward_risk",
            rr is not None and rr >= limits["minimum_reward_risk"],
            rr,
            limits["minimum_reward_risk"],
            ">=",
        ),
        RiskCheck(
            "maximum_risk_percent",
            risk is not None and 0 < risk <= limits["maximum_risk_percent"],
            risk,
            limits["maximum_risk_percent"],
            "<=",
        ),
        RiskCheck(
            "skip_above_not_below_entry_high",
            skip_above is None or (entry_high is not None and skip_above >= entry_high),
            skip_above,
            entry_high,
            ">=",
        ),
        RiskCheck(
            "time_horizon_present",
            bool(selection.get("valid_until") or selection.get("time_stop")),
            selection.get("valid_until") or selection.get("time_stop"),
            "1-2 US regular sessions",
            "present",
        ),
    )
    return build_assessment(
        engine_id=ENGINE_ID,
        policy_id=POLICY_ID,
        policy_version=policy_version,
        policy_inputs=limits,
        assessed_at=assessed_at,
        action=action,
        checks=checks,
    )
