#!/usr/bin/env python3
"""Canonical multi-position Stock Trading portfolio for GPW and US equities.

The daily GPW/US selectors are candidate generators only. This module owns the
actual Stock Trading portfolio contract:
- CASH is valid; no candidate is admitted only because a slot is empty,
- at most three GPW and three US positions may be open independently,
- there is no fixed holding deadline or week-end time stop,
- every open position always has an explicit SL and TP,
- SL/TP are reviewed once per market day and may be recalculated,
- a failed risk recalculation preserves the last valid SL/TP,
- SL/TP first touch and model thesis invalidation may close a position at any age.

Only long equity positions are admitted today because the existing GPW/US stock
candidate engines publish long setups. The state schema is intentionally market-
neutral so short support can be added later without changing the portfolio caps.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import tempfile
import time
import urllib.parse
import urllib.request
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "data/investments/stock_trading_policy.json"
STATE_PATH = ROOT / "data/investments/stock_trading_portfolio.json"
US_LEGACY_BOOK = ROOT / "data/investments/us_daily_stock_position.json"
GPW_HISTORY_DIR = ROOT / "data/investments/gpw_daily_pick_history"
SCHEMA = "stock-trading-portfolio-v1"
USER_AGENT = "BriefRooms-Stock-Trading-Portfolio/1.0"
MARKET_TZ = {"GPW": ZoneInfo("Europe/Warsaw"), "US": ZoneInfo("America/New_York")}
DECISIONS = {"GPW": "TRANSAKCJA", "US": "TRADE"}
POSITION_CURRENCY = {"GPW": "PLN", "US": "USD"}
FIXED_NOTIONAL_POLICY_VERSION = "FIXED_NOTIONAL_V1"
HISTORY_NOTIONAL_NORMALIZATION_VERSION = "FIXED_NOTIONAL_HISTORY_V1"
RETRIABLE_POLICY_REJECTIONS = {"forced_daily_candidate_rejected", "entry_score_below_threshold"}


def _load(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(body)
        name = handle.name
    Path(name).replace(path)


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _iso(now: datetime) -> str:
    return now.isoformat(timespec="seconds")


def _market_datetime(value: Any, market: str) -> datetime | None:
    """Parse a timestamp into the exchange timezone without inventing a date."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    tz = MARKET_TZ.get(str(market or "").upper())
    if parsed.tzinfo is None:
        if tz is None:
            return None
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz) if tz is not None else parsed


def risk_geometry_effective_from(position: Mapping[str, Any]) -> datetime | None:
    """Earliest timestamp from which the *current* SL/TP geometry may trigger.

    For a long position the current risk geometry did not exist before the
    position was opened, and a later ratchet did not exist before its
    risk_last_changed_at timestamp.  Using any earlier candle would be a
    retroactive fill.
    """
    market = str(position.get("market") or "").upper()
    candidates = [
        _market_datetime(position.get("opened_at"), market),
        _market_datetime(position.get("risk_last_changed_at"), market),
    ]
    valid = [value for value in candidates if value is not None]
    return max(valid) if valid else None


def history_normalized_metrics(
    position: Mapping[str, Any],
    market: str | None = None,
    *,
    target_notional: float = 5000.0,
    quantity_precision: int = 8,
) -> dict[str, Any]:
    """Derived 5K analytics for history without rewriting execution facts."""
    market_name = str(market or position.get("market") or "").upper()
    if market_name not in POSITION_CURRENCY:
        return {}
    entry = _float(position.get("entry") or position.get("entry_price") or position.get("open_price"))
    exit_price = _float(position.get("exit_price") or position.get("exit") or position.get("close_price"))
    if entry is None or entry <= 0 or exit_price is None or exit_price <= 0:
        return {}
    quantity = round(float(target_notional) / entry, int(quantity_precision))
    if quantity <= 0:
        return {}
    entry_notional = entry * quantity
    exit_notional = exit_price * quantity
    pnl_amount = (exit_price - entry) * quantity
    return {
        "history_normalization_version": HISTORY_NOTIONAL_NORMALIZATION_VERSION,
        "history_normalization_basis": "ANALYTICAL_FIXED_5000_NOTIONAL",
        "history_position_currency": POSITION_CURRENCY[market_name],
        "history_target_position_notional": round(float(target_notional), 2),
        "history_normalized_quantity": quantity,
        "history_normalized_entry_notional": round(entry_notional, 2),
        "history_normalized_exit_notional": round(exit_notional, 2),
        "history_normalized_pnl_amount": round(pnl_amount, 2),
    }


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    policy = _load(path, {})
    if not isinstance(policy, dict) or not isinstance(policy.get("markets"), dict):
        raise ValueError("Stock Trading policy is missing or invalid")
    if policy.get("forced_trade_allowed") is not False:
        raise ValueError("Stock Trading production policy must keep forced_trade_allowed=false")
    for market in ("GPW", "US"):
        cfg = policy["markets"].get(market) or {}
        if int(cfg.get("max_open_positions") or 0) != 3:
            raise ValueError(f"{market} Stock Trading cap must be exactly 3")
        if float(cfg.get("target_position_notional") or 0) != 5000.0:
            raise ValueError(f"{market} target_position_notional must be exactly 5000")
        if str(cfg.get("position_currency") or "") != POSITION_CURRENCY[market]:
            raise ValueError(f"{market} position_currency must be {POSITION_CURRENCY[market]}")
        if cfg.get("fractional_quantity_allowed") is not True:
            raise ValueError(f"{market} fixed-notional policy requires fractional_quantity_allowed=true")
        if str(cfg.get("sizing_policy_version") or "") != FIXED_NOTIONAL_POLICY_VERSION:
            raise ValueError(f"{market} sizing_policy_version must be {FIXED_NOTIONAL_POLICY_VERSION}")
    return policy


def empty_state(now: datetime | None = None, policy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or load_policy()
    return {
        "schema_version": SCHEMA,
        "policy_version": policy.get("policy_version"),
        "updated_at": _iso(now) if now else None,
        "objective": "maximize_net_expectancy_not_trade_frequency",
        "fixed_holding_deadline": None,
        "markets": {
            market: {
                "max_open_positions": int(policy["markets"][market]["max_open_positions"]),
                "target_position_notional": float(policy["markets"][market].get("target_position_notional") or 5000.0),
                "position_currency": str(policy["markets"][market].get("position_currency") or POSITION_CURRENCY[market]),
                "sizing_policy_version": str(policy["markets"][market].get("sizing_policy_version") or FIXED_NOTIONAL_POLICY_VERSION),
                "open_positions": [],
                "closed_positions": [],
                "last_candidate_key": None,
                "last_candidate_decision": None,
                "last_candidate_reason": None,
            }
            for market in ("GPW", "US")
        },
    }


def _ensure_fixed_notional_sizing(position: Mapping[str, Any], market: str, cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Backfill the mandatory 5K sizing fields on older still-open positions."""
    updated = deepcopy(dict(position))
    target_notional = float(cfg.get("target_position_notional") or 5000.0)
    entry = _float(updated.get("entry") or updated.get("entry_price") or updated.get("open_price"))
    if target_notional != 5000.0 or entry is None or entry <= 0:
        return updated
    precision = int(cfg.get("quantity_precision") or 8)
    quantity = _float(updated.get("quantity"))
    if quantity is None or quantity <= 0:
        quantity = round(target_notional / entry, precision)
        updated["quantity"] = quantity
    updated["sizing_policy_version"] = str(cfg.get("sizing_policy_version") or FIXED_NOTIONAL_POLICY_VERSION)
    updated["position_currency"] = str(cfg.get("position_currency") or POSITION_CURRENCY[market])
    updated["target_position_notional"] = round(target_notional, 2)
    updated["entry_notional"] = round(entry * quantity, 2)
    updated["fractional_quantity"] = True
    initial_risk = _float(updated.get("initial_risk_amount"))
    if initial_risk is not None:
        updated["initial_risk_cash"] = round(initial_risk * quantity, 2)
    return updated


def load_state(path: Path = STATE_PATH, *, now: datetime | None = None, policy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or load_policy()
    state = _load(path)
    if isinstance(state, dict) and state.get("schema_version") == SCHEMA:
        for market in ("GPW", "US"):
            row = state.setdefault("markets", {}).setdefault(market, {})
            row["max_open_positions"] = int(policy["markets"][market]["max_open_positions"])
            row["target_position_notional"] = float(policy["markets"][market].get("target_position_notional") or 5000.0)
            row["position_currency"] = str(policy["markets"][market].get("position_currency") or POSITION_CURRENCY[market])
            row["sizing_policy_version"] = str(policy["markets"][market].get("sizing_policy_version") or FIXED_NOTIONAL_POLICY_VERSION)
            row.setdefault("open_positions", [])
            row["open_positions"] = [
                _ensure_fixed_notional_sizing(position, market, policy["markets"][market])
                if isinstance(position, Mapping) else position
                for position in row["open_positions"]
            ]
            row.setdefault("closed_positions", [])
            row.setdefault("last_candidate_key", None)
        state["fixed_holding_deadline"] = None
        state["policy_version"] = policy.get("policy_version")
        return state
    state = empty_state(now, policy)
    bootstrap_legacy_positions(state, now=now or datetime.now(ZoneInfo("UTC")), policy=policy)
    return state


def save_state(state: Mapping[str, Any], path: Path = STATE_PATH, *, now: datetime) -> None:
    payload = deepcopy(dict(state))
    payload["schema_version"] = SCHEMA
    payload["updated_at"] = _iso(now)
    payload["fixed_holding_deadline"] = None
    _atomic(path, payload)


def market_state(state: Mapping[str, Any], market: str) -> dict[str, Any]:
    market = market.upper()
    value = (state.get("markets") or {}).get(market)
    if not isinstance(value, dict):
        raise ValueError(f"Unknown Stock Trading market: {market}")
    return value


def open_positions(state: Mapping[str, Any], market: str) -> list[dict[str, Any]]:
    rows = market_state(state, market).get("open_positions") or []
    return [dict(row) for row in rows if isinstance(row, Mapping) and str(row.get("status") or "OPEN").upper() == "OPEN"]


def available_slots(state: Mapping[str, Any], market: str, policy: Mapping[str, Any] | None = None) -> int:
    policy = policy or load_policy()
    cap = int(policy["markets"][market.upper()]["max_open_positions"])
    return max(0, cap - len(open_positions(state, market)))


def valid_long_risk(entry: Any, stop: Any, target: Any, *, max_risk_percent: float = 1.0) -> bool:
    entry_f, stop_f, target_f = _float(entry), _float(stop), _float(target)
    if entry_f is None or stop_f is None or target_f is None or entry_f <= 0:
        return False
    if not stop_f < entry_f < target_f:
        return False
    return (entry_f - stop_f) / entry_f <= float(max_risk_percent) + 1e-12


def candidate_key(payload: Mapping[str, Any]) -> str:
    selection = payload.get("selection") or {}
    return ":".join(
        str(value or "")
        for value in (payload.get("date"), payload.get("generated_at"), selection.get("symbol") or selection.get("ticker"))
    )


def _forced_candidate(payload: Mapping[str, Any]) -> bool:
    selection = payload.get("selection") or {}
    review = selection.get("review") or {}
    text = " ".join(
        str(value or "").upper()
        for value in (selection.get("selection_mode"), review.get("mode"), payload.get("reason"))
    )
    return "MANDATORY" in text or "FORCED" in text


def candidate_admission_authorized(policy: Mapping[str, Any], authority: str = "legacy") -> bool:
    """Only the v2 production bridge may create new production positions.

    Legacy Daily GPW/US paths remain useful for research and candidate
    generation, but cannot bypass the bridge's prospective quote revalidation.
    """
    champion = str(policy.get("champion_engine") or "").lower()
    legacy_enabled = policy.get("legacy_candidate_admission_enabled")
    if champion == "v2" and legacy_enabled is False:
        return str(authority or "").strip().lower() == "v2_production_bridge"
    return True


def _governed_gpw_final_candidate(market: str, payload: Mapping[str, Any]) -> bool:
    """Recognize the deterministic GPW final selector as a qualified model path.

    Its score is a ranking/conviction measure, not an admission veto. The
    selector still has to publish healthy canonical data gates, a positive
    conservative EV and the normal portfolio risk geometry below.
    """
    selection = payload.get("selection") or {}
    quality = payload.get("data_quality") or {}
    mandatory = quality.get("mandatory_selection") or {}
    return (
        market == "GPW"
        and str(selection.get("selection_mode") or "").upper() == "MANDATORY_DAILY_FINAL"
        and str(quality.get("status") or "").lower() == "healthy"
        and mandatory.get("applied") is True
    )


def qualify_candidate(market: str, payload: Mapping[str, Any], policy: Mapping[str, Any] | None = None) -> tuple[bool, str]:
    market = market.upper()
    policy = policy or load_policy()
    cfg = policy["markets"][market]
    if str(payload.get("decision") or "") != DECISIONS[market]:
        return False, "no_trade_or_data_state"
    governed_final = _governed_gpw_final_candidate(market, payload)
    if _forced_candidate(payload) and not governed_final:
        return False, "forced_daily_candidate_rejected"
    selection = payload.get("selection")
    if not isinstance(selection, Mapping):
        return False, "selection_missing"
    score = _float(selection.get("score"))
    if score is None or (score < float(cfg["minimum_entry_score"]) and not governed_final):
        return False, "entry_score_below_threshold"
    rr = _float(selection.get("reward_risk"))
    if rr is None or rr < float(cfg["minimum_reward_risk"]):
        return False, "reward_risk_below_threshold"
    risk_pct = _float(selection.get("risk_percent"))
    if risk_pct is None or risk_pct <= 0 or risk_pct > float(cfg["maximum_risk_percent"]):
        return False, "risk_percent_invalid"
    snapshot = selection.get("market_snapshot") or {}
    entry = _float(snapshot.get("last")) or _float(selection.get("reference_price"))
    if not valid_long_risk(entry, selection.get("stop"), selection.get("target"), max_risk_percent=float(cfg["maximum_risk_percent"])):
        return False, "sl_tp_geometry_invalid"
    ev = selection.get("expected_value_model") or {}
    conservative_ev = _float(ev.get("conservative_ev_r"))
    if conservative_ev is not None and conservative_ev <= 0:
        return False, "non_positive_conservative_expected_value"
    return True, "qualified_high_expectancy_candidate"


def position_from_candidate(market: str, payload: Mapping[str, Any], *, now: datetime, market_cfg: Mapping[str, Any] | None = None) -> dict[str, Any]:
    market = market.upper()
    selection = payload.get("selection") or {}
    snapshot = selection.get("market_snapshot") or {}
    entry = _float(snapshot.get("last")) or _float(selection.get("reference_price"))
    if entry is None:
        raise ValueError("candidate entry price missing")
    symbol = str(selection.get("symbol") or selection.get("ticker") or "").upper()
    if not symbol:
        raise ValueError("candidate symbol missing")
    risk_pct = _float(selection.get("risk_percent"))
    candidate_rr = _float(selection.get("reward_risk"))
    cfg = market_cfg or load_policy()["markets"][market]
    score = float(selection.get("score") or 0.0)
    strategic_rr = float(cfg.get("strategic_target_reward_risk") or 3.0)
    if score >= 80.0:
        strategic_rr = max(strategic_rr, float(cfg.get("exceptional_thesis_target_reward_risk") or 4.0))
    elif score >= 70.0:
        strategic_rr = max(strategic_rr, float(cfg.get("strong_thesis_target_reward_risk") or 3.5))
    initial_risk = float(entry) - float(selection["stop"])
    target = max(float(selection["target"]), float(entry) + initial_risk * strategic_rr)
    rr = max(float(candidate_rr or 0.0), strategic_rr)
    target_notional = float(cfg.get("target_position_notional") or 5000.0)
    if target_notional != 5000.0:
        raise ValueError(f"{market} target_position_notional must be exactly 5000")
    quantity_precision = int(cfg.get("quantity_precision") or 8)
    quantity = round(target_notional / float(entry), quantity_precision)
    if quantity <= 0:
        raise ValueError("fixed-notional quantity must be positive")
    entry_notional = float(entry) * quantity
    return {
        "position_id": f"{market.lower()}:{now.strftime('%Y%m%dT%H%M%S')}:{symbol}",
        "market": market,
        "status": "OPEN",
        "symbol": symbol,
        "ticker": selection.get("ticker") or symbol,
        "name": selection.get("name"),
        "sector": selection.get("sector"),
        "opened_at": _iso(now),
        "source_candidate_date": payload.get("date"),
        "source_candidate_generated_at": payload.get("generated_at"),
        "entry": round(entry, 8),
        "sizing_policy_version": str(cfg.get("sizing_policy_version") or FIXED_NOTIONAL_POLICY_VERSION),
        "position_currency": str(cfg.get("position_currency") or POSITION_CURRENCY[market]),
        "target_position_notional": round(target_notional, 2),
        "quantity": quantity,
        "entry_notional": round(entry_notional, 2),
        "fractional_quantity": True,
        "stop": round(float(selection["stop"]), 8),
        "target": round(target, 8),
        "initial_risk_amount": round(initial_risk, 8),
        "initial_risk_cash": round(initial_risk * quantity, 2),
        "strategic_target_rr": round(strategic_rr, 4),
        "risk_percent": float(risk_pct),
        "reward_risk": float(rr),
        "entry_score": float(selection.get("score") or 0.0),
        "last_mark": round(entry, 8),
        "last_reviewed_at": _iso(now),
        "risk_last_changed_at": _iso(now),
        "risk_review_date": now.astimezone(MARKET_TZ[market]).date().isoformat(),
        "holding_policy": "OPEN_ENDED_MODEL_CONTROLLED",
        "take_profit_mode": "THESIS_RUNNER_CHECKPOINT" if cfg.get("profit_runner_enabled") is True else "HARD_TAKE_PROFIT",
        "profit_runner_enabled": cfg.get("profit_runner_enabled") is True,
        "profit_runner_upside_cap_percent": cfg.get("profit_runner_upside_cap_percent"),
        "scheduled_exit": None,
        "valid_until": None,
        "time_stop": None,
        "thesis_status": "ACTIVE",
        "risk_reviews": [],
        "peak_mark": round(entry, 8),
        "peak_thesis_score": score,
    }


def upgrade_open_position_geometry(position: Mapping[str, Any], market_cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Upgrade legacy/tight positions without ever widening their downside."""
    updated = deepcopy(dict(position))
    entry = float(updated["entry"])
    stop = float(updated["stop"])
    initial_risk = float(updated.get("initial_risk_amount") or max(entry - stop, 1e-12))
    score = float(updated.get("entry_score") or 0.0)
    strategic_rr = float(market_cfg.get("strategic_target_reward_risk") or 3.0)
    if score >= 80.0:
        strategic_rr = max(strategic_rr, float(market_cfg.get("exceptional_thesis_target_reward_risk") or 4.0))
    elif score >= 70.0:
        strategic_rr = max(strategic_rr, float(market_cfg.get("strong_thesis_target_reward_risk") or 3.5))
    updated["initial_risk_amount"] = round(initial_risk, 8)
    updated["strategic_target_rr"] = round(strategic_rr, 4)
    updated["reward_risk"] = round(max(float(updated.get("reward_risk") or 0.0), strategic_rr), 4)
    updated["target"] = round(max(float(updated["target"]), entry + initial_risk * strategic_rr), 8)
    updated["peak_mark"] = round(max(float(updated.get("peak_mark") or entry), float(updated.get("last_mark") or entry)), 8)
    updated["peak_thesis_score"] = max(float(updated.get("peak_thesis_score") or score), score)
    updated["profit_runner_enabled"] = market_cfg.get("profit_runner_enabled") is True
    updated["take_profit_mode"] = "THESIS_RUNNER_CHECKPOINT" if updated["profit_runner_enabled"] else "HARD_TAKE_PROFIT"
    updated["profit_runner_upside_cap_percent"] = market_cfg.get("profit_runner_upside_cap_percent")
    return updated


def admit_candidate(
    state: Mapping[str, Any],
    market: str,
    payload: Mapping[str, Any],
    *,
    now: datetime,
    policy: Mapping[str, Any] | None = None,
    authority: str = "legacy",
) -> tuple[dict[str, Any], dict[str, Any]]:
    market = market.upper()
    policy = policy or load_policy()
    updated = deepcopy(dict(state))
    if not candidate_admission_authorized(policy, authority):
        return updated, {
            "action": "candidate_admission_blocked",
            "market": market,
            "reason": "v2_production_bridge_only",
            "authority": authority,
        }
    row = market_state(updated, market)
    key = candidate_key(payload)
    if (
        key
        and row.get("last_candidate_key") == key
        and row.get("last_candidate_reason") not in RETRIABLE_POLICY_REJECTIONS
    ):
        return updated, {"action": "candidate_already_reviewed", "market": market, "candidate_key": key}
    row["last_candidate_key"] = key
    ok, reason = qualify_candidate(market, payload, policy)
    row["last_candidate_decision"] = "ADMIT" if ok else "CASH"
    row["last_candidate_reason"] = reason
    row["last_candidate_reviewed_at"] = _iso(now)
    if not ok:
        return updated, {"action": "cash", "market": market, "reason": reason}
    selection = payload.get("selection") or {}
    symbol = str(selection.get("symbol") or selection.get("ticker") or "").upper()
    if any(str(pos.get("symbol") or "").upper() == symbol for pos in open_positions(updated, market)):
        return updated, {"action": "hold_existing_symbol", "market": market, "symbol": symbol}
    if available_slots(updated, market, policy) <= 0:
        return updated, {"action": "portfolio_full", "market": market, "reason": "market_cap_3"}
    position = position_from_candidate(market, payload, now=now, market_cfg=policy["markets"][market])
    row["open_positions"] = open_positions(updated, market) + [position]
    return updated, {
        "action": "open",
        "market": market,
        "position_id": position["position_id"],
        "symbol": symbol,
        "sizing_policy_version": position["sizing_policy_version"],
        "target_position_notional": position["target_position_notional"],
        "position_currency": position["position_currency"],
        "quantity": position["quantity"],
    }


def _closure(position: Mapping[str, Any], *, now: datetime, exit_price: float, reason: str, conservative_same_bar: bool = False) -> dict[str, Any]:
    entry = float(position["entry"])
    exit_price_f = float(exit_price)
    pnl_pct = (exit_price_f / entry - 1.0) * 100.0 if entry else 0.0
    initial_risk = max(entry - float(position.get("stop") or entry), 1e-12)
    r_multiple = (exit_price_f - entry) / initial_risk
    quantity = _float(position.get("quantity"))
    pnl_amount = (exit_price_f - entry) * quantity if quantity is not None else None
    exit_notional = exit_price_f * quantity if quantity is not None else None
    closure = {
        **deepcopy(dict(position)),
        "status": "CLOSED",
        "closed_at": _iso(now),
        "exit_price": round(exit_price_f, 8),
        "exit_reason": reason,
        "return_percent": round(pnl_pct, 5),
        "r_multiple": round(r_multiple, 4),
        "conservative_same_bar": bool(conservative_same_bar),
        "scheduled_exit": None,
        "valid_until": None,
        "time_stop": None,
    }
    if pnl_amount is not None:
        closure["pnl_amount"] = round(pnl_amount, 2)
        closure["exit_notional"] = round(exit_notional, 2)
    closure.update(history_normalized_metrics(closure, str(position.get("market") or "")))
    return closure


def close_position(state: Mapping[str, Any], market: str, position_id: str, *, now: datetime, exit_price: float, reason: str) -> tuple[dict[str, Any], dict[str, Any]]:
    market = market.upper()
    updated = deepcopy(dict(state))
    row = market_state(updated, market)
    remaining: list[dict[str, Any]] = []
    closure = None
    for position in open_positions(updated, market):
        if str(position.get("position_id")) == str(position_id) and closure is None:
            closure = _closure(position, now=now, exit_price=exit_price, reason=reason)
        else:
            remaining.append(position)
    if closure is None:
        return updated, {"action": "position_not_found", "market": market, "position_id": position_id}
    row["open_positions"] = remaining
    closed = [dict(item) for item in row.get("closed_positions") or [] if isinstance(item, Mapping)]
    closed.append(closure)
    row["closed_positions"] = closed[-300:]
    return updated, {"action": "close", "market": market, "position_id": position_id, "reason": reason, "exit_price": exit_price}


def recalculate_risk(position: Mapping[str, Any], *, mark: float, atr: float, now: datetime, market_cfg: Mapping[str, Any], thesis_score_value: float | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    updated = upgrade_open_position_geometry(position, market_cfg)
    old_stop, old_target = updated.get("stop"), updated.get("target")
    risk = max(float(atr) * float(market_cfg["atr_multiple"]), float(mark) * float(market_cfg["risk_floor_percent"]))
    proposed_risk_pct = risk / float(mark) if mark > 0 else 99.0
    rr = max(float(updated.get("strategic_target_rr") or 3.0), float(market_cfg.get("strategic_target_reward_risk") or 3.0))
    if thesis_score_value is not None and thesis_score_value >= 80.0:
        rr = max(rr, float(market_cfg.get("exceptional_thesis_target_reward_risk") or 4.0))
    elif thesis_score_value is not None and thesis_score_value >= 70.0:
        rr = max(rr, float(market_cfg.get("strong_thesis_target_reward_risk") or 3.5))
    # Long-position risk ratchet: neither the stop nor the strategic target is
    # ever moved down. The model can still close earlier on thesis invalidation.
    new_stop = max(float(old_stop), float(mark) - risk)
    new_target = max(float(old_target), float(mark) + risk * rr)
    risk = float(mark) - new_stop
    risk_pct = risk / float(mark) if mark > 0 else 99.0
    valid = proposed_risk_pct <= float(market_cfg["maximum_risk_percent"]) and risk_pct <= float(market_cfg["maximum_risk_percent"]) and valid_long_risk(mark, new_stop, new_target, max_risk_percent=float(market_cfg["maximum_risk_percent"]))
    review = {
        "reviewed_at": _iso(now),
        "mark": round(float(mark), 8),
        "atr": round(float(atr), 8),
        "old_stop": old_stop,
        "old_target": old_target,
        "status": "updated" if valid else "preserved_last_valid_risk",
    }
    if valid:
        updated["stop"] = round(new_stop, 8)
        updated["target"] = round(new_target, 8)
        updated["risk_percent"] = round(risk_pct, 6)
        updated["reward_risk"] = round(rr, 4)
        updated["risk_last_changed_at"] = _iso(now)
        review["new_stop"] = updated["stop"]
        review["new_target"] = updated["target"]
    else:
        review["reason"] = "invalid_recalculation_kept_previous_sl_tp"
    reviews = [dict(item) for item in updated.get("risk_reviews") or [] if isinstance(item, Mapping)]
    reviews.append(review)
    updated["risk_reviews"] = reviews[-40:]
    updated["risk_review_date"] = now.date().isoformat()
    updated["last_reviewed_at"] = _iso(now)
    updated["last_mark"] = round(float(mark), 8)
    return updated, review


def thesis_score(closes: Iterable[float]) -> float | None:
    values = [float(value) for value in closes if _float(value) is not None]
    if len(values) < 50 or values[-1] <= 0 or values[-6] <= 0 or values[-21] <= 0:
        return None
    mark = values[-1]
    ret5 = mark / values[-6] - 1.0
    ret20 = mark / values[-21] - 1.0
    ma20 = statistics.fmean(values[-20:])
    ma50 = statistics.fmean(values[-50:])
    score = 50.0 + ret5 * 300.0 + ret20 * 120.0 + (10.0 if mark > ma20 else -10.0) + (8.0 if ma20 > ma50 else -8.0)
    return max(0.0, min(100.0, score))


def _profit_runner_extension(
    position: Mapping[str, Any],
    *,
    high: float,
    last: float,
    atr: float,
    score: float,
    now: datetime,
    market_cfg: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Ratchet risk and move the profit checkpoint above the current high."""
    updated, review = recalculate_risk(
        position,
        mark=last,
        atr=atr,
        now=now,
        market_cfg=market_cfg,
        thesis_score_value=score,
    )
    if review.get("status") != "updated":
        return None, review

    risk_buffer = max(
        float(atr) * float(market_cfg["atr_multiple"]),
        float(last) * float(market_cfg["risk_floor_percent"]),
    )
    runner_rr = max(
        float(updated.get("strategic_target_rr") or 3.0),
        float(market_cfg.get("profit_runner_target_buffer_rr") or 3.0),
    )
    old_target = float(position["target"])
    new_target = max(float(updated["target"]), max(float(high), float(last)) + risk_buffer * runner_rr)
    updated["target"] = round(new_target, 8)
    updated["risk_last_changed_at"] = _iso(now)
    updated["last_mark"] = round(float(last), 8)
    updated["last_reviewed_at"] = _iso(now)
    updated["thesis_score"] = round(float(score), 4)
    updated["thesis_status"] = "ACTIVE_RUNNER"
    updated["profit_runner_active"] = True
    updated["profit_runner_enabled"] = True
    updated["take_profit_mode"] = "THESIS_RUNNER_CHECKPOINT"
    updated["peak_mark"] = round(max(float(updated.get("peak_mark") or last), float(high), float(last)), 8)
    updated["peak_thesis_score"] = round(max(float(updated.get("peak_thesis_score") or score), float(score)), 4)

    checkpoints = [dict(item) for item in updated.get("profit_runner_checkpoints") or [] if isinstance(item, Mapping)]
    checkpoints.append({
        "reached_at": _iso(now),
        "old_target": round(old_target, 8),
        "session_high": round(float(high), 8),
        "last": round(float(last), 8),
        "thesis_score": round(float(score), 4),
        "new_stop": updated.get("stop"),
        "new_target": updated.get("target"),
        "upside_cap_percent": market_cfg.get("profit_runner_upside_cap_percent"),
    })
    updated["profit_runner_checkpoints"] = checkpoints[-40:]
    return updated, review


def review_one_position(position: Mapping[str, Any], *, snapshot: Mapping[str, Any], closes: Iterable[float], atr: float, now: datetime, market_cfg: Mapping[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    """Review one open LONG position. Returns (position, closure, audit)."""
    market = str(position.get("market") or "").upper()
    effective_from = risk_geometry_effective_from(position)
    if effective_from is not None and effective_from.date() == now.astimezone(effective_from.tzinfo).date():
        window_start = _market_datetime(snapshot.get("trigger_window_start"), market)
        if snapshot.get("post_effective_only") is not True or window_start is None or window_start < effective_from:
            audit = {
                "action": "hold_data_error",
                "reason": "unverified_post_effective_trigger_window",
                "position_id": position.get("position_id"),
                "risk_effective_from": _iso(effective_from),
                "trigger_window_start": snapshot.get("trigger_window_start"),
            }
            return deepcopy(dict(position)), None, audit
    try:
        high, low, last = float(snapshot["high"]), float(snapshot["low"]), float(snapshot["last"])
    except (KeyError, TypeError, ValueError):
        audit = {"action": "hold_data_error", "reason": "snapshot_missing", "position_id": position.get("position_id")}
        return deepcopy(dict(position)), None, audit

    position = upgrade_open_position_geometry(position, market_cfg)
    stop, target = float(position["stop"]), float(position["target"])
    same_bar = low <= stop and high >= target
    if same_bar or low <= stop:
        closure = _closure(position, now=now, exit_price=stop, reason="stop_loss", conservative_same_bar=same_bar)
        return None, closure, {"action": "close", "reason": "stop_loss", "position_id": position.get("position_id")}

    score = thesis_score(closes)
    if score is not None and score <= float(market_cfg["model_exit_score"]):
        closure = _closure(position, now=now, exit_price=last, reason="model_thesis_invalidated")
        closure["exit_model_score"] = round(score, 4)
        return None, closure, {"action": "close", "reason": "model_thesis_invalidated", "score": round(score, 4), "position_id": position.get("position_id")}

    previous_peak_score = float(position.get("peak_thesis_score") or score or 0.0)
    reversal = float(market_cfg.get("model_reversal_from_peak") or 22.0)
    if score is not None and last > float(position["entry"]) and previous_peak_score >= 65.0 and score <= previous_peak_score - reversal:
        closure = _closure(position, now=now, exit_price=last, reason="model_momentum_reversal")
        closure["exit_model_score"] = round(score, 4)
        closure["peak_model_score"] = round(previous_peak_score, 4)
        return None, closure, {"action": "close", "reason": "model_momentum_reversal", "score": round(score, 4), "position_id": position.get("position_id")}

    if high >= target:
        runner_enabled = market_cfg.get("profit_runner_enabled") is True
        runner_score = float(market_cfg.get("profit_runner_min_thesis_score") or 60.0)
        if runner_enabled and score is not None and score >= runner_score:
            updated, review = _profit_runner_extension(
                position,
                high=high,
                last=last,
                atr=atr,
                score=score,
                now=now,
                market_cfg=market_cfg,
            )
            if updated is not None:
                return updated, None, {
                    "action": "hold_profit_runner",
                    "reason": "target_reached_thesis_intact",
                    "thesis_score": round(score, 4),
                    "old_target": round(target, 8),
                    "new_target": updated.get("target"),
                    "new_stop": updated.get("stop"),
                    "position_id": position.get("position_id"),
                }
        closure = _closure(position, now=now, exit_price=target, reason="take_profit")
        if runner_enabled:
            closure["profit_runner_fallback"] = "thesis_not_strong_enough_or_risk_recalculation_failed"
        return None, closure, {"action": "close", "reason": "take_profit", "position_id": position.get("position_id")}

    local_date = now.date().isoformat()
    if str(position.get("risk_review_date") or "") != local_date:
        updated, review = recalculate_risk(position, mark=last, atr=atr, now=now, market_cfg=market_cfg, thesis_score_value=score)
        updated["thesis_score"] = round(score, 4) if score is not None else None
        updated["thesis_status"] = "ACTIVE"
        return updated, None, {"action": "hold_risk_reviewed", "risk_review": review, "thesis_score": score, "position_id": position.get("position_id")}

    updated = deepcopy(dict(position))
    updated["last_mark"] = round(last, 8)
    updated["last_reviewed_at"] = _iso(now)
    updated["thesis_score"] = round(score, 4) if score is not None else None
    updated["peak_mark"] = round(max(float(updated.get("peak_mark") or last), last), 8)
    if score is not None:
        updated["peak_thesis_score"] = round(max(float(updated.get("peak_thesis_score") or score), score), 4)
    return updated, None, {"action": "hold", "thesis_score": score, "position_id": position.get("position_id")}


def review_market(state: Mapping[str, Any], market: str, *, observations: Mapping[str, Mapping[str, Any]], now: datetime, policy: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    market = market.upper()
    policy = policy or load_policy()
    updated = deepcopy(dict(state))
    row = market_state(updated, market)
    kept: list[dict[str, Any]] = []
    closed = [dict(item) for item in row.get("closed_positions") or [] if isinstance(item, Mapping)]
    audits: list[dict[str, Any]] = []
    for position in open_positions(updated, market):
        obs = observations.get(str(position.get("symbol") or "")) or {}
        if not obs:
            held = deepcopy(dict(position))
            held["last_review_error"] = "market_observation_unavailable_preserved_last_valid_sl_tp"
            held["last_reviewed_at"] = _iso(now)
            kept.append(held)
            audits.append({"action": "hold_data_error", "position_id": position.get("position_id")})
            continue
        current, closure, audit = review_one_position(
            position,
            snapshot=obs.get("snapshot") or {},
            closes=obs.get("closes") or [],
            atr=float(obs.get("atr") or 0.0),
            now=now,
            market_cfg=policy["markets"][market],
        )
        audits.append(audit)
        if closure is not None:
            closed.append(closure)
        elif current is not None:
            kept.append(current)
    row["open_positions"] = kept
    row["closed_positions"] = closed[-300:]
    row["last_portfolio_review_at"] = _iso(now)
    return updated, audits


def verify_state(state: Mapping[str, Any], policy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or load_policy()
    errors: list[str] = []
    seen: set[str] = set()
    for market in ("GPW", "US"):
        positions = open_positions(state, market)
        cap = int(policy["markets"][market]["max_open_positions"])
        if len(positions) > cap:
            errors.append(f"{market}:open_positions={len(positions)}>cap={cap}")
        for position in positions:
            pid = str(position.get("position_id") or "")
            if not pid or pid in seen:
                errors.append(f"{market}:duplicate_or_missing_position_id:{pid}")
            seen.add(pid)
            if str(position.get("holding_policy") or "") != "OPEN_ENDED_MODEL_CONTROLLED":
                errors.append(f"{market}:{pid}:wrong_holding_policy")
            if position.get("scheduled_exit") is not None or position.get("valid_until") is not None or position.get("time_stop") is not None:
                errors.append(f"{market}:{pid}:fixed_holding_deadline_present")
            if not valid_long_risk(position.get("last_mark") or position.get("entry"), position.get("stop"), position.get("target"), max_risk_percent=float(policy["markets"][market]["maximum_risk_percent"])):
                errors.append(f"{market}:{pid}:invalid_sl_tp")
            if str(position.get("sizing_policy_version") or "") == FIXED_NOTIONAL_POLICY_VERSION:
                target_notional = _float(position.get("target_position_notional"))
                quantity = _float(position.get("quantity"))
                entry = _float(position.get("entry"))
                currency = str(position.get("position_currency") or "")
                if target_notional != 5000.0:
                    errors.append(f"{market}:{pid}:invalid_target_position_notional")
                if currency != POSITION_CURRENCY[market]:
                    errors.append(f"{market}:{pid}:invalid_position_currency")
                if quantity is None or quantity <= 0 or entry is None or entry <= 0:
                    errors.append(f"{market}:{pid}:invalid_fixed_notional_quantity")
                elif abs(entry * quantity - 5000.0) > 0.05:
                    errors.append(f"{market}:{pid}:fixed_notional_not_5000")
        for position in (market_state(state, market).get("closed_positions") or []):
            if not isinstance(position, Mapping):
                continue
            pid = str(position.get("position_id") or "")
            version = str(position.get("history_normalization_version") or "")
            currency = str(position.get("history_position_currency") or "")
            notional = _float(position.get("history_target_position_notional"))
            quantity = _float(position.get("history_normalized_quantity"))
            entry = _float(position.get("entry"))
            exit_price = _float(position.get("exit_price"))
            pnl = _float(position.get("history_normalized_pnl_amount"))
            if version != HISTORY_NOTIONAL_NORMALIZATION_VERSION:
                errors.append(f"{market}:{pid}:missing_history_5k_normalization")
                continue
            if currency != POSITION_CURRENCY[market]:
                errors.append(f"{market}:{pid}:invalid_history_currency")
            if notional != 5000.0:
                errors.append(f"{market}:{pid}:invalid_history_notional")
            if quantity is None or quantity <= 0 or entry is None or entry <= 0:
                errors.append(f"{market}:{pid}:invalid_history_quantity")
                continue
            if abs(entry * quantity - 5000.0) > 0.05:
                errors.append(f"{market}:{pid}:history_notional_not_5000")
            if exit_price is None or exit_price <= 0 or pnl is None:
                errors.append(f"{market}:{pid}:invalid_history_pnl")
            else:
                expected_pnl = (exit_price - entry) * quantity
                if abs(expected_pnl - pnl) > 0.02:
                    errors.append(f"{market}:{pid}:history_pnl_mismatch")
    return {"status": "OK" if not errors else "ERROR", "errors": errors, "open_gpw": len(open_positions(state, "GPW")), "open_us": len(open_positions(state, "US"))}


def _candidate_path(market: str, policy: Mapping[str, Any]) -> Path:
    return ROOT / str(policy["markets"][market]["candidate_file"])


def _request_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def _chart(symbol: str, *, interval: str, range_value: str) -> dict[str, Any]:
    params = urllib.parse.urlencode({"range": range_value, "interval": interval, "events": "history", "includePrePost": "false"})
    encoded = urllib.parse.quote(symbol, safe="")
    failures: list[str] = []
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            payload = _request_json(f"https://{host}/v8/finance/chart/{encoded}?{params}")
            result = (payload.get("chart", {}).get("result") or [None])[0]
            if not result:
                raise ValueError("empty_chart")
            return result
        except Exception as exc:
            failures.append(f"{host}:{type(exc).__name__}")
            time.sleep(0.25)
    raise RuntimeError(f"Yahoo chart unavailable for {symbol}: {'|'.join(failures)}")


def _daily_observation(
    symbol: str,
    market: str,
    now: datetime,
    *,
    opened_at: str | None = None,
    risk_effective_at: str | None = None,
) -> dict[str, Any]:
    daily = _chart(symbol, interval="1d", range_value="6mo")
    stamps = daily.get("timestamp") or []
    quote = ((daily.get("indicators") or {}).get("quote") or [{}])[0]
    rows: list[tuple[float, float, float]] = []
    closes: list[float] = []
    for index, _stamp in enumerate(stamps):
        try:
            high = float((quote.get("high") or [])[index])
            low = float((quote.get("low") or [])[index])
            close = float((quote.get("close") or [])[index])
        except (TypeError, ValueError, IndexError):
            continue
        rows.append((high, low, close))
        closes.append(close)
    if len(rows) < 50:
        raise RuntimeError("insufficient_daily_history")
    tr: list[float] = []
    for previous, current in zip(rows[-15:-1], rows[-14:]):
        prev_close = previous[2]
        tr.append(max(current[0] - current[1], abs(current[0] - prev_close), abs(current[1] - prev_close)))
    atr = statistics.fmean(tr) if tr else 0.0

    intraday = _chart(symbol, interval="5m", range_value="1d")
    istamps = intraday.get("timestamp") or []
    iquote = ((intraday.get("indicators") or {}).get("quote") or [{}])[0]
    tz = MARKET_TZ[market]
    points: list[tuple[datetime, float, float, float]] = []
    opened_dt = _market_datetime(opened_at, market)
    risk_effective_dt = _market_datetime(risk_effective_at, market)
    trigger_candidates = [value for value in (opened_dt, risk_effective_dt) if value is not None]
    trigger_from = max(trigger_candidates) if trigger_candidates else None
    for index, stamp in enumerate(istamps):
        try:
            high = float((iquote.get("high") or [])[index])
            low = float((iquote.get("low") or [])[index])
            close = float((iquote.get("close") or [])[index])
        except (TypeError, ValueError, IndexError):
            continue
        dt = datetime.fromtimestamp(int(stamp), tz)
        if dt.date() != now.date():
            continue
        # The current SL/TP geometry may only see candles that began after the
        # geometry became effective.  This blocks both pre-entry fills and the
        # subtler case where a newly ratcheted stop is applied to an earlier
        # same-day low.
        if trigger_from is not None and trigger_from.date() == now.date() and dt < trigger_from:
            continue
        points.append((dt, high, low, close))
    if points:
        snapshot = {
            "high": max(item[1] for item in points),
            "low": min(item[2] for item in points),
            "last": points[-1][3],
            "observed_at": _iso(points[-1][0]),
            "provider": "Yahoo",
            "trigger_window_start": _iso(points[0][0]),
            "trigger_effective_from": _iso(trigger_from) if trigger_from is not None else None,
            "post_effective_only": True,
        }
    else:
        if trigger_from is not None and trigger_from.date() == now.date():
            raise RuntimeError("no_post_effective_intraday_bar_yet")
        snapshot = {
            "high": rows[-1][0],
            "low": rows[-1][1],
            "last": rows[-1][2],
            "provider": "Yahoo:daily_fallback",
            "trigger_window_start": _iso(now),
            "trigger_effective_from": _iso(trigger_from) if trigger_from is not None else None,
            "post_effective_only": True,
        }
    return {"snapshot": snapshot, "closes": closes, "atr": atr}


def bootstrap_legacy_positions(state: dict[str, Any], *, now: datetime, policy: Mapping[str, Any]) -> None:
    """Carry the one existing US legacy position into the new open-ended book.

    Forced legacy entries are not repeated, but an already-open trade is retained
    to avoid silently losing risk control during migration. Its old time stop is
    deliberately discarded.
    """
    legacy = _load(US_LEGACY_BOOK, {})
    old = legacy.get("open_position") if isinstance(legacy, Mapping) else None
    if isinstance(old, Mapping) and available_slots(state, "US", policy) > 0:
        entry = _float(old.get("entry")); stop = _float(old.get("stop")); target = _float(old.get("target"))
        if entry and valid_long_risk(entry, stop, target, max_risk_percent=float(policy["markets"]["US"]["maximum_risk_percent"])):
            selection = old.get("entry_selection") or {}
            position = {
                "position_id": str(old.get("position_id") or f"us:migrated:{old.get('symbol') or ''}"),
                "market": "US", "status": "OPEN", "symbol": str(old.get("symbol") or "").upper(),
                "ticker": old.get("ticker") or old.get("symbol"), "name": old.get("name"), "sector": old.get("sector"),
                "opened_at": old.get("opened_at") or _iso(now), "source_candidate_date": old.get("source_history_date"),
                "source_candidate_generated_at": old.get("opened_at"), "entry": entry, "stop": stop, "target": target,
                "risk_percent": float(selection.get("risk_percent") or max((entry-stop)/entry, 0.0)),
                "reward_risk": float(selection.get("reward_risk") or max((target-entry)/(entry-stop), 1.5)),
                "entry_score": float(old.get("entry_score") or selection.get("score") or 0.0),
                "last_mark": float(old.get("last_mark") or entry), "last_reviewed_at": _iso(now),
                "risk_last_changed_at": old.get("opened_at") or _iso(now), "risk_review_date": None,
                "holding_policy": "OPEN_ENDED_MODEL_CONTROLLED", "scheduled_exit": None, "valid_until": None, "time_stop": None,
                "thesis_status": "ACTIVE_MIGRATED", "risk_reviews": [], "migration": "legacy_us_open_position_no_time_stop",
            }
            market_state(state, "US")["open_positions"] = [position]


def run_market(state: Mapping[str, Any], market: str, *, now: datetime, policy: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    market = market.upper()
    observations: dict[str, dict[str, Any]] = {}
    audits: list[dict[str, Any]] = []
    for position in open_positions(state, market):
        symbol = str(position.get("symbol") or "")
        try:
            observations[symbol] = _daily_observation(
                symbol,
                market,
                now,
                opened_at=position.get("opened_at"),
                risk_effective_at=position.get("risk_last_changed_at"),
            )
        except Exception as exc:
            audits.append({"action": "observation_error", "market": market, "symbol": symbol, "error": f"{type(exc).__name__}:{str(exc)[:160]}"})
    updated, review_audit = review_market(state, market, observations=observations, now=now, policy=policy)
    audits.extend(review_audit)
    if candidate_admission_authorized(policy, "legacy"):
        payload = _load(_candidate_path(market, policy), {})
        if isinstance(payload, Mapping):
            updated, admission = admit_candidate(updated, market, payload, now=now, policy=policy, authority="legacy")
            audits.append(admission)
    else:
        audits.append({
            "action": "candidate_admission_blocked",
            "market": market,
            "reason": "v2_production_bridge_only",
            "authority": "legacy",
        })
    return updated, audits


def run(markets: Iterable[str] = ("GPW", "US"), *, state_path: Path = STATE_PATH, now_map: Mapping[str, datetime] | None = None) -> dict[str, Any]:
    policy = load_policy()
    utc_now = datetime.now(ZoneInfo("UTC"))
    state = load_state(state_path, now=utc_now, policy=policy)
    report: dict[str, Any] = {"policy_version": policy.get("policy_version"), "actions": []}
    for market in markets:
        market = market.upper()
        now = (now_map or {}).get(market) if now_map else None
        now = now or datetime.now(MARKET_TZ[market])
        state, actions = run_market(state, market, now=now, policy=policy)
        report["actions"].extend(actions)
    verify = verify_state(state, policy)
    if verify["status"] != "OK":
        raise ValueError("Stock Trading state invariant violation: " + "; ".join(verify["errors"]))
    save_state(state, state_path, now=datetime.now(ZoneInfo("UTC")))
    report.update(verify)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["run", "verify", "sync-candidates"], default="run")
    parser.add_argument("--market", choices=["all", "GPW", "US"], default="all")
    args = parser.parse_args()
    policy = load_policy()
    state = load_state(STATE_PATH, now=datetime.now(ZoneInfo("UTC")), policy=policy)
    markets = ["GPW", "US"] if args.market == "all" else [args.market]
    if args.mode == "verify":
        result = verify_state(state, policy)
        if result["status"] != "OK":
            raise SystemExit(json.dumps(result, ensure_ascii=False))
        print(json.dumps(result, ensure_ascii=False, indent=2)); return
    if args.mode == "sync-candidates":
        if not candidate_admission_authorized(policy, "legacy"):
            result = verify_state(state, policy)
            print(json.dumps({
                "actions": [{
                    "action": "candidate_admission_blocked",
                    "market": market,
                    "reason": "v2_production_bridge_only",
                    "authority": "legacy",
                } for market in markets],
                "state_written": False,
                **result,
            }, ensure_ascii=False, indent=2))
            return
        actions = []
        for market in markets:
            now = datetime.now(MARKET_TZ[market])
            payload = _load(_candidate_path(market, policy), {})
            if isinstance(payload, Mapping):
                state, action = admit_candidate(state, market, payload, now=now, policy=policy, authority="legacy")
                actions.append(action)
        verify = verify_state(state, policy)
        if verify["status"] != "OK":
            raise SystemExit(json.dumps(verify, ensure_ascii=False))
        save_state(state, STATE_PATH, now=datetime.now(ZoneInfo("UTC")))
        print(json.dumps({"actions": actions, "state_written": True, **verify}, ensure_ascii=False, indent=2)); return
    print(json.dumps(run(markets), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
