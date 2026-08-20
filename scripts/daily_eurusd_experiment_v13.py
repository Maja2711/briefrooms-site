#!/usr/bin/env python3
"""PR24 virtual trade-path evaluation for the Daily EUR/USD A/B/C experiment.

This layer preserves the existing point-in-time forward outcomes (30m/1h/2h/4h/24h)
and adds a separate virtual trade path for prospective v1.3 captures only:
- entry at the frozen reference price,
- SL/TP risk contract aligned with active Daily EUR/USD,
- MFE / MAE from 1-minute OHLC after the signal was generated,
- first-touch TP/SL handling,
- 24h time exit,
- fail-closed ambiguity when TP and SL are both touched inside the same 1m bar.

It remains research_shadow only. It never executes a trade and never writes back to
Belief Core or the active Daily EUR/USD decision path.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from belief_market_data_adapter import Bar
import daily_eurusd_experiment_v12 as v12

base = v12.base

ENGINE_VERSION = "eurusd-daily-abc-v1.3.0"
TRADE_PLAN_SCHEMA = "eurusd-abc-virtual-trade-plan-v1"
TRADE_PATH_SCHEMA = "eurusd-abc-virtual-trade-path-v1"
ATR_30M_WINDOW = 26
ATR_MULTIPLE = 1.35
RISK_FLOOR_PERCENT = 0.0027
REWARD_RISK = 1.8
POSITION_HORIZON_MINUTES = 1440
MONITOR_INTERVAL = "1m"

_base_build_capture = base.build_capture
_base_validate_state = base.validate_state
_base_build_report = base.build_report

# Upgrade the frozen experiment version before the inherited capture builder runs.
base.ENGINE_VERSION = ENGINE_VERSION


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str | datetime) -> datetime:
    return base._parse_time(value)


def _number(value: Any, digits: int = 6) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _eligible_30m_rows(rows: Sequence[Bar], observed_at: datetime) -> list[Bar]:
    return [
        bar for bar in sorted(rows, key=lambda item: item.timestamp)
        if bar.timestamp.astimezone(timezone.utc) <= observed_at
    ]


def build_trade_plan(capture: Mapping[str, Any], rows_30m: Sequence[Bar]) -> dict[str, Any]:
    """Freeze one risk plan at capture time using the active Daily EUR/USD risk contract."""
    observed_at = _parse_time(str(capture["market_observed_at"]))
    captured_at = _parse_time(str(capture["captured_at"]))
    eligible = _eligible_30m_rows(rows_30m, observed_at)
    atr = base._atr(eligible, ATR_30M_WINDOW)
    if atr is None or float(atr) <= 0:
        raise ValueError("PR24 trade plan requires positive ATR(26) on 30m EUR/USD")

    entry = float(capture["reference_price"])
    risk = max(float(atr) * ATR_MULTIPLE, entry * RISK_FLOOR_PERCENT)
    horizon_end = captured_at + timedelta(minutes=POSITION_HORIZON_MINUTES)
    arm_plans: dict[str, Any] = {}

    for arm_id in ("A", "B", "C"):
        arm = (capture.get("arms") or {}).get(arm_id) or {}
        available = bool(arm.get("available"))
        direction = str(arm.get("direction") or "UNAVAILABLE").upper()
        if not available:
            arm_plans[arm_id] = {
                "available": False,
                "direction": "UNAVAILABLE",
                "status": "UNAVAILABLE",
                "entry_price": None,
                "stop_price": None,
                "target_price": None,
            }
            continue
        if direction == "FLAT":
            arm_plans[arm_id] = {
                "available": True,
                "direction": "FLAT",
                "status": "NO_TRADE",
                "entry_price": None,
                "stop_price": None,
                "target_price": None,
            }
            continue
        if direction not in {"LONG", "SHORT"}:
            raise ValueError(f"unsupported PR24 arm direction: {arm_id}={direction}")

        if direction == "LONG":
            stop = entry - risk
            target = entry + risk * REWARD_RISK
        else:
            stop = entry + risk
            target = entry - risk * REWARD_RISK
        arm_plans[arm_id] = {
            "available": True,
            "direction": direction,
            "status": "TRACKED",
            "entry_price": round(entry, 5),
            "stop_price": round(stop, 5),
            "target_price": round(target, 5),
        }

    return {
        "schema_version": TRADE_PLAN_SCHEMA,
        "mode": "research_shadow",
        "entry_basis": "frozen_reference_price_not_executable_quote",
        "signal_generated_at": _iso_z(captured_at),
        "horizon_end_at": _iso_z(horizon_end),
        "risk_contract": {
            "source": "active_daily_eurusd_v1.2_parity",
            "atr_timeframe": "30m",
            "atr_window": ATR_30M_WINDOW,
            "atr_value": round(float(atr), 8),
            "atr_multiple": ATR_MULTIPLE,
            "risk_floor_percent": RISK_FLOOR_PERCENT,
            "risk_distance": round(risk, 8),
            "reward_risk": REWARD_RISK,
            "position_horizon_minutes": POSITION_HORIZON_MINUTES,
            "monitor_interval": MONITOR_INTERVAL,
        },
        "arms": arm_plans,
    }


def _initial_trade_path(plan: Mapping[str, Any]) -> dict[str, Any]:
    arms: dict[str, Any] = {}
    for arm_id in ("A", "B", "C"):
        arm_plan = (plan.get("arms") or {}).get(arm_id) or {}
        plan_status = str(arm_plan.get("status") or "UNAVAILABLE")
        status = "OPEN" if plan_status == "TRACKED" else plan_status
        arms[arm_id] = {
            "status": status,
            "mfe_bps": 0.0 if status == "OPEN" else None,
            "mfe_at": None,
            "mae_bps": 0.0 if status == "OPEN" else None,
            "mae_at": None,
            "first_touch": None,
            "first_touch_at": None,
            "minutes_to_first_touch": None,
            "exit_reason": None,
            "exit_at": None,
            "exit_price": None,
            "realized_bps": None,
        }
    return {
        "schema_version": TRADE_PATH_SCHEMA,
        "source_plan_sha256": None,
        "arms": arms,
    }


def build_capture(
    rows_30m: Sequence[Bar],
    belief_payload: Mapping[str, Any] | None,
    *,
    hourly_rows: Sequence[Bar],
    daily_rows: Sequence[Bar],
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    capture = _base_build_capture(
        rows_30m,
        belief_payload,
        hourly_rows=hourly_rows,
        daily_rows=daily_rows,
        captured_at=captured_at,
    )
    capture["engine_version"] = ENGINE_VERSION
    plan = build_trade_plan(capture, rows_30m)
    plan_sha = base._canonical_sha(plan)
    capture["trade_plan"] = plan
    capture["trade_plan_sha256"] = plan_sha
    path = _initial_trade_path(plan)
    path["source_plan_sha256"] = plan_sha
    capture["trade_path"] = path
    return capture


def _directional_bps(direction: str, entry: float, price: float) -> float:
    if direction == "LONG":
        return (price / entry - 1.0) * 10000.0
    return (entry / price - 1.0) * 10000.0


def _bar_extremes(bar: Bar) -> tuple[float, float, float]:
    close = float(bar.close)
    high = float(bar.high if bar.high is not None else close)
    low = float(bar.low if bar.low is not None else close)
    return high, low, close


def evaluate_arm_trade_path(
    arm_plan: Mapping[str, Any],
    minute_rows: Sequence[Bar],
    *,
    signal_generated_at: datetime,
    horizon_end_at: datetime,
    as_of: datetime,
) -> dict[str, Any]:
    """Evaluate one virtual arm path from 1m OHLC without guessing same-bar ordering."""
    direction = str(arm_plan.get("direction") or "UNAVAILABLE").upper()
    plan_status = str(arm_plan.get("status") or "UNAVAILABLE")
    if plan_status == "UNAVAILABLE":
        return _initial_trade_path({"arms": {"A": arm_plan, "B": arm_plan, "C": arm_plan}})["arms"]["A"]
    if plan_status == "NO_TRADE" or direction == "FLAT":
        row = _initial_trade_path({"arms": {"A": {**dict(arm_plan), "status": "NO_TRADE"}, "B": {}, "C": {}}})["arms"]["A"]
        row["status"] = "NO_TRADE"
        return row
    if direction not in {"LONG", "SHORT"}:
        raise ValueError(f"cannot evaluate trade path direction={direction}")

    entry = float(arm_plan["entry_price"])
    stop = float(arm_plan["stop_price"])
    target = float(arm_plan["target_price"])
    cutoff = min(as_of.astimezone(timezone.utc), horizon_end_at.astimezone(timezone.utc))
    rows = [
        bar for bar in sorted(minute_rows, key=lambda item: item.timestamp)
        if signal_generated_at < bar.timestamp.astimezone(timezone.utc) <= cutoff
    ]

    mfe_bps = 0.0
    mae_bps = 0.0
    mfe_at = mae_at = None
    exit_reason = exit_at = first_touch = first_touch_at = None
    exit_price: float | None = None
    realized_bps: float | None = None
    minutes_to_first_touch: float | None = None
    status = "OPEN"

    for bar in rows:
        ts = bar.timestamp.astimezone(timezone.utc)
        high, low, _ = _bar_extremes(bar)
        if direction == "LONG":
            favorable = _directional_bps(direction, entry, high)
            adverse = _directional_bps(direction, entry, low)
            tp_hit = high >= target
            sl_hit = low <= stop
        else:
            favorable = _directional_bps(direction, entry, low)
            adverse = _directional_bps(direction, entry, high)
            tp_hit = low <= target
            sl_hit = high >= stop

        if favorable > mfe_bps:
            mfe_bps = favorable
            mfe_at = _iso_z(ts)
        if adverse < mae_bps:
            mae_bps = adverse
            mae_at = _iso_z(ts)

        if tp_hit and sl_hit:
            status = "AMBIGUOUS"
            first_touch = "AMBIGUOUS_SAME_1M_BAR"
            first_touch_at = _iso_z(ts)
            exit_reason = "AMBIGUOUS_SAME_1M_BAR"
            exit_at = _iso_z(ts)
            minutes_to_first_touch = round((ts - signal_generated_at).total_seconds() / 60.0, 2)
            break
        if tp_hit or sl_hit:
            first_touch = "TAKE_PROFIT" if tp_hit else "STOP_LOSS"
            first_touch_at = _iso_z(ts)
            exit_reason = first_touch
            exit_at = _iso_z(ts)
            exit_price = target if tp_hit else stop
            realized_bps = _directional_bps(direction, entry, exit_price)
            minutes_to_first_touch = round((ts - signal_generated_at).total_seconds() / 60.0, 2)
            status = "CLOSED"
            break

    if status == "OPEN" and as_of.astimezone(timezone.utc) >= horizon_end_at.astimezone(timezone.utc):
        eligible_at_exit = [
            bar for bar in sorted(minute_rows, key=lambda item: item.timestamp)
            if signal_generated_at < bar.timestamp.astimezone(timezone.utc) <= horizon_end_at.astimezone(timezone.utc)
        ]
        if eligible_at_exit:
            last = eligible_at_exit[-1]
            ts = last.timestamp.astimezone(timezone.utc)
            _, _, close = _bar_extremes(last)
            status = "CLOSED"
            exit_reason = "TIME_EXIT_24H"
            exit_at = _iso_z(ts)
            exit_price = close
            realized_bps = _directional_bps(direction, entry, close)

    return {
        "status": status,
        "mfe_bps": round(mfe_bps, 4),
        "mfe_at": mfe_at,
        "mae_bps": round(mae_bps, 4),
        "mae_at": mae_at,
        "first_touch": first_touch,
        "first_touch_at": first_touch_at,
        "minutes_to_first_touch": minutes_to_first_touch,
        "exit_reason": exit_reason,
        "exit_at": exit_at,
        "exit_price": None if exit_price is None else round(exit_price, 5),
        "realized_bps": None if realized_bps is None else round(realized_bps, 4),
    }


def update_trade_paths(
    state: Mapping[str, Any],
    minute_rows: Sequence[Bar],
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Recompute v1.3 trade paths deterministically; older captures are never backfilled."""
    updated = copy.deepcopy(dict(state))
    evaluation_time = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    changed = False

    for capture in updated.get("captures") or []:
        if str(capture.get("engine_version")) != ENGINE_VERSION:
            continue
        plan = capture.get("trade_plan") or {}
        plan_sha = str(capture.get("trade_plan_sha256") or "")
        if not plan or base._canonical_sha(plan) != plan_sha:
            raise ValueError("PR24 trade plan hash mismatch")
        path = capture.get("trade_path") or {}
        if path.get("source_plan_sha256") != plan_sha:
            raise ValueError("PR24 trade path is not bound to frozen trade plan")

        signal_generated_at = _parse_time(str(plan["signal_generated_at"]))
        horizon_end_at = _parse_time(str(plan["horizon_end_at"]))
        new_arms = {}
        for arm_id in ("A", "B", "C"):
            new_arms[arm_id] = evaluate_arm_trade_path(
                (plan.get("arms") or {}).get(arm_id) or {},
                minute_rows,
                signal_generated_at=signal_generated_at,
                horizon_end_at=horizon_end_at,
                as_of=evaluation_time,
            )
        if new_arms != (path.get("arms") or {}):
            capture["trade_path"] = {
                "schema_version": TRADE_PATH_SCHEMA,
                "source_plan_sha256": plan_sha,
                "arms": new_arms,
            }
            changed = True

    if changed:
        updated["updated_at"] = _iso_z(evaluation_time)
    return updated


def trade_path_digest(state: Mapping[str, Any]) -> str:
    payload = [
        {
            "capture_id": capture.get("capture_id"),
            "engine_version": capture.get("engine_version"),
            "trade_plan_sha256": capture.get("trade_plan_sha256"),
            "trade_path": capture.get("trade_path"),
        }
        for capture in state.get("captures") or []
        if str(capture.get("engine_version")) == ENGINE_VERSION
    ]
    return base._canonical_sha(payload)


def _mean(values: list[float]) -> float | None:
    return None if not values else round(sum(values) / len(values), 4)


def trade_performance_summary(state: Mapping[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for arm_id in ("A", "B", "C"):
        signals = closed = open_count = ambiguous = tp = sl = time_exit = wins = 0
        realized: list[float] = []
        mfe_values: list[float] = []
        mae_values: list[float] = []
        touch_minutes: list[float] = []
        for capture in state.get("captures") or []:
            if str(capture.get("engine_version")) != ENGINE_VERSION:
                continue
            plan_arm = ((capture.get("trade_plan") or {}).get("arms") or {}).get(arm_id) or {}
            if str(plan_arm.get("status")) != "TRACKED":
                continue
            signals += 1
            path_arm = ((capture.get("trade_path") or {}).get("arms") or {}).get(arm_id) or {}
            status = str(path_arm.get("status") or "OPEN")
            mfe = _number(path_arm.get("mfe_bps"), 4)
            mae = _number(path_arm.get("mae_bps"), 4)
            if mfe is not None:
                mfe_values.append(mfe)
            if mae is not None:
                mae_values.append(mae)
            minutes = _number(path_arm.get("minutes_to_first_touch"), 2)
            if minutes is not None:
                touch_minutes.append(minutes)
            if status == "OPEN":
                open_count += 1
                continue
            if status == "AMBIGUOUS":
                ambiguous += 1
                continue
            if status == "CLOSED":
                closed += 1
                reason = str(path_arm.get("exit_reason") or "")
                if reason == "TAKE_PROFIT":
                    tp += 1
                elif reason == "STOP_LOSS":
                    sl += 1
                elif reason == "TIME_EXIT_24H":
                    time_exit += 1
                pnl = _number(path_arm.get("realized_bps"), 4)
                if pnl is not None:
                    realized.append(pnl)
                    if pnl > 0:
                        wins += 1

        summary[arm_id] = {
            "signals": signals,
            "open_trades": open_count,
            "closed_trades": closed,
            "ambiguous_same_1m_bar": ambiguous,
            "take_profit": tp,
            "stop_loss": sl,
            "time_exit_24h": time_exit,
            "win_rate": None if closed == 0 else round(wins / closed, 6),
            "mean_realized_bps": _mean(realized),
            "mean_mfe_bps": _mean(mfe_values),
            "mean_mae_bps": _mean(mae_values),
            "mean_minutes_to_first_touch": _mean(touch_minutes),
        }
    return summary


def build_report(state: Mapping[str, Any]) -> dict[str, Any]:
    report = _base_build_report(state)
    report["engine_version"] = str((state.get("captures") or [{}])[-1].get("engine_version") or ENGINE_VERSION)
    report["trade_path"] = {
        "schema_version": TRADE_PATH_SCHEMA,
        "prospective_from_engine_version": ENGINE_VERSION,
        "historical_backfill": False,
        "virtual_only": True,
        "risk_contract": {
            "atr_timeframe": "30m",
            "atr_window": ATR_30M_WINDOW,
            "atr_multiple": ATR_MULTIPLE,
            "risk_floor_percent": RISK_FLOOR_PERCENT,
            "reward_risk": REWARD_RISK,
            "position_horizon_minutes": POSITION_HORIZON_MINUTES,
            "monitor_interval": MONITOR_INTERVAL,
            "same_1m_tp_sl_touch": "AMBIGUOUS_FAIL_CLOSED",
        },
        "performance": trade_performance_summary(state),
    }
    return report


def validate_state(state: Mapping[str, Any]) -> None:
    _base_validate_state(state)
    for capture in state.get("captures") or []:
        if str(capture.get("engine_version")) != ENGINE_VERSION:
            continue
        boundary = capture.get("research_boundary") or {}
        if boundary.get("trade_execution") is not False or boundary.get("decision_influence") is not False:
            raise ValueError("PR24 cannot acquire production authority")
        plan = capture.get("trade_plan")
        plan_sha = capture.get("trade_plan_sha256")
        path = capture.get("trade_path")
        if not isinstance(plan, Mapping) or plan.get("schema_version") != TRADE_PLAN_SCHEMA:
            raise ValueError("PR24 v1.3 capture requires frozen trade plan")
        if not isinstance(plan_sha, str) or base._canonical_sha(plan) != plan_sha:
            raise ValueError("PR24 frozen trade plan hash invalid")
        if not isinstance(path, Mapping) or path.get("schema_version") != TRADE_PATH_SCHEMA:
            raise ValueError("PR24 v1.3 capture requires trade path")
        if path.get("source_plan_sha256") != plan_sha:
            raise ValueError("PR24 trade path plan lineage mismatch")
        if set((plan.get("arms") or {}).keys()) != {"A", "B", "C"}:
            raise ValueError("PR24 trade plan must contain A/B/C")
        if set((path.get("arms") or {}).keys()) != {"A", "B", "C"}:
            raise ValueError("PR24 trade path must contain A/B/C")
        if str(plan.get("signal_generated_at")) != str(capture.get("captured_at")):
            raise ValueError("PR24 trade monitoring must start at frozen signal generation time")
        for arm_id in ("A", "B", "C"):
            arm_plan = (plan.get("arms") or {})[arm_id]
            direction = str(arm_plan.get("direction") or "UNAVAILABLE")
            status = str(arm_plan.get("status") or "UNAVAILABLE")
            if direction == "LONG" and status == "TRACKED":
                if not (float(arm_plan["stop_price"]) < float(arm_plan["entry_price"]) < float(arm_plan["target_price"])):
                    raise ValueError("invalid LONG PR24 risk geometry")
            if direction == "SHORT" and status == "TRACKED":
                if not (float(arm_plan["target_price"]) < float(arm_plan["entry_price"]) < float(arm_plan["stop_price"])):
                    raise ValueError("invalid SHORT PR24 risk geometry")
            path_status = str(((path.get("arms") or {})[arm_id]).get("status") or "")
            if path_status not in {"OPEN", "CLOSED", "AMBIGUOUS", "NO_TRADE", "UNAVAILABLE"}:
                raise ValueError(f"invalid PR24 trade path status {arm_id}={path_status}")


# Patch inherited runtime/report validation entrypoints used by base.main/run_cycle.
base.build_capture = build_capture
base.validate_state = validate_state
base.build_report = build_report

# Re-export the stable experiment API.
BELIEF_WEIGHTS = v12.BELIEF_WEIGHTS
MA_WINDOWS = v12.MA_WINDOWS
TECHNICAL_WEIGHTS = v12.TECHNICAL_WEIGHTS
HYBRID_CONTEXT_BELIEF_IDS = v12.HYBRID_CONTEXT_BELIEF_IDS
HYBRID_TECHNICAL_WEIGHT = v12.HYBRID_TECHNICAL_WEIGHT
HYBRID_BELIEF_CONTEXT_WEIGHT = v12.HYBRID_BELIEF_CONTEXT_WEIGHT
BOLLINGER_WINDOW = v12.BOLLINGER_WINDOW
BOLLINGER_STDDEV_LEVELS = v12.BOLLINGER_STDDEV_LEVELS
append_capture = base.append_capture
empty_state = base.empty_state
performance_summary = base.performance_summary
resolve_outcomes = base.resolve_outcomes
technical_snapshot = base.technical_snapshot
validate_files = base.validate_files
run_cycle = base.run_cycle


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
