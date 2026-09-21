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

ROOT = Path(__file__).resolve().parents[1]
METHOD = ROOT / "data/investments/methodology.json"
POLICY = ROOT / "data/investments/multi_instrument_exposure_policy.json"
STATE = ROOT / "data/investments/multi_instrument_exposure_state_v5.json"
REPORT = ROOT / "data/investments/multi_instrument_exposure_report_v5.json"
VERSION = "5.8.0-experimental"

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
        "version": str(cfg.get("version") or "WES-1.1.0"),
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


def abstain(item: Dict[str, Any], decision: Dict[str, Any]) -> None:
    item.update(pending_entry_decision=None, next_entry_status="no_trade", no_trade_decision=decision,
                no_trade_reason=",".join(decision.get("reason_codes", [])), continuous_exposure_active=False,
                continuous_exposure_status="no_trade")
    if not closed_position(item):
        item.update(direction="neutral", score=0, trade_status="no_trade", entry_price=None,
                    entry_captured_at=None, entry_source=None, risk_plan=None, result="no_trade",
                    result_value=0.0, result_percent=0.0)


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def build_entry_price_plan(
    item: Dict[str, Any],
    decision: Dict[str, Any],
    fresh: Dict[str, Any],
    now: datetime,
    policy: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Freeze the desired entry price before execution.

    WES 1.2 deliberately separates directional admission from price execution.
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
        "version": str(cfg.get("version") or "WES-1.2.0"),
        "frozen_at": now.isoformat(timespec="seconds"),
        "instrument_id": iid,
        "direction": direction,
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
        raise RuntimeError("WES 1.2 entry price plan could not be frozen")
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
        "rule": "execute_only_when_frozen_entry_target_is_touched_after_decision",
    }
    item.update(
        pending_entry_decision=pending,
        trade_status="pending",
        next_entry_status="waiting_for_entry_target",
        entry_quality_status="wes_1_2_waiting_for_frozen_entry_target",
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


def entry_point(
    symbol: str,
    pending: Dict[str, Any],
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Execute only a pre-frozen WES 1.2 price target; never choose a market price here."""
    plan = pending.get("entry_price_plan") if isinstance(pending.get("entry_price_plan"), dict) else {}
    direction = str(plan.get("direction") or (pending.get("decision") or {}).get("direction") or "neutral")
    target = sf(plan.get("target_price"))
    start = parse_dt(plan.get("entry_not_before") or pending.get("entry_not_before"))
    expires = parse_dt(plan.get("expires_at"))
    checked_at = now or legacy.now_local()
    if direction not in {"long", "short"} or target is None or target <= 0 or start is None or expires is None:
        return None
    end = min(checked_at, expires)
    if end < start:
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
        if decision.get("direction") not in {"long", "short"}:
            abstain(item, decision); changed = True
            report["actions"].append({"instrument_id": iid, "action": "no_trade", "reason_codes": decision.get("reason_codes")}); continue
        authorized, authorization_reason = wes_authorization_matches(item, decision, now, policy)
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
                item.update(pending_entry_decision=None, next_entry_status="no_trade")
            changed = True
            report["actions"].append({"instrument_id": iid, "action": "no_trade", "reason_codes": [authorization_reason]})
            continue
        saved_pending = item.get("pending_entry_decision")
        pending = saved_pending if pending_matches_wes_authorization(item, saved_pending) else freeze_decision(item, decision, fresh, weekly, now)
        changed = True
        point = entry_point(str(cfg.get("symbol") or ""), pending)
        if not point:
            report["actions"].append({"instrument_id": iid, "action": "defer_entry", "entry_not_before": pending.get("entry_not_before")}); continue
        frozen = pending["decision"]; v4.open_leg(item, cfg, frozen, pending["fresh_signal"], pending["weekly_signal"], point, now)
        item.update(entry_decision_at=pending["decided_at"], entry_execution_rule="first_completed_5m_bar_on_or_after_frozen_decision",
                    entry_macro_context=pending.get("macro_context"), pending_entry_decision=None, next_entry_status="open")
        report["actions"].append({"instrument_id": iid, "action": "open", "direction": frozen.get("direction"),
                                  "decision_at": item["entry_decision_at"], "entry_at": item.get("entry_captured_at"),
                                  "macro_score": (pending.get("macro_context") or {}).get("score")})
    week["multi_instrument_exposure_layer"] = {
        "enabled": True, "version": VERSION, "common_validation_gate": True,
        "wes_1_1_directional_admission": True,
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
