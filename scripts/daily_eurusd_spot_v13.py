#!/usr/bin/env python3
"""Legacy Arm-A compatibility layer for Daily EUR/USD.

Production direction authority is exclusively owned by the native Daily EUR/USD
Direction Engine. Arm A remains available for research/shadow analysis only and
can never turn a native FLAT into LONG/SHORT.
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
    """Record Arm-A shadow diagnostics without production direction authority."""
    metadata = dict(native.metadata)
    technical_direction = str(technical.get("direction") or "FLAT").upper()
    metadata["a_fallback"] = {
        "production_authority": False,
        "status": "RESEARCH_ONLY",
        "method": "same_technical_model_as_research_arm_A_recomputed_live",
        "research_state_consumed": False,
        "shadow_direction": technical_direction,
        "shadow_score": technical.get("score"),
        "shadow_confidence": technical.get("confidence"),
        "native_direction": native.direction,
        "note": "Native FLAT remains FLAT; Arm A cannot create a production trade.",
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


def build_output(
    snapshot: Any,
    history: Mapping[str, Any] | None = None,
    *,
    allow_entry: bool = True,
) -> DailyEngineOutput:
    native = _original_build_output(snapshot, history, allow_entry=allow_entry)
    metadata = dict(native.metadata)
    metadata["a_fallback"] = {
        "production_authority": False,
        "status": "DETACHED_FROM_PRODUCTION",
        "research_state_consumed": False,
        "native_direction": native.direction,
        "note": "Arm A is evaluated only in dedicated shadow workflows; production cannot promote it.",
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


def _install() -> None:
    # v12 already installed direct admission (no daily limit/cooldown/vetoes).
    base.ENGINE_VERSION = ENGINE_VERSION
    base.build_output = build_output


_install()


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
