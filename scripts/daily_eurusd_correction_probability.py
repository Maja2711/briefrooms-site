#!/usr/bin/env python3
"""EURUSD correction probabilities at fixed 5/15/30 minute horizons.

Market-only, event-denominator model. It NEVER reads trade results, today's
SHORT outcome, profit/loss, Daily decisions or Belief Core. All examples are
drawn from *eligible downside states*, including failures and censored paths.
A probability is research-only and remains explicitly uncalibrated until
independent prospective, date-blocked evidence reaches promotion gates.

No execution, no policy mutation, no automatic promotion.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from daily_eurusd_exit_intelligence_v2 import normalize_bars, at, iso, load, save

SCHEMA = "eurusd-correction-probability-shadow-v1"
HORIZONS_MINUTES = (5, 15, 30)
PIP = 0.0001
MIN_IMPULSE_PIPS = 8.0
OBSERVATION_SPACING_MINUTES = 30
MAX_GAP_SECONDS = 180
MAX_HISTORY = 2800
MIN_TRAINING = 40
# Conservative *release gates*, NOT tuned to 8 October's trade.
PROMOTION_TRAIN_DAYS = 15
PROMOTION_TRAIN_EPISODES = 300
PROMOTION_HOLDOUT_DAYS = 7
PROMOTION_HOLDOUT_EPISODES = 100
PROMOTION_MIN_CLASS_COUNT = 35
PROMOTION_BRIER_IMPROVEMENT = 0.01
PROMOTION_MAX_ECE = 0.10
INSTRUMENT = "EUR/USD"


def f(value: float) -> float:
    return round(float(value), 6)


def utc_day(dt: datetime | str) -> str:
    parsed = dt if isinstance(dt, datetime) else datetime.fromisoformat(str(dt).replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).date().isoformat()


def utc_session(dt: datetime) -> str:
    h = dt.hour
    return "EUROPE" if 6 <= h < 12 else "US_OVERLAP" if 12 <= h < 20 else "OFF_HOURS"


def ema(values: Sequence[float], window: int) -> float:
    result = sum(values[:window]) / window
    alpha = 2.0 / (window + 1.0)
    for v in values[window:]:
        result = alpha * v + (1 - alpha) * result
    return result


def valid_window(bars: Sequence[Mapping[str, Any]]) -> bool:
    if not bars:
        return False
    return all(
        0 < (bars[i]["time"] - bars[i-1]["time"]).total_seconds() <= MAX_GAP_SECONDS
        for i in range(1, len(bars))
    )


def candidate(bars: Sequence[Mapping[str, Any]], now: datetime) -> dict | None:
    """Only fully closed 1-minute bars, with a 60-bar as-of trend check.

    No scanner hindsight event or completed-correction label is an input.
    The impulse uses closes (rather than non-executable intrabar extremes).
    """
    closed = [b for b in bars if b["time"] + timedelta(minutes=1) <= now]
    if len(closed) < 90:
        return None
    recent = closed[-90:]
    if not valid_window(recent):
        return None
    closes = [float(b["close"]) for b in recent]
    e20, e60 = ema(closes[-60:], 20), ema(closes[-60:], 60)
    if e20 >= e60:
        return None
    peak_at = max(range(len(closes)), key=lambda i: closes[i])
    if peak_at >= len(closes)-4:
        return None
    low_index = min(range(peak_at, len(closes)), key=lambda i: closes[i])
    peak, low = closes[peak_at], closes[low_index]
    drop = (peak - low) / PIP
    last = closes[-1]
    bounce_pips = (last - low) / PIP
    if drop + 1e-6 < MIN_IMPULSE_PIPS or bounce_pips > max(1.0, 0.10 * drop) + 1e-6:
        return None
    minutes = (recent[low_index]["time"] - recent[peak_at]["time"]).total_seconds() / 60
    if not 3 <= minutes <= 85:
        return None
    when = recent[-1]["time"] + timedelta(minutes=1)
    threshold = max(3.0, 0.25 * drop)
    volatility = sum(abs(closes[i] - closes[i-1]) / PIP for i in range(1, len(closes))) / 89
    features = {
        "impulse_pips": f(drop),
        "impulse_minutes": f(minutes),
        "time_since_low_min": f((recent[-1]["time"] - recent[low_index]["time"]).total_seconds()/60),
        "bounce_from_low_pips": f(bounce_pips),
        "momentum_5m_pips": f((last - closes[-6])/PIP),
        "ema20_minus_ema60_pips": f((e20 - e60)/PIP),
        "realized_abs_change_1m_pips": f(volatility),
        "utc_session": utc_session(when),
    }
    return {
        "id": "eurusd-correction:" + iso(when),
        "observed_at": iso(when),
        "source_bar_at": iso(recent[-1]["time"]),
        "source_bar_closed_at": iso(when),
        "date_utc": utc_day(when),
        "direction": "DOWNSWING",
        "spot_mid": f(last),
        "target_rebound_pips": f(threshold),
        "features": features,
        "source": "YAHOO_EURUSD_1M_CLOSED_BAR",
        "signal_uses_future": False,
        "outcomes": {str(h): {"status": "PENDING"} for h in HORIZONS_MINUTES},
    }


def outcome(episode: Mapping[str, Any], bars: Sequence[Mapping[str, Any]],
            now: datetime, horizon: int) -> dict:
    """Fixed as-of target: mid-close gain >= max(3p, 25% observed impulse).

    All fully closed 1m observations in the horizon must be present; gaps are
    UNKNOWN, never counted as a negative. Close-only labels avoid OHLC ambiguity.
    """
    origin = datetime.fromisoformat(episode["observed_at"].replace("Z", "+00:00"))
    end = origin + timedelta(minutes=horizon)
    if now < end:
        return {"status": "PENDING"}
    path = [b for b in bars if origin <= b["time"] and b["time"] + timedelta(minutes=1) <= end]
    if not path:
        return {"status": "CENSORED_DATA_GAP"}
    # Require first minute and final minute of horizon, with max 1m slack.
    if abs((path[0]["time"] - origin).total_seconds()) > 60:
        return {"status": "CENSORED_DATA_GAP"}
    if abs((path[-1]["time"] + timedelta(minutes=1) - end).total_seconds()) > 60:
        return {"status": "CENSORED_DATA_GAP"}
    if not valid_window(path):
        return {"status": "CENSORED_DATA_GAP"}
    target_price = float(episode["spot_mid"]) + float(episode["target_rebound_pips"]) * PIP
    hit = next((b for b in path if float(b["close"]) + 1e-8 >= target_price), None)
    return {
        "status": "RESOLVED",
        "label": int(hit is not None),
        "known_at": iso(end),
        "rebound_at": iso(hit["time"] + timedelta(minutes=1)) if hit else None,
        "target_price_mid": f(target_price),
        "max_close_rebound_pips": f((max(float(x["close"]) for x in path) - float(episode["spot_mid"]))/PIP),
        "bar_count": len(path),
        "is_executable_quote": False,
    }


def collect_historical(bars: Sequence[Mapping[str, Any]], now: datetime) -> list[dict]:
    """Mechanical retrospective replay, not a record of real-time alerts.

    Exactly one fixed-clock 30-minute candidate per window, selected without
    inspecting its outcome. On day D, *only* earlier UTC dates may train p(D).
    """
    rows = [b for b in bars if b["time"] + timedelta(minutes=1) <= now]
    episodes = []
    for i in range(89, len(rows)):
        when = rows[i]["time"] + timedelta(minutes=1)
        if when.minute % OBSERVATION_SPACING_MINUTES != 0:
            continue
        row = candidate(rows[i-89:i+1], when)
        if row is None:
            continue
        row["cohort"] = "RETROSPECTIVE_UNTRADED_MARKET_REPLAY"
        row["outcomes"] = {str(h): outcome(row, rows, now, h) for h in HORIZONS_MINUTES}
        episodes.append(row)
    return episodes


def resolved_for(episodes: Sequence[Mapping[str, Any]], horizon: int,
                 before_day: str) -> list[dict]:
    return [
        dict(e) for e in episodes
        if e.get("date_utc", "9999") < before_day
        and (e.get("outcomes") or {}).get(str(horizon), {}).get("status") == "RESOLVED"
    ]


def probability(feat: Mapping[str, Any], train: Sequence[Mapping[str, Any]],
                horizon: int) -> dict:
    """Shrunk market-only empirical neighbor estimator.

    Priors are fitted on historical successes *and failures*, never only on
    confirmed correction episodes. All thresholds are fixed a priori.
    """
    eligible = [e for e in train if (e.get("outcomes") or {}).get(str(horizon), {}).get("status") == "RESOLVED"]
    n = len(eligible)
    if n < MIN_TRAINING:
        return {"status": "INSUFFICIENT_TRAINING_EVIDENCE", "n": n, "p": None}
    positives = sum(int(e["outcomes"][str(horizon)]["label"]) for e in eligible)
    baseline = (positives + 1) / (n + 2)
    weighted_pos = weighted_total = 0.0
    for row in eligible:
        r = row["features"]
        weight = 1.0
        weight *= math.exp(-abs(float(r["impulse_pips"]) - float(feat["impulse_pips"])) / 12)
        weight *= math.exp(-abs(float(r["impulse_minutes"]) - float(feat["impulse_minutes"])) / 40)
        weight *= math.exp(-abs(float(r["momentum_5m_pips"]) - float(feat["momentum_5m_pips"])) / 4)
        weight *= 1.0 if r["utc_session"] == feat["utc_session"] else 0.65
        weighted_total += weight
        weighted_pos += weight * int(row["outcomes"][str(horizon)]["label"])
    prior_strength = 20.0
    p = (weighted_pos + prior_strength * baseline) / (weighted_total + prior_strength)
    return {
        "status": "RESEARCH_UNCALIBRATED",
        "p": f(min(0.999, max(0.001, p))),
        "unconditional_rate": f(positives/n),
        "n": n, "positive": positives, "negative": n-positives,
        "effective_neighbors": f(weighted_total),
        "shrinkage_prior_weight": prior_strength,
        "training_days": len({e["date_utc"] for e in eligible}),
    }


def calibration(records: Sequence[Mapping[str, Any]]) -> dict:
    if not records:
        return {"status": "NO_OOS_FORECASTS", "n": 0}
    labels = [int(x["actual"]) for x in records]
    probs = [float(x["forecast"]) for x in records]
    base = [float(x["train_baseline"]) for x in records]
    n = len(labels)
    brier = sum((p-y)**2 for p,y in zip(probs,labels))/n
    null_brier = sum((p-y)**2 for p,y in zip(base,labels))/n
    ece = 0.0
    for k in range(5):
        inds = [i for i,p in enumerate(probs) if min(4,int(p*5)) == k]
        if inds:
            ece += len(inds)/n * abs(sum(probs[i] for i in inds)/len(inds) - sum(labels[i] for i in inds)/len(inds))
    return {
        "status": "BACKTEST_ONLY_NOT_LIVE_VALIDATED",
        "n": n, "days": len({r["date_utc"] for r in records}),
        "positive": sum(labels), "negative": n-sum(labels),
        "brier": f(brier), "null_brier": f(null_brier),
        "brier_improvement": f(null_brier-brier), "ece_5_bin": f(ece),
        "no_retroactive_trade_execution": True,
    }


def replay_validation(episodes: Sequence[Mapping[str, Any]], horizon: int) -> dict:
    """Expanding past-date walk-forward; current day's outcomes never train it."""
    resolved = sorted((e for e in episodes if e["outcomes"][str(horizon)]["status"] == "RESOLVED"),
                      key=lambda x: x["observed_at"])
    pred = []
    for row in resolved:
        train = [e for e in resolved if e["date_utc"] < row["date_utc"]]
        result = probability(row["features"], train, horizon)
        if result["p"] is None:
            continue
        pred.append({"date_utc": row["date_utc"], "forecast": result["p"],
                     "train_baseline": (result["positive"]+1)/(result["n"]+2),
                     "actual": row["outcomes"][str(horizon)]["label"]})
    return calibration(pred)


def step(previous: Mapping[str, Any], bars_raw: Sequence[Any], now: datetime) -> dict:
    """Research report. Today's trade result is not an input anywhere."""
    bars = normalize_bars(bars_raw, now)
    asof = now - timedelta(minutes=1)
    closed = [b for b in bars if b["time"] + timedelta(minutes=1) <= now]
    replay = collect_historical(closed, now)
    old_live = {e["id"]: dict(e) for e in (previous.get("prospective_episodes") or [])}
    # Verify that a prospective episode is *actually observed now*, not replayed.
    current = candidate(closed, now) if closed else None
    if current and current["observed_at"] >= iso(now - timedelta(minutes=4)):
        slot = current["observed_at"][:13] + (":00" if int(current["observed_at"][14:16]) < 30 else ":30")
        # Fixed 30-minute sampling independent of the path's outcome.
        if not any(e.get("observation_slot") == slot for e in old_live.values()):
            current["cohort"] = "PROSPECTIVE_OBSERVED"
            current["observation_slot"] = slot
            old_live[current["id"]] = current
    live = sorted(old_live.values(), key=lambda e:e["observed_at"])[-MAX_HISTORY:]
    for e in live:
        for h in HORIZONS_MINUTES:
            key = str(h)
            if e["outcomes"][key]["status"] != "PENDING":
                continue
            result = outcome(e, closed, now, h)
            if result["status"] != "PENDING":
                e["outcomes"][key] = result
    # Replayed rows have no prospective-alert provenance and are always
    # separate from live observations, never counted twice.
    history = replay
    past_live_ids = {e["observed_at"] for e in history}
    history += [e for e in live if e["observed_at"] not in past_live_ids]
    history.sort(key=lambda e:e["observed_at"])
    point = current if current and current["observed_at"] >= iso(now - timedelta(minutes=4)) else None
    horizons: dict[str,dict] = {}
    for h in HORIZONS_MINUTES:
        eligible = resolved_for(history, h, utc_day(now))
        estimate = probability(point["features"], eligible, h) if point else {
            "status": "NO_FRESH_ELIGIBLE_DOWNSWING", "n": len(eligible), "p": None
        }
        oos = replay_validation(replay, h)
        live_mature = [e for e in live if e["outcomes"][str(h)]["status"] == "RESOLVED"]
        # No automatic promotion. Future governance might require BOTH
        # sufficiently independent live OOS forecasts and past-day records.
        ready = (
            estimate.get("training_days", 0) >= PROMOTION_TRAIN_DAYS
            and estimate.get("n", 0) >= PROMOTION_TRAIN_EPISODES
            and oos.get("days", 0) >= PROMOTION_HOLDOUT_DAYS
            and oos.get("n", 0) >= PROMOTION_HOLDOUT_EPISODES
            and oos.get("positive", 0) >= PROMOTION_MIN_CLASS_COUNT
            and oos.get("negative", 0) >= PROMOTION_MIN_CLASS_COUNT
            and oos.get("brier_improvement", -1) >= PROMOTION_BRIER_IMPROVEMENT
            and oos.get("ece_5_bin", 1) <= PROMOTION_MAX_ECE
            and len({e["date_utc"] for e in live_mature}) >= PROMOTION_HOLDOUT_DAYS
        )
        horizons[str(h)] = {
            "forecast": estimate,
            "replay_walk_forward": oos,
            "live_resolved_episodes": len(live_mature),
            "research_release_gates_met": bool(ready),
            "production_authority": False,
        }
    state = {
        "schema_version": SCHEMA,
        "instrument": INSTRUMENT,
        "mode": "PROSPECTIVE_PLUS_MARKET_REPLAY_SHADOW",
        "authority": {
            "trade_execution": False, "daily_exit_mutation": False,
            "belief_core_mutation": False, "automatic_promotion": False,
            "direction_decision_influence": False,
            "same_day_training": False,
        },
        "definition": {
            "forecast_horizons_minutes": list(HORIZONS_MINUTES),
            "eligibility": "EMA20_below_EMA60;90_contiguous_1m_bars;8p_min_impulse;near_trough",
            "binary_target": "future_closed_1m_mid_above_observed_mid_by_max_3p_or_25pct_of_observed_downswing",
            "negative_class": "full_contiguous_horizon_without_target",
            "censored_class": "missing_future_1m_close_or_gap",
            "sampling": "max_one_eligible_asof_observation_per_30min_UTC_slot",
            "features_source": "only_closed_1m_EURUSD_prices",
            "no_trade_selection_or_PnL_inputs": True,
            "train_uses_strictly_previous_UTC_dates": True,
        },
        "latest_market_observation": {
            "at": point["observed_at"], "features": point["features"],
            "spot_mid": point["spot_mid"],
            "threshold_pips": point["target_rebound_pips"],
        } if point else None,
        "horizons": horizons,
        "replay_episode_count": len(replay),
        "prospective_episode_count": len(live),
        "prospective_episodes": live,
        "replay_coverage": {
            "first": replay[0]["observed_at"] if replay else None,
            "last": replay[-1]["observed_at"] if replay else None,
            "dates": len({e["date_utc"] for e in replay}),
        },
        "research_note": "NO calibrated probability or trading exit claim until prospective OOS, sufficient days and risk validation",
    }
    # Keep GitHub state idempotent on runs without new observation/maturity.
    comparable = dict(state)
    prior = dict(previous)
    prior.pop("updated_at", None)
    if comparable == prior:
        state["updated_at"] = previous.get("updated_at", iso(now))
    else:
        state["updated_at"] = iso(now)
    return state


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--state", default="data/investments/eurusd_correction_probability_shadow.json")
    args = p.parse_args()
    now = datetime.now(timezone.utc)
    from belief_market_data_adapter import YahooChartClient
    try:
        bars = YahooChartClient(timeout=12).bars("EURUSD=X", "5d", "1m")
    except Exception as exc:
        print("CORRECTION_PROBABILITY_FX_UNAVAILABLE", type(exc).__name__)
        return 2  # Fail closed: never erase previous research on API failure.
    if not bars:
        print("CORRECTION_PROBABILITY_NO_FX_BARS")
        return 2
    target = Path(args.state)
    state = step(load(target, {}), bars, now)
    save(target, state)
    print("EURUSD_CORRECTION_PROBABILITY_SHADOW", json.dumps({
        "replay": state["replay_episode_count"],
        "prospective": state["prospective_episode_count"],
        "status_5m": state["horizons"]["5"]["forecast"]["status"],
        "status_15m": state["horizons"]["15"]["forecast"]["status"],
        "status_30m": state["horizons"]["30"]["forecast"]["status"],
        "execution_authority": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
