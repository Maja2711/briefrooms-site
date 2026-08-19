#!/usr/bin/env python3
"""Shared deterministic quant/learning core for BriefRooms Daily Stock engines.

Market adapters own calendars, currencies, market-data providers and official
catalyst channels.  This module owns the common mechanics that should stay
identical across GPW and US Daily Stock: momentum, liquidity, risk, reward/risk,
cross-sectional context, composite scoring and bounded historical learning.

The GPW adapter intentionally preserves the existing GPW learning/event layers
and passes their historical score into this core.  The US adapter can use the
same bounded learning primitive against US-only history without mixing markets.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Iterable


HistoryScorer = Callable[[list[dict[str, Any]], str, int], tuple[float, int]]


@dataclass(frozen=True)
class QuantProfile:
    market: str
    currency: str
    turnover_config_key: str
    turnover_output_key: str
    turnover_floor: float
    liquidity_base: float
    liquidity_turnover_scale: float
    liquidity_volume_scale: float
    risk_atr_multiple: float
    risk_floor_percent: float
    ideal_atr_percent: float
    entry_low_multiple: float
    entry_high_multiple: float
    reward_risk: float = 1.8


GPW_PROFILE = QuantProfile(
    market="GPW",
    currency="PLN",
    turnover_config_key="minimum_median_turnover_pln",
    turnover_output_key="median_turnover_pln",
    turnover_floor=1_000_000.0,
    liquidity_base=38.0,
    liquidity_turnover_scale=32.0,
    liquidity_volume_scale=16.0,
    risk_atr_multiple=1.10,
    risk_floor_percent=0.012,
    ideal_atr_percent=0.025,
    entry_low_multiple=0.995,
    entry_high_multiple=1.015,
)


US_PROFILE = QuantProfile(
    market="US",
    currency="USD",
    turnover_config_key="minimum_median_turnover_usd",
    turnover_output_key="median_turnover_usd",
    turnover_floor=25_000_000.0,
    liquidity_base=42.0,
    liquidity_turnover_scale=25.0,
    liquidity_volume_scale=14.0,
    risk_atr_multiple=1.05,
    risk_floor_percent=0.011,
    ideal_atr_percent=0.023,
    entry_low_multiple=0.995,
    entry_high_multiple=1.012,
)


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def round2(value: float) -> float:
    return round(float(value) + 1e-10, 2)


def true_range(bars: list[Any], window: int = 14) -> float:
    values: list[float] = []
    for previous, current in zip(bars[-window - 1 : -1], bars[-window:]):
        values.append(
            max(
                float(current.high) - float(current.low),
                abs(float(current.high) - float(previous.close)),
                abs(float(current.low) - float(previous.close)),
            )
        )
    return statistics.fmean(values) if values else 0.0


def return_over(bars: list[Any], sessions: int) -> float:
    if len(bars) <= sessions or float(bars[-sessions - 1].close or 0) <= 0:
        return 0.0
    return float(bars[-1].close) / float(bars[-sessions - 1].close) - 1.0


def percentile_score(value: float, lower: float, upper: float) -> float:
    if upper <= lower:
        return 50.0
    return clamp((float(value) - lower) * 100.0 / (upper - lower))


def _mean_r(rows: Iterable[dict[str, Any]]) -> float | None:
    values = [float((row.get("outcome") or {}).get("r_multiple", 0.0)) for row in rows]
    return statistics.fmean(values) if values else None


def _shrink(value: float | None, sample: int, prior_strength: float) -> float:
    if value is None or sample <= 0:
        return 0.0
    return float(value) * sample / (sample + max(float(prior_strength), 0.0))


def resolved_activated(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        row
        for row in history
        if (row.get("outcome") or {}).get("status") == "RESOLVED"
        and (row.get("outcome") or {}).get("activated") is True
    ]
    return sorted(rows, key=lambda row: str(row.get("date") or ""))


def bayesian_history_expectancy_score(
    history: list[dict[str, Any]],
    sector: str,
    minimum_sample: int,
    *,
    recent_window: int = 20,
    prior_strength: float = 10.0,
    max_adjustment: float = 12.0,
) -> tuple[float, int]:
    """Bounded historical overlay used by the shared core.

    This deliberately mirrors the proven GPW control-loop logic: 45% global,
    35% recent and 20% sector expectancy, each shrunk toward zero.  The result
    can only adjust the 10% historical component and never mutates strategy
    weights after a single trade.
    """
    resolved = resolved_activated(history)
    sample = len(resolved)
    if sample < int(minimum_sample):
        return 50.0, sample

    recent = resolved[-max(1, int(recent_window)) :]
    sector_rows = [
        row for row in resolved if (row.get("selection") or {}).get("sector") == sector
    ]
    components: list[tuple[float, float]] = [
        (_shrink(_mean_r(resolved), len(resolved), prior_strength), 0.45),
        (_shrink(_mean_r(recent), len(recent), prior_strength), 0.35),
    ]
    if sector_rows:
        components.append(
            (_shrink(_mean_r(sector_rows), len(sector_rows), prior_strength), 0.20)
        )
    total = sum(weight for _, weight in components) or 1.0
    expected_r = sum(value * weight for value, weight in components) / total
    adjustment = max(-float(max_adjustment), min(float(max_adjustment), expected_r * 18.0))
    return round2(clamp(50.0 + adjustment)), sample


def history_score_from_config(
    history: list[dict[str, Any]], sector: str, config: dict[str, Any]
) -> tuple[float, int]:
    learning = config.get("learning") or {}
    return bayesian_history_expectancy_score(
        history,
        sector,
        int(learning.get("minimum_resolved_trades_for_adaptation", 8)),
        recent_window=int(learning.get("recent_window", 20)),
        prior_strength=float(learning.get("prior_strength", 10.0)),
        max_adjustment=float(learning.get("max_historical_score_adjustment", 12.0)),
    )


def build_quant_candidate(
    company: dict[str, str],
    bars: list[Any],
    expected_day: date,
    config: dict[str, Any],
    profile: QuantProfile,
    *,
    history: list[dict[str, Any]] | None = None,
    history_scorer: HistoryScorer | None = None,
) -> dict[str, Any] | None:
    completed = [bar for bar in (bars or []) if bar.day <= expected_day]
    if not completed or completed[-1].day != expected_day or len(completed) < 60:
        return None
    bars = completed
    close = float(bars[-1].close)
    turnover = statistics.median(float(bar.close) * int(bar.volume or 0) for bar in bars[-20:])
    if turnover < float(config[profile.turnover_config_key]):
        return None
    atr = true_range(bars)
    if close <= 0 or atr <= 0:
        return None

    atr_percent = atr / close
    ret_1 = return_over(bars, 1)
    ret_5 = return_over(bars, 5)
    ret_20 = return_over(bars, 20)
    volume_average = statistics.fmean(max(int(bar.volume or 0), 0) for bar in bars[-21:-1]) or 1.0
    volume_ratio = int(bars[-1].volume or 0) / volume_average
    ma20 = statistics.fmean(float(bar.close) for bar in bars[-20:])
    ma50 = statistics.fmean(float(bar.close) for bar in bars[-50:])

    momentum = clamp(
        50
        + ret_5 * 330
        + ret_20 * 125
        + (8 if close > ma20 else -8)
        + (6 if ma20 > ma50 else -6)
    )
    liquidity = clamp(
        profile.liquidity_base
        + math.log10(max(turnover, profile.turnover_floor) / profile.turnover_floor)
        * profile.liquidity_turnover_scale
        + math.log(max(volume_ratio, 0.25)) * profile.liquidity_volume_scale
    )
    risk = max(atr * profile.risk_atr_multiple, close * profile.risk_floor_percent)
    risk_percent = risk / close
    if risk_percent > float(config["maximum_risk_percent"]):
        return None
    risk_score = clamp(92 - abs(atr_percent - profile.ideal_atr_percent) * 1250)

    historical = 50.0
    historical_n = 0
    history_rows = history or []
    if history_scorer is not None:
        minimum = int((config.get("learning") or {}).get("minimum_resolved_trades_for_adaptation", 8))
        historical, historical_n = history_scorer(history_rows, company["sector"], minimum)
    elif history_rows:
        historical, historical_n = history_score_from_config(history_rows, company["sector"], config)

    reward_risk = float(profile.reward_risk)
    result = {
        **company,
        "last_session": bars[-1].day.isoformat(),
        "reference_price": round2(close),
        "entry_zone": [
            round2(close * profile.entry_low_multiple),
            round2(close * profile.entry_high_multiple),
        ],
        "stop": round2(close - risk),
        "target": round2(close + risk * reward_risk),
        "risk_percent": round(risk_percent, 4),
        "reward_risk": reward_risk,
        "returns": {"1d": round(ret_1, 5), "5d": round(ret_5, 5), "20d": round(ret_20, 5)},
        profile.turnover_output_key: round(turnover),
        "volume_ratio": round(volume_ratio, 3),
        "raw_momentum": momentum,
        "scores": {
            "relative_momentum": round2(momentum),
            "volume_liquidity": round2(liquidity),
            "market_context": 50.0,
            "risk_reward": round2(risk_score),
            "historical_expectancy": round2(historical),
        },
        "historical_sample": historical_n,
        "core_market": profile.market,
        "core_currency": profile.currency,
    }
    return result


def normalize_cross_section(candidates: list[dict[str, Any]]) -> None:
    if not candidates:
        return
    returns = [float(candidate["returns"]["5d"]) for candidate in candidates]
    lower, upper = min(returns), max(returns)
    median_return = statistics.median(returns)
    market_1d = statistics.median(float(candidate["returns"]["1d"]) for candidate in candidates)
    breadth = sum(float(candidate["returns"]["1d"]) > 0 for candidate in candidates) / len(candidates)
    sectors = {candidate["sector"] for candidate in candidates}
    sector_5d = {
        sector: statistics.median(
            float(candidate["returns"]["5d"])
            for candidate in candidates
            if candidate["sector"] == sector
        )
        for sector in sectors
    }
    for candidate in candidates:
        relative = float(candidate["returns"]["5d"]) - median_return
        cross = percentile_score(float(candidate["returns"]["5d"]), lower, upper)
        candidate["relative_5d"] = round(relative, 5)
        candidate["scores"]["relative_momentum"] = round2(
            0.55 * float(candidate["raw_momentum"]) + 0.45 * cross
        )
        candidate["scores"]["market_context"] = round2(
            clamp(
                50
                + (breadth - 0.5) * 36
                + market_1d * 180
                + median_return * 70
                + sector_5d[candidate["sector"]] * 85
            )
        )
        candidate["quant_pre_score"] = quant_pre_score(candidate["scores"])


def quant_pre_score(scores: dict[str, Any]) -> float:
    return round2(
        (
            float(scores["relative_momentum"]) * 20
            + float(scores["volume_liquidity"]) * 15
            + float(scores["market_context"]) * 15
            + float(scores["risk_reward"]) * 15
            + float(scores["historical_expectancy"]) * 10
        )
        / 75
    )


def composite_score(
    candidate: dict[str, Any], analysis: dict[str, Any], config: dict[str, Any]
) -> float:
    scores = {
        **candidate["scores"],
        "catalyst": float(analysis.get("catalyst_score") or 0.0),
    }
    return round2(
        sum(float(scores[key]) * float(config["weights"][key]) for key in config["weights"])
        / 100.0
    )


def methodology_contract(profile: QuantProfile, config: dict[str, Any]) -> dict[str, Any]:
    return {
        "core": "daily-stock-core-v1",
        "market": profile.market,
        "currency": profile.currency,
        "weights": dict(config.get("weights") or {}),
        "minimum_reward_risk": float(config.get("minimum_reward_risk", 1.5)),
        "maximum_risk_percent": float(config.get("maximum_risk_percent", 0.07)),
        "learning": {
            "method": (config.get("learning") or {}).get("method", "bayesian_shrinkage_historical_overlay_v1"),
            "weights_frozen": True,
            "market_memory_isolated": True,
        },
    }
