#!/usr/bin/env python3
"""PR20 v1.2 configuration layer for the Daily EUR/USD A/B/C experiment.

This module upgrades the v1.1 technical contract without changing the research
boundary: Bollinger Bands use a 30-period middle band on H1 and D1 and expose
1σ/2σ/3σ dispersion levels. The underlying experiment remains prospective,
research-shadow only and non-executable.
"""
from __future__ import annotations

import statistics
from typing import Any, Sequence

from belief_market_data_adapter import Bar
import daily_eurusd_experiment as base

ENGINE_VERSION = "eurusd-daily-abc-v1.2.0"
BOLLINGER_WINDOW = 30
BOLLINGER_STDDEV_LEVELS = (1.0, 2.0, 3.0)
BOLLINGER_SCORE_REFERENCE_STDDEV = 2.0

# Freeze the upgraded engine version before any capture/state helper is called.
base.ENGINE_VERSION = ENGINE_VERSION


def _bollinger30(rows: Sequence[Bar], window: int = BOLLINGER_WINDOW) -> dict[str, Any]:
    if window != BOLLINGER_WINDOW:
        raise ValueError("PR20 v1.2 Bollinger window is frozen at 30")
    if len(rows) < window + 1:
        raise ValueError("Bollinger(30) requires at least 31 bars")

    closes = [float(bar.close) for bar in rows]
    recent = closes[-window:]
    middle = sum(recent) / window
    sigma = statistics.pstdev(recent)
    previous_middle = sum(closes[-window - 1 : -1]) / window
    close = closes[-1]
    atr = float(base._atr(rows, 14) or 0.0)

    z_score = 0.0 if sigma <= 1e-15 else (close - middle) / sigma
    location_score = base._clamp(z_score / BOLLINGER_SCORE_REFERENCE_STDDEV)
    middle_slope = 0.0 if atr <= 0 else base._clamp((middle - previous_middle) / (0.20 * atr))
    score = base._clamp(0.75 * location_score + 0.25 * middle_slope)

    bands: dict[str, float] = {}
    for level in BOLLINGER_STDDEV_LEVELS:
        key = str(int(level)) if float(level).is_integer() else str(level).replace(".", "_")
        bands[f"upper_{key}sigma"] = middle + level * sigma
        bands[f"lower_{key}sigma"] = middle - level * sigma

    upper_2 = bands["upper_2sigma"]
    lower_2 = bands["lower_2sigma"]
    bandwidth_2 = 0.0 if abs(middle) <= 1e-15 else (upper_2 - lower_2) / abs(middle)
    percent_b_2 = None if upper_2 - lower_2 <= 1e-15 else (close - lower_2) / (upper_2 - lower_2)

    return {
        "score": round(score, 6),
        "middle": round(middle, 5),
        "sigma": round(sigma, 8),
        "z_score": round(z_score, 6),
        "upper_1sigma": round(bands["upper_1sigma"], 5),
        "lower_1sigma": round(bands["lower_1sigma"], 5),
        "upper_2sigma": round(upper_2, 5),
        "lower_2sigma": round(lower_2, 5),
        "upper_3sigma": round(bands["upper_3sigma"], 5),
        "lower_3sigma": round(bands["lower_3sigma"], 5),
        # Backward-compatible aliases point to the conventional ±2σ envelope.
        "upper": round(upper_2, 5),
        "lower": round(lower_2, 5),
        "bandwidth": round(bandwidth_2, 8),
        "percent_b": None if percent_b_2 is None else round(percent_b_2, 6),
        "above_1sigma": close > bands["upper_1sigma"],
        "below_1sigma": close < bands["lower_1sigma"],
        "above_2sigma": close > upper_2,
        "below_2sigma": close < lower_2,
        "above_3sigma": close > bands["upper_3sigma"],
        "below_3sigma": close < bands["lower_3sigma"],
        "above_upper": close > upper_2,
        "below_lower": close < lower_2,
        "parameters": {
            "window": BOLLINGER_WINDOW,
            "stddev_levels": list(BOLLINGER_STDDEV_LEVELS),
            "score_reference_stddev": BOLLINGER_SCORE_REFERENCE_STDDEV,
            "dispersion": "population_standard_deviation",
        },
    }


# base.technical_snapshot resolves this global at call time, so A and C now use
# Bollinger(30) on both H1 and D1 without touching Belief-only Arm B.
base._bollinger = _bollinger30

_base_build_report = base.build_report
_base_validate_state = base.validate_state


def build_report(state):
    report = _base_build_report(state)
    indicators = report["arms"]["A"]["technical_indicators"]
    report["arms"]["A"]["technical_indicators"] = [
        "H1_Bollinger_30_sigma1_sigma2_sigma3" if item.startswith("H1_Bollinger_")
        else "D1_Bollinger_30_sigma1_sigma2_sigma3" if item.startswith("D1_Bollinger_")
        else item
        for item in indicators
    ]
    report["arms"]["A"]["bollinger_contract"] = {
        "window": BOLLINGER_WINDOW,
        "stddev_levels": list(BOLLINGER_STDDEV_LEVELS),
        "timeframes": ["H1", "D1"],
        "score_uses": "z_score_at_2sigma_reference_plus_middle_band_slope",
    }
    report["arms"]["C"]["bollinger_contract"] = dict(report["arms"]["A"]["bollinger_contract"])
    return report


def validate_state(state) -> None:
    _base_validate_state(state)
    for capture in state.get("captures") or []:
        indicators = capture["arms"]["A"]["technical"]["indicators"]
        for tf in ("H1", "D1"):
            boll = indicators[tf]["bollinger"]
            params = boll.get("parameters") or {}
            if params.get("window") != BOLLINGER_WINDOW:
                raise ValueError(f"{tf} Bollinger window must be 30")
            if params.get("stddev_levels") != list(BOLLINGER_STDDEV_LEVELS):
                raise ValueError(f"{tf} Bollinger must expose 1σ/2σ/3σ")
            required = {
                "sigma", "z_score",
                "upper_1sigma", "lower_1sigma",
                "upper_2sigma", "lower_2sigma",
                "upper_3sigma", "lower_3sigma",
            }
            if not required.issubset(boll):
                raise ValueError(f"{tf} Bollinger dispersion contract incomplete")


# Patch runtime/report validation paths used by base.run_cycle/base.validate_files.
base.build_report = build_report
base.validate_state = validate_state

# Re-export the stable public experiment API used by tests/consumers.
BELIEF_WEIGHTS = base.BELIEF_WEIGHTS
MA_WINDOWS = base.MA_WINDOWS
TECHNICAL_WEIGHTS = base.TECHNICAL_WEIGHTS
HYBRID_CONTEXT_BELIEF_IDS = base.HYBRID_CONTEXT_BELIEF_IDS
HYBRID_TECHNICAL_WEIGHT = base.HYBRID_TECHNICAL_WEIGHT
HYBRID_BELIEF_CONTEXT_WEIGHT = base.HYBRID_BELIEF_CONTEXT_WEIGHT
append_capture = base.append_capture
build_capture = base.build_capture
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
