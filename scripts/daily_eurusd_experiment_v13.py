#!/usr/bin/env python3
"""PR24 virtual trade-path evaluation for the Daily EUR/USD A/B/C experiment.

The existing 30m/1h/2h/4h/24h point-forward outcomes remain untouched. This
layer adds a separate prospective virtual trade path for v1.3 captures only:
entry at frozen reference, active-engine-parity SL/TP, 1m MFE/MAE, first-touch
TP/SL, and 24h time exit. Terminal trade outcomes are append-only.

Research boundary is unchanged: no trade execution, no active-engine influence,
no Belief Core writeback.
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
TERMINAL_PATH_STATUSES = {"CLOSED", "AMBIGUOUS", "NO_TRADE", "UNAVAILABLE"}

_base_build_capture = base.build_capture
_base_validate_state = base.validate_state
_base_build_report = base.build_report

# Freeze v1.3 before inherited capture/state helpers execute.
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


def _empty_path_arm(status: str) -> dict[str, Any]:
    return {
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


def build_trade_plan(capture: Mapping[str, Any], rows_30m: Sequence[Bar]) -> dict[str, Any]:
    """Freeze risk geometry at capture time using active Daily EUR/USD parity."""
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

        stop = entry - risk if direction == "LONG" else entry + risk
        target = entry + risk * REWARD_RISK if direction == "LONG" else entry - risk * REWARD_RISK
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


def _initial_trade_path(plan: Mapping[str, Any], plan_sha: str) -> dict[str, Any]:
    arms: dict[str, Any] = {}
    for arm_id in ("A", "B", "C"):
        plan_status = str(((plan.get("arms") or {}).get(arm_id) or {}).get("status") or "UNAVAILABLE")
        status = "OPEN" if plan_status == "TRACKED" else plan_status
        arms[arm_id] = _empty_path_arm(status)
    return {
        "schema_version": TRADE_PATH_SCHEMA,
        "source_plan_sha256": plan_sha,
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
    plan = build_trade_plan(capture, rows_30m)
    plan_sha = base._canonical_sha(plan)
    capture["trade_plan"] = plan
    capture["trade_plan_sha256"] = plan_sha
    capture["trade_path"] = _initial_trade_path(plan, plan_sha)
    return capture


def _directional_bps(direction: str, entry: float, price: float) -> float:
    return (price / entry - 1.0) * 10000.0 if direction == "LONG" else (entry / price - 1.0) * 10000.0


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
    """Evaluate one arm from 1m OHLC; never guess TP-vs-SL order in one bar."""
    direction = str(arm_plan.get("direction") or "UNAVAILABLE").upper()
    plan_status = str(arm_plan.get("status") or "UNAVAILABLE")
    if plan_status == "UNAVAILABLE":
        return _empty_path_arm("UNAVAILABLE")
    if plan_status == "NO_TRADE" or direction == "FLAT":
        return _empty_path_arm("NO_TRADE")
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

    mfe_bps = mae_bps = 0.0
    mfe_at = mae_at = None
    first_touch = first_touch_at = exit_reason = exit_at = None
    exit_price = realized_bps = minutes_to_first_touch = None
    status = "OPEN"

    for bar in rows:
        ts = bar.timestamp.astimezone(timezone.utc)
        high, low, _ = _bar_extremes(bar)
        if direction == "LONG":
            favorable = _directional_bps(direction, entry, high)
            adverse = _directional_bps(direction, entry, low)
            tp_hit, sl_hit = high >= target, low <= stop
        else:
            favorable = _directional_bps(direction, entry, low)
            adverse = _directional_bps(direction, entry, high)
            tp_hit, sl_hit = low <= target, high >= stop

        # 1m OHLC has no intrabar ordering. We include the exit bar in MFE/MAE,
        # and fail closed if both thresholds are present inside that same bar.
        if favorable > mfe_bps:
            mfe_bps, mfe_at = favorable, _iso_z(ts)
        if adverse < mae_bps:
            mae_bps, mae_at = adverse, _iso_z(ts)

        if tp_hit and sl_hit:
            status = "AMBIGUOUS"
            first_touch = exit_reason = "AMBIGUOUS_SAME_1M_BAR"
            first_touch_at = exit_at = _iso_z(ts)
            minutes_to_first_touch = round((ts - signal_generated_at).total_seconds() / 60.0, 2)
            break
        if tp_hit or sl_hit:
            status = "CLOSED"
            first_touch = exit_reason = "TAKE_PROFIT" if tp_hit else "STOP_LOSS"
            first_touch_at = exit_at = _iso_z(ts)
            exit_price = target if tp_hit else stop
            realized_bps = _directional_bps(direction, entry, exit_price)
            minutes_to_first_touch = round((ts - signal_generated_at).total_seconds() / 60.0, 2)
            break

    if status == "OPEN" and as_of.astimezone(timezone.utc) >= horizon_end_at.astimezone(timezone.utc):
        eligible = [
            bar for bar in sorted(minute_rows, key=lambda item: item.timestamp)
            if signal_generated_at < bar.timestamp.astimezone(timezone.utc) <= horizon_end_at.astimezone(timezone.utc)
        ]
        if eligible:
            last = eligible[-1]
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
    """Update only OPEN v1.3 arms; terminal outcomes remain append-only forever."""
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
        old_arms = path.get("arms") or {}
        new_arms: dict[str, Any] = {}
        for arm_id in ("A", "B", "C"):
            existing = copy.deepcopy(old_arms.get(arm_id) or {})
            if str(existing.get("status") or "") in TERMINAL_PATH_STATUSES:
                new_arms[arm_id] = existing
                continue
            new_arms[arm_id] = evaluate_arm_trade_path(
                (plan.get("arms") or {}).get(arm_id) or {},
                minute_rows,
                signal_generated_at=signal_generated_at,
                horizon_end_at=horizon_end_at,
                as_of=evaluation_time,
            )
        if new_arms != old_arms:
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
    return base._canonical_sha([
        {
            "capture_id": capture.get("capture_id"),
            "engine_version": capture.get("engine_version"),
            "trade_plan_sha256": capture.get("trade_plan_sha256"),
            "trade_path": capture.get("trade_path"),
        }
        for capture in state.get("captures") or []
        if str(capture.get("engine_version")) == ENGINE_VERSION
    ])


def has_v13_trade_paths(state: Mapping[str, Any]) -> bool:
    return any(str(capture.get("engine_version")) == ENGINE_VERSION for capture in state.get("captures") or [])


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
            mfe, mae = _number(path_arm.get("mfe_bps"), 4), _number(path_arm.get("mae_bps"), 4)
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
                tp += int(reason == "TAKE_PROFIT")
                sl += int(reason == "STOP_LOSS")
                time_exit += int(reason == "TIME_EXIT_24H")
                pnl = _number(path_arm.get("realized_bps"), 4)
                if pnl is not None:
                    realized.append(pnl)
                    wins += int(pnl > 0)
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
    report["trade_path"] = {
        "schema_version": TRADE_PATH_SCHEMA,
        "prospective_from_engine_version": ENGINE_VERSION,
        "historical_backfill": False,
        "virtual_only": True,
        "entry_basis": "frozen_reference_price_not_executable_quote",
        "costs": "spread_and_slippage_not_available_in_yahoo_ohlc",
        "risk_contract": {
            "atr_timeframe": "30m",
            "atr_window": ATR_30M_WINDOW,
            "atr_multiple": ATR_MULTIPLE,
            "risk_floor_percent": RISK_FLOOR_PERCENT,
            "reward_risk": REWARD_RISK,
            "position_horizon_minutes": POSITION_HORIZON_MINUTES,
            "monitor_interval": MONITOR_INTERVAL,
            "same_1m_tp_sl_touch": "AMBIGUOUS_FAIL_CLOSED",
            "terminal_outcomes": "APPEND_ONLY",
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
        plan, path = capture.get("trade_plan"), capture.get("trade_path")
        plan_sha = capture.get("trade_plan_sha256")
        if not isinstance(plan, Mapping) or plan.get("schema_version") != TRADE_PLAN_SCHEMA:
            raise ValueError("PR24 v1.3 capture requires frozen trade plan")
        if not isinstance(plan_sha, str) or base._canonical_sha(plan) != plan_sha:
            raise ValueError("PR24 frozen trade plan hash invalid")
        if not isinstance(path, Mapping) or path.get("schema_version") != TRADE_PATH_SCHEMA:
            raise ValueError("PR24 v1.3 capture requires trade path")
        if path.get("source_plan_sha256") != plan_sha:
            raise ValueError("PR24 trade path plan lineage mismatch")
        if set((plan.get("arms") or {}).keys()) != {"A", "B", "C"} or set((path.get("arms") or {}).keys()) != {"A", "B", "C"}:
            raise ValueError("PR24 trade plan/path must contain A/B/C")
        if str(plan.get("signal_generated_at")) != str(capture.get("captured_at")):
            raise ValueError("PR24 monitoring must start at frozen signal generation time")
        risk = plan.get("risk_contract") or {}
        if risk.get("atr_window") != ATR_30M_WINDOW or float(risk.get("atr_multiple")) != ATR_MULTIPLE:
            raise ValueError("PR24 risk contract drift")
        if float(risk.get("risk_floor_percent")) != RISK_FLOOR_PERCENT or float(risk.get("reward_risk")) != REWARD_RISK:
            raise ValueError("PR24 risk/reward contract drift")
        for arm_id in ("A", "B", "C"):
            arm_plan = (plan.get("arms") or {})[arm_id]
            direction, status = str(arm_plan.get("direction") or "UNAVAILABLE"), str(arm_plan.get("status") or "UNAVAILABLE")
            if direction == "LONG" and status == "TRACKED" and not (float(arm_plan["stop_price"]) < float(arm_plan["entry_price"]) < float(arm_plan["target_price"])):
                raise ValueError("invalid LONG PR24 risk geometry")
            if direction == "SHORT" and status == "TRACKED" and not (float(arm_plan["target_price"]) < float(arm_plan["entry_price"]) < float(arm_plan["stop_price"])):
                raise ValueError("invalid SHORT PR24 risk geometry")
            path_status = str(((path.get("arms") or {})[arm_id]).get("status") or "")
            if path_status not in {"OPEN", "CLOSED", "AMBIGUOUS", "NO_TRADE", "UNAVAILABLE"}:
                raise ValueError(f"invalid PR24 trade path status {arm_id}={path_status}")


# Patch inherited runtime/report validation paths.
base.build_capture = build_capture
base.validate_state = validate_state
base.build_report = build_report

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
