#!/usr/bin/env python3
"""Prospective A/B/C research shadow for Daily EUR/USD.

A = EUR/USD technical-only timing arm.
B = canonical frozen Belief Core arm.
C = overlap-controlled hybrid: technical timing + orthogonal Belief context.

The module never writes to Belief Core and never influences the live Daily
EUR/USD decision path. All captures are prospective and outcome resolution is
forward-only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from belief_market_data_adapter import Bar, YahooChartClient

SCHEMA_VERSION = "eurusd-daily-abc-experiment-v1"
REPORT_SCHEMA_VERSION = "eurusd-daily-abc-report-v1"
ENGINE_VERSION = "eurusd-daily-abc-v1.0.0"
MODE = "research_shadow"
EURUSD = "EURUSD=X"

HORIZONS_MINUTES = (30, 60, 120, 240, 1440)
LONG_THRESHOLD = 60.0
SHORT_THRESHOLD = 40.0

TECHNICAL_WEIGHTS = {
    "ma_structure": 0.24,
    "rsi_momentum": 0.16,
    "trendline": 0.20,
    "support_resistance": 0.20,
    "price_momentum": 0.20,
}

BELIEF_WEIGHTS = {
    "eurusd.trend.bullish": 0.55,
    "eurusd.usd_environment.supportive": 0.25,
    "eurusd.us_rates_pressure.supportive": 0.20,
}

# The direct EUR/USD trend Belief overlaps with Arm A's price-derived trend
# family, so Arm C excludes it from the context sub-score instead of counting
# the same information twice.
HYBRID_CONTEXT_BELIEF_IDS = (
    "eurusd.usd_environment.supportive",
    "eurusd.us_rates_pressure.supportive",
)
HYBRID_TECHNICAL_WEIGHT = 0.70
HYBRID_BELIEF_CONTEXT_WEIGHT = 0.30


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _canonical_sha(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _return(rows: Sequence[Bar], bars: int) -> float | None:
    if len(rows) <= bars:
        return None
    start = float(rows[-1 - bars].close)
    if start == 0:
        return None
    return float(rows[-1].close) / start - 1.0


def _sma(rows: Sequence[Bar], window: int) -> float | None:
    if len(rows) < window:
        return None
    values = [float(bar.close) for bar in rows[-window:]]
    return sum(values) / len(values)


def _ema(rows: Sequence[Bar], window: int) -> float | None:
    if len(rows) < window:
        return None
    alpha = 2.0 / (window + 1.0)
    value = float(rows[-window].close)
    for bar in rows[-window + 1 :]:
        value = alpha * float(bar.close) + (1.0 - alpha) * value
    return value


def _atr(rows: Sequence[Bar], window: int = 14) -> float | None:
    chunk = list(rows[-window - 1 :])
    if len(chunk) < 3:
        return None
    values: list[float] = []
    for previous, current in zip(chunk[:-1], chunk[1:]):
        high = float(current.high if current.high is not None else current.close)
        low = float(current.low if current.low is not None else current.close)
        prev_close = float(previous.close)
        values.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(values) / len(values) if values else None


def _rsi(rows: Sequence[Bar], window: int = 14) -> float | None:
    if len(rows) < window + 1:
        return None
    closes = [float(bar.close) for bar in rows]
    changes = [b - a for a, b in zip(closes[:-1], closes[1:])]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain = sum(gains[:window]) / window
    avg_loss = sum(losses[:window]) / window
    for gain, loss in zip(gains[window:], losses[window:]):
        avg_gain = ((window - 1) * avg_gain + gain) / window
        avg_loss = ((window - 1) * avg_loss + loss) / window
    if avg_loss <= 1e-15:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _trendline(rows: Sequence[Bar], window: int = 24) -> tuple[float, float, float] | None:
    if len(rows) < window:
        return None
    ys = [float(bar.close) for bar in rows[-window:]]
    n = len(ys)
    x_mean = (n - 1) / 2.0
    y_mean = sum(ys) / n
    denominator = sum((i - x_mean) ** 2 for i in range(n))
    if denominator <= 0:
        return None
    slope = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(ys)) / denominator
    intercept = y_mean - slope * x_mean
    fitted = [intercept + slope * i for i in range(n)]
    ss_tot = sum((y - y_mean) ** 2 for y in ys)
    ss_res = sum((y - yhat) ** 2 for y, yhat in zip(ys, fitted))
    r2 = 0.0 if ss_tot <= 1e-18 else _clamp(1.0 - ss_res / ss_tot, 0.0, 1.0)
    return slope, r2, fitted[-1]


def _support_resistance(rows: Sequence[Bar], window: int = 48) -> dict[str, float | bool] | None:
    if len(rows) < window + 2:
        return None
    prior = list(rows[-window - 1 : -1])
    support = min(float(bar.low if bar.low is not None else bar.close) for bar in prior)
    resistance = max(float(bar.high if bar.high is not None else bar.close) for bar in prior)
    latest = rows[-1]
    previous = rows[-2]
    close = float(latest.close)
    open_ = float(latest.open if latest.open is not None else latest.close)
    high = float(latest.high if latest.high is not None else latest.close)
    low = float(latest.low if latest.low is not None else latest.close)
    prev_close = float(previous.close)
    atr = float(_atr(rows, 14) or 0.0)

    breakout_up = close > resistance
    breakout_down = close < support
    support_bounce = bool(
        atr > 0
        and low <= support + 0.20 * atr
        and close > open_
        and close > prev_close
    )
    resistance_rejection = bool(
        atr > 0
        and high >= resistance - 0.20 * atr
        and close < open_
        and close < prev_close
    )

    if breakout_up:
        score = 1.0 if atr <= 0 else _clamp((close - resistance) / (0.50 * atr), 0.0, 1.0)
    elif breakout_down:
        score = -1.0 if atr <= 0 else -_clamp((support - close) / (0.50 * atr), 0.0, 1.0)
    elif support_bounce and not resistance_rejection:
        score = 0.55
    elif resistance_rejection and not support_bounce:
        score = -0.55
    else:
        score = 0.0

    distance_support_atr = None if atr <= 0 else (close - support) / atr
    distance_resistance_atr = None if atr <= 0 else (resistance - close) / atr
    return {
        "support": support,
        "resistance": resistance,
        "breakout_up": breakout_up,
        "breakout_down": breakout_down,
        "support_bounce": support_bounce,
        "resistance_rejection": resistance_rejection,
        "distance_support_atr": distance_support_atr,
        "distance_resistance_atr": distance_resistance_atr,
        "score": float(score),
    }


def technical_snapshot(rows: Sequence[Bar]) -> dict[str, Any]:
    if len(rows) < 60:
        raise ValueError("technical arm requires at least 60 EUR/USD 30-minute bars")

    atr14 = _atr(rows, 14)
    sma20 = _sma(rows, 20)
    sma50 = _sma(rows, 50)
    ema20 = _ema(rows, 20)
    rsi14 = _rsi(rows, 14)
    trendline = _trendline(rows, 24)
    sr = _support_resistance(rows, 48)
    if None in {atr14, sma20, sma50, ema20, rsi14} or trendline is None or sr is None:
        raise ValueError("insufficient EUR/USD bars for technical feature computation")

    close = float(rows[-1].close)
    atr_value = float(atr14)
    if atr_value <= 0:
        raise ValueError("ATR must be positive")

    ma_gap_atr = (float(sma20) - float(sma50)) / atr_value
    price_vs_fast_atr = (close - float(sma20)) / atr_value
    ma_score = _clamp(
        0.65 * _clamp(ma_gap_atr / 1.20)
        + 0.35 * _clamp(price_vs_fast_atr / 1.20)
    )

    rsi_value = float(rsi14)
    rsi_score = _clamp((rsi_value - 50.0) / 20.0)

    slope, r2, trendline_last = trendline
    slope_atr_per_bar = float(slope) / atr_value
    trendline_score = _clamp(slope_atr_per_bar / 0.08) * math.sqrt(max(0.0, float(r2)))

    r6 = _return(rows, 6)
    r13 = _return(rows, 13)
    r26 = _return(rows, 26)
    if None in {r6, r13, r26}:
        raise ValueError("insufficient EUR/USD bars for momentum feature")
    momentum_score = _clamp(
        0.35 * _clamp(float(r6) / 0.0030)
        + 0.40 * _clamp(float(r13) / 0.0065)
        + 0.25 * _clamp(float(r26) / 0.0120)
    )

    components = {
        "ma_structure": float(ma_score),
        "rsi_momentum": float(rsi_score),
        "trendline": float(trendline_score),
        "support_resistance": float(sr["score"]),
        "price_momentum": float(momentum_score),
    }
    composite = _clamp(sum(TECHNICAL_WEIGHTS[key] * components[key] for key in TECHNICAL_WEIGHTS))
    score = round(50.0 + 50.0 * composite, 2)
    direction = _direction_from_score(score)

    return {
        "signed_score": round(composite, 6),
        "score": score,
        "direction": direction,
        "confidence": _confidence_from_score(score),
        "components": {key: round(value, 6) for key, value in components.items()},
        "weights": dict(TECHNICAL_WEIGHTS),
        "indicators": {
            "close": round(close, 5),
            "sma20": round(float(sma20), 5),
            "sma50": round(float(sma50), 5),
            "ema20": round(float(ema20), 5),
            "rsi14": round(rsi_value, 4),
            "rsi_overbought": rsi_value >= 70.0,
            "rsi_oversold": rsi_value <= 30.0,
            "atr14": round(atr_value, 6),
            "trendline_window_bars": 24,
            "trendline_slope": round(float(slope), 8),
            "trendline_slope_atr_per_bar": round(slope_atr_per_bar, 6),
            "trendline_r2": round(float(r2), 6),
            "trendline_last": round(float(trendline_last), 5),
            "support_window_bars": 48,
            "support": round(float(sr["support"]), 5),
            "resistance": round(float(sr["resistance"]), 5),
            "breakout_up": bool(sr["breakout_up"]),
            "breakout_down": bool(sr["breakout_down"]),
            "support_bounce": bool(sr["support_bounce"]),
            "resistance_rejection": bool(sr["resistance_rejection"]),
            "distance_support_atr": None if sr["distance_support_atr"] is None else round(float(sr["distance_support_atr"]), 4),
            "distance_resistance_atr": None if sr["distance_resistance_atr"] is None else round(float(sr["distance_resistance_atr"]), 4),
        },
    }


def _direction_from_score(score: float) -> str:
    if float(score) >= LONG_THRESHOLD:
        return "LONG"
    if float(score) <= SHORT_THRESHOLD:
        return "SHORT"
    return "FLAT"


def _confidence_from_score(score: float) -> float:
    if _direction_from_score(score) == "FLAT":
        return 0.0
    return round(min(0.90, abs(float(score) - 50.0) / 50.0), 3)


def _belief_rows(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = payload.get("beliefs")
    if not isinstance(rows, list):
        return {}
    return {
        str(row.get("belief_id")): row
        for row in rows
        if isinstance(row, Mapping) and row.get("belief_id")
    }


def belief_snapshot(
    payload: Mapping[str, Any] | None,
    observed_at: datetime,
) -> dict[str, Any]:
    if not payload:
        return {
            "available": False,
            "reason": "belief_state_unavailable",
            "signed_score": None,
            "score": None,
            "direction": "UNAVAILABLE",
            "confidence": None,
            "beliefs": {},
        }
    rows = _belief_rows(payload)
    missing = [belief_id for belief_id in BELIEF_WEIGHTS if belief_id not in rows]
    if missing:
        return {
            "available": False,
            "reason": "missing_required_beliefs",
            "missing_belief_ids": missing,
            "signed_score": None,
            "score": None,
            "direction": "UNAVAILABLE",
            "confidence": None,
            "beliefs": {},
        }

    states: dict[str, Any] = {}
    for belief_id in BELIEF_WEIGHTS:
        row = rows[belief_id]
        try:
            probability = float(row["probability"])
            confidence = float(row["confidence"])
            last_updated = _parse_time(str(row["last_updated"]))
        except (KeyError, TypeError, ValueError):
            return {
                "available": False,
                "reason": "invalid_belief_state",
                "belief_id": belief_id,
                "signed_score": None,
                "score": None,
                "direction": "UNAVAILABLE",
                "confidence": None,
                "beliefs": {},
            }
        if last_updated > observed_at:
            return {
                "available": False,
                "reason": "future_belief_state_rejected",
                "belief_id": belief_id,
                "last_updated": _iso_z(last_updated),
                "market_observed_at": _iso_z(observed_at),
                "signed_score": None,
                "score": None,
                "direction": "UNAVAILABLE",
                "confidence": None,
                "beliefs": {},
            }
        signal = _clamp((2.0 * probability - 1.0) * confidence)
        states[belief_id] = {
            "probability": round(probability, 6),
            "confidence": round(confidence, 6),
            "last_updated": _iso_z(last_updated),
            "age_minutes": round((observed_at - last_updated).total_seconds() / 60.0, 2),
            "signed_support": round(signal, 6),
        }

    signed = _clamp(sum(BELIEF_WEIGHTS[belief_id] * states[belief_id]["signed_support"] for belief_id in BELIEF_WEIGHTS))
    score = round(50.0 + 50.0 * signed, 2)
    return {
        "available": True,
        "reason": None,
        "signed_score": round(signed, 6),
        "score": score,
        "direction": _direction_from_score(score),
        "confidence": _confidence_from_score(score),
        "beliefs": states,
        "weights": dict(BELIEF_WEIGHTS),
        "known_information_overlap_with_arm_a": ["eurusd.trend.bullish"],
    }


def _hybrid_context_signed(belief: Mapping[str, Any]) -> float | None:
    if not belief.get("available"):
        return None
    states = belief.get("beliefs") or {}
    weights = {belief_id: BELIEF_WEIGHTS[belief_id] for belief_id in HYBRID_CONTEXT_BELIEF_IDS}
    total = sum(weights.values()) or 1.0
    return _clamp(
        sum((weights[belief_id] / total) * float(states[belief_id]["signed_support"]) for belief_id in HYBRID_CONTEXT_BELIEF_IDS)
    )


def build_arms(rows: Sequence[Bar], belief_payload: Mapping[str, Any] | None) -> dict[str, Any]:
    observed_at = rows[-1].timestamp.astimezone(timezone.utc)
    technical = technical_snapshot(rows)
    belief = belief_snapshot(belief_payload, observed_at)

    arm_a = {
        "arm_id": "A",
        "label": "TECHNICAL_ONLY",
        "available": True,
        "direction": technical["direction"],
        "score": technical["score"],
        "confidence": technical["confidence"],
        "decision_influence": False,
        "technical": technical,
    }

    arm_b = {
        "arm_id": "B",
        "label": "BELIEF_ONLY",
        "available": bool(belief.get("available")),
        "direction": belief["direction"],
        "score": belief["score"],
        "confidence": belief["confidence"],
        "decision_influence": False,
        "belief": belief,
    }

    context_signed = _hybrid_context_signed(belief)
    if context_signed is None:
        arm_c = {
            "arm_id": "C",
            "label": "HYBRID",
            "available": False,
            "direction": "UNAVAILABLE",
            "score": None,
            "confidence": None,
            "decision_influence": False,
            "reason": "belief_context_unavailable",
            "overlap_control": {
                "excluded_from_hybrid_context": ["eurusd.trend.bullish"],
                "rationale": "direct EUR/USD trend is already represented by Arm A technical features",
            },
        }
    else:
        technical_signed = float(technical["signed_score"])
        hybrid_signed = _clamp(
            HYBRID_TECHNICAL_WEIGHT * technical_signed
            + HYBRID_BELIEF_CONTEXT_WEIGHT * float(context_signed)
        )
        hybrid_score = round(50.0 + 50.0 * hybrid_signed, 2)
        strong_conflict = (
            technical_signed * float(context_signed) < 0
            and abs(technical_signed) >= 0.35
            and abs(float(context_signed)) >= 0.35
        )
        direction = "FLAT" if strong_conflict else _direction_from_score(hybrid_score)
        confidence = 0.0 if direction == "FLAT" else _confidence_from_score(hybrid_score)
        arm_c = {
            "arm_id": "C",
            "label": "HYBRID",
            "available": True,
            "direction": direction,
            "score": hybrid_score,
            "confidence": confidence,
            "decision_influence": False,
            "hybrid": {
                "signed_score": round(hybrid_signed, 6),
                "technical_signed_score": round(technical_signed, 6),
                "belief_context_signed_score": round(float(context_signed), 6),
                "technical_weight": HYBRID_TECHNICAL_WEIGHT,
                "belief_context_weight": HYBRID_BELIEF_CONTEXT_WEIGHT,
                "strong_conflict_filter_triggered": strong_conflict,
            },
            "technical": technical,
            "belief_context": {
                belief_id: belief["beliefs"][belief_id]
                for belief_id in HYBRID_CONTEXT_BELIEF_IDS
            },
            "overlap_control": {
                "excluded_from_hybrid_context": ["eurusd.trend.bullish"],
                "rationale": "direct EUR/USD trend is already represented by Arm A technical features",
                "remaining_context": list(HYBRID_CONTEXT_BELIEF_IDS),
            },
        }
    return {"A": arm_a, "B": arm_b, "C": arm_c}


def _decision_fingerprint(capture: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "capture_id": capture["capture_id"],
        "engine_version": capture["engine_version"],
        "market_observed_at": capture["market_observed_at"],
        "reference_price": capture["reference_price"],
        "arms": capture["arms"],
        "horizons": {
            key: {
                "minutes": value["minutes"],
                "target_at": value["target_at"],
            }
            for key, value in capture["horizons"].items()
        },
        "research_boundary": capture["research_boundary"],
    }


def build_capture(
    rows: Sequence[Bar],
    belief_payload: Mapping[str, Any] | None,
    *,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    if not rows:
        raise ValueError("EUR/USD rows are required")
    observed_at = rows[-1].timestamp.astimezone(timezone.utc)
    captured = (captured_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if captured < observed_at:
        captured = observed_at
    reference = round(float(rows[-1].close), 5)
    capture_id = "eurusd-abc-" + _canonical_sha(
        {"engine_version": ENGINE_VERSION, "market_observed_at": _iso_z(observed_at), "reference_price": reference}
    )[:24]
    horizons = {
        f"{minutes}m": {
            "minutes": minutes,
            "target_at": _iso_z(observed_at + timedelta(minutes=minutes)),
            "outcome": None,
        }
        for minutes in HORIZONS_MINUTES
    }
    capture = {
        "capture_id": capture_id,
        "engine_version": ENGINE_VERSION,
        "mode": MODE,
        "captured_at": _iso_z(captured),
        "market_observed_at": _iso_z(observed_at),
        "reference_price": reference,
        "arms": build_arms(rows, belief_payload),
        "horizons": horizons,
        "research_boundary": {
            "prospective_only": True,
            "historical_backfill": False,
            "decision_influence": False,
            "trade_execution": False,
            "belief_writeback": False,
            "automatic_tuning": False,
            "pnl_tuned_weights": False,
            "executable_bid_ask_available": False,
            "cost_adjusted_performance_enabled": False,
        },
    }
    capture["decision_sha256"] = _canonical_sha(_decision_fingerprint(capture))
    return capture


def empty_state(now: datetime | None = None) -> dict[str, Any]:
    activated = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "schema_version": SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "mode": MODE,
        "activated_at": _iso_z(activated),
        "updated_at": _iso_z(activated),
        "captures": [],
    }


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_state()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION or not isinstance(payload.get("captures"), list):
        raise ValueError("invalid EUR/USD A/B/C experiment state")
    return payload


def _find_outcome_bar(rows: Sequence[Bar], target_at: datetime) -> Bar | None:
    eligible = [bar for bar in rows if bar.timestamp.astimezone(timezone.utc) >= target_at]
    return min(eligible, key=lambda bar: bar.timestamp) if eligible else None


def resolve_outcomes(state: Mapping[str, Any], rows: Sequence[Bar]) -> dict[str, Any]:
    payload = json.loads(json.dumps(state))
    for capture in payload.get("captures") or []:
        decision_sha = str(capture.get("decision_sha256") or "")
        if decision_sha != _canonical_sha(_decision_fingerprint(capture)):
            raise ValueError(f"decision fingerprint mismatch for {capture.get('capture_id')}")
        reference = float(capture["reference_price"])
        for horizon in (capture.get("horizons") or {}).values():
            if horizon.get("outcome") is not None:
                continue
            target_at = _parse_time(horizon["target_at"])
            bar = _find_outcome_bar(rows, target_at)
            if bar is None:
                continue
            exit_price = float(bar.close)
            raw_return = exit_price / reference - 1.0 if reference else 0.0
            arm_results: dict[str, Any] = {}
            for arm_id, arm in capture["arms"].items():
                direction = str(arm.get("direction") or "UNAVAILABLE")
                available = bool(arm.get("available"))
                if not available or direction == "UNAVAILABLE":
                    arm_results[arm_id] = {
                        "available": False,
                        "direction": direction,
                        "directional_correct": None,
                        "signed_return_bps": None,
                    }
                    continue
                if direction == "LONG":
                    signed = raw_return
                    correct = raw_return > 0
                elif direction == "SHORT":
                    signed = -raw_return
                    correct = raw_return < 0
                else:
                    signed = 0.0
                    correct = None
                arm_results[arm_id] = {
                    "available": True,
                    "direction": direction,
                    "directional_correct": correct,
                    "signed_return_bps": round(signed * 10000.0, 4),
                }
            horizon["outcome"] = {
                "resolved_at": _iso_z(bar.timestamp.astimezone(timezone.utc)),
                "price": round(exit_price, 5),
                "raw_return_bps": round(raw_return * 10000.0, 4),
                "source": "Yahoo Finance EURUSD=X 30m OHLC close",
                "cost_adjusted": False,
                "arms": arm_results,
            }
    if rows:
        payload["updated_at"] = _iso_z(rows[-1].timestamp.astimezone(timezone.utc))
    return payload


def append_capture(state: Mapping[str, Any], capture: Mapping[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(state))
    captures = payload.setdefault("captures", [])
    capture_id = str(capture["capture_id"])
    existing = next((row for row in captures if str(row.get("capture_id")) == capture_id), None)
    if existing is not None:
        if str(existing.get("decision_sha256")) != str(capture.get("decision_sha256")):
            raise ValueError("capture_id collision with different frozen decision")
        return payload
    captures.append(dict(capture))
    captures.sort(key=lambda row: str(row.get("market_observed_at") or ""))
    payload["updated_at"] = str(capture["captured_at"])
    return payload


def performance_summary(state: Mapping[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for arm_id in ("A", "B", "C"):
        by_horizon: dict[str, Any] = {}
        for minutes in HORIZONS_MINUTES:
            key = f"{minutes}m"
            rows: list[Mapping[str, Any]] = []
            total_captures = 0
            available_captures = 0
            for capture in state.get("captures") or []:
                outcome = ((capture.get("horizons") or {}).get(key) or {}).get("outcome")
                if outcome is None:
                    continue
                total_captures += 1
                result = (outcome.get("arms") or {}).get(arm_id)
                if not isinstance(result, Mapping) or not result.get("available"):
                    continue
                available_captures += 1
                rows.append(result)

            signals = [row for row in rows if row.get("direction") in {"LONG", "SHORT"}]
            correct = [row for row in signals if row.get("directional_correct") is True]
            signal_returns = [float(row["signed_return_bps"]) for row in signals if row.get("signed_return_bps") is not None]
            strategy_returns = [
                float(row["signed_return_bps"])
                for row in rows
                if row.get("signed_return_bps") is not None
            ]
            by_horizon[key] = {
                "matured_captures": total_captures,
                "available_captures": available_captures,
                "signals": len(signals),
                "decision_rate": None if not available_captures else round(len(signals) / available_captures, 6),
                "hit_rate": None if not signals else round(len(correct) / len(signals), 6),
                "mean_signed_return_bps_signal_only": None if not signal_returns else round(sum(signal_returns) / len(signal_returns), 4),
                "mean_strategy_return_bps_all_available": None if not strategy_returns else round(sum(strategy_returns) / len(strategy_returns), 4),
            }
        summary[arm_id] = by_horizon
    return summary


def build_report(state: Mapping[str, Any]) -> dict[str, Any]:
    captures = list(state.get("captures") or [])
    latest = captures[-1] if captures else None
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "mode": MODE,
        "decision_influence": False,
        "sample": {
            "captures": len(captures),
            "latest_market_observed_at": None if latest is None else latest["market_observed_at"],
        },
        "arms": {
            "A": {
                "label": "TECHNICAL_ONLY",
                "technical_indicators": [
                    "SMA20",
                    "SMA50",
                    "EMA20",
                    "RSI14",
                    "algorithmic_trendline",
                    "algorithmic_support_resistance",
                    "ATR14",
                    "price_momentum",
                ],
            },
            "B": {
                "label": "BELIEF_ONLY",
                "belief_ids": list(BELIEF_WEIGHTS),
            },
            "C": {
                "label": "HYBRID",
                "technical_weight": HYBRID_TECHNICAL_WEIGHT,
                "belief_context_weight": HYBRID_BELIEF_CONTEXT_WEIGHT,
                "overlap_control_excludes": ["eurusd.trend.bullish"],
                "belief_context_ids": list(HYBRID_CONTEXT_BELIEF_IDS),
            },
        },
        "performance": performance_summary(state),
        "latest_capture": latest,
        "governance": {
            "prospective_only": True,
            "historical_backfill": False,
            "pnl_tuned_weights": False,
            "automatic_tuning": False,
            "belief_writeback": False,
            "trade_execution": False,
            "active_daily_engine_influence": False,
            "promotion_status": "NOT_ELIGIBLE_FOR_PROMOTION_REVIEW",
            "cost_adjusted_performance_enabled": False,
            "note": "Yahoo OHLC does not provide executable EUR/USD bid/ask; no synthetic spread is fabricated.",
        },
    }


def _load_belief_state(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def run_cycle(
    state_dir: Path,
    *,
    belief_state_path: Path | None = None,
    client: YahooChartClient | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "EURUSD_DAILY_ABC_STATE.json"
    report_path = state_dir / "EURUSD_DAILY_ABC_REPORT.json"
    state = load_state(state_path)
    client = client or YahooChartClient(timeout=15)
    rows = client.bars(EURUSD, "10d", "30m")
    if len(rows) < 60:
        raise ValueError("EUR/USD market data unavailable or insufficient")
    rows = sorted(rows, key=lambda bar: bar.timestamp)
    state = resolve_outcomes(state, rows)

    belief_payload = _load_belief_state(belief_state_path)
    capture = build_capture(rows, belief_payload, captured_at=now)
    state = append_capture(state, capture)
    report = build_report(state)

    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return state, report


def validate_state(state: Mapping[str, Any]) -> None:
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unexpected experiment state schema")
    seen: set[str] = set()
    previous_time: datetime | None = None
    for capture in state.get("captures") or []:
        capture_id = str(capture.get("capture_id") or "")
        if not capture_id or capture_id in seen:
            raise ValueError("duplicate or missing capture_id")
        seen.add(capture_id)
        observed = _parse_time(capture["market_observed_at"])
        if previous_time is not None and observed < previous_time:
            raise ValueError("captures are not chronologically ordered")
        previous_time = observed
        if str(capture.get("decision_sha256")) != _canonical_sha(_decision_fingerprint(capture)):
            raise ValueError(f"decision fingerprint mismatch for {capture_id}")
        if capture.get("research_boundary", {}).get("decision_influence") is not False:
            raise ValueError("research capture must have zero decision influence")
        for horizon in (capture.get("horizons") or {}).values():
            if _parse_time(horizon["target_at"]) <= observed:
                raise ValueError("target_at must be prospective")
        arms = capture.get("arms") or {}
        if set(arms) != {"A", "B", "C"}:
            raise ValueError("capture must contain A/B/C arms")
        if "technical" not in arms["A"]:
            raise ValueError("Arm A must contain technical features")
        if arms["C"].get("available") and "technical" not in arms["C"]:
            raise ValueError("available Arm C must contain technical features")
        if arms["B"].get("available") and "technical" in arms["B"]:
            raise ValueError("Arm B must not contain Arm A technical feature payload")


def validate_files(state_dir: Path) -> None:
    state = json.loads((state_dir / "EURUSD_DAILY_ABC_STATE.json").read_text(encoding="utf-8"))
    report = json.loads((state_dir / "EURUSD_DAILY_ABC_REPORT.json").read_text(encoding="utf-8"))
    validate_state(state)
    if report.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise ValueError("unexpected experiment report schema")
    if report.get("decision_influence") is not False:
        raise ValueError("report must remain research shadow")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--belief-state")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    state_dir = Path(args.state_dir)
    if args.validate:
        validate_files(state_dir)
        state = json.loads((state_dir / "EURUSD_DAILY_ABC_STATE.json").read_text(encoding="utf-8"))
        print("EURUSD_ABC_EXPERIMENT_OK", len(state.get("captures") or []))
        return 0
    _, report = run_cycle(
        state_dir,
        belief_state_path=Path(args.belief_state) if args.belief_state else None,
    )
    latest = report.get("latest_capture") or {}
    arms = latest.get("arms") or {}
    print(
        "EURUSD_ABC_RESEARCH_SHADOW",
        latest.get("market_observed_at"),
        {arm_id: (arm.get("direction"), arm.get("score")) for arm_id, arm in arms.items()},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
