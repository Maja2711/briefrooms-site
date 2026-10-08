#!/usr/bin/env python3
"""EURUSD downside impulse -> upside correction research (NO trade authority).

As-of snapshots use only bars available at the observation timestamp. Historical
corrections are confirmed only on the close that reaches a fixed retracement
threshold; their troughs are retrospective, not early-warning timestamps.
No correction probability is estimated from *completed* swings alone because
unfinished/censored swings are excluded from that denominator.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Mapping, Sequence

PIP = 0.0001
SCHEMA = "eurusd-downswing-correction-research-v1"
MIN_IMPULSE_PIPS = 8.0
MIN_REBOUND_PIPS = 3.0
MIN_RETRACE_FRACTION = 0.25
MAX_GAP_MINUTES = 5
MIN_HISTORY_EVENTS_FOR_STATS = 20
MIN_BARS_TO_CLASSIFY_TREND = 60
MAX_ARCHIVED_EVENTS = 1200


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _utc_group(when: datetime) -> str:
    hour = when.astimezone(timezone.utc).hour
    if 6 <= hour < 12:
        return "UTC_06_12"
    if 12 <= hour < 20:
        return "UTC_12_20"
    return "UTC_20_06"


def _bucket(drop: float) -> str:
    if drop < 12:
        return "8_to_12_pips"
    if drop < 20:
        return "12_to_20_pips"
    if drop < 35:
        return "20_to_35_pips"
    return "35_plus_pips"


def _median_or_none(values: Sequence[float]) -> float | None:
    return round(median(values), 3) if values else None


def _quantile(sorted_values: Sequence[float], fraction: float) -> float | None:
    if not sorted_values:
        return None
    rank = (len(sorted_values) - 1) * fraction
    left = int(rank)
    right = min(left + 1, len(sorted_values) - 1)
    return round(sorted_values[left] + (sorted_values[right] - sorted_values[left]) * (rank - left), 3)


def _ema(values: Sequence[float], window: int) -> float:
    result = sum(values[:window]) / window
    alpha = 2 / (window + 1)
    for price in values[window:]:
        result += (price - result) * alpha
    return result


def classify_1m_trend(bars: Sequence[Mapping[str, Any]]) -> dict:
    """One-minute microstructure context, not the higher-timeframe Daily trend."""
    if len(bars) < MIN_BARS_TO_CLASSIFY_TREND:
        return {"status": "INSUFFICIENT_BARS", "local_downtrend": None}
    recent = list(bars)[-MIN_BARS_TO_CLASSIFY_TREND:]
    if any((recent[i]["time"] - recent[i - 1]["time"]).total_seconds() > 300 for i in range(1, len(recent))):
        return {"status": "DISCONTINUOUS_BARS", "local_downtrend": None}
    closes = [float(x["close"]) for x in recent]
    ema20 = _ema(closes, 20)
    ema60 = _ema(closes, 60)
    # Explicitly a local EMA relationship, not proof of macro/trend regime.
    return {
        "status": "OBSERVED_1M_ONLY",
        "local_downtrend": ema20 < ema60,
        "ema20_minus_ema60_pips": round((ema20 - ema60) / PIP, 3),
    }


def scan_swings(bars: Sequence[Mapping[str, Any]], cutoff: datetime) -> tuple[list[dict], dict]:
    """Online deterministic close-based downswing scanner.

    If observed later, prior completed events remain historical. A correction
    becomes knowable at confirmation_at; low_at is labelled retrospective.
    Session gaps reset the swing so overnight jumps are NOT intraday impulses.
    Input bars must be chronological, normalized and timestamped in UTC.
    """
    rows = sorted((r for r in bars if r["time"] <= cutoff), key=lambda r: r["time"])
    if not rows:
        return [], {"status": "NO_MARKET_DATA"}
    events: list[dict] = []
    peak = low = float(rows[0]["close"])
    peak_at = low_at = last_at = rows[0]["time"]
    segment_start_at = peak_at
    segment_bars = 1
    for r in rows[1:]:
        moment = r["time"]
        price = float(r["close"])
        if (moment - last_at).total_seconds() > MAX_GAP_MINUTES or moment <= last_at:
            peak = low = price
            peak_at = low_at = segment_start_at = moment
            segment_bars = 1
            last_at = moment
            continue
        last_at = moment
        segment_bars += 1
        if price > peak:
            peak = low = price
            peak_at = low_at = moment
            continue
        if price < low:
            low, low_at = price, moment
            continue
        drop = (peak - low) / PIP
        rebound = (price - low) / PIP
        if drop >= MIN_IMPULSE_PIPS and rebound >= max(MIN_REBOUND_PIPS, MIN_RETRACE_FRACTION * drop):
            age = round((low_at - peak_at).total_seconds() / 60, 3)
            wait = round((moment - low_at).total_seconds() / 60, 3)
            # If pivot was in initial warmup, the feed may omit the true high.
            # Preserve evidence but exclude from reliable statistics.
            truncated_start = (peak_at - segment_start_at).total_seconds() < 15 * 60
            events.append({
                "event_id": f"down-correction:{_iso(moment)}",
                "pivot_high_at": _iso(peak_at),
                "trough_at": _iso(low_at),
                "confirmed_at": _iso(moment),
                "known_at": _iso(moment),
                "pivot_high": round(peak, 8),
                "trough": round(low, 8),
                "confirmed_price": round(price, 8),
                "fall_pips": round(drop, 3),
                "fall_duration_minutes": age,
                "confirmation_delay_minutes": wait,
                "rebound_at_confirmation_pips": round(rebound, 3),
                "retracement_fraction_at_confirmation": round(rebound / drop, 4),
                "session_utc": _utc_group(low_at),
                "size_bucket": _bucket(drop),
                "source": "RETROSPECTIVE_CONFIRMED_CLOSE_BASED_OHLC",
                "valid_for_statistics": not truncated_start,
                "warning": "trough_at_known_only_after_correction_confirmation",
                "execution_authority": False,
            })
            # Confirmation terminates this event; new impulse starts from the
            # confirmed close, preventing one correction being counted many times.
            peak = low = price
            peak_at = low_at = moment
    drop = (peak - low) / PIP
    last = rows[-1]
    bounce = (float(last["close"]) - low) / PIP
    return events, {
        "status": "OBSERVED_AS_OF_BAR" if segment_bars >= 15 else "SESSION_WARMUP",
        "as_of": _iso(last["time"]),
        "pivot_high_at": _iso(peak_at),
        "last_trough_at": _iso(low_at),
        "decline_from_high_pips": round(max(0.0, drop), 3),
        "decline_duration_minutes": round((low_at - peak_at).total_seconds() / 60, 3),
        "minutes_since_low": round((last["time"] - low_at).total_seconds() / 60, 3),
        "bounce_from_low_pips": round(max(0.0, bounce), 3),
        "provisional_correction_threshold_pips": round(max(MIN_REBOUND_PIPS, MIN_RETRACE_FRACTION * drop), 3),
        "significant_impulse": drop >= MIN_IMPULSE_PIPS,
        "session_utc": _utc_group(last["time"]),
        "trend_1m": classify_1m_trend(rows),
        "point_in_time_only": True,
        "confirmation_is_not_a_predictive_signal": True,
    }


def summarize_events(events: Sequence[Mapping[str, Any]]) -> dict:
    """Descriptive frequencies ONLY. No prediction/confidence without censored swings."""
    values = [e for e in events if e.get("valid_for_statistics") is True]
    values.sort(key=lambda x: str(x.get("confirmed_at") or ""))
    def describe(rows: Sequence[Mapping[str, Any]]) -> dict:
        count = len(rows)
        drops = sorted(float(e["fall_pips"]) for e in rows)
        durations = sorted(float(e["fall_duration_minutes"]) for e in rows)
        delays = sorted(float(e["confirmation_delay_minutes"]) for e in rows)
        supported = count >= MIN_HISTORY_EVENTS_FOR_STATS
        return {
            "confirmed_event_count": count,
            "status": "DESCRIPTIVE_SAMPLE" if supported else "INSUFFICIENT_SAMPLE",
            "median_fall_pips": _median_or_none(drops) if supported else None,
            "fall_pips_p25": _quantile(drops, 0.25) if supported else None,
            "fall_pips_p75": _quantile(drops, 0.75) if supported else None,
            "median_fall_duration_minutes": _median_or_none(durations) if supported else None,
            "median_confirmation_delay_minutes": _median_or_none(delays) if supported else None,
        }
    return {
        "all": describe(values),
        "by_utc_session": {key: describe([x for x in values if x["session_utc"] == key])
                           for key in ("UTC_06_12", "UTC_12_20", "UTC_20_06")},
        "by_decline_bucket": {key: describe([x for x in values if x["size_bucket"] == key])
                              for key in ("8_to_12_pips", "12_to_20_pips", "20_to_35_pips", "35_plus_pips")},
        "note": "completed_corrections_only; no correction probability or tradeable timing claim",
        "minimum_events_for_descriptive_stats": MIN_HISTORY_EVENTS_FOR_STATS,
    }


def current_context(bars: Sequence[Mapping[str, Any]], cutoff: datetime,
                    historical_events: Sequence[Mapping[str, Any]] = ()) -> dict:
    """Intraday as-of scanner + empirically descriptive context, NEVER an exit."""
    relevant = [r for r in bars if r["time"] <= cutoff]
    events, state = scan_swings(relevant, cutoff)
    all_events = merge_events(historical_events, events)
    stats = summarize_events([e for e in all_events if e.get("confirmed_at") <= _iso(cutoff)])
    warning = {
        "downmove_at_least_10p": (state.get("decline_from_high_pips") or 0) >= 10,
        "downmove_at_least_20p": (state.get("decline_from_high_pips") or 0) >= 20,
        "no_new_low_for_5m": state.get("significant_impulse") is True
                            and (state.get("minutes_since_low") or 0) >= 5,
        "bounce_from_low_at_least_2p": state.get("significant_impulse") is True
                                      and (state.get("bounce_from_low_pips") or 0) >= 2,
    }
    return {
        "status": state.get("status"),
        "current_downswing": state,
        "research_warning_flags": warning,
        "confirmed_event_count_as_of": stats["all"]["confirmed_event_count"],
        "study_status": stats["all"]["status"],
        "historical_median_fall_pips": stats["all"]["median_fall_pips"],
        "historical_median_fall_duration_minutes": stats["all"]["median_fall_duration_minutes"],
        "no_predicted_correction_probability": True,
        "research_only": True,
    }


def merge_events(old: Sequence[Mapping[str, Any]], current: Sequence[Mapping[str, Any]]) -> list[dict]:
    """Historical learning archive keyed by first confirmation, capped and stable."""
    events: dict[str, dict] = {
        str(e["event_id"]): dict(e) for e in old
        if isinstance(e, Mapping) and e.get("event_id") and e.get("confirmed_at")
    }
    for event in current:
        events.setdefault(str(event["event_id"]), dict(event))
    return sorted(events.values(), key=lambda e: e["confirmed_at"])[-MAX_ARCHIVED_EVENTS:]


def journal_study(old: Mapping[str, Any] | None, fx: Sequence[Mapping[str, Any]],
                  cutoff: datetime) -> dict:
    """Store only completed research episodes; never fabricate a live alert."""
    prior = list((old or {}).get("events") or [])
    recent, _ = scan_swings(fx, cutoff)
    events = merge_events(prior, recent)
    return {
        "schema_version": SCHEMA,
        "research_only": True, "exit_authority": False,
        "source": "YAHOO_EURUSD_1M_RETROSPECTIVE_OHLC",
        "minimum_impulse_pips": MIN_IMPULSE_PIPS,
        "correction_confirmation_rule": "close_rebound_at_least_max_3pips_25pct_of_downswing",
        "confirmed_events": len(events),
        "events": events,
        "summary": summarize_events(events),
        "no_trade_probability_claim": True,
    }
