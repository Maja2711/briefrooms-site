#!/usr/bin/env python3
"""PR32 dynamic exit layer for active Daily EUR/USD.

Adds a soft 24h horizon, a hard 27h horizon and deterministic dynamic exits.
The engine is still shadow/virtual: no broker execution is added here.

Exit priority:
1. hard SL / TP from the existing lifecycle,
2. dynamic profit protection,
3. dynamic loss containment,
4. soft-horizon decision at 24h,
5. unconditional hard time exit at 27h.

The dynamic decision is calculation-based. It uses current R, MFE/giveback,
1h and 3h directional R-velocity, remaining R to TP and the pace required to
reach TP before the hard horizon. It deliberately does not claim a calibrated
probability of reaching TP.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from belief_market_data_adapter import Bar
from daily_engine_contract import DailyEngineOutput
import daily_eurusd_lifecycle as lifecycle
import daily_eurusd_spot as base
import daily_eurusd_spot_v13 as v13  # installs PR25 admission/A fallback first

ENGINE_VERSION = "eurusd-daily-spot-v1.4.0"
SOFT_HORIZON_HOURS = 24.0
HARD_HORIZON_HOURS = 27.0
MIN_DYNAMIC_AGE_HOURS = 4.0
PROFIT_PROTECT_MIN_R = 0.25
PROFIT_GIVEBACK_R = 0.25
LOSS_CONTAINMENT_R = -0.35
LATE_LOSS_R = -0.15
SOFT_EXTENSION_MIN_R = -0.10
SOFT_EXTENSION_MIN_HOLD_SCORE = 0.10
SOFT_EXTENSION_MIN_FEASIBILITY = 0.20

_original_build_output = base.build_output
_original_create_position = base.create_position
_original_position_from_output = base.position_from_output
_original_evaluate_position = base.evaluate_position
_original_open_output = base._open_output
_original_closed_output = base._closed_output


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value: str | None) -> datetime | None:
    return lifecycle.parse_iso(value)


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def normalize_position(position: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Upgrade existing/open positions to soft-24h + hard-27h semantics."""
    if not position:
        return None
    result = dict(position)
    opened = _parse(str(result.get("opened_at") or ""))
    if opened is None:
        return result
    result["soft_expires_at"] = _iso(opened + timedelta(hours=SOFT_HORIZON_HOURS))
    result["expires_at"] = _iso(opened + timedelta(hours=HARD_HORIZON_HOURS))
    result["dynamic_exit_policy"] = "R_PACE_V1"
    result["soft_horizon_hours"] = SOFT_HORIZON_HOURS
    result["hard_horizon_hours"] = HARD_HORIZON_HOURS
    return result


def create_position(payload: Mapping[str, Any]) -> dict[str, Any]:
    return normalize_position(_original_create_position(payload)) or {}


def position_from_output(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    return normalize_position(_original_position_from_output(payload))


def _direction_sign(direction: str) -> float:
    return 1.0 if str(direction).upper() == "LONG" else -1.0


def _bar_at_or_before(bars: Sequence[Bar], at: datetime) -> Bar | None:
    eligible = [bar for bar in bars if bar.timestamp <= at]
    return eligible[-1] if eligible else None


def _r_at_price(position: Mapping[str, Any], price: float) -> float:
    entry = float(position["entry"])
    stop = float(position["stop"])
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    return _direction_sign(str(position["direction"])) * (float(price) - entry) / risk


def _velocity_r_per_hour(
    position: Mapping[str, Any],
    bars: Sequence[Bar],
    observed_at: datetime,
    lookback_hours: float,
) -> float:
    last = _bar_at_or_before(bars, observed_at)
    earlier = _bar_at_or_before(bars, observed_at - timedelta(hours=lookback_hours))
    if last is None or earlier is None or last.timestamp <= earlier.timestamp:
        return 0.0
    hours = (last.timestamp - earlier.timestamp).total_seconds() / 3600.0
    if hours <= 0:
        return 0.0
    return (_r_at_price(position, float(last.close)) - _r_at_price(position, float(earlier.close))) / hours


def dynamic_exit_diagnostics(
    position: Mapping[str, Any],
    bars: Sequence[Bar],
    observed_at: datetime,
) -> dict[str, Any] | None:
    opened = _parse(str(position.get("opened_at") or ""))
    if opened is None:
        return None
    relevant = sorted(
        (bar for bar in bars if opened <= bar.timestamp <= observed_at),
        key=lambda bar: bar.timestamp,
    )
    if not relevant:
        return None

    last = relevant[-1]
    current_r = _r_at_price(position, float(last.close))
    direction = str(position["direction"]).upper()
    sign = _direction_sign(direction)
    entry = float(position["entry"])
    stop = float(position["stop"])
    target = float(position["target"])
    risk = abs(entry - stop)
    target_r = sign * (target - entry) / risk if risk else 0.0

    if direction == "LONG":
        best_price = max(float(bar.high if bar.high is not None else bar.close) for bar in relevant)
    else:
        best_price = min(float(bar.low if bar.low is not None else bar.close) for bar in relevant)
    mfe_r = sign * (best_price - entry) / risk if risk else 0.0
    giveback_r = max(0.0, mfe_r - current_r)

    age_hours = max(0.0, (observed_at - opened).total_seconds() / 3600.0)
    hard_at = opened + timedelta(hours=HARD_HORIZON_HOURS)
    soft_at = opened + timedelta(hours=SOFT_HORIZON_HOURS)
    remaining_hard_hours = max(0.0, (hard_at - observed_at).total_seconds() / 3600.0)
    remaining_r = max(0.0, target_r - current_r)

    velocity_1h = _velocity_r_per_hour(position, relevant, observed_at, 1.0)
    velocity_3h = _velocity_r_per_hour(position, relevant, observed_at, 3.0)
    blended_velocity = 0.65 * velocity_1h + 0.35 * velocity_3h
    required_velocity = remaining_r / max(remaining_hard_hours, 0.25)
    feasibility = 2.0 if required_velocity <= 0 else max(0.0, min(2.0, blended_velocity / required_velocity))
    projected_hours_to_target = None
    if remaining_r <= 0:
        projected_hours_to_target = 0.0
    elif blended_velocity > 0.01:
        projected_hours_to_target = remaining_r / blended_velocity

    pace_score = _clamp((feasibility - 0.50) / 0.50)
    momentum_score = _clamp(blended_velocity / 0.25)
    pnl_score = _clamp(current_r / 0.75)
    time_pressure = _clamp((age_hours - 12.0) / 15.0, 0.0, 1.0)
    hold_score = _clamp(0.45 * pace_score + 0.35 * momentum_score + 0.20 * pnl_score - 0.15 * time_pressure)

    return {
        "policy": "R_PACE_V1",
        "age_hours": round(age_hours, 3),
        "soft_horizon_reached": observed_at >= soft_at,
        "hard_horizon_reached": observed_at >= hard_at,
        "remaining_hard_hours": round(remaining_hard_hours, 3),
        "current_r": round(current_r, 4),
        "target_r": round(target_r, 4),
        "remaining_r_to_target": round(remaining_r, 4),
        "mfe_r": round(mfe_r, 4),
        "giveback_r": round(giveback_r, 4),
        "velocity_1h_rph": round(velocity_1h, 4),
        "velocity_3h_rph": round(velocity_3h, 4),
        "blended_velocity_rph": round(blended_velocity, 4),
        "required_velocity_rph": round(required_velocity, 4),
        "tp_feasibility_ratio": round(feasibility, 4),
        "projected_hours_to_target": None if projected_hours_to_target is None else round(projected_hours_to_target, 3),
        "hold_score": round(hold_score, 4),
        "mark_price": round(float(last.close), 5),
        "bar_timestamp": _iso(last.timestamp),
    }


def _dynamic_close(
    position: Mapping[str, Any],
    bars: Sequence[Bar],
    reason: str,
    diagnostics: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not bars:
        return None
    last = bars[-1]
    trade = lifecycle._close_record(
        position,
        exit_reason=reason,
        exit_price=float(last.close),
        exited_at=last.timestamp,
        exit_bar=last,
    )
    trade["monitor"]["dynamic_exit"] = dict(diagnostics)
    return trade


def evaluate_position(
    position: Mapping[str, Any],
    bars: Sequence[Bar],
    observed_at: datetime,
) -> dict[str, Any] | None:
    position = normalize_position(position) or dict(position)

    # Existing lifecycle keeps first priority for hard SL/TP and hard 27h time exit.
    terminal = _original_evaluate_position(position, bars, observed_at)
    if terminal is not None:
        if str(terminal.get("exit_reason")) == "TIME_EXIT":
            terminal["exit_reason"] = "TIME_EXIT_27H"
            diagnostics = dynamic_exit_diagnostics(position, bars, observed_at)
            if diagnostics is not None:
                terminal["monitor"]["dynamic_exit"] = diagnostics
        return terminal

    opened = _parse(str(position.get("opened_at") or ""))
    if opened is None:
        return None
    relevant = sorted(
        (bar for bar in bars if opened <= bar.timestamp <= observed_at),
        key=lambda bar: bar.timestamp,
    )
    if not relevant:
        return None
    diagnostics = dynamic_exit_diagnostics(position, relevant, observed_at)
    if diagnostics is None:
        return None

    age = float(diagnostics["age_hours"])
    current_r = float(diagnostics["current_r"])
    giveback = float(diagnostics["giveback_r"])
    v1 = float(diagnostics["velocity_1h_rph"])
    v3 = float(diagnostics["velocity_3h_rph"])
    feasibility = float(diagnostics["tp_feasibility_ratio"])
    hold_score = float(diagnostics["hold_score"])

    if age >= MIN_DYNAMIC_AGE_HOURS:
        # Protect an existing gain when momentum has rolled over or a meaningful
        # fraction of MFE has already been surrendered.
        if current_r >= PROFIT_PROTECT_MIN_R and (
            (giveback >= PROFIT_GIVEBACK_R and v1 <= 0.0)
            or (age >= 8.0 and hold_score <= -0.15)
        ):
            return _dynamic_close(position, relevant, "DYNAMIC_PROFIT_EXIT", diagnostics)

        # Do not wait mechanically for -1R when both current P&L and recent
        # directional pace have deteriorated.
        if current_r <= LOSS_CONTAINMENT_R and v1 < 0.0 and v3 <= 0.0 and hold_score <= -0.10:
            return _dynamic_close(position, relevant, "DYNAMIC_RISK_EXIT", diagnostics)

        # Late losing/stalled trades are cut earlier when TP pace is no longer
        # economically plausible under the remaining hard-horizon time.
        if age >= 12.0 and current_r <= LATE_LOSS_R and feasibility < 0.35 and v1 <= 0.0 and hold_score < 0.0:
            return _dynamic_close(position, relevant, "DYNAMIC_RISK_EXIT", diagnostics)

    # 24h is now a soft decision point, not an automatic exit. Extension to 27h
    # is granted only if the position still has positive hold economics.
    if bool(diagnostics["soft_horizon_reached"]):
        extend = (
            current_r >= SOFT_EXTENSION_MIN_R
            and hold_score >= SOFT_EXTENSION_MIN_HOLD_SCORE
            and feasibility >= SOFT_EXTENSION_MIN_FEASIBILITY
            and (v1 > 0.0 or v3 > 0.0)
        )
        if not extend:
            return _dynamic_close(position, relevant, "SOFT_HORIZON_EXIT", diagnostics)

    return None


def _with_dynamic_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(metadata)
    risk = dict(result.get("risk") or {})
    risk.update({
        "position_horizon_hours": HARD_HORIZON_HOURS,
        "soft_horizon_hours": SOFT_HORIZON_HOURS,
        "hard_horizon_hours": HARD_HORIZON_HOURS,
        "dynamic_exit_policy": "R_PACE_V1",
        "dynamic_monitor_cycle": "5m workflow / 1m OHLC path",
    })
    result["risk"] = risk
    result["dynamic_exit"] = {
        "enabled": True,
        "policy": "R_PACE_V1",
        "soft_horizon_hours": SOFT_HORIZON_HOURS,
        "hard_horizon_hours": HARD_HORIZON_HOURS,
        "calibrated_probability_claim": False,
        "inputs": [
            "current_r",
            "mfe_r_and_giveback",
            "1h_directional_r_velocity",
            "3h_directional_r_velocity",
            "remaining_r_to_target",
            "required_r_velocity_to_hard_horizon",
        ],
    }
    return result


def _clone_output(output: DailyEngineOutput, *, metadata: Mapping[str, Any] | None = None) -> DailyEngineOutput:
    return DailyEngineOutput(
        instrument=output.instrument,
        timestamp=output.timestamp,
        direction=output.direction,
        score=output.score,
        confidence=output.confidence,
        entry=output.entry,
        stop=output.stop,
        target=output.target,
        horizon="intraday_to_27h",
        engine_version=ENGINE_VERSION,
        status=output.status,
        decision_mode=output.decision_mode,
        metadata=dict(metadata if metadata is not None else output.metadata),
    ).validate()


def build_output(snapshot: Any, history: Mapping[str, Any] | None = None, *, allow_entry: bool = True) -> DailyEngineOutput:
    output = _original_build_output(snapshot, history, allow_entry=allow_entry)
    return _clone_output(output, metadata=_with_dynamic_metadata(output.metadata))


def _open_output(candidate: DailyEngineOutput, position: Mapping[str, Any], mark_price: float) -> DailyEngineOutput:
    normalized = normalize_position(position) or dict(position)
    output = _original_open_output(candidate, normalized, mark_price)
    metadata = _with_dynamic_metadata(output.metadata)
    pos = dict(metadata.get("position") or {})
    if pos:
        metadata["position"] = normalize_position(pos)
    return _clone_output(output, metadata=metadata)


def _closed_output(candidate: DailyEngineOutput, trade: Mapping[str, Any], history: Mapping[str, Any]) -> DailyEngineOutput:
    output = _original_closed_output(candidate, trade, history)
    return _clone_output(output, metadata=_with_dynamic_metadata(output.metadata))


def _install() -> None:
    base.ENGINE_VERSION = ENGINE_VERSION
    base.build_output = build_output
    base.create_position = create_position
    base.position_from_output = position_from_output
    base.evaluate_position = evaluate_position
    base._open_output = _open_output
    base._closed_output = _closed_output


_install()


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
