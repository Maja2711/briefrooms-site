#!/usr/bin/env python3
"""Governed weekly paper-trading runtime. Never sends broker orders."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import investments_weekly as legacy
import investments_weekly_v2 as v2
import investments_weekly_v3 as v3
import investments_weekly_v4 as v4
import investments_weekly_macro as macro
import no_retroactive_execution as no_retro
import execution_price_engine as epe

ROOT = Path(__file__).resolve().parents[1]
METHOD = ROOT / "data/investments/methodology.json"
POLICY = ROOT / "data/investments/multi_instrument_exposure_policy.json"
STATE = ROOT / "data/investments/multi_instrument_exposure_state_v5.json"
REPORT = ROOT / "data/investments/multi_instrument_exposure_report_v5.json"
VERSION = "5.9.1-experimental"

read, write, sf, parse_dt = v4.read, v4.write, v2.sf, v2.parse_dt


def open_position(item: Dict[str, Any]) -> bool:
    return sf(item.get("entry_price")) is not None and sf(item.get("exit_price")) is None and item.get("direction") in {"long", "short"}


def closed_position(item: Dict[str, Any]) -> bool:
    return sf(item.get("entry_price")) is not None and sf(item.get("exit_price")) is not None


def method_row(method: Dict[str, Any], instrument_id: str) -> Dict[str, Any]:
    return next((x for x in method.get("instruments", []) if str(x.get("id")) == instrument_id), {})


def gate(item: Dict[str, Any], method: Dict[str, Any], instrument_id: str) -> Tuple[bool, bool]:
    cfg = method_row(method, instrument_id)
    allowed = bool(cfg.get("enabled_for_new_positions", False))
    reason = str(cfg.get("validation_gate_reason") or "instrument_not_validated")
    if allowed:
        changed = item.get("validation_gate") != "enabled_for_paper_trading" or item.get("validation_gate_reason") is not None
        item["validation_gate"] = "enabled_for_paper_trading"
        item.pop("validation_gate_reason", None)
        return True, changed
    if open_position(item):
        changed = item.get("validation_gate") != "grandfathered_existing_position_no_new_entries"
        item.update(validation_gate="grandfathered_existing_position_no_new_entries", validation_gate_reason=reason)
        return False, changed
    changed = item.get("validation_gate") != reason or item.get("next_entry_status") != "no_trade"
    item.update(validation_gate=reason, validation_gate_reason=reason, next_entry_status="no_trade", pending_entry_decision=None)
    if not closed_position(item):
        item.update(direction="neutral", trade_status="no_trade", entry_price=None, entry_captured_at=None,
                    entry_source=None, risk_plan=None, result="no_trade", result_value=0.0, result_percent=0.0)
    return False, changed


def invalidation_exit(item: Dict[str, Any]) -> bool:
    reason = str(item.get("exit_reason") or "")
    status = str(item.get("risk_status") or "")
    return reason.startswith(("event_review_", "daily_model_")) or status in {
        "closed_by_material_event_review", "closed_by_daily_model_review"
    }


def lock_reentry(item: Dict[str, Any], week: Dict[str, Any], now: datetime) -> Tuple[bool, bool]:
    lock = item.get("reentry_lock") if isinstance(item.get("reentry_lock"), dict) else {}
    until = parse_dt(lock.get("until"))
    if lock.get("active") and until and now < until:
        return True, False
    if lock.get("active"):
        item["reentry_lock"] = {**lock, "active": False, "released_at": now.isoformat(timespec="seconds")}
    if closed_position(item) and invalidation_exit(item):
        end = parse_dt((week.get("market_window") or {}).get("exit_target_local")) or now.replace(hour=23, minute=59, second=59)
        item["reentry_lock"] = {
            "active": True, "scope": "same_week", "until": end.isoformat(timespec="seconds"),
            "reason": item.get("exit_reason") or item.get("risk_status"),
            "policy": "thesis_or_material_event_exit_blocks_same_week_reentry",
        }
        item.update(pending_entry_decision=None, next_entry_status="blocked_after_thesis_invalidation")
        return True, True
    return False, bool(lock.get("active"))


def _direction_from_score(score: float) -> str:
    return "long" if score > 0 else "short" if score < 0 else "neutral"


def directional_confirmation_sources(
    direction: str,
    fresh: Dict[str, Any],
    weekly: Dict[str, Any],
    macro_context: Optional[Dict[str, Any]],
    policy: Dict[str, Any],
) -> list[str]:
    cfg = policy.get("directional_admission") or {}
    macro_context = macro_context if isinstance(macro_context, dict) else {}
    names: list[str] = []
    daily_score = float(fresh.get("score") or 0.0)
    weekly_score = float(weekly.get("score") or 0.0)
    if fresh.get("data_quality") == "passed" and _direction_from_score(daily_score) == direction and abs(daily_score) >= float(cfg.get("daily_min_abs_score") or 25):
        names.append("daily")
    if weekly.get("data_quality") == "passed" and _direction_from_score(weekly_score) == direction and abs(weekly_score) >= float(cfg.get("weekly_min_abs_score") or 15):
        names.append("weekly")
    if macro_context.get("data_quality") == "passed" and str(macro_context.get("direction") or "") == direction:
        names.append("macro")
    ma = macro_context.get("ma_structure") if isinstance(macro_context.get("ma_structure"), dict) else {}
    ma_score = float(ma.get("score") or 0.0)
    if ma.get("data_quality") == "passed" and _direction_from_score(ma_score) == direction and abs(ma_score) >= float(cfg.get("ma_min_abs_score") or 1):
        names.append("ma_structure")
    return sorted(set(names))


def directional_admission(
    decision: Dict[str, Any],
    fresh: Dict[str, Any],
    weekly: Dict[str, Any],
    macro_context: Optional[Dict[str, Any]],
    policy: Dict[str, Any],
) -> Tuple[bool, list[str], Dict[str, Any]]:
    cfg = policy.get("directional_admission") or {}
    direction = str(decision.get("direction") or "neutral")
    sources = directional_confirmation_sources(direction, fresh, weekly, macro_context, policy)
    diagnostics = {
        "version": str(cfg.get("version") or "WES-1.2.0"),
        "direction": direction,
        "confirmations": len(sources),
        "confirmation_sources": sources,
        "execution_authority": decision.get("execution_authority"),
    }
    if not cfg.get("enabled", True):
        return True, [], diagnostics
    reasons: list[str] = []
    if direction not in {"long", "short"}:
        reasons.append("non_directional_candidate")
        return False, reasons, diagnostics
    if decision.get("execution_eligible") is False or str(decision.get("execution_authority") or "") == "challenger_shadow":
        reasons.append("candidate_has_no_execution_authority")
    minimum = int(cfg.get("minimum_confirmations") or 2)
    if len(sources) < minimum:
        reasons.append("insufficient_directional_confirmations")

    if cfg.get("block_against_aligned_daily_weekly", True):
        daily_score = float(fresh.get("score") or 0.0)
        weekly_score = float(weekly.get("score") or 0.0)
        daily_dir = _direction_from_score(daily_score)
        weekly_dir = _direction_from_score(weekly_score)
        daily_valid = fresh.get("data_quality") == "passed" and abs(daily_score) >= float(cfg.get("daily_min_abs_score") or 25)
        weekly_valid = weekly.get("data_quality") == "passed" and abs(weekly_score) >= float(cfg.get("weekly_min_abs_score") or 15)
        if daily_valid and weekly_valid and daily_dir == weekly_dir and daily_dir in {"long", "short"} and daily_dir != direction:
            reasons.append("candidate_opposes_aligned_daily_weekly")

    return not reasons, reasons, diagnostics


def no_trade(
    decision: Dict[str, Any],
    fresh: Dict[str, Any],
    weekly: Dict[str, Any],
    policy: Dict[str, Any],
    macro_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if str(decision.get("direction") or "neutral") not in {"long", "short"}:
        return decision
    cfg = policy.get("no_trade") or {}
    base = float(fresh.get("score") or 0)
    week = float(weekly.get("score") or 0) if weekly.get("data_quality") == "passed" else 0.0
    raw = abs(float(decision.get("raw_score") or 0))
    utility = float(decision.get("utility") or 0)
    floor = float(cfg.get("minimum_directional_raw_score") or 35)
    utility_floor = float(cfg.get("minimum_directional_utility") or 6)
    reasons = list(decision.get("reason_codes") or [])
    calendar = (macro_context or {}).get("belief_macro_calendar") if isinstance(macro_context, dict) else None
    if isinstance(calendar, dict) and calendar.get("imminent") is True:
        reasons.append("belief_macro_high_impact_event_imminent")
    if cfg.get("enabled", True):
        if fresh.get("data_quality") != "passed" and weekly.get("data_quality") != "passed":
            reasons.append("insufficient_data_quality")
        if max(raw, abs(base), abs(week)) < floor:
            reasons.append("directional_edge_below_threshold")
        if utility < utility_floor:
            reasons.append("selected_utility_below_threshold")
        if base * week < 0 and max(abs(base), abs(week)) < float(cfg.get("conflict_no_trade_below_raw_score") or 45):
            reasons.append("daily_weekly_conflict_without_dominant_edge")

    admitted, admission_reasons, diagnostics = directional_admission(
        decision, fresh, weekly, macro_context, policy
    )
    reasons.extend(admission_reasons)
    reasons = list(dict.fromkeys(reasons))
    if not reasons and admitted:
        out = dict(decision)
        out["directional_admission"] = {**diagnostics, "passed": True}
        return out
    return {
        "strategy_id": "no_trade",
        "direction": "neutral",
        "raw_score": 0.0,
        "utility": utility,
        "reason_codes": reasons,
        "blocked_candidate": {
            "strategy_id": decision.get("strategy_id"),
            "direction": decision.get("direction"),
            "raw_score": decision.get("raw_score"),
            "utility": decision.get("utility"),
            "execution_authority": decision.get("execution_authority"),
        },
        "directional_admission": {**diagnostics, "passed": False},
        "candidates": decision.get("candidates", {}),
    }


def wes_authorization_matches(
    item: Dict[str, Any],
    decision: Dict[str, Any],
    now: datetime,
    policy: Dict[str, Any],
) -> Tuple[bool, str]:
    cfg = policy.get("directional_admission") or {}
    if not cfg.get("require_for_all_new_entries", True):
        return True, "authorization_not_required"
    auth = item.get("wes_entry_authorization") if isinstance(item.get("wes_entry_authorization"), dict) else {}
    if not auth:
        return False, "wes_entry_authorization_missing"
    expires = parse_dt(auth.get("expires_at"))
    if expires is None or now >= expires:
        return False, "wes_entry_authorization_expired"
    candidate = auth.get("candidate") if isinstance(auth.get("candidate"), dict) else {}
    if auth.get("directional_admission_passed") is not True:
        return False, "wes_directional_admission_not_passed"
    if str(candidate.get("execution_authority") or "") != "champion_execution":
        return False, "wes_candidate_not_execution_authorized"
    if str(candidate.get("direction") or "") != str(decision.get("direction") or ""):
        return False, "wes_authorized_direction_mismatch"
    if str(candidate.get("strategy_id") or "") != str(decision.get("strategy_id") or ""):
        return False, "wes_authorized_strategy_mismatch"
    return True, "authorized"


def refresh_expired_authorization_for_live_decision(
    item: Dict[str, Any],
    decision: Dict[str, Any],
    now: datetime,
    policy: Dict[str, Any],
) -> bool:
    """Re-authorize a still-valid live WES thesis after an execution window was missed.

    This never backfills an old fill. It creates a new authorization timestamp and
    therefore a new prospective MARKET/LIMIT execution contract from fresh evidence.
    """
    cfg = policy.get("directional_admission") or {}
    if not cfg.get("require_for_all_new_entries", True):
        return False
    auth = item.get("wes_entry_authorization") if isinstance(item.get("wes_entry_authorization"), dict) else {}
    expires = parse_dt(auth.get("expires_at"))
    if expires is not None and now < expires:
        return False
    admission = decision.get("directional_admission") if isinstance(decision.get("directional_admission"), dict) else {}
    direction = str(decision.get("direction") or "")
    strategy_id = str(decision.get("strategy_id") or "")
    if direction not in {"long", "short"} or not strategy_id:
        return False
    if admission.get("passed") is not True:
        return False
    if str(decision.get("execution_authority") or "") != "champion_execution":
        return False
    ttl = max(5, int(cfg.get("authorization_ttl_minutes") or 60))
    item["wes_entry_authorization"] = {
        "authorized_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + timedelta(minutes=ttl)).isoformat(timespec="seconds"),
        "authorization_type": "live_reauthorization_after_missed_execution_window",
        "directional_admission_passed": True,
        "candidate": {
            "direction": direction,
            "strategy_id": strategy_id,
            "raw_score": abs(float(decision.get("raw_score") or 0.0)),
            "utility": float(decision.get("utility") or 0.0),
            "confirmations": int(admission.get("confirmations") or 0),
            "confirmation_sources": list(admission.get("confirmation_sources") or []),
            "execution_authority": "champion_execution",
            "directional_admission": dict(admission),
        },
        "required": {
            "allowed": True,
            "raw": float((cfg.get("initial_weekly_profile") or {}).get("raw") or 0.0),
            "utility": float((cfg.get("initial_weekly_profile") or {}).get("utility") or 0.0),
            "confirmations": int(cfg.get("minimum_confirmations") or 2),
        },
        "source_exit_at": None,
        "source_exit_reason": None,
        "prospective_only": True,
        "no_retroactive_execution": True,
    }
    item["wes_status"] = "directional_entry_pending_execution"
    return True


def abstain(item: Dict[str, Any], decision: Dict[str, Any]) -> None:
    item.update(pending_entry_decision=None, next_entry_status="no_trade", no_trade_decision=decision,
                no_trade_reason=",".join(decision.get("reason_codes", [])), continuous_exposure_active=False,
                continuous_exposure_status="no_trade")
    if not closed_position(item):
        item.update(direction="neutral", score=0, trade_status="no_trade", entry_price=None,
                    entry_captured_at=None, entry_source=None, risk_plan=None, result="no_trade",
                    result_value=0.0, result_percent=0.0)


def settle_unfilled_reentry(item: Dict[str, Any], decision: Dict[str, Any], reason: str) -> None:
    """Return a closed historical leg to a coherent closed state after an unfilled re-entry expires.

    Never rewrites the completed leg's direction, entry, exit, result or frozen risk plan.
    """
    risk_plan = item.get("risk_plan") if isinstance(item.get("risk_plan"), dict) else {}
    historical_class = str(risk_plan.get("wes_entry_class") or "")
    item.update(
        pending_entry_decision=None,
        wes_entry_authorization=None,
        next_entry_status="no_trade",
        trade_status="closed",
        no_trade_decision=decision,
        no_trade_reason=reason,
        continuous_exposure_active=False,
        wes_status="closed_waiting_new_trigger",
    )
    if historical_class:
        item["entry_quality_status"] = f"wes_{historical_class}"


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _entry_market_mode(
    decision: Dict[str, Any],
    fresh: Dict[str, Any],
    weekly: Dict[str, Any],
    policy: Dict[str, Any],
    entry_plan: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """Choose MARKET vs LIMIT with Weekly as the thesis owner.

    WES keeps Weekly and Daily independent. The primary execution score is built
    only from Weekly conviction, selected-candidate utility, independent
    confirmations and raw market momentum. Daily never contributes a primary
    weight: it is a bounded confirmation/timing modifier. A strongly opposed
    Daily signal may veto MARKET, but it never changes the Weekly thesis.
    """
    engine = policy.get("entry_price_engine") if isinstance(policy.get("entry_price_engine"), dict) else {}
    cfg = engine.get("market_entry") if isinstance(engine.get("market_entry"), dict) else {}
    scoring = cfg.get("scoring") if isinstance(cfg.get("scoring"), dict) else {}
    directional_cfg = policy.get("directional_admission") if isinstance(policy.get("directional_admission"), dict) else {}

    direction = str(decision.get("direction") or "neutral")
    daily_score = float(fresh.get("score") or 0.0)
    weekly_score = float(weekly.get("score") or 0.0) if weekly.get("data_quality") == "passed" else 0.0
    utility = float(decision.get("utility") or 0.0)
    admission = decision.get("directional_admission") if isinstance(decision.get("directional_admission"), dict) else {}
    confirmations = int(admission.get("confirmations") or 0)

    signals = fresh.get("signals") if isinstance(fresh.get("signals"), dict) else {}
    ret5 = sf(signals.get("ret5_pct"))
    ret20 = sf(signals.get("ret20_pct"))
    momentum_floor = abs(float(cfg.get("minimum_absolute_momentum_pct") or 0.15))
    utility_floor = float(cfg.get("min_selected_utility") or 8.0)
    confirmations_floor = int(cfg.get("min_confirmations") or 2)
    max_overextension = float(cfg.get("maximum_overextension_score") or 0.90)

    daily_dir = _direction_from_score(daily_score)
    weekly_dir = _direction_from_score(weekly_score)
    daily_valid = fresh.get("data_quality") == "passed"
    weekly_valid = weekly.get("data_quality") == "passed"
    daily_confirmation_floor = float(directional_cfg.get("daily_min_abs_score") or 25.0)
    weekly_confirmation_floor = float(directional_cfg.get("weekly_min_abs_score") or 15.0)

    daily_aligned = (
        daily_valid
        and daily_dir == direction
        and abs(daily_score) >= daily_confirmation_floor
    )
    weekly_aligned = (
        weekly_valid
        and weekly_dir == direction
        and abs(weekly_score) >= weekly_confirmation_floor
    )
    daily_opposed = (
        daily_valid
        and daily_dir in {"long", "short"}
        and daily_dir != direction
        and abs(daily_score) >= daily_confirmation_floor
    )
    weekly_opposed = (
        weekly_valid
        and weekly_dir in {"long", "short"}
        and weekly_dir != direction
        and abs(weekly_score) >= weekly_confirmation_floor
    )

    momentum_aligned = False
    momentum_opposed = False
    if ret5 is not None and ret20 is not None:
        if direction == "long":
            momentum_aligned = ret5 >= momentum_floor and ret20 >= momentum_floor
            momentum_opposed = ret5 <= -momentum_floor and ret20 <= -momentum_floor
        elif direction == "short":
            momentum_aligned = ret5 <= -momentum_floor and ret20 <= -momentum_floor
            momentum_opposed = ret5 >= momentum_floor and ret20 >= momentum_floor

    inputs = (entry_plan or {}).get("inputs") if isinstance((entry_plan or {}).get("inputs"), dict) else {}
    overextension = sf(inputs.get("overextension_score"))
    post_stop_reversal = bool(inputs.get("post_stop_reversal"))
    overextension_value = _clip(float(overextension or 0.0), 0.0, 1.0)

    refs = scoring.get("full_strength_reference") if isinstance(scoring.get("full_strength_reference"), dict) else {}
    weekly_ref = max(1.0, float(refs.get("weekly_abs_score") or 55.0))
    utility_ref = max(0.1, float(refs.get("utility") or 14.0))
    confirmations_ref = max(1.0, float(refs.get("confirmations") or 4.0))

    weekly_strength = _clip(abs(weekly_score) / weekly_ref, 0.0, 1.0) if weekly_aligned else 0.0
    utility_strength = _clip(utility / utility_ref, 0.0, 1.0)
    confirmations_strength = _clip(confirmations / confirmations_ref, 0.0, 1.0)
    momentum_strength = 1.0 if momentum_aligned else 0.0 if momentum_opposed else 0.5

    weights = scoring.get("primary_weights") if isinstance(scoring.get("primary_weights"), dict) else {}
    w_weekly = max(0.0, float(weights.get("weekly") or 0.45))
    w_utility = max(0.0, float(weights.get("utility") or 0.25))
    w_confirmations = max(0.0, float(weights.get("confirmations") or 0.15))
    w_momentum = max(0.0, float(weights.get("momentum") or 0.15))
    weight_total = max(0.0001, w_weekly + w_utility + w_confirmations + w_momentum)
    primary_score = (
        w_weekly * weekly_strength
        + w_utility * utility_strength
        + w_confirmations * confirmations_strength
        + w_momentum * momentum_strength
    ) / weight_total

    penalty_weight = max(0.0, float(scoring.get("overextension_penalty_weight") or 0.20))
    overextension_penalty = penalty_weight * overextension_value
    score_before_daily = _clip(primary_score - overextension_penalty, 0.0, 1.0)

    daily_cfg = scoring.get("daily_confirmation_modifier") if isinstance(scoring.get("daily_confirmation_modifier"), dict) else {}
    daily_neutral_floor = max(0.0, float(daily_cfg.get("neutral_abs_score_below") or daily_confirmation_floor))
    daily_full_strength = max(daily_neutral_floor + 0.0001, float(daily_cfg.get("full_strength_abs_score") or 55.0))
    aligned_bonus_max = max(0.0, float(daily_cfg.get("aligned_bonus_max") or 0.08))
    opposed_penalty_max = max(0.0, float(daily_cfg.get("opposed_penalty_max") or 0.10))
    strong_opposition_veto = max(
        daily_neutral_floor,
        float(daily_cfg.get("strong_opposition_veto_abs_score") or daily_full_strength),
    )
    daily_strength = (
        _clip(abs(daily_score) / daily_full_strength, 0.0, 1.0)
        if daily_valid and abs(daily_score) >= daily_neutral_floor
        else 0.0
    )
    daily_modifier = 0.0
    daily_modifier_state = "neutral_or_unavailable"
    if daily_aligned:
        daily_modifier = aligned_bonus_max * daily_strength
        daily_modifier_state = "aligned_bonus"
    elif daily_opposed:
        daily_modifier = -opposed_penalty_max * daily_strength
        daily_modifier_state = "opposed_penalty"

    market_score = _clip(score_before_daily + daily_modifier, 0.0, 1.0)
    minimum_market_score = _clip(float(scoring.get("minimum_score") or 0.70), 0.0, 1.0)

    reasons: list[str] = []
    if direction not in {"long", "short"}:
        reasons.append("non_directional")
    if not cfg.get("enabled", False):
        reasons.append("market_entry_disabled")
    if admission.get("passed") is not True:
        reasons.append("directional_admission_not_passed")
    if confirmations < confirmations_floor:
        reasons.append("insufficient_confirmations_for_market_entry")
    if utility < utility_floor:
        reasons.append("selected_utility_below_market_entry_floor")
    if cfg.get("require_weekly_primary_alignment", True) and not weekly_aligned:
        reasons.append("weekly_primary_not_aligned")
    if weekly_opposed:
        reasons.append("weekly_primary_opposes_market_entry")
    if (
        cfg.get("block_strong_daily_opposition", True)
        and daily_opposed
        and abs(daily_score) >= strong_opposition_veto
    ):
        reasons.append("strong_daily_opposition_veto")
    if cfg.get("block_opposed_momentum", True) and momentum_opposed:
        reasons.append("momentum_opposes_market_entry")
    if overextension is not None and overextension > max_overextension:
        reasons.append("too_overextended_for_market_entry")
    if post_stop_reversal and cfg.get("block_immediate_market_after_stop", True):
        reasons.append("post_stop_reversal_requires_pullback")
    if market_score < minimum_market_score:
        reasons.append("market_score_below_threshold")

    diagnostics = {
        "version": str(cfg.get("version") or "WES-1.3.1"),
        "scoring_model": "weekly_primary_daily_confirmation_modifier",
        "direction": direction,
        "daily_score": round(daily_score, 4),
        "weekly_score": round(weekly_score, 4),
        "selected_utility": round(utility, 4),
        "confirmations": confirmations,
        "ret5_pct": ret5,
        "ret20_pct": ret20,
        "overextension_score": overextension,
        "daily_aligned": daily_aligned,
        "weekly_aligned": weekly_aligned,
        "daily_opposed": daily_opposed,
        "weekly_opposed": weekly_opposed,
        "momentum_aligned": momentum_aligned,
        "momentum_opposed": momentum_opposed,
        "primary_score_components": {
            "weekly": round(weekly_strength, 4),
            "utility": round(utility_strength, 4),
            "confirmations": round(confirmations_strength, 4),
            "momentum": round(momentum_strength, 4),
        },
        "primary_market_score": round(primary_score, 4),
        "overextension_penalty": round(overextension_penalty, 4),
        "score_before_daily_modifier": round(score_before_daily, 4),
        "daily_confirmation_modifier": round(daily_modifier, 4),
        "daily_confirmation_state": daily_modifier_state,
        "daily_modifier_bounds": {
            "aligned_bonus_max": aligned_bonus_max,
            "opposed_penalty_max": opposed_penalty_max,
            "strong_opposition_veto_abs_score": strong_opposition_veto,
        },
        "market_score": round(market_score, 4),
        "minimum_market_score": round(minimum_market_score, 4),
        "eligible": not reasons,
        "reasons": reasons,
    }
    return not reasons, diagnostics


def renew_persistent_entry_plan(
    pending: Dict[str, Any],
    now: datetime,
    policy: Dict[str, Any],
) -> bool:
    """Reaffirm the same frozen LIMIT without moving its target.

    Renewal is allowed only within a bounded liveness gap. This keeps a valid
    thesis executable across repeated WES cycles while failing closed if WES
    itself stops running for too long.
    """
    engine = policy.get("entry_price_engine") if isinstance(policy.get("entry_price_engine"), dict) else {}
    cfg = engine.get("persistent_plan") if isinstance(engine.get("persistent_plan"), dict) else {}
    if not cfg.get("enabled", True):
        return False
    plan = pending.get("entry_price_plan") if isinstance(pending.get("entry_price_plan"), dict) else {}
    if not plan or str(plan.get("execution_mode") or "limit_pullback") != "limit_pullback":
        return False
    expires = parse_dt(plan.get("expires_at"))
    if expires is None:
        return False
    max_gap = max(5, int(cfg.get("max_reaffirmation_gap_minutes") or 20))
    if now > expires + timedelta(minutes=max_gap):
        return False
    extension = max(15, int(cfg.get("reaffirmation_extension_minutes") or 60))
    desired_expiry = now + timedelta(minutes=extension)
    if desired_expiry > expires:
        plan["expires_at"] = desired_expiry.isoformat(timespec="seconds")
    plan["last_reaffirmed_at"] = now.isoformat(timespec="seconds")
    plan["reaffirmation_count"] = int(plan.get("reaffirmation_count") or 0) + 1
    plan["persistence_policy"] = "same_thesis_reaffirmation_extends_time_only_never_moves_frozen_target"
    pending["entry_price_plan"] = plan
    return True


def _promote_plan_to_market_now(
    plan: Dict[str, Any],
    diagnostics: Dict[str, Any],
    now: datetime,
    policy: Dict[str, Any],
    *,
    promotion: bool = False,
) -> Dict[str, Any]:
    engine = policy.get("entry_price_engine") if isinstance(policy.get("entry_price_engine"), dict) else {}
    cfg = engine.get("market_entry") if isinstance(engine.get("market_entry"), dict) else {}
    promoted = dict(plan)
    if promoted.get("execution_mode") != "market_now":
        promoted["original_order_type"] = promoted.get("order_type")
        promoted["original_target_price"] = promoted.get("target_price")
    promoted["execution_mode"] = "market_now"
    promoted["order_type"] = "market"
    promoted["entry_not_before"] = now.isoformat(timespec="seconds")
    max_wait = max(5, int(cfg.get("max_wait_minutes") or 15))
    existing_expiry = parse_dt(promoted.get("expires_at"))
    desired_expiry = now + timedelta(minutes=max_wait)
    if existing_expiry is not None:
        desired_expiry = min(existing_expiry, desired_expiry)
    promoted["expires_at"] = desired_expiry.isoformat(timespec="seconds")
    promoted["market_entry_diagnostics"] = diagnostics
    promoted["market_entry_reason"] = "strong_aligned_trend_continuation"
    promoted["status"] = "waiting_for_fresh_market_bar"
    if promotion:
        promoted["promoted_from_limit_at"] = now.isoformat(timespec="seconds")
        promoted["promotion_rule"] = "same_authorized_thesis_strengthened_to_market_entry"
    return promoted


def maybe_promote_pending_to_market(
    pending: Dict[str, Any],
    decision: Dict[str, Any],
    fresh: Dict[str, Any],
    weekly: Dict[str, Any],
    policy: Dict[str, Any],
    now: datetime,
) -> bool:
    """One-way LIMIT -> MARKET escalation for the same still-authorized thesis."""
    engine = policy.get("entry_price_engine") if isinstance(policy.get("entry_price_engine"), dict) else {}
    cfg = engine.get("market_entry") if isinstance(engine.get("market_entry"), dict) else {}
    if not cfg.get("allow_limit_to_market_promotion", True):
        return False
    plan = pending.get("entry_price_plan") if isinstance(pending.get("entry_price_plan"), dict) else {}
    if not plan or plan.get("execution_mode") == "market_now":
        return False
    eligible, diagnostics = _entry_market_mode(decision, fresh, weekly, policy, plan)
    if not eligible:
        return False
    pending["entry_price_plan"] = _promote_plan_to_market_now(plan, diagnostics, now, policy, promotion=True)
    pending["entry_not_before"] = pending["entry_price_plan"]["entry_not_before"]
    pending["rule"] = "strong_trend_promoted_limit_to_market_on_first_fresh_completed_5m_bar"
    return True


def build_entry_price_plan(
    item: Dict[str, Any],
    decision: Dict[str, Any],
    fresh: Dict[str, Any],
    now: datetime,
    policy: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Freeze the desired entry price before execution.

    WES 1.3.1 separates directional admission from adaptive execution.
    A valid LONG/SHORT thesis may remain WAIT indefinitely until the frozen
    price is touched or the plan expires.
    """
    cfg = policy.get("entry_price_engine") if isinstance(policy.get("entry_price_engine"), dict) else {}
    if not cfg.get("enabled", True):
        return None

    direction = str(decision.get("direction") or "neutral")
    signals = fresh.get("signals") if isinstance(fresh.get("signals"), dict) else {}
    reference = sf(signals.get("last_close"))
    atr = sf(signals.get("atr14"))
    ema20 = sf(signals.get("ema20"))
    ret5 = sf(signals.get("ret5_pct"))
    ret20 = sf(signals.get("ret20_pct"))
    range55 = sf(signals.get("range55_position"))
    if direction not in {"long", "short"} or reference is None or reference <= 0:
        return None
    if atr is None or atr <= 0:
        return None
    if ema20 is None or ema20 <= 0:
        ema20 = reference
    ret5 = float(ret5 or 0.0)
    ret20 = float(ret20 or 0.0)
    range55 = _clip(float(range55 if range55 is not None else 0.5), 0.0, 1.0)

    ret5_scale = max(0.01, float(cfg.get("ret5_full_scale_percent") or 8.0))
    ema_scale = max(0.01, float(cfg.get("ema_distance_full_scale_atr") or 2.5))
    weights = cfg.get("overextension_weights") if isinstance(cfg.get("overextension_weights"), dict) else {}
    w_momentum = float(weights.get("momentum_5d") or 0.40)
    w_range = float(weights.get("range_55d") or 0.35)
    w_ema = float(weights.get("ema20_distance") or 0.25)
    weight_total = max(0.0001, w_momentum + w_range + w_ema)

    signed_momentum = ret5 if direction == "long" else -ret5
    signed_ema_distance_atr = ((reference - ema20) / atr) if direction == "long" else ((ema20 - reference) / atr)
    directional_range = range55 if direction == "long" else 1.0 - range55
    momentum_score = _clip(max(0.0, signed_momentum) / ret5_scale, 0.0, 1.0)
    range_score = _clip(directional_range, 0.0, 1.0)
    ema_score = _clip(max(0.0, signed_ema_distance_atr) / ema_scale, 0.0, 1.0)
    overextension = (
        w_momentum * momentum_score
        + w_range * range_score
        + w_ema * ema_score
    ) / weight_total

    minimum_pullback = max(0.0, float(cfg.get("minimum_pullback_atr") or 0.10))
    base_pullback = max(minimum_pullback, float(cfg.get("base_pullback_atr") or 0.12))
    extra_pullback = max(0.0, float(cfg.get("overextension_extra_pullback_atr") or 0.48))
    max_pullback = max(base_pullback, float(cfg.get("maximum_pullback_atr") or 0.75))
    pullback_atr = base_pullback + overextension * extra_pullback

    source_exit_at = parse_dt(item.get("wes_early_reentry_source_exit_at") or item.get("exit_captured_at"))
    source_exit_reason = str(item.get("wes_early_reentry_source_exit_reason") or item.get("exit_reason") or "")
    post_stop_reversal = (
        source_exit_at is not None
        and source_exit_reason == "stop_loss"
        and closed_position(item)
    )
    if post_stop_reversal:
        pullback_atr += max(0.0, float(cfg.get("post_stop_reversal_extra_pullback_atr") or 0.15))
    pullback_atr = _clip(pullback_atr, minimum_pullback, max_pullback)

    target = reference - pullback_atr * atr if direction == "long" else reference + pullback_atr * atr

    structural_buffer = max(0.0, float(cfg.get("structural_ema20_buffer_atr") or 0.25))
    if direction == "long":
        structural_floor = ema20 + structural_buffer * atr
        if target < structural_floor < reference:
            target = structural_floor
    else:
        structural_ceiling = ema20 - structural_buffer * atr
        if target > structural_ceiling > reference:
            target = structural_ceiling

    max_distance_cfg = cfg.get("max_target_distance_percent") if isinstance(cfg.get("max_target_distance_percent"), dict) else {}
    iid = str(item.get("instrument_id") or decision.get("instrument_id") or "")
    max_distance_pct = max(0.01, float(max_distance_cfg.get(iid) or 2.0))
    if direction == "long":
        target = max(target, reference * (1.0 - max_distance_pct / 100.0))
        target = min(target, reference - minimum_pullback * atr)
        order_type = "buy_limit"
    else:
        target = min(target, reference * (1.0 + max_distance_pct / 100.0))
        target = max(target, reference + minimum_pullback * atr)
        order_type = "sell_limit"

    wait_minutes = max(5, int(cfg.get("max_wait_minutes") or 60))
    entry_not_before = now
    if post_stop_reversal and source_exit_at is not None:
        bars = max(0, int(cfg.get("post_stop_reversal_min_completed_bars") or 3))
        entry_not_before = max(entry_not_before, source_exit_at + timedelta(minutes=5 * bars))
    expires_at = now + timedelta(minutes=wait_minutes)

    return {
        "version": str(cfg.get("version") or "WES-1.3.1"),
        "frozen_at": now.isoformat(timespec="seconds"),
        "instrument_id": iid,
        "direction": direction,
        "execution_mode": "limit_pullback",
        "order_type": order_type,
        "target_price": round(target, 8),
        "reference_price": round(reference, 8),
        "reference_source": "fresh_signal.signals.last_close",
        "entry_not_before": entry_not_before.isoformat(timespec="seconds"),
        "expires_at": expires_at.isoformat(timespec="seconds"),
        "execution_bar_interval_minutes": int(cfg.get("execution_bar_interval_minutes") or 5),
        "fill_rule": str(cfg.get("fill_rule") or "frozen_limit_target_touch"),
        "inputs": {
            "atr14": round(atr, 8),
            "ema20": round(ema20, 8),
            "ret5_pct": round(ret5, 6),
            "ret20_pct": round(ret20, 6),
            "range55_position": round(range55, 6),
            "directional_ema20_distance_atr": round(signed_ema_distance_atr, 6),
            "momentum_overextension_score": round(momentum_score, 6),
            "range_overextension_score": round(range_score, 6),
            "ema_overextension_score": round(ema_score, 6),
            "overextension_score": round(overextension, 6),
            "pullback_atr_fraction": round(pullback_atr, 6),
            "post_stop_reversal": post_stop_reversal,
        },
        "status": "waiting_for_target_touch",
    }


def freeze_decision(
    item: Dict[str, Any],
    decision: Dict[str, Any],
    fresh: Dict[str, Any],
    weekly: Dict[str, Any],
    now: datetime,
    policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    policy = policy if isinstance(policy, dict) else read(POLICY, {})
    frozen = dict(decision)
    frozen.update(decided_at=now.isoformat(timespec="seconds"), validation_gate=item.get("validation_gate"))
    entry_plan = build_entry_price_plan(item, frozen, fresh, now, policy)
    if (policy.get("entry_price_engine") or {}).get("require_for_all_new_entries", True) and entry_plan is None:
        raise RuntimeError("WES 1.3.1 entry price plan could not be frozen")
    if entry_plan is not None:
        market_now, diagnostics = _entry_market_mode(frozen, fresh, weekly, policy, entry_plan)
        entry_plan["market_entry_diagnostics"] = diagnostics
        if market_now:
            entry_plan = _promote_plan_to_market_now(entry_plan, diagnostics, now, policy)
    entry_not_before = (entry_plan or {}).get("entry_not_before") or frozen["decided_at"]
    auth = item.get("wes_entry_authorization") if isinstance(item.get("wes_entry_authorization"), dict) else {}
    pending = {
        "decided_at": frozen["decided_at"],
        "entry_not_before": entry_not_before,
        "decision": frozen,
        "fresh_signal": fresh,
        "weekly_signal": weekly,
        "macro_context": frozen.get("macro_context"),
        "entry_price_plan": entry_plan,
        "authorization_basis": {
            "authorized_at": auth.get("authorized_at"),
            "strategy_id": frozen.get("strategy_id"),
            "direction": frozen.get("direction"),
            "directional_admission_passed": auth.get("directional_admission_passed"),
        },
        "rule": (
            "execute_market_now_on_first_fresh_completed_5m_bar_after_decision"
            if (entry_plan or {}).get("execution_mode") == "market_now"
            else "execute_only_when_frozen_entry_target_is_touched_after_decision"
        ),
    }
    market_mode = (entry_plan or {}).get("execution_mode") == "market_now"
    item.update(
        pending_entry_decision=pending,
        trade_status="pending",
        next_entry_status="waiting_for_market_entry" if market_mode else "waiting_for_entry_target",
        entry_quality_status="wes_1_3_1_waiting_for_market_entry" if market_mode else "wes_1_3_1_waiting_for_frozen_entry_target",
    )
    return pending


def _yahoo_entry_target_touch(
    symbol: str,
    direction: str,
    target: float,
    start: datetime,
    end: datetime,
) -> Optional[Dict[str, Any]]:
    df = v2.intraday_bars(symbol, start, end)
    if df is None:
        return None
    try:
        df = df[(df.index >= start) & (df.index <= end)]
        for ts, row in df.iterrows():
            high = v2._row_value(row, "High")
            low = v2._row_value(row, "Low")
            if high is None or low is None:
                continue
            touched = low <= target if direction == "long" else high >= target
            if touched:
                return {
                    "price": target,
                    "timestamp": ts.to_pydatetime().astimezone(legacy.TZ).isoformat(timespec="seconds"),
                    "source": f"Yahoo Finance:{symbol}:5m:frozen_entry_target_touch",
                    "observed_high": high,
                    "observed_low": low,
                }
    except Exception:
        return None
    return None


def _yahoo_market_entry(
    symbol: str,
    start: datetime,
    end: datetime,
    checked_at: datetime,
) -> Optional[Dict[str, Any]]:
    """Return the freshest completed 5m close after authorization."""
    cutoff = checked_at - timedelta(minutes=5)
    effective_end = min(end, cutoff)
    if effective_end < start:
        return None
    df = v2.intraday_bars(symbol, start, effective_end)
    if df is None:
        return None
    try:
        df = df[(df.index >= start) & (df.index <= effective_end)]
        if df.empty:
            return None
        ts = df.index[-1]
        row = df.iloc[-1]
        close = v2._row_value(row, "Close")
        high = v2._row_value(row, "High")
        low = v2._row_value(row, "Low")
        if close is None or high is None or low is None:
            return None
        return {
            "price": close,
            "timestamp": ts.to_pydatetime().astimezone(legacy.TZ).isoformat(timespec="seconds"),
            "source": f"Yahoo Finance:{symbol}:5m:market_now_completed_bar",
            "observed_high": high,
            "observed_low": low,
        }
    except Exception:
        return None


def _canonical_live_entry_point(
    pending: Dict[str, Any],
    checked_at: datetime,
) -> Optional[Dict[str, Any]]:
    """Use the exact WES public live-price snapshot as primary execution trigger."""
    plan = pending.get("entry_price_plan") if isinstance(pending.get("entry_price_plan"), dict) else {}
    iid = str(plan.get("instrument_id") or "")
    direction = str(plan.get("direction") or (pending.get("decision") or {}).get("direction") or "")
    mode = str(plan.get("execution_mode") or "limit_pullback")
    target = sf(plan.get("target_price"))
    start = parse_dt(plan.get("entry_not_before") or pending.get("entry_not_before"))
    expires = parse_dt(plan.get("expires_at"))
    if not iid or direction not in {"long", "short"} or start is None:
        return None
    live = read(ROOT / "data/investments/live_prices.json", {})
    rec = (live.get("prices") or {}).get(iid) if isinstance(live.get("prices"), dict) else None
    if not isinstance(rec, dict):
        return None
    price = sf(rec.get("price"))
    stamp = parse_dt(rec.get("current_price_updated_at") or rec.get("timestamp"))
    if price is None or price <= 0 or stamp is None:
        return None
    # The public target remains executable until it is filled or explicitly
    # replaced/cancelled. A stale scheduler expiry must never leave a visible
    # target that cannot execute.
    if stamp < start or stamp > checked_at:
        return None
    max_age = timedelta(minutes=6)
    if checked_at - stamp > max_age or stamp - checked_at > timedelta(seconds=30):
        return None
    source = str(rec.get("source") or "WES canonical live price")
    if mode == "market_now" and iid == "eurusd":
        # EURUSD aggressive MARKET override only. An expired MARKET authorization
        # is not recoverable. Return no point so
        # ensure_all() can re-authorize the still-valid thesis prospectively and
        # freeze a new MARKET plan instead of getting trapped on an old plan.
        if expires is None or stamp > expires:
            return None
        # MARKET means hit the fresh market after authorization. The old LIMIT
        # target is retained only as audit metadata and must never gate a market
        # fill; otherwise a strong SHORT can chase a falling market forever.
        return {
            "price": price,
            "timestamp": stamp.astimezone(legacy.TZ).isoformat(timespec="seconds"),
            "source": f"{source}:canonical_live_market_now",
            "observed_high": price,
            "observed_low": price,
            "canonical_live_price": price,
        }
    if target is None or target <= 0:
        return None
    # LIMIT contract: the exact displayed frozen target remains authoritative.
    # LONG opens when Cena teraz <= target; SHORT opens when Cena teraz >= target.
    marketable = price <= target if direction == "long" else price >= target
    if not marketable:
        return None
    return {
        "price": target,
        "timestamp": stamp.astimezone(legacy.TZ).isoformat(timespec="seconds"),
        "source": f"{source}:canonical_live_marketable_limit",
        "observed_high": max(price, target),
        "observed_low": min(price, target),
        "canonical_live_price": price,
    }


def entry_point(
    symbol: str,
    pending: Dict[str, Any],
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Execute the frozen WES 1.3 mode: strong-trend MARKET or pullback LIMIT."""
    plan = pending.get("entry_price_plan") if isinstance(pending.get("entry_price_plan"), dict) else {}
    direction = str(plan.get("direction") or (pending.get("decision") or {}).get("direction") or "neutral")
    execution_mode = str(plan.get("execution_mode") or "limit_pullback")
    target = sf(plan.get("target_price"))
    start = parse_dt(plan.get("entry_not_before") or pending.get("entry_not_before"))
    expires = parse_dt(plan.get("expires_at"))
    checked_at = now or legacy.now_local()
    if direction not in {"long", "short"} or start is None or expires is None:
        return None

    # A delayed runner may replay only within the global NO RETROACTIVE
    # EXECUTION lag. MARKET mode uses the freshest completed bar in that
    # window; LIMIT mode may recover a recent frozen-target touch.
    replay_floor = checked_at - no_retro.MAX_LIVE_MARKET_DATA_LAG
    start = max(start, replay_floor)
    canonical = _canonical_live_entry_point(pending, checked_at)
    if canonical is not None:
        return canonical

    end = min(checked_at, expires)
    if end < start:
        return None

    if execution_mode == "market_now":
        return _yahoo_market_entry(symbol, start, end, checked_at)

    if target is None or target <= 0:
        return None

    iid = str(plan.get("instrument_id") or "")
    if iid == "btcusd":
        try:
            import audit_intraday_risk_exits as risk_market
            bars = risk_market.fetch_coinbase_bars(start, end)
            for bar in bars:
                if bar.ts < start.astimezone(bar.ts.tzinfo) or bar.ts > end.astimezone(bar.ts.tzinfo):
                    continue
                touched = bar.low <= target if direction == "long" else bar.high >= target
                if touched:
                    return {
                        "price": target,
                        "timestamp": bar.ts.astimezone(legacy.TZ).isoformat(timespec="seconds"),
                        "source": "Coinbase Exchange:BTC-USD:5m:frozen_entry_target_touch",
                        "observed_high": bar.high,
                        "observed_low": bar.low,
                    }
        except Exception:
            pass
    return _yahoo_entry_target_touch(symbol, direction, target, start, end)


def entry_plan_expired(pending: Any, now: datetime) -> bool:
    if not isinstance(pending, dict):
        return False
    plan = pending.get("entry_price_plan") if isinstance(pending.get("entry_price_plan"), dict) else {}
    expires = parse_dt(plan.get("expires_at"))
    return expires is not None and now >= expires


def recover_frozen_pending_touch(
    item: Dict[str, Any],
    symbol: str,
    now: datetime,
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Resolve a recent touch of an already-frozen entry plan before refresh.

    The frozen authorization basis must prove that the pending decision was
    execution-authorized when created. Current authorization may already have
    expired; that must not erase a target touch that occurred moments earlier.
    The entry_point() replay window is bounded by NO RETROACTIVE EXECUTION.
    """
    pending = item.get("pending_entry_decision")
    if not isinstance(pending, dict):
        return None
    decision = pending.get("decision") if isinstance(pending.get("decision"), dict) else {}
    plan = pending.get("entry_price_plan") if isinstance(pending.get("entry_price_plan"), dict) else {}
    basis = pending.get("authorization_basis") if isinstance(pending.get("authorization_basis"), dict) else {}
    if not plan or not basis or basis.get("directional_admission_passed") is not True:
        return None
    for key in ("direction", "strategy_id"):
        value = str(decision.get(key) or "")
        if not value or value != str(basis.get(key) or ""):
            return None
    if str(decision.get("direction") or "") not in {"long", "short"}:
        return None
    point = entry_point(symbol, pending, now)
    return (pending, point) if point is not None else None


def epe_verified_entry_point(
    pending: Dict[str, Any],
    point: Dict[str, Any],
    checked_at: datetime,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Verify WES MARKET and LIMIT execution as two distinct contracts."""
    plan = pending.get("entry_price_plan") if isinstance(pending.get("entry_price_plan"), dict) else {}
    direction = str(plan.get("direction") or (pending.get("decision") or {}).get("direction") or "neutral")
    execution_mode = str(plan.get("execution_mode") or "limit_pullback")
    iid = str(plan.get("instrument_id") or "")
    stamp = parse_dt(point.get("timestamp"))

    if execution_mode == "market_now" and iid == "eurusd":
        price = sf(point.get("price"))
        start = parse_dt(plan.get("entry_not_before") or pending.get("entry_not_before"))
        expires = parse_dt(plan.get("expires_at"))
        if direction not in {"long", "short"} or price is None or price <= 0 or stamp is None or start is None or expires is None:
            verification = epe.blocked("missing_fresh_market_execution_contract", mode="MARKET_NOW", instrument="WES")
            return None, verification
        if stamp < start:
            verification = epe.blocked("market_price_predates_authorization", mode="MARKET_NOW", instrument="WES")
            return None, verification
        if stamp > checked_at + timedelta(seconds=30):
            verification = epe.blocked("market_price_from_future", mode="MARKET_NOW", instrument="WES")
            return None, verification
        if stamp > expires:
            verification = epe.blocked("market_price_after_plan_expiry", mode="MARKET_NOW", instrument="WES")
            return None, verification
        if checked_at - stamp > no_retro.MAX_LIVE_MARKET_DATA_LAG:
            verification = epe.blocked("market_price_outside_live_replay_window", mode="MARKET_NOW", instrument="WES")
            return None, verification
        verification = {
            "verified": True,
            "status": "VERIFIED",
            "mode": "MARKET_NOW",
            "instrument": "WES",
            "direction": direction,
            "execution_price": float(price),
            "observed_at": stamp.isoformat(timespec="seconds"),
            "checked_at": checked_at.isoformat(timespec="seconds"),
            "rule": "Strong-conviction MARKET hits a fresh post-authorization market price; the archived LIMIT target does not gate execution.",
        }
        verified = dict(point)
        verified["price"] = float(price)
        verified["execution_price_engine"] = dict(verification)
        return verified, verification

    target = sf(plan.get("target_price"))
    live_price = sf(point.get("canonical_live_price"))
    if direction not in {"long", "short"} or target is None or live_price is None or stamp is None:
        verification = epe.blocked("missing_canonical_live_target_contract", mode="CANONICAL_LIVE_TARGET", instrument="WES")
        return None, verification
    marketable = live_price <= target if direction == "long" else live_price >= target
    if not marketable:
        verification = epe.blocked("canonical_live_price_has_not_reached_target", mode="CANONICAL_LIVE_TARGET", instrument="WES")
        return None, verification
    verification = {
        "verified": True,
        "status": "VERIFIED",
        "mode": "CANONICAL_LIVE_TARGET",
        "instrument": "WES",
        "direction": direction,
        "target_price": float(target),
        "canonical_live_price": float(live_price),
        "observed_at": stamp.isoformat(timespec="seconds"),
        "checked_at": checked_at.isoformat(timespec="seconds"),
        "rule": "LONG: Cena teraz <= target; SHORT: Cena teraz >= target",
    }
    verified = dict(point)
    verified["price"] = float(target)
    verified["execution_price_engine"] = dict(verification)
    return verified, verification

def pending_matches_wes_authorization(
    item: Dict[str, Any],
    pending: Any,
    now: Optional[datetime] = None,
) -> bool:
    """Keep a frozen price target stable while the same WES thesis remains authorized."""
    if not isinstance(pending, dict):
        return False
    decision = pending.get("decision") if isinstance(pending.get("decision"), dict) else {}
    plan = pending.get("entry_price_plan") if isinstance(pending.get("entry_price_plan"), dict) else {}
    basis = pending.get("authorization_basis") if isinstance(pending.get("authorization_basis"), dict) else {}
    auth = item.get("wes_entry_authorization") if isinstance(item.get("wes_entry_authorization"), dict) else {}
    candidate = auth.get("candidate") if isinstance(auth.get("candidate"), dict) else {}
    if not plan or not basis:
        return False
    if entry_plan_expired(pending, now or legacy.now_local()):
        return False
    if auth.get("directional_admission_passed") is not True:
        return False
    if basis.get("directional_admission_passed") is not True:
        return False
    for key in ("direction", "strategy_id"):
        expected = str(decision.get(key) or "")
        if expected != str(candidate.get(key) or "") or expected != str(basis.get(key) or ""):
            return False
    if str(candidate.get("execution_authority") or "") != "champion_execution":
        return False
    return True


def _store_macro_review(item: Dict[str, Any], context: Dict[str, Any]) -> bool:
    if context.get("data_quality") not in {"passed", "failed"}:
        return False
    review = macro.position_review(item, context)
    previous = item.get("latest_macro_context") if isinstance(item.get("latest_macro_context"), dict) else {}
    comparable_previous = {key: previous.get(key) for key in review}
    if comparable_previous == review:
        return False
    item["latest_macro_context"] = review
    return True


def _candidate_net_percent(direction: str, entry: float, exit_price: float, cost_percent: float) -> Optional[float]:
    if direction not in {"long", "short"} or entry <= 0:
        return None
    gross = ((exit_price - entry) / entry * 100.0) if direction == "long" else ((entry - exit_price) / entry * 100.0)
    return gross - cost_percent


def _enrich_latest_archived_leg(item: Dict[str, Any]) -> None:
    legs = item.get("position_legs") if isinstance(item.get("position_legs"), list) else []
    lid = str(item.get("continuous_last_closed_leg_id") or "")
    leg = next((x for x in reversed(legs) if isinstance(x, dict) and str(x.get("leg_id") or "") == lid), None)
    if not leg:
        return
    decision = leg.get("entry_decision") if isinstance(leg.get("entry_decision"), dict) else {}
    candidates = decision.get("candidates") if isinstance(decision.get("candidates"), dict) else {}
    entry, exit_price = sf(leg.get("entry_price")), sf(leg.get("exit_price"))
    cost = float(sf(leg.get("estimated_round_trip_cost_percent")) or 0.0)
    if entry is None or exit_price is None or not candidates:
        return
    outcomes: Dict[str, Dict[str, Any]] = {}
    for method_id, candidate in candidates.items():
        if not isinstance(candidate, dict):
            continue
        direction = str(candidate.get("direction") or "neutral")
        net = _candidate_net_percent(direction, entry, exit_price, cost)
        if net is None:
            continue
        gross = net + cost
        outcomes[str(method_id)] = {
            "direction": direction,
            "gross_result_percent": round(gross, 6),
            "estimated_round_trip_cost_percent": round(cost, 6),
            "net_result_percent": round(net, 6),
            "source": "same_entry_exit_counterfactual_candidate",
        }
    if outcomes:
        leg["candidate_outcomes"] = outcomes
        leg["contextual_learning_schema"] = "2.0"


def archive_leg_with_contextual_outcomes(item: Dict[str, Any], policy_row: Dict[str, Any]) -> bool:
    archived = v4.archive_leg(item, policy_row)
    if archived:
        _enrich_latest_archived_leg(item)
    return archived


def archive_closed_learning_samples(week: Dict[str, Any], policy: Dict[str, Any]) -> int:
    """Archive every newly closed governed leg before any time-window early return."""
    items = {str(x.get("instrument_id")): x for x in week.get("instruments", []) if isinstance(x, dict)}
    archived = 0
    for p_cfg in v4.policy_instruments(policy):
        iid = str(p_cfg.get("instrument_id"))
        item = items.get(iid)
        if item is not None and closed_position(item) and archive_leg_with_contextual_outcomes(item, p_cfg):
            archived += 1
    return archived


def momentum_context(direction: str, fresh: Dict[str, Any], policy: Dict[str, Any]) -> str:
    cfg = policy.get("contextual_learning") or {}
    signals = fresh.get("signals") if isinstance(fresh.get("signals"), dict) else {}
    r5, r20 = sf(signals.get("ret5_pct")), sf(signals.get("ret20_pct"))
    floor = abs(float(cfg.get("minimum_absolute_momentum_pct") or 0.15))
    if r5 is None or r20 is None:
        return "momentum_context_unavailable"
    aligned_up = r5 >= floor and r20 >= floor
    aligned_down = r5 <= -floor and r20 <= -floor
    if direction == "short" and aligned_up:
        return "against_aligned_up_momentum"
    if direction == "long" and aligned_down:
        return "against_aligned_down_momentum"
    return "not_against_aligned_momentum"


def _alignment_context(prefix: str, direction: str, reference: Dict[str, Any]) -> str:
    if reference.get("data_quality") != "passed" or str(reference.get("direction") or "neutral") not in {"long", "short"}:
        return f"{prefix}_not_applicable"
    ref_direction = str(reference.get("direction"))
    return f"{prefix}_aligned" if direction == ref_direction else f"{prefix}_opposed"


def contextual_key(direction: str, fresh: Dict[str, Any], weekly: Optional[Dict[str, Any]], macro_context: Optional[Dict[str, Any]], policy: Dict[str, Any]) -> Dict[str, str]:
    weekly = weekly if isinstance(weekly, dict) else {}
    macro_context = macro_context if isinstance(macro_context, dict) else {}
    ma_context = macro_context.get("ma_structure") if isinstance(macro_context.get("ma_structure"), dict) else {}
    regime = str(weekly.get("regime") or "unknown") if weekly.get("data_quality") == "passed" else "unknown"
    momentum = momentum_context(direction, fresh, policy)
    macro_alignment = _alignment_context("macro", direction, macro_context)
    ma_alignment = _alignment_context("ma", direction, ma_context)
    key = f"momentum={momentum}|regime={regime}|macro={macro_alignment}|ma={ma_alignment}"
    return {
        "key": key,
        "momentum": momentum,
        "weekly_regime": regime,
        "macro_alignment": macro_alignment,
        "ma_alignment": ma_alignment,
    }


def _historical_candidate(leg: Dict[str, Any], method_id: str) -> Dict[str, Any]:
    decision = leg.get("entry_decision") if isinstance(leg.get("entry_decision"), dict) else {}
    candidates = decision.get("candidates") if isinstance(decision.get("candidates"), dict) else {}
    candidate = candidates.get(method_id)
    if isinstance(candidate, dict):
        return candidate
    if str(leg.get("strategy_id") or "") == method_id:
        return {"direction": leg.get("direction") or decision.get("direction")}
    return {}


def historical_leg_context(leg: Dict[str, Any], policy: Dict[str, Any], method_id: Optional[str] = None) -> str:
    decision = leg.get("entry_decision") if isinstance(leg.get("entry_decision"), dict) else {}
    candidate = _historical_candidate(leg, method_id) if method_id else {}
    direction = str(candidate.get("direction") or leg.get("direction") or decision.get("direction") or "neutral")
    fresh = decision.get("fresh_v2_signal") if isinstance(decision.get("fresh_v2_signal"), dict) else {}
    weekly = decision.get("weekly_features") if isinstance(decision.get("weekly_features"), dict) else {}
    if not weekly and leg.get("entry_regime"):
        weekly = {"data_quality": "passed", "regime": leg.get("entry_regime")}
    macro_context = decision.get("macro_context") if isinstance(decision.get("macro_context"), dict) else {}
    return contextual_key(direction, fresh, weekly, macro_context, policy)["key"]


def _historical_candidate_value(leg: Dict[str, Any], method_id: str) -> Optional[float]:
    outcomes = leg.get("candidate_outcomes") if isinstance(leg.get("candidate_outcomes"), dict) else {}
    outcome = outcomes.get(method_id)
    if isinstance(outcome, dict) and sf(outcome.get("net_result_percent")) is not None:
        return float(outcome["net_result_percent"])
    candidate = _historical_candidate(leg, method_id)
    direction = str(candidate.get("direction") or "neutral")
    entry, exit_price = sf(leg.get("entry_price")), sf(leg.get("exit_price"))
    cost = float(sf(leg.get("estimated_round_trip_cost_percent")) or 0.0)
    if entry is not None and exit_price is not None and candidate:
        return _candidate_net_percent(direction, entry, exit_price, cost)
    if str(leg.get("strategy_id") or "") == method_id and sf(leg.get("net_result_percent")) is not None:
        return float(leg["net_result_percent"])
    return None


def apply_contextual_learning(instrument_id: str, candidates: Dict[str, Dict[str, Any]], fresh: Dict[str, Any], policy: Dict[str, Any], weekly: Optional[Dict[str, Any]] = None, macro_context: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    cfg = policy.get("contextual_learning") or {}
    out = {key: dict(value) for key, value in candidates.items()}
    summary: Dict[str, Any] = {"enabled": bool(cfg.get("enabled")), "instrument_id": instrument_id, "schema": "2.0", "methods": {}}
    if not cfg.get("enabled") or instrument_id not in set(cfg.get("scope") or []):
        return out, summary
    allowed_methods = set(cfg.get("apply_to_methods") or out.keys())
    limit = int((policy.get("strategy_tournament") or {}).get("rolling_closed_legs") or 120)
    minimum = int(cfg.get("minimum_observations_before_adjustment") or 6)
    prior_n = float(cfg.get("prior_observations") or 5)
    weight = float(cfg.get("performance_weight") or 6.0)
    cap = abs(float(cfg.get("maximum_adjustment") or 2.0))
    legs = list(v4.iter_legs(instrument_id, limit))
    for method_id, row in out.items():
        direction = str(row.get("direction") or "neutral")
        context = contextual_key(direction, fresh, weekly, macro_context, policy)
        all_values = [value for leg in legs if (value := _historical_candidate_value(leg, method_id)) is not None]
        values = [
            value for leg in legs
            if (value := _historical_candidate_value(leg, method_id)) is not None
            and historical_leg_context(leg, policy, method_id) == context["key"]
        ]
        mean = sum(values) / len(values) if values else 0.0
        shrunk = mean * len(values) / (len(values) + prior_n) if values else 0.0
        eligible_context = context["momentum"] != "momentum_context_unavailable" and context["weekly_regime"] != "unknown"
        adjustment = max(-cap, min(cap, shrunk * weight)) if method_id in allowed_methods and eligible_context and len(values) >= minimum else 0.0
        row["base_conviction"] = row.get("conviction", 0.0)
        row["contextual_learning_context"] = context["key"]
        row["contextual_learning_components"] = context
        row["contextual_learning_count"] = len(values)
        row["contextual_candidate_observation_count"] = len(all_values)
        row["contextual_learning_mean_net_percent"] = round(mean, 6)
        row["contextual_learning_adjustment"] = round(adjustment, 4)
        row["conviction"] = round(max(0.0, float(row.get("conviction") or 0.0) + adjustment), 4)
        summary["methods"][method_id] = {
            "context": context,
            "count": len(values),
            "candidate_observation_count": len(all_values),
            "mean_net_percent": round(mean, 6),
            "shrunk_mean_percent": round(shrunk, 6),
            "adjustment": round(adjustment, 4),
            "eligible": bool(method_id in allowed_methods and eligible_context and len(values) >= minimum),
        }
    return out, summary


def learning_with_candidate_observations(learning: Dict[str, Any], contextual_learning: Dict[str, Any]) -> Dict[str, Any]:
    out = {key: value for key, value in learning.items()}
    methods = {key: dict(value) for key, value in (learning.get("methods") or {}).items()}
    for method_id, contextual in (contextual_learning.get("methods") or {}).items():
        row = methods.setdefault(method_id, {})
        selected_count = int(row.get("count") or 0)
        candidate_count = int(contextual.get("candidate_observation_count") or 0)
        if candidate_count > selected_count:
            row["count"] = candidate_count
            row["count_source"] = "counterfactual_candidate_observations_for_exploration_only"
            row["selected_leg_count"] = selected_count
    out["methods"] = methods
    return out


def ensure_all() -> Dict[str, Any]:
    now = legacy.now_local(); policy = read(POLICY, {}); method = read(METHOD, {})
    report = {"layer_version": VERSION, "checked_at": now.isoformat(timespec="seconds"), "actions": [], "status": "skipped"}
    if not policy.get("enabled"):
        report["reason"] = "policy_disabled"; write(REPORT, report); return report
    path = v4.ensure_week(now, method)
    if not path or not path.exists():
        report["reason"] = "current_week_missing"; write(REPORT, report); return report
    week = read(path, {})
    archived_after_close = archive_closed_learning_samples(week, policy)
    if archived_after_close:
        write(path, week)
        report["actions"].append({"action": "archive_closed_learning_samples", "count": archived_after_close})
    active, reason = v4.active_window(week, now)
    if not active:
        report["reason"] = reason
        if archived_after_close:
            report["status"] = "completed"
            report["week_id"] = week.get("week_id")
        write(REPORT, report)
        return report
    week.setdefault("base_method_version", week.get("method_version")); week.setdefault("base_forecast_hash", week.get("forecast_hash"))
    week.update(method_version=VERSION, model_status="experimental_governed_execution_parity_paper_only")
    items = {str(x.get("instrument_id")): x for x in week.get("instruments", [])}
    state = {"layer_version": VERSION, "generated_at": now.isoformat(timespec="seconds"), "instruments": {}}
    changed = False
    for p_cfg in v4.policy_instruments(policy):
        iid = str(p_cfg.get("instrument_id")); cfg = v4.instrument_cfg(method, iid); item = items.get(iid)
        if not cfg or item is None:
            report["actions"].append({"instrument_id": iid, "action": "skip", "reason": "missing_config"}); continue
        if closed_position(item) and archive_leg_with_contextual_outcomes(item, p_cfg): changed = True
        allowed, c = gate(item, method, iid); changed |= c
        blocked, c = lock_reentry(item, week, now); changed |= c
        if not allowed or blocked:
            state["instruments"][iid] = {"validation_gate": item.get("validation_gate"), "reentry_lock": item.get("reentry_lock")}
            report["actions"].append({"instrument_id": iid, "action": "no_trade", "reason": "gate_or_reentry_lock"}); continue
        fresh = v2.model_signal(cfg, method, str(week.get("week_id") or ""), now)
        weekly = v3.weekly_candle_signal(cfg, policy); regime = str(weekly.get("regime") or "unknown")
        macro_context = macro.context(iid, now, policy)
        learning = v4.learning_stats(iid, regime, policy)
        base_candidates = v4.candidate_methods(fresh, weekly, str(p_cfg.get("default_tie_direction") or "long"))
        candidates = macro.apply_to_candidates(iid, base_candidates, fresh, weekly, macro_context, policy)
        candidates, contextual_learning = apply_contextual_learning(iid, candidates, fresh, policy, weekly=weekly, macro_context=macro_context)
        choice_learning = learning_with_candidate_observations(learning, contextual_learning)
        decision = no_trade(
            v4.choose_governed(candidates, choice_learning, policy, iid),
            fresh,
            weekly,
            policy,
            macro_context,
        )
        decision["macro_context"] = macro_context
        decision["contextual_learning"] = contextual_learning
        state["instruments"][iid] = {"regime": regime, "learning": choice_learning, "selected_leg_learning": learning,
                                     "contextual_learning": contextual_learning, "decision": decision, "macro_context": macro_context,
                                     "validation_gate": item.get("validation_gate"), "reentry_lock": item.get("reentry_lock")}
        if _store_macro_review(item, macro_context):
            changed = True
        if open_position(item):
            item.update(continuous_exposure_active=True, continuous_exposure_status="open")
            report["actions"].append({"instrument_id": iid, "action": "keep_open", "direction": item.get("direction"),
                                      "macro_score": macro_context.get("score"),
                                      "next_governed_review": "daily_review_23_00_europe_warsaw"})
            continue

        # Before changing/refreshing a pending thesis, settle any recent touch
        # of its already-frozen target. This closes the expiry-boundary gap
        # between two scheduler runs without allowing historical backfills.
        recovered = recover_frozen_pending_touch(item, str(cfg.get("symbol") or ""), now)
        if recovered is not None:
            pending, point = recovered
            entry_plan = pending.get("entry_price_plan") if isinstance(pending.get("entry_price_plan"), dict) else {}
            point, execution_verification = epe_verified_entry_point(pending, point, now)
            execution_mode = str(entry_plan.get("execution_mode") or "limit_pullback")
            if point is None:
                item["execution_price_engine"] = execution_verification
                report["actions"].append({
                    "instrument_id": iid,
                    "action": "reject_unverified_market_entry" if execution_mode == "market_now" else "reject_unverified_frozen_target_touch",
                    "reason": execution_verification.get("reason"),
                    "target_price": entry_plan.get("target_price"),
                })
                changed = True
                continue
            frozen = pending["decision"]
            v4.open_leg(item, cfg, frozen, pending["fresh_signal"], pending["weekly_signal"], point, now)
            item.update(
                entry_decision_at=pending["decided_at"],
                entry_execution_rule=(
                    "epe_verified_wes_1_3_1_market_now_5m"
                    if execution_mode == "market_now"
                    else "epe_verified_frozen_wes_1_3_1_limit_target_touch"
                ),
                entry_price_plan_frozen=entry_plan,
                entry_quality_status=(
                    "wes_1_3_1_market_entry_filled"
                    if execution_mode == "market_now"
                    else "wes_1_3_1_frozen_entry_target_filled"
                ),
                entry_macro_context=pending.get("macro_context"),
                execution_price_engine=execution_verification,
                pending_entry_decision=None,
                next_entry_status="open",
            )
            changed = True
            report["actions"].append({
                "instrument_id": iid,
                "action": "open_at_preexisting_market_entry" if execution_mode == "market_now" else "open_at_preexisting_frozen_entry_target",
                "direction": frozen.get("direction"),
                "target_price": entry_plan.get("target_price"),
                "decision_at": item["entry_decision_at"],
                "entry_at": item.get("entry_captured_at"),
                "entry_price": item.get("entry_price"),
                "macro_score": (pending.get("macro_context") or {}).get("score"),
            })
            continue

        if decision.get("direction") not in {"long", "short"}:
            abstain(item, decision); changed = True
            report["actions"].append({"instrument_id": iid, "action": "no_trade", "reason_codes": decision.get("reason_codes")}); continue
        authorized, authorization_reason = wes_authorization_matches(item, decision, now, policy)
        if not authorized and authorization_reason in {"wes_entry_authorization_expired", "wes_entry_authorization_missing"}:
            if refresh_expired_authorization_for_live_decision(item, decision, now, policy):
                changed = True
                authorized, authorization_reason = wes_authorization_matches(item, decision, now, policy)
                report["actions"].append({
                    "instrument_id": iid,
                    "action": "live_reauthorize_after_missed_execution_window",
                    "direction": decision.get("direction"),
                    "strategy_id": decision.get("strategy_id"),
                    "authorized_at": (item.get("wes_entry_authorization") or {}).get("authorized_at"),
                    "expires_at": (item.get("wes_entry_authorization") or {}).get("expires_at"),
                })
        if not authorized:
            blocked = {
                "strategy_id": "no_trade",
                "direction": "neutral",
                "raw_score": 0.0,
                "utility": decision.get("utility", 0.0),
                "reason_codes": [authorization_reason],
                "blocked_candidate": {
                    "strategy_id": decision.get("strategy_id"),
                    "direction": decision.get("direction"),
                    "raw_score": decision.get("raw_score"),
                    "utility": decision.get("utility"),
                },
                "candidates": decision.get("candidates", {}),
            }
            if not closed_position(item):
                abstain(item, blocked)
            else:
                settle_unfilled_reentry(item, blocked, authorization_reason)
            changed = True
            report["actions"].append({"instrument_id": iid, "action": "no_trade", "reason_codes": [authorization_reason]})
            continue
        saved_pending = item.get("pending_entry_decision")
        pending = (
            saved_pending
            if pending_matches_wes_authorization(item, saved_pending, now)
            else freeze_decision(item, decision, fresh, weekly, now, policy)
        )
        changed = True
        if maybe_promote_pending_to_market(pending, decision, fresh, weekly, policy, now):
            item["pending_entry_decision"] = pending
            item["next_entry_status"] = "waiting_for_market_entry"
            item["entry_quality_status"] = "wes_1_3_1_limit_promoted_to_market"
            report["actions"].append({
                "instrument_id": iid,
                "action": "promote_limit_to_market",
                "direction": (pending.get("decision") or {}).get("direction"),
                "original_target_price": (pending.get("entry_price_plan") or {}).get("original_target_price"),
                "promoted_at": (pending.get("entry_price_plan") or {}).get("promoted_from_limit_at"),
                "market_entry_diagnostics": (pending.get("entry_price_plan") or {}).get("market_entry_diagnostics"),
            })
        point = entry_point(str(cfg.get("symbol") or ""), pending, now)
        entry_plan = pending.get("entry_price_plan") if isinstance(pending.get("entry_price_plan"), dict) else {}
        execution_mode = str(entry_plan.get("execution_mode") or "limit_pullback")
        if not point:
            report["actions"].append({
                "instrument_id": iid,
                "action": "wait_for_market_entry" if execution_mode == "market_now" else "wait_for_entry_target",
                "direction": (pending.get("decision") or {}).get("direction"),
                "target_price": entry_plan.get("target_price"),
                "reference_price": entry_plan.get("reference_price"),
                "order_type": entry_plan.get("order_type"),
                "expires_at": entry_plan.get("expires_at"),
                "overextension_score": (entry_plan.get("inputs") or {}).get("overextension_score"),
                "market_entry_diagnostics": entry_plan.get("market_entry_diagnostics"),
            })
            continue
        point, execution_verification = epe_verified_entry_point(pending, point, now)
        if point is None:
            item["execution_price_engine"] = execution_verification
            report["actions"].append({
                "instrument_id": iid,
                "action": "reject_unverified_market_entry" if execution_mode == "market_now" else "reject_unverified_frozen_target_touch",
                "reason": execution_verification.get("reason"),
                "target_price": entry_plan.get("target_price"),
            })
            continue
        frozen = pending["decision"]
        v4.open_leg(item, cfg, frozen, pending["fresh_signal"], pending["weekly_signal"], point, now)
        item.update(
            entry_decision_at=pending["decided_at"],
            entry_execution_rule=(
                "epe_verified_wes_1_3_1_market_now_5m"
                if execution_mode == "market_now"
                else "epe_verified_frozen_wes_1_3_1_limit_target_touch"
            ),
            entry_price_plan_frozen=entry_plan,
            entry_quality_status=(
                "wes_1_3_1_market_entry_filled"
                if execution_mode == "market_now"
                else "wes_1_3_1_frozen_entry_target_filled"
            ),
            entry_macro_context=pending.get("macro_context"),
            execution_price_engine=execution_verification,
            pending_entry_decision=None,
            next_entry_status="open",
        )
        report["actions"].append({
            "instrument_id": iid,
            "action": "open_at_market_now" if execution_mode == "market_now" else "open_at_frozen_entry_target",
            "direction": frozen.get("direction"),
            "target_price": entry_plan.get("target_price"),
            "decision_at": item["entry_decision_at"],
            "entry_at": item.get("entry_captured_at"),
            "entry_price": item.get("entry_price"),
            "macro_score": (pending.get("macro_context") or {}).get("score"),
        })
    week["multi_instrument_exposure_layer"] = {
        "enabled": True, "version": VERSION, "common_validation_gate": True,
        "wes_1_3_1_directional_admission": True,
        "price_aware_entry_engine": True,
        "hybrid_market_or_frozen_limit_entry": True,
        "champion_challenger_execution_authority": True,
        "retroactive_entries_forbidden": True, "same_week_reentry_block_after_invalidation": True,
        "no_trade_first_class": True, "weekly_candles_used": True,
        "eurusd_oil_us10y_context_used": True,
        "contextual_momentum_learning": True,
        "contextual_multidimensional_learning": True,
        "counterfactual_candidate_learning": True,
        "counterfactual_observations_reduce_exploration_only": True,
        "learning_settlement_archive_before_window_exit": True,
        "execution_parity_report": "data/investments/weekly_execution_parity_v5.json",
        "last_checked_at": now.isoformat(timespec="seconds"),
    }
    if changed: write(path, week)
    write(STATE, state); report.update(status="completed", week_id=week.get("week_id")); write(REPORT, report); return report


def auto() -> None:
    legacy.capture_live_prices(); now = legacy.now_local(); method = read(METHOD, {})
    if now.weekday() == 6: v2.make_forecast()
    elif now.weekday() <= 4 and not v2.current_week_path(now).exists(): v4.emergency_current_week(now, method)
    v2.review_open_positions(); v2.close_due_weeks(); ensure_all()


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--mode", choices=["auto", "forecast", "close", "ensure-exposure", "render"], default="auto")
    mode = parser.parse_args().mode
    if mode == "forecast": v2.make_forecast(); legacy.capture_live_prices()
    elif mode == "close": legacy.capture_live_prices(); v2.review_open_positions(); v2.close_due_weeks(); ensure_all()
    elif mode == "ensure-exposure": legacy.capture_live_prices(); ensure_all()
    elif mode == "render": legacy.capture_live_prices()
    else: auto()


if __name__ == "__main__": main()
