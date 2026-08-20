#!/usr/bin/env python3
"""Daily EUR/USD Spot adapter for BriefRooms Daily Trading.

v1 is intentionally a deterministic shadow engine. It computes a market-state
candidate WITHOUT Belief influence and emits the shared DailyEngineOutput
contract. Existing WES/Belief EURUSD evidence may later be attached in a
controlled WITH/WITHOUT calibration; it does not influence this v1 decision.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from belief_market_data_adapter import Bar, MarketSnapshot, YahooChartClient
from daily_engine_contract import DailyEngineOutput

ENGINE_VERSION = "eurusd-daily-spot-v1.0.0"
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
        values.append(
            max(
                float(current.high) - float(current.low),
                abs(float(current.high) - float(previous.close)),
                abs(float(current.low) - float(previous.close)),
            )
        )
    return sum(values) / len(values) if values else None


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


def build_output(snapshot: MarketSnapshot) -> DailyEngineOutput:
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
    if None in {trend, usd, rates, atr}:
        raise ValueError("insufficient bars for EUR/USD scoring")

    composite = _clamp(0.55 * float(trend) + 0.25 * float(usd) + 0.20 * float(rates))
    score = round(50.0 + 50.0 * composite, 2)
    if score >= 60.0:
        direction = "LONG"
    elif score <= 40.0:
        direction = "SHORT"
    else:
        direction = "FLAT"

    confidence = 0.0 if direction == "FLAT" else round(min(0.90, abs(score - 50.0) / 50.0), 3)
    entry = float(fx_rows[-1].close)
    risk = max(float(atr) * 1.15, entry * 0.0025)
    reward_risk = 1.8

    stop = target = None
    if direction == "LONG":
        stop = entry - risk
        target = entry + risk * reward_risk
    elif direction == "SHORT":
        stop = entry + risk
        target = entry - risk * reward_risk

    round_px = lambda value: None if value is None else round(float(value), 5)
    observed = fx_rows[-1].timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    metadata = {
        "market": "FX_SPOT",
        "currency_pair": "EUR/USD",
        "rollout_stage": "shadow",
        "paper_trading_only": True,
        "decision_influence": False,
        "belief": {
            "mode": "WITHOUT",
            "decision_influence": False,
            "bridge_ready": True,
            "note": "Existing WES EURUSD belief coverage remains shadow-only and is not used in v1 scoring.",
        },
        "components": {
            "trend": round(float(trend), 4),
            "broad_usd_environment": round(float(usd), 4),
            "us_rates_pressure_proxy": round(float(rates), 4),
        },
        "weights": {"trend": 0.55, "broad_usd_environment": 0.25, "us_rates_pressure_proxy": 0.20},
        "thresholds": {"long": 60.0, "short": 40.0},
        "risk": {
            "atr_30m_window": 26,
            "atr_multiple": 1.15,
            "risk_floor_percent": 0.0025,
            "reward_risk": reward_risk,
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
        score=score,
        confidence=confidence,
        entry=round_px(entry),
        stop=round_px(stop),
        target=round_px(target),
        horizon="intraday_to_24h",
        engine_version=ENGINE_VERSION,
        status="SHADOW",
        decision_mode="WITHOUT",
        metadata=metadata,
    ).validate()


def fetch_snapshot() -> MarketSnapshot:
    client = YahooChartClient(timeout=15)
    bars = {symbol: client.bars(symbol, "10d", "30m") for symbol in SYMBOLS}
    return MarketSnapshot(bars)


def write_output(path: Path) -> DailyEngineOutput:
    output = build_output(fetch_snapshot())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/investments/eurusd_daily_spot.json")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    path = Path(args.output)
    if args.validate:
        payload = json.loads(path.read_text(encoding="utf-8"))
        required = {"schema_version","instrument","timestamp","direction","score","confidence","entry","stop","target","horizon","engine_version","status","decision_mode"}
        missing = sorted(required - set(payload))
        if missing:
            raise SystemExit(f"missing contract fields: {', '.join(missing)}")
        if payload["schema_version"] != "daily-engine-output-v1":
            raise SystemExit("unexpected schema_version")
        print("EURUSD_DAILY_CONTRACT_OK", payload["direction"], payload["score"], payload["status"])
        return 0

    output = write_output(path)
    print("EURUSD_DAILY_SPOT", output.direction, output.score, output.confidence, output.status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
