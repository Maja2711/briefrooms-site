#!/usr/bin/env python3
"""Daily EUR/USD Spot engine with monitored position lifecycle and outcome learning."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from belief_market_data_adapter import Bar, MarketSnapshot, YahooChartClient
from daily_engine_contract import DailyEngineOutput
from daily_eurusd_lifecycle import (
    BASE_WEIGHTS,
    append_trade,
    create_position,
    empty_history,
    entry_gate,
    evaluate_position,
    learning_state,
    load_history,
    position_from_output,
    save_history,
)

ENGINE_VERSION = "eurusd-daily-spot-v1.1.0"
EURUSD = "EURUSD=X"
UUP = "UUP"
TLT = "TLT"
SYMBOLS = (EURUSD, UUP, TLT)


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _return(rows: Sequence[Bar], bars: int) -> float | None:
    if len(rows) <= bars or rows[-1 - bars].close == 0:
        return None
    return rows[-1].close / rows[-1 - bars].close - 1.0


def _atr(rows: Sequence[Bar], window: int = 26) -> float | None:
    chunk = list(rows[-window - 1 :])
    if len(chunk) < 3:
        return None
    values: list[float] = []
    for previous, current in zip(chunk[:-1], chunk[1:]):
        if current.high is None or current.low is None:
            continue
        values.append(max(
            float(current.high) - float(current.low),
            abs(float(current.high) - float(previous.close)),
            abs(float(current.low) - float(previous.close)),
        ))
    return sum(values) / len(values) if values else None


def _ema(rows: Sequence[Bar], window: int = 20) -> float | None:
    if len(rows) < window:
        return None
    alpha = 2.0 / (window + 1.0)
    value = float(rows[-window].close)
    for bar in rows[-window + 1 :]:
        value = alpha * float(bar.close) + (1.0 - alpha) * value
    return value


def _trend_component(rows: Sequence[Bar]) -> float | None:
    r3 = _return(rows, 6)
    r1 = _return(rows, 13)
    r5 = _return(rows, 65)
    if None in {r3, r1, r5}:
        return None
    return _clamp(
        0.30 * _clamp(float(r3) / 0.0035)
        + 0.35 * _clamp(float(r1) / 0.0075)
        + 0.35 * _clamp(float(r5) / 0.018)
    )


def _usd_component(rows: Sequence[Bar]) -> float | None:
    r1 = _return(rows, 13)
    r5 = _return(rows, 65)
    if None in {r1, r5}:
        return None
    return _clamp(-(0.45 * _clamp(float(r1) / 0.012) + 0.55 * _clamp(float(r5) / 0.035)))


def _rates_component(rows: Sequence[Bar]) -> float | None:
    r1 = _return(rows, 13)
    r5 = _return(rows, 65)
    if None in {r1, r5}:
        return None
    return _clamp(0.45 * _clamp(float(r1) / 0.018) + 0.55 * _clamp(float(r5) / 0.045))


def _raw_state(snapshot: MarketSnapshot, weights: Mapping[str, float]) -> dict[str, Any]:
    missing = [symbol for symbol in SYMBOLS if symbol not in snapshot.bars or not snapshot.bars[symbol]]
    if missing:
        raise ValueError(f"missing required market series: {', '.join(missing)}")
    fx_rows = snapshot.bars[EURUSD]
    if len(fx_rows) < 70:
        raise ValueError("EUR/USD requires at least 70 30-minute bars")

    trend = _trend_component(fx_rows)
    usd = _usd_component(snapshot.bars[UUP])
    rates = _rates_component(snapshot.bars[TLT])
    atr = _atr(fx_rows)
    ema20 = _ema(fx_rows, 20)
    if None in {trend, usd, rates, atr, ema20}:
        raise ValueError("insufficient bars for EUR/USD scoring")

    components = {
        "trend": float(trend),
        "broad_usd_environment": float(usd),
        "us_rates_pressure_proxy": float(rates),
    }
    composite = _clamp(sum(float(weights[key]) * components[key] for key in BASE_WEIGHTS))
    score = round(50.0 + 50.0 * composite, 2)
    raw_direction = "LONG" if score >= 60.0 else "SHORT" if score <= 40.0 else "FLAT"
    confidence = 0.0 if raw_direction == "FLAT" else round(min(0.90, abs(score - 50.0) / 50.0), 3)
    entry = float(fx_rows[-1].close)
    atr_value = float(atr)
    stretch_atr = abs(entry - float(ema20)) / atr_value if atr_value else None
    latest = fx_rows[-1]
    latest_range = None
    if latest.high is not None and latest.low is not None and atr_value:
        latest_range = (float(latest.high) - float(latest.low)) / atr_value
    observed = fx_rows[-1].timestamp.astimezone(timezone.utc)
    return {
        "components": components,
        "score": score,
        "raw_direction": raw_direction,
        "confidence": confidence,
        "entry": entry,
        "atr": atr_value,
        "ema20": float(ema20),
        "stretch_atr": stretch_atr,
        "shock_ratio": latest_range,
        "observed_at": observed,
    }


def _previous_score(snapshot: MarketSnapshot, weights: Mapping[str, float]) -> float | None:
    truncated = {}
    for symbol in SYMBOLS:
        rows = list(snapshot.bars.get(symbol) or [])
        if len(rows) < 2:
            return None
        truncated[symbol] = rows[:-1]
    try:
        return float(_raw_state(MarketSnapshot(truncated), weights)["score"])
    except ValueError:
        return None


def build_output(
    snapshot: MarketSnapshot,
    history: Mapping[str, Any] | None = None,
    *,
    allow_entry: bool = True,
) -> DailyEngineOutput:
    history_payload = dict(history or empty_history())
    learning = learning_state(history_payload.get("trades") or [])
    weights = learning["adaptive_weights"]
    raw = _raw_state(snapshot, weights)
    previous_score = _previous_score(snapshot, weights)
    gate = entry_gate(
        direction=str(raw["raw_direction"]),
        score=float(raw["score"]),
        confidence=float(raw["confidence"]),
        history=history_payload,
        observed_at=raw["observed_at"],
        previous_score=previous_score,
        stretch_atr=raw["stretch_atr"],
        shock_ratio=raw["shock_ratio"],
    )
    if not allow_entry:
        gate = {**gate, "accepted": False, "reasons": [*gate["reasons"], "entry_disabled_this_cycle"]}

    direction = str(raw["raw_direction"]) if gate["accepted"] else "FLAT"
    entry = float(raw["entry"])
    risk = max(float(raw["atr"]) * 1.35, entry * 0.0027)
    reward_risk = 1.8
    stop = target = None
    if direction == "LONG":
        stop = entry - risk
        target = entry + risk * reward_risk
    elif direction == "SHORT":
        stop = entry + risk
        target = entry - risk * reward_risk

    round_px = lambda value: None if value is None else round(float(value), 5)
    observed = raw["observed_at"].isoformat().replace("+00:00", "Z")
    metadata = {
        "market": "FX_SPOT",
        "currency_pair": "EUR/USD",
        "rollout_stage": "shadow",
        "decision_influence": False,
        "belief": {
            "mode": "WITHOUT",
            "decision_influence": False,
            "bridge_ready": True,
            "note": "Belief Core remains outside the v1.1 decision path; lifecycle and learning are EUR/USD-local.",
        },
        "components": {key: round(float(value), 4) for key, value in raw["components"].items()},
        "weights": {key: round(float(value), 6) for key, value in weights.items()},
        "candidate": {
            "direction": raw["raw_direction"],
            "score": raw["score"],
            "confidence": raw["confidence"],
            "accepted": bool(gate["accepted"]),
            "gate_reasons": list(gate["reasons"]),
            "previous_score": gate["previous_score"],
            "stretch_atr": gate["stretch_atr"],
            "shock_ratio": gate["shock_ratio"],
        },
        "learning": learning,
        "risk": {
            "atr_30m_window": 26,
            "atr_multiple": 1.35,
            "risk_floor_percent": 0.0027,
            "reward_risk": reward_risk,
            "position_horizon_hours": 24,
            "monitor_interval": "1m",
        },
        "data": {
            "provider": "Yahoo Finance chart",
            "symbols": list(SYMBOLS),
            "executable_bid_ask_available": False,
            "rate_differential_claimed": False,
            "ecb_policy_coverage": False,
        },
    }
    return DailyEngineOutput(
        instrument="EUR/USD",
        timestamp=observed,
        direction=direction,
        score=float(raw["score"]),
        confidence=float(raw["confidence"]),
        entry=round_px(entry) if direction != "FLAT" else None,
        stop=round_px(stop),
        target=round_px(target),
        horizon="intraday_to_24h",
        engine_version=ENGINE_VERSION,
        status="SIGNAL" if direction != "FLAT" else "NO_TRADE",
        decision_mode="WITHOUT",
        metadata=metadata,
    ).validate()


def fetch_snapshot(client: YahooChartClient | None = None) -> MarketSnapshot:
    client = client or YahooChartClient(timeout=15)
    bars = {symbol: client.bars(symbol, "10d", "30m") for symbol in SYMBOLS}
    return MarketSnapshot(bars)


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _mark_percent(position: Mapping[str, Any], price: float) -> float:
    entry = float(position["entry"])
    if not entry:
        return 0.0
    if str(position["direction"]).upper() == "LONG":
        return (price / entry - 1.0) * 100.0
    return (entry / price - 1.0) * 100.0


def _open_output(candidate: DailyEngineOutput, position: Mapping[str, Any], mark_price: float) -> DailyEngineOutput:
    metadata = dict(candidate.metadata)
    metadata["position"] = {
        **dict(position),
        "mark_price": round(float(mark_price), 5),
        "unrealized_percent": round(_mark_percent(position, mark_price), 5),
    }
    metadata["candidate_at_refresh"] = metadata.get("candidate")
    return DailyEngineOutput(
        instrument="EUR/USD",
        timestamp=candidate.timestamp,
        direction=str(position["direction"]),
        score=float(position.get("entry_score") or candidate.score),
        confidence=float(position.get("entry_confidence") or candidate.confidence),
        entry=float(position["entry"]),
        stop=float(position["stop"]),
        target=float(position["target"]),
        horizon="intraday_to_24h",
        engine_version=str(position.get("engine_version") or ENGINE_VERSION),
        status="OPEN",
        decision_mode="WITHOUT",
        metadata=metadata,
    ).validate()


def _closed_output(candidate: DailyEngineOutput, trade: Mapping[str, Any], history: Mapping[str, Any]) -> DailyEngineOutput:
    metadata = dict(candidate.metadata)
    metadata["position"] = None
    metadata["last_trade"] = dict(trade)
    metadata["learning"] = learning_state(history.get("trades") or [])
    reason = str(trade.get("exit_reason") or "CLOSED")
    status = "CLOSED_SL" if reason == "STOP_LOSS" else "CLOSED_TP" if reason == "TAKE_PROFIT" else "CLOSED_TIME"
    return DailyEngineOutput(
        instrument="EUR/USD",
        timestamp=candidate.timestamp,
        direction="FLAT",
        score=candidate.score,
        confidence=candidate.confidence,
        entry=None,
        stop=None,
        target=None,
        horizon="intraday_to_24h",
        engine_version=ENGINE_VERSION,
        status=status,
        decision_mode="WITHOUT",
        metadata=metadata,
    ).validate()


def run_cycle(output_path: Path, history_path: Path, client: YahooChartClient | None = None) -> DailyEngineOutput:
    client = client or YahooChartClient(timeout=15)
    snapshot = fetch_snapshot(client)
    monitor_bars = client.bars(EURUSD, "5d", "1m")
    observed_at = monitor_bars[-1].timestamp.astimezone(timezone.utc)
    history = load_history(history_path)
    previous = _load_json(output_path)
    previous_metadata = previous.get("metadata") if isinstance(previous, dict) and isinstance(previous.get("metadata"), Mapping) else {}
    previous_trade = previous_metadata.get("last_trade") if isinstance(previous_metadata, Mapping) else None
    if isinstance(previous_trade, Mapping):
        history = append_trade(history, previous_trade)
    position = position_from_output(previous)

    if position:
        trade = evaluate_position(position, monitor_bars, observed_at)
        if trade:
            history = append_trade(history, trade)
            save_history(history_path, history, observed_at)
            candidate = build_output(snapshot, history, allow_entry=False)
            output = _closed_output(candidate, trade, history)
        else:
            candidate = build_output(snapshot, history, allow_entry=False)
            output = _open_output(candidate, position, monitor_bars[-1].close)
            save_history(history_path, history, observed_at)
    else:
        candidate = build_output(snapshot, history)
        if candidate.direction in {"LONG", "SHORT"}:
            position = create_position(candidate.to_dict())
            output = _open_output(candidate, position, monitor_bars[-1].close)
        else:
            output = candidate
        save_history(history_path, history, observed_at)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def validate_files(output_path: Path, history_path: Path) -> None:
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    required = {"schema_version","instrument","timestamp","direction","score","confidence","entry","stop","target","horizon","engine_version","status","decision_mode"}
    missing = sorted(required - set(payload))
    if missing:
        raise SystemExit(f"missing contract fields: {', '.join(missing)}")
    if payload["schema_version"] != "daily-engine-output-v1":
        raise SystemExit("unexpected schema_version")
    history = json.loads(history_path.read_text(encoding="utf-8"))
    if history.get("schema_version") != "eurusd-daily-history-v1" or not isinstance(history.get("trades"), list):
        raise SystemExit("invalid EURUSD history")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/investments/eurusd_daily_spot.json")
    parser.add_argument("--history", default="data/investments/eurusd_daily_history.json")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    output_path = Path(args.output)
    history_path = Path(args.history)
    if args.validate:
        validate_files(output_path, history_path)
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        history = json.loads(history_path.read_text(encoding="utf-8"))
        print("EURUSD_DAILY_LIFECYCLE_OK", payload["status"], len(history.get("trades") or []))
        return 0
    output = run_cycle(output_path, history_path)
    print("EURUSD_DAILY_SPOT", output.direction, output.score, output.confidence, output.status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
