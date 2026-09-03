#!/usr/bin/env python3
"""Engine-owned GPW Daily risk policy for PR-C.

The thresholds remain GPW-owned and are read from the existing GPW configs.
The shared risk-policy contract standardizes only the assessment output.

The mandatory-final-selector policy is deliberately scoped to decisions tagged
``MANDATORY_DAILY_FINAL``. It must never silently tighten the primary GPW path.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

try:
    from risk_policy_contract import RiskAssessment, RiskCheck, build_assessment
except ModuleNotFoundError:
    from scripts.risk_policy_contract import RiskAssessment, RiskCheck, build_assessment

ENGINE_ID = "gpw_daily"
POLICY_ID = "gpw-daily-risk-policy-v1"
ROOT = Path(__file__).resolve().parents[1]
MANDATORY_POLICY_PATH = ROOT / "data/investments/gpw_mandatory_daily_config.json"


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mandatory_policy() -> dict[str, Any]:
    try:
        value = json.loads(MANDATORY_POLICY_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _limits(config: Mapping[str, Any], *, include_mandatory: bool) -> dict[str, Any]:
    max_risk_values = [float(config.get("maximum_risk_percent", 0.07))]
    mandatory = _mandatory_policy() if include_mandatory else {}
    if include_mandatory and mandatory.get("enabled"):
        for key in ("maximum_candidate_risk_percent", "maximum_published_risk_percent"):
            if mandatory.get(key) is not None:
                max_risk_values.append(float(mandatory[key]))
    return {
        "minimum_reward_risk": float(config.get("minimum_reward_risk", 1.5)),
        "maximum_risk_percent": min(max_risk_values),
        "direction": "LONG_ONLY",
        "horizon": "1-2 GPW sessions",
        "mandatory_final_selector": bool(include_mandatory),
        "mandatory_policy_schema": mandatory.get("schema_version") if include_mandatory else None,
    }


def evaluate(
    payload: Mapping[str, Any],
    *,
    assessed_at: str,
    config: Mapping[str, Any],
) -> RiskAssessment:
    decision = str(payload.get("decision") or "").upper()
    action = "LONG" if decision == "TRANSAKCJA" else "FLAT" if decision == "BRAK_TRANSAKCJI" else decision
    selection = payload.get("selection") if isinstance(payload.get("selection"), Mapping) else {}
    include_mandatory = str(selection.get("selection_mode") or "").upper() == "MANDATORY_DAILY_FINAL"
    limits = _limits(config, include_mandatory=include_mandatory)
    policy_version = f"{config.get('policy_version') or 'gpw-policy-unknown'}|risk-v1"
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
            "1-2 GPW sessions",
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
