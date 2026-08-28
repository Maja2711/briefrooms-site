#!/usr/bin/env python3
"""PR33 controlled learning exploration for active Daily EUR/USD.

The normal v1.4 decision path keeps priority. If it remains FLAT, v1.5 may
open one low-edge learning position when the active engine's own continuous
score has a measurable direction and the native components broadly agree.

This is deliberately not a random always-in-market rule. Exploration requires:
- |score - 50| >= 1.0 point,
- at least two native components supporting the chosen direction,
- weighted directional dominance >= 0.20,
- fresh market state,
- no exploration trade already closed on the same UTC day,
- a two-hour exploration cooldown after the latest closed trade.

Exploration uses the existing 30m ATR SL/TP geometry and one-open-position
lifecycle. It is tagged end-to-end so closed outcomes, MFE and MAE can be used
for learning/audit without pretending the trade was a normal high-conviction
signal. The engine remains shadow/virtual and does not execute broker orders.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from belief_market_data_adapter import Bar
from daily_engine_contract import DailyEngineOutput
import daily_eurusd_lifecycle as lifecycle
import daily_eurusd_spot as base
import daily_eurusd_spot_v14 as v14  # installs PR32 + PR25 + PR22 first

ENGINE_VERSION = "eurusd-daily-spot-v1.5.0"
EXPLORATION_MIN_EDGE_POINTS = 1.0
EXPLORATION_MIN_SUPPORTING_COMPONENTS = 2
EXPLORATION_MIN_DIRECTIONAL_DOMINANCE = 0.20
EXPLORATION_COOLDOWN_HOURS = 2.0
EXPLORATION_MAX_PER_UTC_DAY = 1
EXPLORATION_RISK_BUDGET_MULTIPLIER = 0.35
EXPLORATION_MAX_MARKET_AGE_MINUTES = 90.0

_original_build_output = base.build_output
_original_create_position = base.create_position
_original_position_from_output = base.position_from_output
_original_evaluate_position = base.evaluate_position
_original_open_output = base._open_output
_original_closed_output = base._closed_output


def _parse(value: str | None) -> datetime | None:
    return lifecycle.parse_iso(value)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _clone(output: DailyEngineOutput, *, metadata: Mapping[str, Any] | None = None) -> DailyEngineOutput:
    return DailyEngineOutput(
        instrument=output.instrument,
        timestamp=output.timestamp,
        direction=output.direction,
        score=float(output.score),
        confidence=float(output.confidence),
        entry=output.entry,
        stop=output.stop,
        target=output.target,
        horizon=output.horizon,
        engine_version=ENGINE_VERSION,
        status=output.status,
        decision_mode=output.decision_mode,
        metadata=dict(metadata if metadata is not None else output.metadata),
    ).validate()


def _closed_trades(history: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not history:
        return []
    return [row for row in (history.get("trades") or []) if isinstance(row, Mapping) and row.get("closed_at")]


def _exploration_admission(
    native: DailyEngineOutput,
    history: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> tuple[bool, dict[str, Any]]:
    observed_at = _parse(native.timestamp)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if observed_at is None:
        return False, {"reason": "missing_market_timestamp"}

    market_age_minutes = max(0.0, (current - observed_at).total_seconds() / 60.0)
    if current < observed_at or market_age_minutes > EXPLORATION_MAX_MARKET_AGE_MINUTES:
        return False, {
            "reason": "stale_market_state",
            "market_age_minutes": round(market_age_minutes, 2),
        }

    score = float(native.score)
    edge = score - 50.0
    if abs(edge) < EXPLORATION_MIN_EDGE_POINTS:
        return False, {
            "reason": "edge_too_small",
            "edge_points": round(edge, 4),
            "min_edge_points": EXPLORATION_MIN_EDGE_POINTS,
        }

    sign = 1.0 if edge > 0 else -1.0
    direction = "LONG" if sign > 0 else "SHORT"
    metadata = dict(native.metadata)
    components = {
        str(key): float(value)
        for key, value in dict(metadata.get("components") or {}).items()
        if isinstance(value, (int, float))
    }
    weights = {
        str(key): float(value)
        for key, value in dict(metadata.get("weights") or {}).items()
        if isinstance(value, (int, float))
    }
    if not components:
        return False, {"reason": "native_components_unavailable", "edge_points": round(edge, 4)}

    supporting = [key for key, value in components.items() if sign * float(value) > 0.0]
    weighted = {
        key: float(value) * float(weights.get(key, 0.0))
        for key, value in components.items()
    }
    abs_weighted = sum(abs(value) for value in weighted.values())
    net_weighted = sum(weighted.values())
    dominance = 0.0 if abs_weighted <= 1e-15 else sign * net_weighted / abs_weighted
    if len(supporting) < EXPLORATION_MIN_SUPPORTING_COMPONENTS:
        return False, {
            "reason": "insufficient_component_agreement",
            "direction": direction,
            "supporting_components": supporting,
            "required": EXPLORATION_MIN_SUPPORTING_COMPONENTS,
            "directional_dominance": round(dominance, 4),
        }
    if dominance < EXPLORATION_MIN_DIRECTIONAL_DOMINANCE:
        return False, {
            "reason": "insufficient_directional_dominance",
            "direction": direction,
            "supporting_components": supporting,
            "directional_dominance": round(dominance, 4),
            "required": EXPLORATION_MIN_DIRECTIONAL_DOMINANCE,
        }

    trades = _closed_trades(history)
    same_day_explorations = [
        trade for trade in trades
        if str(trade.get("decision_source") or "") == "LOW_EDGE_LEARNING_EXPLORATION"
        and (_parse(str(trade.get("closed_at") or "")) or datetime.min.replace(tzinfo=timezone.utc)).date() == observed_at.date()
    ]
    if len(same_day_explorations) >= EXPLORATION_MAX_PER_UTC_DAY:
        return False, {
            "reason": "exploration_daily_limit",
            "daily_limit": EXPLORATION_MAX_PER_UTC_DAY,
            "closed_today": len(same_day_explorations),
        }

    latest_closed_at = max(
        (_parse(str(trade.get("closed_at") or "")) for trade in trades),
        default=None,
        key=lambda value: value or datetime.min.replace(tzinfo=timezone.utc),
    )
    if latest_closed_at is not None:
        cooldown_until = latest_closed_at + timedelta(hours=EXPLORATION_COOLDOWN_HOURS)
        if observed_at < cooldown_until:
            return False, {
                "reason": "learning_exploration_cooldown",
                "cooldown_until": _iso(cooldown_until),
                "cooldown_hours": EXPLORATION_COOLDOWN_HOURS,
            }

    return True, {
        "reason": "eligible",
        "direction": direction,
        "edge_points": round(edge, 4),
        "supporting_components": supporting,
        "supporting_component_count": len(supporting),
        "directional_dominance": round(dominance, 4),
        "market_age_minutes": round(market_age_minutes, 2),
        "weighted_contributions": {key: round(value, 6) for key, value in weighted.items()},
    }


def _promote_learning_exploration(
    native: DailyEngineOutput,
    snapshot: Any,
    history: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> DailyEngineOutput:
    if native.direction != "FLAT":
        metadata = dict(native.metadata)
        if metadata.get("decision_source") == "A_TECHNICAL_FALLBACK":
            metadata["learning_eligible"] = True
            metadata["learning_namespace"] = "A_TECHNICAL_FALLBACK"
        return _clone(native, metadata=metadata)

    eligible, diagnostics = _exploration_admission(native, history, now=now)
    metadata = dict(native.metadata)
    metadata["exploration"] = {
        "mode": "LOW_EDGE_LEARNING_EXPLORATION",
        "eligible": bool(eligible),
        "min_edge_points": EXPLORATION_MIN_EDGE_POINTS,
        "min_supporting_components": EXPLORATION_MIN_SUPPORTING_COMPONENTS,
        "min_directional_dominance": EXPLORATION_MIN_DIRECTIONAL_DOMINANCE,
        "cooldown_hours": EXPLORATION_COOLDOWN_HOURS,
        "max_per_utc_day": EXPLORATION_MAX_PER_UTC_DAY,
        "risk_budget_multiplier": EXPLORATION_RISK_BUDGET_MULTIPLIER,
        **diagnostics,
    }
    if not eligible:
        return _clone(native, metadata=metadata)

    fx_rows = list(snapshot.bars.get(base.EURUSD) or [])
    if not fx_rows:
        metadata["exploration"].update({"eligible": False, "reason": "missing_execution_bars"})
        return _clone(native, metadata=metadata)
    atr = base._atr(fx_rows, 26)
    if atr is None or float(atr) <= 0.0:
        metadata["exploration"].update({"eligible": False, "reason": "invalid_execution_atr"})
        return _clone(native, metadata=metadata)

    direction = str(diagnostics["direction"])
    entry = float(fx_rows[-1].close)
    risk = max(float(atr) * 1.35, entry * 0.0027)
    reward_risk = 1.8
    stop = entry - risk if direction == "LONG" else entry + risk
    target = entry + risk * reward_risk if direction == "LONG" else entry - risk * reward_risk
    confidence = min(0.25, abs(float(native.score) - 50.0) / 50.0)

    previous_candidate = dict(metadata.get("candidate") or {})
    metadata["native_candidate"] = previous_candidate
    metadata["decision_source"] = "LOW_EDGE_LEARNING_EXPLORATION"
    metadata["learning_eligible"] = True
    metadata["learning_namespace"] = "NATIVE_COMPONENTS_LOW_EDGE"
    metadata["candidate"] = {
        "direction": direction,
        "score": round(float(native.score), 2),
        "confidence": round(confidence, 3),
        "accepted": True,
        "gate_reasons": [],
        "source": "LOW_EDGE_LEARNING_EXPLORATION",
        "native_was_flat": True,
        "edge_points": diagnostics["edge_points"],
        "supporting_components": diagnostics["supporting_components"],
        "directional_dominance": diagnostics["directional_dominance"],
    }
    risk_meta = dict(metadata.get("risk") or {})
    risk_meta["exploration_risk_budget_multiplier"] = EXPLORATION_RISK_BUDGET_MULTIPLIER
    risk_meta["reward_risk"] = reward_risk
    metadata["risk"] = risk_meta

    def px(value: float) -> float:
        return round(float(value), 5)

    return DailyEngineOutput(
        instrument=native.instrument,
        timestamp=native.timestamp,
        direction=direction,
        score=float(native.score),
        confidence=confidence,
        entry=px(entry),
        stop=px(stop),
        target=px(target),
        horizon=native.horizon,
        engine_version=ENGINE_VERSION,
        status="SIGNAL",
        decision_mode=native.decision_mode,
        metadata=metadata,
    ).validate()


def build_output(snapshot: Any, history: Mapping[str, Any] | None = None, *, allow_entry: bool = True) -> DailyEngineOutput:
    output = _original_build_output(snapshot, history, allow_entry=allow_entry)
    if not allow_entry:
        return _clone(output)
    return _promote_learning_exploration(output, snapshot, history)


def create_position(payload: Mapping[str, Any]) -> dict[str, Any]:
    position = dict(_original_create_position(payload))
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
    position["decision_source"] = str(metadata.get("decision_source") or "NATIVE")
    position["learning_eligible"] = bool(metadata.get("learning_eligible", True))
    position["learning_namespace"] = str(metadata.get("learning_namespace") or "NATIVE")
    if isinstance(metadata.get("exploration"), Mapping):
        position["exploration"] = dict(metadata.get("exploration") or {})
    return position


def position_from_output(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    position = _original_position_from_output(payload)
    if not position:
        return None
    result = dict(position)
    result.setdefault("decision_source", "NATIVE_LEGACY")
    result.setdefault("learning_eligible", True)
    result.setdefault("learning_namespace", "NATIVE")
    return result


def _excursion_metrics(position: Mapping[str, Any], bars: Sequence[Bar], closed_at: datetime) -> dict[str, float]:
    opened_at = _parse(str(position.get("opened_at") or ""))
    if opened_at is None:
        return {}
    relevant = [bar for bar in bars if opened_at <= bar.timestamp <= closed_at]
    if not relevant:
        return {}
    entry = float(position["entry"])
    stop = float(position["stop"])
    risk = abs(entry - stop)
    if risk <= 0.0:
        return {}
    direction = str(position.get("direction") or "").upper()
    highs = [float(bar.high if bar.high is not None else bar.close) for bar in relevant]
    lows = [float(bar.low if bar.low is not None else bar.close) for bar in relevant]
    if direction == "LONG":
        favorable = max(highs) - entry
        adverse = min(lows) - entry
    else:
        favorable = entry - min(lows)
        adverse = entry - max(highs)
    return {
        "mfe_r": round(favorable / risk, 4),
        "mae_r": round(adverse / risk, 4),
        "mfe_pips": round(favorable / 0.0001, 2),
        "mae_pips": round(adverse / 0.0001, 2),
    }


def evaluate_position(position: Mapping[str, Any], bars: Sequence[Bar], observed_at: datetime) -> dict[str, Any] | None:
    trade = _original_evaluate_position(position, bars, observed_at)
    if trade is None:
        return None
    enriched = dict(trade)
    enriched["decision_source"] = str(position.get("decision_source") or "NATIVE_LEGACY")
    enriched["learning_eligible"] = bool(position.get("learning_eligible", True))
    enriched["learning_namespace"] = str(position.get("learning_namespace") or "NATIVE")
    if isinstance(position.get("exploration"), Mapping):
        enriched["exploration"] = dict(position.get("exploration") or {})
    closed_at = _parse(str(enriched.get("closed_at") or ""))
    if closed_at is not None:
        enriched.update(_excursion_metrics(position, bars, closed_at))
    return enriched


def _open_output(candidate: DailyEngineOutput, position: Mapping[str, Any], mark_price: float) -> DailyEngineOutput:
    output = _original_open_output(candidate, position, mark_price)
    return _clone(output)


def _closed_output(candidate: DailyEngineOutput, trade: Mapping[str, Any], history: Mapping[str, Any]) -> DailyEngineOutput:
    output = _original_closed_output(candidate, trade, history)
    return _clone(output)


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
