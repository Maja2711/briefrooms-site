#!/usr/bin/env python3
"""Deterministic BRACE promotion, degradation and baseline fallback control."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping, Optional

from brace_portfolio_config import EngineConfig, METHODOLOGY_STATUSES
from brace_portfolio_data import canonical_sha256

CONTROL_STATUSES = {
    "CANDIDATE",
    "TESTING",
    "SHADOW",
    "PROBATIONARY_CONTROL",
    "ACTIVE_PAPER_CONTROL",
    "DEGRADED",
    "SAFE_MODE",
    "SUSPENDED",
    "FALLBACK_BASELINE",
}


def _float_value(metrics: Mapping[str, Any], key: str, default: float) -> float:
    value = metrics.get(key)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_value(metrics: Mapping[str, Any], key: str, default: int) -> int:
    value = metrics.get(key)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _date(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _code_sha() -> str:
    if os.environ.get("GITHUB_SHA"):
        return str(os.environ["GITHUB_SHA"])
    root = Path(__file__).resolve().parents[1]
    engine_paths = [
        str(path.relative_to(root)).replace("\\", "/")
        for path in sorted((root / "scripts").glob("brace_portfolio_*.py"))
    ]
    try:
        code_sha = subprocess.check_output(
            ["git", "log", "-1", "--format=%H", "--", *engine_paths],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if code_sha:
            return code_sha
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "UNKNOWN"


def _methodology(
    registry: MutableMapping[str, Any],
    methodology_id: str,
) -> MutableMapping[str, Any]:
    for item in registry.get("methodologies", []) or []:
        if item.get("methodology_id") == methodology_id:
            return item
    raise ValueError(f"Methodology is missing: {methodology_id}")


def fallback_reasons(
    operational: Mapping[str, Any],
    risk: Mapping[str, Any],
    config: EngineConfig,
) -> list[str]:
    reasons = []
    if operational.get("stale_data"):
        reasons.append("STALE_DATA")
    if _int_value(operational, "consecutive_workflow_failures", 0) >= 3:
        reasons.append("REPEATED_WORKFLOW_FAILURES")
    if operational.get("price_fx_inconsistent"):
        reasons.append("PRICE_OR_FX_INCONSISTENT")
    if operational.get("history_integrity_failed"):
        reasons.append("HISTORY_INTEGRITY_FAILED")
    if operational.get("unexplained_parameter_change"):
        reasons.append("UNEXPLAINED_PARAMETER_CHANGE")
    if operational.get("missing_rationale"):
        reasons.append("MISSING_DECISION_RATIONALE")
    if operational.get("risk_data_missing"):
        reasons.append("RISK_DATA_MISSING")
    if _float_value(risk, "maximum_drawdown", 0.0) < -config.max_expected_drawdown:
        reasons.append("MAXIMUM_DRAWDOWN_LIMIT_BREACHED")
    if _float_value(risk, "current_drawdown", 0.0) < -config.emergency_drawdown:
        reasons.append("EMERGENCY_DRAWDOWN")
    if _float_value(risk, "annual_turnover", 0.0) > config.max_annual_turnover:
        reasons.append("TURNOVER_LIMIT_BREACHED")
    if operational.get("live_validation_divergence"):
        reasons.append("LIVE_VALIDATION_DIVERGENCE")
    return sorted(set(reasons))


def historical_conditions(metrics: Mapping[str, Any], config: EngineConfig) -> Dict[str, bool]:
    return {
        "positive_after_costs": _float_value(metrics, "oos_return_after_costs", 0.0) > 0,
        "out_of_sample_beats_baseline": _float_value(
            metrics, "oos_excess_vs_baseline", 0.0
        )
        > 0,
        "no_lookahead_audit": bool(metrics.get("no_lookahead_audit")),
        "costs_and_fx_included": bool(metrics.get("costs_and_fx_included")),
        "regime_stability": bool(metrics.get("regime_stability")),
        "minimum_observations": _int_value(metrics, "observations", 0) >= 260,
        "parameter_neighborhood_stable": bool(
            metrics.get("parameter_neighborhood_stable")
        ),
        "not_single_instrument_dependent": bool(
            metrics.get("not_single_instrument_dependent")
        ),
        "reproducible_run": bool(metrics.get("reproducible_run")),
        "full_manifest": bool(metrics.get("full_manifest")),
    }


def risk_conditions(metrics: Mapping[str, Any], config: EngineConfig) -> Dict[str, bool]:
    return {
        "drawdown_within_limit": abs(
            _float_value(metrics, "maximum_drawdown", 1.0)
        )
        <= config.max_expected_drawdown,
        "drawdown_not_materially_worse": _float_value(
            metrics, "drawdown_disadvantage", 1.0
        )
        <= 0.02,
        "concentration_within_limits": bool(
            metrics.get("concentration_within_limits")
        ),
        "no_leverage": bool(metrics.get("no_leverage")),
        "no_short_sales": bool(metrics.get("no_short_sales")),
        "no_cfds": bool(metrics.get("no_cfds")),
        "turnover_within_limit": _float_value(metrics, "annual_turnover", 99.0)
        <= config.max_annual_turnover,
        "expected_shortfall_within_limit": abs(
            _float_value(metrics, "expected_shortfall", 1.0)
        )
        <= config.max_expected_drawdown,
        "downside_volatility_within_limit": _float_value(
            metrics, "downside_volatility", 1.0
        )
        <= 0.35,
    }


def shadow_conditions(
    metrics: Mapping[str, Any],
    started_at: Any,
    evaluated_at: datetime,
    config: EngineConfig,
) -> Dict[str, bool]:
    started = _date(started_at)
    days = (evaluated_at.date() - started).days if started else 0
    return {
        "minimum_calendar_days": days >= config.minimum_shadow_calendar_days,
        "minimum_decisions": _int_value(metrics, "decisions", 0)
        >= config.minimum_shadow_decisions,
        "minimum_completed_trades": _int_value(metrics, "completed_trades", 0)
        >= config.minimum_shadow_completed_trades,
        "return_advantage": _float_value(metrics, "shadow_return", 0.0)
        > _float_value(metrics, "baseline_return", 0.0),
        "risk_adjusted_advantage": _float_value(
            metrics, "shadow_risk_adjusted_return", 0.0
        )
        > _float_value(metrics, "baseline_risk_adjusted_return", 0.0),
        "drawdown_within_limit": abs(
            _float_value(metrics, "shadow_max_drawdown", 1.0)
        )
        <= config.max_expected_drawdown,
        "turnover_within_limit": _float_value(metrics, "shadow_turnover", 99.0)
        <= config.max_annual_turnover,
        "confidence_interval_positive": _float_value(
            metrics, "excess_return_ci_low", -1.0
        )
        > 0,
    }


def operational_conditions(metrics: Mapping[str, Any]) -> Dict[str, bool]:
    return {
        "no_critical_data_errors": _int_value(metrics, "critical_data_errors", 0)
        == 0,
        "decisions_reproducible": bool(metrics.get("decisions_reproducible")),
        "entry_history_unchanged": bool(metrics.get("entry_history_unchanged")),
        "timestamps_complete": bool(metrics.get("timestamps_complete")),
        "workflow_stable": bool(metrics.get("workflow_stable")),
        "public_internal_consistent": bool(
            metrics.get("public_internal_consistent")
        ),
        "integrity_tests_pass": bool(metrics.get("integrity_tests_pass")),
    }


def probation_conditions(
    metrics: Mapping[str, Any],
    started_at: Any,
    evaluated_at: datetime,
    config: EngineConfig,
) -> Dict[str, bool]:
    started = _date(started_at)
    days = (evaluated_at.date() - started).days if started else 0
    return {
        "minimum_calendar_days": days >= config.minimum_probation_calendar_days,
        "daily_rotation_limit": _int_value(metrics, "max_rotations_per_day", 99)
        <= config.max_probation_rotations_per_day,
        "weekly_change_limit": _int_value(
            metrics, "max_position_changes_per_week", 99
        )
        <= config.max_probation_position_changes_per_week,
        "weekly_turnover_limit": _float_value(
            metrics, "maximum_weekly_turnover", 99.0
        )
        <= config.max_weekly_turnover_probation,
        "new_position_limit": _float_value(
            metrics, "maximum_new_position_weight", 99.0
        )
        <= config.max_probation_new_position_weight,
        "minimum_confidence": _float_value(metrics, "minimum_confidence", 0.0)
        >= config.probationary_minimum_confidence,
        "no_full_portfolio_replacement": not bool(
            metrics.get("full_portfolio_replacement")
        ),
        "transaction_cost_buffer_applied": bool(
            metrics.get("transaction_cost_buffer_applied")
        ),
        "cooldown_applied": bool(metrics.get("cooldown_applied")),
        "material_advantage_required": bool(
            metrics.get("material_advantage_required")
        ),
        "no_fallback_trigger": not bool(metrics.get("fallback_trigger")),
    }


def _promotion_record(
    methodology_id: str,
    previous: str,
    new: str,
    evaluated_at: datetime,
    conditions: Mapping[str, Any],
    validation_window: Mapping[str, Any],
    reason: str,
    data_manifest: Mapping[str, Any],
) -> Dict[str, Any]:
    payload = {
        "methodology_id": methodology_id,
        "previous_status": previous,
        "new_status": new,
        "evaluated_at": evaluated_at.isoformat(timespec="seconds"),
        "conditions": conditions,
    }
    return {
        "promotion_id": "promotion-" + canonical_sha256(payload)[:16],
        **payload,
        "validation_window": dict(validation_window),
        "all_conditions_passed": all(
            value
            for group in conditions.values()
            for value in (group.values() if isinstance(group, Mapping) else [group])
        ),
        "reason": reason,
        "code_commit_sha": _code_sha(),
        "data_manifest_sha": canonical_sha256(data_manifest),
    }


def evaluate_and_apply(
    registry: Mapping[str, Any],
    validation: Mapping[str, Any],
    shadow: Mapping[str, Any],
    probation: Mapping[str, Any],
    operational: Mapping[str, Any],
    live_risk: Mapping[str, Any],
    config: EngineConfig,
    evaluated_at: Optional[datetime] = None,
    history: Optional[Mapping[str, Any]] = None,
) -> tuple[Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]]]:
    evaluated_at = evaluated_at or datetime.now(timezone.utc)
    updated = copy.deepcopy(dict(registry))
    methodology_id = str(updated.get("challenger_methodology_id"))
    challenger = _methodology(updated, methodology_id)
    baseline = _methodology(updated, str(updated.get("baseline_methodology_id")))
    current = str(challenger.get("status") or "CANDIDATE")
    if current not in METHODOLOGY_STATUSES | CONTROL_STATUSES:
        raise ValueError(f"Unsupported methodology status: {current}")

    fallback = fallback_reasons(operational, live_risk, config)
    record = None
    condition_groups: Dict[str, Any] = {}
    new_status = current
    reason = "No status change; promotion gates remain incomplete."

    if current in {"ACTIVE_PAPER_CONTROL", "PROBATIONARY_CONTROL"} and fallback:
        new_status = "FALLBACK_BASELINE"
        reason = "Automatic fallback: " + ", ".join(fallback)
        condition_groups = {"fallback": {item: True for item in fallback}}
        updated["controller_state"] = "FALLBACK_BASELINE"
        updated["champion_methodology_id"] = baseline["methodology_id"]
        baseline["status"] = "ACTIVE_BASELINE"
    elif current == "CANDIDATE":
        manifest_ok = bool(validation.get("full_manifest")) and bool(
            validation.get("reproducible_run")
        )
        condition_groups = {"candidate": {"manifest_and_hashes": manifest_ok}}
        if manifest_ok:
            new_status = "TESTING"
            reason = "Candidate manifest is complete and reproducible."
    elif current == "TESTING":
        historical = historical_conditions(validation, config)
        risks = risk_conditions(validation, config)
        condition_groups = {"historical": historical, "risk": risks}
        if all(historical.values()) and all(risks.values()):
            new_status = "SHADOW"
            reason = "Historical and risk validation passed; shadow begins."
            challenger.setdefault("validation_results", {})[
                "shadow_started_at"
            ] = evaluated_at.isoformat(timespec="seconds")
    elif current == "SHADOW":
        historical = historical_conditions(validation, config)
        risks = risk_conditions(validation, config)
        shadow_checks = shadow_conditions(
            shadow,
            (challenger.get("validation_results") or {}).get(
                "shadow_started_at"
            )
            or challenger.get("activated_at"),
            evaluated_at,
            config,
        )
        operations = operational_conditions(operational)
        condition_groups = {
            "historical": historical,
            "risk": risks,
            "shadow": shadow_checks,
            "operational": operations,
        }
        if all(value for group in condition_groups.values() for value in group.values()):
            new_status = "PROBATIONARY_CONTROL"
            reason = "All historical, risk, shadow and operational gates passed."
            challenger.setdefault("validation_results", {})[
                "probation_started_at"
            ] = evaluated_at.isoformat(timespec="seconds")
            updated["controller_state"] = "PROBATIONARY_CONTROL"
            updated["champion_methodology_id"] = methodology_id
    elif current == "PROBATIONARY_CONTROL":
        checks = probation_conditions(
            probation,
            (challenger.get("validation_results") or {}).get(
                "probation_started_at"
            ),
            evaluated_at,
            config,
        )
        operations = operational_conditions(operational)
        condition_groups = {"probation": checks, "operational": operations}
        if all(value for group in condition_groups.values() for value in group.values()):
            new_status = "ACTIVE_PAPER_CONTROL"
            reason = "Probation completed with every control and integrity gate passing."
            updated["controller_state"] = "ACTIVE_PAPER_CONTROL"
            updated["champion_methodology_id"] = methodology_id
            baseline["status"] = "FALLBACK_BASELINE"

    if new_status != current:
        challenger["status"] = new_status
        record = _promotion_record(
            methodology_id,
            current,
            new_status,
            evaluated_at,
            condition_groups,
            validation.get("validation_window") or {},
            reason,
            {
                "validation": validation,
                "shadow": shadow,
                "probation": probation,
                "operational": operational,
                "risk": live_risk,
            },
        )
    challenger.setdefault("validation_results", {})["latest_gate_evaluation"] = {
        "evaluated_at": evaluated_at.isoformat(timespec="seconds"),
        "current_status": new_status,
        "conditions": condition_groups,
        "reason": reason,
    }
    updated["generated_at"] = evaluated_at.isoformat(timespec="seconds")
    updated["data_freshness"] = "current"

    promotion_history = copy.deepcopy(
        dict(
            history
            or {
                "schema_version": "1.0.0",
                "generated_at": evaluated_at.isoformat(timespec="seconds"),
                "methodology_version": "promotion-controller-v1.0.0",
                "data_freshness": "current",
                "source_metadata": {
                    "controller": "brace_portfolio_promotion_controller.py"
                },
                "records": [],
            }
        )
    )
    if record and record["promotion_id"] not in {
        item.get("promotion_id") for item in promotion_history.get("records", [])
    }:
        promotion_history.setdefault("records", []).append(record)
    promotion_history["generated_at"] = evaluated_at.isoformat(timespec="seconds")
    return updated, promotion_history, record
