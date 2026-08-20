#!/usr/bin/env python3
"""State, exit monitoring and bounded outcome learning for Daily EUR/USD.

The module is intentionally independent from Belief Core. It owns only the
EUR/USD Daily engine lifecycle: one open position at a time, deterministic
SL/TP/TIME exits, append-only outcomes and bounded learning from those outcomes.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from belief_market_data_adapter import Bar

HISTORY_SCHEMA = "eurusd-daily-history-v1"
POSITION_SCHEMA = "eurusd-daily-position-v1"
BASE_WEIGHTS = {
    "trend": 0.55,
    "broad_usd_environment": 0.25,
    "us_rates_pressure_proxy": 0.20,
}
BASE_LONG_THRESHOLD = 66.0
BASE_SHORT_THRESHOLD = 34.0
BASE_MIN_CONFIDENCE = 0.32
LOSS_COOLDOWN_HOURS = 4
MAX_ENTRIES_PER_UTC_DAY = 1


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def empty_history() -> dict[str, Any]:
    return {
        "schema_version": HISTORY_SCHEMA,
        "updated_at": None,
        "trades": [],
        "learning_state": learning_state([]),
    }


def load_history(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_history()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_history()
    trades = payload.get("trades") if isinstance(payload, dict) else None
    if payload.get("schema_version") != HISTORY_SCHEMA or not isinstance(trades, list):
        return empty_history()
    payload["learning_state"] = learning_state(trades)
    return payload


def save_history(path: Path, history: Mapping[str, Any], observed_at: datetime) -> None:
    trades = list(history.get("trades") or [])
    payload = {
        "schema_version": HISTORY_SCHEMA,
        "updated_at": iso_z(observed_at),
        "trades": trades,
        "learning_state": learning_state(trades),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _direction_sign(direction: str) -> float:
    return 1.0 if str(direction).upper() == "LONG" else -1.0


def _trade_id(opened_at: str, direction: str) -> str:
    clean = str(opened_at).replace(":", "").replace("-", "")
    return f"eurusd:{clean}:{str(direction).upper()}"


def position_from_output(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
    position = metadata.get("position") if isinstance(metadata, Mapping) else None
    if isinstance(position, Mapping) and str(position.get("status")).upper() == "OPEN":
        return dict(position)

    direction = str(payload.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return None
    try:
        entry = float(payload["entry"])
        stop = float(payload["stop"])
        target = float(payload["target"])
    except (KeyError, TypeError, ValueError):
        return None
    opened = parse_iso(str(payload.get("timestamp") or ""))
    if opened is None:
        return None
    components = metadata.get("components") if isinstance(metadata, Mapping) else {}
    weights = metadata.get("weights") if isinstance(metadata, Mapping) else {}
    return {
        "schema_version": POSITION_SCHEMA,
        "trade_id": _trade_id(iso_z(opened), direction),
        "status": "OPEN",
        "direction": direction,
        "opened_at": iso_z(opened),
        "expires_at": iso_z(opened + timedelta(hours=24)),
        "entry": entry,
        "stop": stop,
        "target": target,
        "entry_score": float(payload.get("score") or 0.0),
        "entry_confidence": float(payload.get("confidence") or 0.0),
        "entry_components": dict(components or {}),
        "entry_weights": dict(weights or BASE_WEIGHTS),
        "engine_version": str(payload.get("engine_version") or "legacy"),
        "legacy_bootstrap": True,
    }


def create_position(payload: Mapping[str, Any]) -> dict[str, Any]:
    direction = str(payload.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("cannot open a FLAT candidate")
    opened = parse_iso(str(payload.get("timestamp") or ""))
    if opened is None:
        raise ValueError("candidate timestamp is required")
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
    return {
        "schema_version": POSITION_SCHEMA,
        "trade_id": _trade_id(iso_z(opened), direction),
        "status": "OPEN",
        "direction": direction,
        "opened_at": iso_z(opened),
        "expires_at": iso_z(opened + timedelta(hours=24)),
        "entry": float(payload["entry"]),
        "stop": float(payload["stop"]),
        "target": float(payload["target"]),
        "entry_score": float(payload.get("score") or 0.0),
        "entry_confidence": float(payload.get("confidence") or 0.0),
        "entry_components": dict(metadata.get("components") or {}),
        "entry_weights": dict(metadata.get("weights") or BASE_WEIGHTS),
        "engine_version": str(payload.get("engine_version") or ""),
        "legacy_bootstrap": False,
    }


def _close_record(
    position: Mapping[str, Any],
    *,
    exit_reason: str,
    exit_price: float,
    exited_at: datetime,
    exit_bar: Bar | None = None,
    conservative_same_bar: bool = False,
) -> dict[str, Any]:
    direction = str(position["direction"]).upper()
    entry = float(position["entry"])
    stop = float(position["stop"])
    sign = _direction_sign(direction)
    pnl_price = sign * (float(exit_price) - entry)
    return_fraction = pnl_price / entry if entry else 0.0
    risk_unit = abs(entry - stop)
    r_multiple = pnl_price / risk_unit if risk_unit else 0.0
    return {
        "trade_id": str(position.get("trade_id") or _trade_id(str(position["opened_at"]), direction)),
        "instrument": "EUR/USD",
        "direction": direction,
        "opened_at": str(position["opened_at"]),
        "closed_at": iso_z(exited_at),
        "entry": round(entry, 5),
        "stop": round(stop, 5),
        "target": round(float(position["target"]), 5),
        "exit_price": round(float(exit_price), 5),
        "exit_reason": exit_reason,
        "result_percent": round(return_fraction * 100.0, 5),
        "return_fraction": round(return_fraction, 8),
        "r_multiple": round(r_multiple, 4),
        "outcome": "WIN" if r_multiple > 0 else "LOSS" if r_multiple < 0 else "FLAT",
        "entry_score": round(float(position.get("entry_score") or 0.0), 2),
        "entry_confidence": round(float(position.get("entry_confidence") or 0.0), 3),
        "entry_components": dict(position.get("entry_components") or {}),
        "entry_weights": dict(position.get("entry_weights") or BASE_WEIGHTS),
        "engine_version": str(position.get("engine_version") or ""),
        "monitor": {
            "source": "Yahoo Finance EURUSD=X 1m OHLC",
            "conservative_same_bar": bool(conservative_same_bar),
            "bar_timestamp": iso_z(exit_bar.timestamp) if exit_bar else None,
            "bar_open": None if not exit_bar or exit_bar.open is None else float(exit_bar.open),
            "bar_high": None if not exit_bar or exit_bar.high is None else float(exit_bar.high),
            "bar_low": None if not exit_bar or exit_bar.low is None else float(exit_bar.low),
            "bar_close": None if not exit_bar else float(exit_bar.close),
        },
    }


def evaluate_position(
    position: Mapping[str, Any],
    bars: Sequence[Bar],
    observed_at: datetime,
) -> dict[str, Any] | None:
    opened = parse_iso(str(position.get("opened_at") or ""))
    expires = parse_iso(str(position.get("expires_at") or ""))
    if opened is None or expires is None:
        raise ValueError("position must contain valid opened_at and expires_at")
    direction = str(position["direction"]).upper()
    stop = float(position["stop"])
    target = float(position["target"])

    relevant = sorted((bar for bar in bars if bar.timestamp >= opened), key=lambda bar: bar.timestamp)
    for bar in relevant:
        high = float(bar.high if bar.high is not None else bar.close)
        low = float(bar.low if bar.low is not None else bar.close)
        if direction == "LONG":
            stop_hit = low <= stop
            target_hit = high >= target
        else:
            stop_hit = high >= stop
            target_hit = low <= target

        if stop_hit and target_hit:
            return _close_record(
                position,
                exit_reason="STOP_LOSS",
                exit_price=stop,
                exited_at=bar.timestamp,
                exit_bar=bar,
                conservative_same_bar=True,
            )
        if stop_hit:
            return _close_record(
                position,
                exit_reason="STOP_LOSS",
                exit_price=stop,
                exited_at=bar.timestamp,
                exit_bar=bar,
            )
        if target_hit:
            return _close_record(
                position,
                exit_reason="TAKE_PROFIT",
                exit_price=target,
                exited_at=bar.timestamp,
                exit_bar=bar,
            )

    if observed_at >= expires:
        eligible = [bar for bar in relevant if bar.timestamp <= observed_at]
        if not eligible:
            return None
        last = eligible[-1]
        return _close_record(
            position,
            exit_reason="TIME_EXIT",
            exit_price=float(last.close),
            exited_at=last.timestamp,
            exit_bar=last,
        )
    return None


def append_trade(history: Mapping[str, Any], trade: Mapping[str, Any]) -> dict[str, Any]:
    trades = [dict(item) for item in history.get("trades") or []]
    trade_id = str(trade.get("trade_id") or "")
    if trade_id and any(str(item.get("trade_id") or "") == trade_id for item in trades):
        return {
            "schema_version": HISTORY_SCHEMA,
            "updated_at": history.get("updated_at"),
            "trades": trades,
            "learning_state": learning_state(trades),
        }
    trades.append(dict(trade))
    trades.sort(key=lambda item: str(item.get("closed_at") or item.get("opened_at") or ""))
    return {
        "schema_version": HISTORY_SCHEMA,
        "updated_at": history.get("updated_at"),
        "trades": trades,
        "learning_state": learning_state(trades),
    }


def _loss_streak(trades: Sequence[Mapping[str, Any]], direction: str | None = None) -> int:
    ordered = sorted(trades, key=lambda item: str(item.get("closed_at") or ""), reverse=True)
    count = 0
    for trade in ordered:
        if direction and str(trade.get("direction") or "").upper() != direction.upper():
            continue
        if float(trade.get("r_multiple") or 0.0) < 0:
            count += 1
        else:
            break
    return count


def _adaptive_weights(trades: Sequence[Mapping[str, Any]]) -> tuple[dict[str, float], dict[str, float]]:
    multipliers = {key: 1.0 for key in BASE_WEIGHTS}
    recent = sorted(trades, key=lambda item: str(item.get("closed_at") or ""))[-20:]
    for trade in recent:
        r = float(trade.get("r_multiple") or 0.0)
        if r == 0:
            continue
        sign = _direction_sign(str(trade.get("direction") or "LONG"))
        components = trade.get("entry_components") or {}
        support = {
            key: max(0.0, sign * float(components.get(key) or 0.0))
            for key in BASE_WEIGHTS
        }
        total_support = sum(support.values())
        if total_support <= 0:
            continue
        magnitude = min(abs(r), 2.0)
        for key, value in support.items():
            share = value / total_support
            if r < 0:
                multipliers[key] -= 0.05 * magnitude * share
            else:
                multipliers[key] += 0.025 * magnitude * share
            multipliers[key] = max(0.80, min(1.20, multipliers[key]))

    raw = {key: BASE_WEIGHTS[key] * multipliers[key] for key in BASE_WEIGHTS}
    total = sum(raw.values()) or 1.0
    weights = {key: round(value / total, 6) for key, value in raw.items()}
    return weights, {key: round(value, 6) for key, value in multipliers.items()}


def learning_state(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    closed = [trade for trade in trades if trade.get("closed_at")]
    total = len(closed)
    wins = sum(1 for trade in closed if float(trade.get("r_multiple") or 0.0) > 0)
    losses = sum(1 for trade in closed if float(trade.get("r_multiple") or 0.0) < 0)
    avg_r = sum(float(trade.get("r_multiple") or 0.0) for trade in closed) / total if total else 0.0
    streak = _loss_streak(closed)
    long_streak = _loss_streak(closed, "LONG")
    short_streak = _loss_streak(closed, "SHORT")
    weights, multipliers = _adaptive_weights(closed)

    general_penalty = min(6.0, float(streak) + max(0.0, -avg_r))
    long_penalty = min(3.0, float(long_streak) * 0.75)
    short_penalty = min(3.0, float(short_streak) * 0.75)
    long_threshold = min(78.0, BASE_LONG_THRESHOLD + general_penalty + long_penalty)
    short_threshold = max(22.0, BASE_SHORT_THRESHOLD - general_penalty - short_penalty)
    min_confidence = min(0.50, BASE_MIN_CONFIDENCE + 0.02 * streak)

    latest_stop = next((
        trade for trade in sorted(closed, key=lambda item: str(item.get("closed_at") or ""), reverse=True)
        if str(trade.get("exit_reason") or "") == "STOP_LOSS"
    ), None)
    cooldown_until = None
    if latest_stop:
        closed_at = parse_iso(str(latest_stop.get("closed_at") or ""))
        if closed_at:
            cooldown_until = iso_z(closed_at + timedelta(hours=LOSS_COOLDOWN_HOURS))

    return {
        "total_closed": total,
        "wins": wins,
        "losses": losses,
        "win_rate_percent": round(100.0 * wins / total, 2) if total else None,
        "average_r": round(avg_r, 4),
        "consecutive_losses": streak,
        "direction_loss_streak": {"LONG": long_streak, "SHORT": short_streak},
        "adaptive_weights": weights,
        "component_multipliers": multipliers,
        "entry_thresholds": {
            "long": round(long_threshold, 2),
            "short": round(short_threshold, 2),
            "min_confidence": round(min_confidence, 3),
        },
        "cooldown_until": cooldown_until,
        "policy": {
            "max_entries_per_utc_day": MAX_ENTRIES_PER_UTC_DAY,
            "loss_cooldown_hours": LOSS_COOLDOWN_HOURS,
            "weight_update": "bounded outcome feedback; losses reduce reliability of components that supported the losing direction",
        },
    }


def entry_gate(
    *,
    direction: str,
    score: float,
    confidence: float,
    history: Mapping[str, Any],
    observed_at: datetime,
    previous_score: float | None,
    stretch_atr: float | None,
    shock_ratio: float | None,
) -> dict[str, Any]:
    direction = str(direction).upper()
    learning = learning_state(history.get("trades") or [])
    thresholds = learning["entry_thresholds"]
    reasons: list[str] = []

    if direction not in {"LONG", "SHORT"}:
        reasons.append("raw_score_neutral")
    elif direction == "LONG" and float(score) < float(thresholds["long"]):
        reasons.append("score_below_adaptive_long_threshold")
    elif direction == "SHORT" and float(score) > float(thresholds["short"]):
        reasons.append("score_above_adaptive_short_threshold")

    if direction in {"LONG", "SHORT"} and float(confidence) < float(thresholds["min_confidence"]):
        reasons.append("confidence_below_minimum")

    if previous_score is not None and direction == "LONG" and previous_score < 58.0:
        reasons.append("signal_not_persistent")
    if previous_score is not None and direction == "SHORT" and previous_score > 42.0:
        reasons.append("signal_not_persistent")

    if stretch_atr is not None and stretch_atr > 1.25:
        reasons.append("entry_overextended_vs_ema20")
    if shock_ratio is not None and shock_ratio > 2.40:
        reasons.append("latest_30m_bar_is_shock_bar")

    cooldown = parse_iso(learning.get("cooldown_until"))
    if cooldown and observed_at < cooldown:
        reasons.append("post_stop_cooldown")

    today = observed_at.astimezone(timezone.utc).date()
    entries_today = 0
    for trade in history.get("trades") or []:
        opened = parse_iso(str(trade.get("opened_at") or ""))
        if opened and opened.date() == today:
            entries_today += 1
    if entries_today >= MAX_ENTRIES_PER_UTC_DAY:
        reasons.append("daily_entry_limit_reached")

    return {
        "accepted": not reasons,
        "reasons": reasons,
        "learning": learning,
        "previous_score": None if previous_score is None else round(float(previous_score), 2),
        "stretch_atr": None if stretch_atr is None else round(float(stretch_atr), 4),
        "shock_ratio": None if shock_ratio is None else round(float(shock_ratio), 4),
    }
