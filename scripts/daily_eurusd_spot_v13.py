#!/usr/bin/env python3
"""PR25 participation layer for active Daily EUR/USD.

Native Daily EUR/USD keeps first priority. When the native score is genuinely
FLAT, this layer computes the same multi-timeframe technical model used by
research Arm A directly from fresh EUR/USD H1/D1 data. A fresh LONG/SHORT A
signal is promoted as a fallback candidate using the active engine's current
30m entry and ATR risk geometry.

The research A/B/C state is NOT read by this module. This preserves the Research
Lab boundary: the active engine re-computes the technical method independently.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from belief_market_data_adapter import YahooChartClient
from daily_engine_contract import DailyEngineOutput
import daily_eurusd_spot as base
import daily_eurusd_spot_v12 as direct
import daily_eurusd_experiment_v12 as abc_a

ENGINE_VERSION = "eurusd-daily-spot-v1.3.0"
A_FALLBACK_MAX_MARKET_AGE_MINUTES = 90.0
A_H1_PERIOD = "1mo"
A_D1_PERIOD = "2y"

_original_build_output = base.build_output


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fetch_a_technical_signal(*, reference_price: float, observed_at: datetime) -> dict[str, Any]:
    """Recompute Arm A from fresh H1/D1 market data; no research state is consumed."""
    client = YahooChartClient(timeout=15)
    hourly = client.bars(base.EURUSD, A_H1_PERIOD, "1h")
    daily = client.bars(base.EURUSD, A_D1_PERIOD, "1d")
    return abc_a.technical_snapshot(
        hourly,
        daily,
        reference_price=reference_price,
        observed_at=observed_at,
    )


def _promote_a_fallback(
    native: DailyEngineOutput,
    snapshot: Any,
    technical: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> DailyEngineOutput:
    """Promote a fresh Arm-A LONG/SHORT only when native candidate is FLAT."""
    if native.direction != "FLAT":
        return native
    direction = str(technical.get("direction") or "FLAT").upper()
    if direction not in {"LONG", "SHORT"}:
        return native

    observed_at = _parse_iso(native.timestamp)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age_minutes = max(0.0, (current - observed_at).total_seconds() / 60.0)
    if current < observed_at or age_minutes > A_FALLBACK_MAX_MARKET_AGE_MINUTES:
        return native

    fx_rows = list(snapshot.bars.get(base.EURUSD) or [])
    if not fx_rows:
        return native
    atr = base._atr(fx_rows, 26)
    if atr is None or float(atr) <= 0:
        return native

    entry = float(fx_rows[-1].close)
    risk = max(float(atr) * 1.35, entry * 0.0027)
    target_multiple = 1.8
    stop = entry - risk if direction == "LONG" else entry + risk
    target = entry + risk * target_multiple if direction == "LONG" else entry - risk * target_multiple
    score = float(technical.get("score") or 50.0)
    confidence = float(technical.get("confidence") or 0.0)

    metadata = dict(native.metadata)
    native_candidate = dict((metadata.get("candidate") or {}))
    metadata["decision_source"] = "A_TECHNICAL_FALLBACK"
    metadata["learning_eligible"] = False
    metadata["native_candidate"] = native_candidate
    metadata["candidate"] = {
        "direction": direction,
        "score": round(score, 2),
        "confidence": round(confidence, 3),
        "accepted": True,
        "gate_reasons": [],
        "source": "A_TECHNICAL_FALLBACK",
        "native_was_flat": True,
        "market_age_minutes": round(age_minutes, 2),
    }
    # Do not attribute a fallback trade to the native trend/UUP/TLT component
    # learner. Empty components make the existing bounded learner skip weight
    # attribution while the trade still enters P&L/history statistics.
    metadata["components"] = {}
    metadata["a_fallback"] = {
        "method": "same_technical_model_as_research_arm_A_recomputed_live",
        "research_state_consumed": False,
        "direction": direction,
        "score": round(score, 2),
        "confidence": round(confidence, 3),
        "market_observed_at": native.timestamp,
        "max_market_age_minutes": A_FALLBACK_MAX_MARKET_AGE_MINUTES,
        "risk_source": "active_daily_eurusd_current_30m_atr",
    }

    def px(value: float) -> float:
        return round(float(value), 5)

    return DailyEngineOutput(
        instrument="EUR/USD",
        timestamp=native.timestamp,
        direction=direction,
        score=score,
        confidence=confidence,
        entry=px(entry),
        stop=px(stop),
        target=px(target),
        horizon="intraday_to_24h",
        engine_version=ENGINE_VERSION,
        status="SIGNAL",
        decision_mode="WITHOUT",
        metadata=metadata,
    ).validate()


def build_output(
    snapshot: Any,
    history: Mapping[str, Any] | None = None,
    *,
    allow_entry: bool = True,
) -> DailyEngineOutput:
    native = _original_build_output(snapshot, history, allow_entry=allow_entry)
    if not allow_entry or native.direction != "FLAT":
        return native

    observed_at = _parse_iso(native.timestamp)
    reference = float((snapshot.bars.get(base.EURUSD) or [])[-1].close)
    try:
        technical = fetch_a_technical_signal(reference_price=reference, observed_at=observed_at)
    except Exception as exc:
        # Fail closed: inability to compute A can never manufacture a trade.
        metadata = dict(native.metadata)
        metadata["a_fallback"] = {
            "available": False,
            "reason": "technical_fallback_unavailable",
            "error_type": type(exc).__name__,
            "research_state_consumed": False,
        }
        return DailyEngineOutput(
            instrument=native.instrument,
            timestamp=native.timestamp,
            direction=native.direction,
            score=native.score,
            confidence=native.confidence,
            entry=native.entry,
            stop=native.stop,
            target=native.target,
            horizon=native.horizon,
            engine_version=ENGINE_VERSION,
            status=native.status,
            decision_mode=native.decision_mode,
            metadata=metadata,
        ).validate()
    return _promote_a_fallback(native, snapshot, technical)


def _install() -> None:
    # v12 already installed direct admission (no daily limit/cooldown/vetoes).
    base.ENGINE_VERSION = ENGINE_VERSION
    base.build_output = build_output


_install()


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
