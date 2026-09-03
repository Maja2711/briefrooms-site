#!/usr/bin/env python3
"""Prospective producer adapters for PR-C DecisionEnvelope + RiskPolicy.

The adapters sit at the persistence boundary. They do not select instruments,
change scores, change SL/TP, or invent risk limits. A supported economic decision
is persisted only after the source engine's own RiskPolicy and P0.2 market-data
lineage both pass.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

try:
    from decision_envelope import build_envelope
    from gpw_daily_risk_policy import evaluate as evaluate_gpw_risk
    from market_snapshot_adapters import equity_execution_policy
    from risk_policy_contract import RiskPolicyError
    from us_daily_stock_risk_policy import evaluate as evaluate_us_risk
except ModuleNotFoundError:
    from scripts.decision_envelope import build_envelope
    from scripts.gpw_daily_risk_policy import evaluate as evaluate_gpw_risk
    from scripts.market_snapshot_adapters import equity_execution_policy
    from scripts.risk_policy_contract import RiskPolicyError
    from scripts.us_daily_stock_risk_policy import evaluate as evaluate_us_risk

COVERAGE_CANONICALIZED = "CANONICALIZED"
COVERAGE_PARTIAL = "PARTIAL"
COVERAGE_NOT_YET = "NOT_YET_CANONICALIZED"

_LINEAGE_KEYS = (
    "decision_envelope",
    "decision_envelope_id",
    "decision_envelope_hash",
    "risk_assessment",
    "risk_assessment_id",
    "risk_assessment_hash",
    "risk_policy_id",
    "risk_policy_version",
    "market_snapshot_id",
    "instrument_id",
)


def _position_plan(selection: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: selection.get(key)
        for key in (
            "reference_price", "entry_zone", "skip_above", "stop", "target",
            "risk_percent", "reward_risk", "valid_until", "time_stop", "early_exit",
        )
        if selection.get(key) is not None
    }


def _attach_fields(
    payload: dict[str, Any],
    *,
    envelope,
    risk,
) -> dict[str, Any]:
    payload["decision_envelope"] = envelope.to_dict()
    payload["decision_envelope_id"] = envelope.envelope_id
    payload["decision_envelope_hash"] = envelope.envelope_hash
    payload["risk_assessment"] = risk.to_dict()
    payload["risk_assessment_id"] = risk.risk_assessment_id
    payload["risk_assessment_hash"] = risk.risk_assessment_hash
    payload["risk_policy_id"] = risk.policy_id
    payload["risk_policy_version"] = risk.policy_version
    payload["market_snapshot_id"] = envelope.market_snapshot_id
    payload["instrument_id"] = envelope.instrument_id
    payload["decision_envelope_coverage"] = COVERAGE_CANONICALIZED
    payload.setdefault("data_quality", {})["decision_envelope"] = COVERAGE_CANONICALIZED
    selection = payload.get("selection")
    if isinstance(selection, dict):
        selection["decision_envelope_id"] = envelope.envelope_id
        selection["risk_assessment_id"] = risk.risk_assessment_id
        if envelope.market_snapshot_id:
            selection["market_snapshot_id"] = envelope.market_snapshot_id
        if envelope.instrument_id:
            selection["instrument_id"] = envelope.instrument_id
    return payload


def _strip_deferred_lineage(payload: dict[str, Any], reason: str) -> dict[str, Any]:
    for key in _LINEAGE_KEYS:
        payload.pop(key, None)
    selection = payload.get("selection")
    if isinstance(selection, dict):
        selection.pop("decision_envelope_id", None)
        selection.pop("risk_assessment_id", None)
    payload["decision_envelope_coverage"] = COVERAGE_PARTIAL
    payload.setdefault("data_quality", {})["decision_envelope"] = reason
    return payload


def attach_gpw_decision_envelope(payload: dict[str, Any], gpw_module) -> dict[str, Any]:
    decision = str(payload.get("decision") or "").upper()
    if decision not in {"TRANSAKCJA", "BRAK_TRANSAKCJI"}:
        return payload
    decision_at = str(payload.get("generated_at") or "")
    if not decision_at:
        raise gpw_module.PublicationError("PR-C: GPW decision missing generated_at")
    config = gpw_module.load_config()
    risk = evaluate_gpw_risk(payload, assessed_at=decision_at, config=config)
    action = "LONG" if decision == "TRANSAKCJA" else "FLAT"
    selection = payload.get("selection") if isinstance(payload.get("selection"), Mapping) else {}

    canonical = None
    quality = None
    policy = None
    if action == "LONG":
        snapshot = selection.get("market_snapshot") if isinstance(selection.get("market_snapshot"), Mapping) else {}
        canonical = snapshot.get("canonical_market_snapshot") if isinstance(snapshot.get("canonical_market_snapshot"), Mapping) else None
        quality = snapshot.get("canonical_data_quality") if isinstance(snapshot.get("canonical_data_quality"), Mapping) else None
        settings = config.get("data_gates") or {}
        policy = equity_execution_policy(
            "gpw",
            max_age_seconds=float(settings.get("maximum_execution_quote_age_minutes", 20)) * 60.0,
            max_future_skew_seconds=float(settings.get("maximum_future_clock_skew_minutes", 2)) * 60.0,
        )

    try:
        envelope = build_envelope(
            engine_id="gpw_daily",
            engine_version=str(config.get("policy_version") or "gpw-policy-unknown"),
            decision_at=decision_at,
            native_decision=decision,
            action=action,
            risk_assessment=risk,
            position_plan=_position_plan(selection),
            market_snapshot=canonical,
            market_data_quality=quality,
            market_policy=policy,
            epistemic_state_id=selection.get("epistemic_state_id") or payload.get("epistemic_state_id"),
            horizon="1-2 GPW sessions",
            confidence=selection.get("score") or selection.get("conviction"),
        )
    except (ValueError, RiskPolicyError) as exc:
        raise gpw_module.PublicationError(f"PR-C GPW decision blocked: {exc}") from exc
    return _attach_fields(payload, envelope=envelope, risk=risk)


def attach_us_decision_envelope(payload: dict[str, Any], us_module) -> dict[str, Any]:
    position_action = str(payload.get("position_action") or "").upper()
    if position_action in {"HOLD", "CLOSED", "HOLD_MARK_ERROR"}:
        # P0.2 has not yet canonicalized the post-entry mark path. Never carry a
        # copied entry DecisionEnvelope forward as though it described HOLD/CLOSE.
        return _strip_deferred_lineage(payload, "DEFERRED_US_POSITION_MARK_LINEAGE")

    decision = str(payload.get("decision") or "").upper()
    if decision not in {"TRADE", "NO_TRADE"}:
        return payload
    decision_at = str(payload.get("generated_at") or "")
    if not decision_at:
        raise us_module.PublicationError("PR-C: US decision missing generated_at")
    config = us_module.load_config()
    risk = evaluate_us_risk(payload, assessed_at=decision_at, config=config)
    action = "LONG" if decision == "TRADE" else "FLAT"
    selection = payload.get("selection") if isinstance(payload.get("selection"), Mapping) else {}

    canonical = None
    quality = None
    policy = None
    if action == "LONG":
        snapshot = selection.get("market_snapshot") if isinstance(selection.get("market_snapshot"), Mapping) else {}
        canonical = snapshot.get("canonical_market_snapshot") if isinstance(snapshot.get("canonical_market_snapshot"), Mapping) else None
        quality = snapshot.get("canonical_data_quality") if isinstance(snapshot.get("canonical_data_quality"), Mapping) else None
        settings = config.get("data_gates") or {}
        policy = equity_execution_policy(
            "us",
            max_age_seconds=float(settings.get("maximum_execution_quote_age_minutes", 20)) * 60.0,
            max_future_skew_seconds=float(settings.get("maximum_future_clock_skew_minutes", 2)) * 60.0,
        )

    try:
        envelope = build_envelope(
            engine_id="us_daily_stock",
            engine_version=str(config.get("policy_version") or "us-policy-unknown"),
            decision_at=decision_at,
            native_decision=decision,
            action=action,
            risk_assessment=risk,
            position_plan=_position_plan(selection),
            market_snapshot=canonical,
            market_data_quality=quality,
            market_policy=policy,
            epistemic_state_id=selection.get("epistemic_state_id") or payload.get("epistemic_state_id"),
            horizon="1-2 US regular sessions",
            confidence=selection.get("score") or selection.get("conviction"),
        )
    except (ValueError, RiskPolicyError) as exc:
        raise us_module.PublicationError(f"PR-C US decision blocked: {exc}") from exc
    return _attach_fields(payload, envelope=envelope, risk=risk)


def _is_decision_path(path: Any, *, public_path: Any, history_dir: Any) -> bool:
    target = Path(path)
    return target == Path(public_path) or target.parent == Path(history_dir)


def install_gpw_persistence_guard(gpw_module) -> None:
    """Fail-closed guard used by both primary and mandatory GPW publishers."""
    if getattr(gpw_module, "_prc_decision_envelope_guard", False):
        return
    original = gpw_module.atomic_json

    def guarded(path, payload):
        if (
            isinstance(payload, dict)
            and _is_decision_path(path, public_path=gpw_module.PUBLIC_PATH, history_dir=gpw_module.HISTORY_DIR)
        ):
            attach_gpw_decision_envelope(payload, gpw_module)
        return original(path, payload)

    gpw_module.atomic_json = guarded
    gpw_module._prc_decision_envelope_guard = True


def install_us_persistence_guard(us_module) -> None:
    """Fail-closed guard for new US Daily entry/FLAT publication state."""
    if getattr(us_module, "_prc_decision_envelope_guard", False):
        return
    original = us_module.atomic_json

    def guarded(path, payload):
        if (
            isinstance(payload, dict)
            and _is_decision_path(path, public_path=us_module.PUBLIC_PATH, history_dir=us_module.HISTORY_DIR)
        ):
            attach_us_decision_envelope(payload, us_module)
        return original(path, payload)

    us_module.atomic_json = guarded
    us_module._prc_decision_envelope_guard = True


def coverage_report() -> dict[str, Any]:
    return {
        "contract_version": "briefrooms-decision-envelope-coverage-v1",
        "components": {
            "gpw_daily_final_decisions": {
                "status": COVERAGE_CANONICALIZED,
                "persistence_guard": "gpw_market_data import -> gpw.atomic_json",
                "risk_policy": "gpw-daily-risk-policy-v1",
            },
            "us_daily_new_entry_and_flat": {
                "status": COVERAGE_CANONICALIZED,
                "persistence_guard": "daily_stock_us_adapter -> us.atomic_json",
                "risk_policy": "us-daily-stock-risk-policy-v1",
            },
            "us_position_hold_close": {
                "status": COVERAGE_PARTIAL,
                "reason": "post-entry position mark is not yet a P0.2 canonical MarketSnapshot",
            },
            "eurusd_daily": {
                "status": COVERAGE_PARTIAL,
                "reason": "P0.2 active decision lifecycle migration is still pending",
            },
            "wes": {
                "status": COVERAGE_PARTIAL,
                "reason": "P0.2 WES MarketSnapshot attachment is still pending",
            },
            "brace_spx": {
                "status": COVERAGE_NOT_YET,
                "reason": "BRACE-SPX does not yet consume CanonicalMarketSnapshot at its decision boundary",
            },
        },
        "centralized_risk_limits": False,
        "legacy_backfill": False,
        "unknown_means_pass": False,
    }
