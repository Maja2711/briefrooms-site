#!/usr/bin/env python3
"""Daily EUR/USD — Contextual Entry Policy Learning.

Prospective learning layer for timing/entry policy, not a new directional model.

At each eligible Daily decision point it freezes:
- observed market move / technical state,
- Belief Core EUR/USD probability curve when available,
- FSE regime / analogue telemetry,
- Event Intelligence context,
- the existing Daily direction/thesis.

It then evaluates, strictly prospectively on the later price path:
- CONTINUATION_NOW,
- CONTINUATION_PULLBACK (several ATR-depth variants),
- REVERSAL_NOW,
- FLAT.

The learner compares net R after the same fixed EPE synthetic spread used by
Daily. It never owns market direction or trade execution. Its prospective
evidence may, however, receive bounded autonomous authority over entry timing
(NOW / pullback / FLAT), with context-local promotion and automatic rollback.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from belief_market_data_adapter import Bar, YahooChartClient
import execution_price_engine as epe

SCHEMA_VERSION = "eurusd-contextual-entry-policy-learning-v2"
LEGACY_SCHEMA_VERSION = "eurusd-contextual-entry-policy-learning-v1"
PUBLIC_SCHEMA = "eurusd-contextual-entry-policy-public-v2"
EPISODE_SCHEMA = "eurusd-contextual-entry-policy-episode-v1"
HORIZON_HOURS = 24
PULLBACK_DEPTHS_ATR = (0.20, 0.35, 0.50)
MIN_RESOLVED_FOR_RECOMMENDATION = 1
MIN_EFFECTIVE_NEIGHBORS = 1.50
UNCERTAINTY_PENALTY = 1.00
MIN_POLICY_EDGE_R = 0.10
RECENCY_HALF_LIFE_DAYS = 45.0
AUTHORITY_LEVELS = (
    ("FULL", 0.80, 1.00),
    ("MEDIUM", 0.58, 0.60),
    ("LOW", 0.35, 0.25),
)
MAX_EPISODES = 600
FEATURE_KEYS = (
    "move_3h_atr",
    "move_12h_atr",
    "move_24h_atr",
    "ema20_distance_atr",
    "daily_score_signed",
    "daily_confidence",
    "p_up_current",
    "p_up_delta",
    "p_up_3h",
    "p_up_12h",
    "p_up_24h",
    "belief_confidence",
    "belief_macro_score",
    "fse_p_up_4h",
    "fse_risk_score",
    "fse_hurst_q2",
    "fse_analogue_median_return_atr",
    "event_score",
    "event_confidence",
)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_json(path: Path | None, default: Any = None) -> Any:
    if path is None:
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def clamp(value: float, low: float = -10.0, high: float = 10.0) -> float:
    return max(low, min(high, float(value)))


def pct_return(new: float, old: float) -> float:
    return float(new) / float(old) - 1.0 if old else 0.0


def atr(rows: Sequence[Bar], window: int = 26) -> float | None:
    usable = list(rows)[-max(window + 1, 2):]
    if len(usable) < 2:
        return None
    trs: list[float] = []
    previous = float(usable[0].close)
    for row in usable[1:]:
        high = float(row.high if row.high is not None else row.close)
        low = float(row.low if row.low is not None else row.close)
        trs.append(max(high - low, abs(high - previous), abs(low - previous)))
        previous = float(row.close)
    if not trs:
        return None
    return sum(trs[-window:]) / min(window, len(trs))


def ema(values: Sequence[float], window: int) -> float | None:
    if len(values) < window:
        return None
    alpha = 2.0 / (window + 1.0)
    out = sum(values[:window]) / window
    for value in values[window:]:
        out = alpha * float(value) + (1.0 - alpha) * out
    return out


def as_of(rows: Sequence[Bar], when: datetime) -> list[Bar]:
    return [row for row in rows if row.timestamp.astimezone(timezone.utc) <= when]


def price_at_or_before(rows: Sequence[Bar], when: datetime) -> float | None:
    eligible = as_of(rows, when)
    return float(eligible[-1].close) if eligible else None


def _lookback_price(rows: Sequence[Bar], when: datetime, hours: int) -> float | None:
    target = when - timedelta(hours=hours)
    eligible = [row for row in rows if row.timestamp.astimezone(timezone.utc) <= target]
    return float(eligible[-1].close) if eligible else None


def _belief_curve(payload: Mapping[str, Any] | None, when: datetime) -> dict[str, Any]:
    result = {
        "available": False,
        "current_p_up": None,
        "previous_p_up": None,
        "delta_p_up": None,
        "confidence": None,
        "horizons": {},
    }
    if not isinstance(payload, Mapping):
        return result

    beliefs = {
        str(row.get("belief_id")): row
        for row in (payload.get("beliefs") or [])
        if isinstance(row, Mapping)
    }
    trend = beliefs.get("eurusd.trend.bullish")
    if isinstance(trend, Mapping):
        p = trend.get("probability")
        prev = trend.get("previous_probability")
        if isinstance(p, (int, float)) and not isinstance(p, bool):
            result["current_p_up"] = float(p)
            result["available"] = True
        if isinstance(prev, (int, float)) and not isinstance(prev, bool):
            result["previous_p_up"] = float(prev)
        if result["current_p_up"] is not None and result["previous_p_up"] is not None:
            result["delta_p_up"] = float(result["current_p_up"]) - float(result["previous_p_up"])
        conf = trend.get("confidence")
        if isinstance(conf, (int, float)) and not isinstance(conf, bool):
            result["confidence"] = float(conf)

    candidates: dict[int, tuple[datetime, Mapping[str, Any]]] = {}
    for row in payload.get("forecasts") or []:
        if not isinstance(row, Mapping) or row.get("belief_id") != "eurusd.trend.bullish":
            continue
        meta = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
        if meta.get("consumer") != "WES-ASSET-SHADOW":
            continue
        forecast_at = parse_time(row.get("forecast_at"))
        if forecast_at is None or forecast_at > when:
            continue
        try:
            horizon = int(round(float(row.get("horizon_hours"))))
        except (TypeError, ValueError):
            continue
        if horizon not in {3, 12, 24, 72, 120}:
            continue
        existing = candidates.get(horizon)
        if existing is None or forecast_at > existing[0]:
            candidates[horizon] = (forecast_at, row)

    for horizon, (_, row) in candidates.items():
        p = row.get("predicted_probability")
        if isinstance(p, (int, float)) and not isinstance(p, bool):
            result["horizons"][str(horizon)] = {
                "p_up": float(p),
                "confidence": float(row.get("forecast_confidence") or 0.0),
                "forecast_at": row.get("forecast_at"),
                "target_at": row.get("target_at"),
            }
    return result


def _fse_context(payload: Mapping[str, Any] | None, when: datetime, atr_value: float) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {"available": False}
    row = next((
        item for item in payload.get("instruments") or []
        if isinstance(item, Mapping) and str(item.get("instrument") or "").upper() == "EURUSD"
    ), None)
    if not isinstance(row, Mapping):
        return {"available": False}
    observed = parse_time(row.get("observed_at"))
    if observed is None or observed > when or (when - observed).total_seconds() > 6 * 3600:
        return {"available": False, "reason": "stale_or_future"}
    fm = row.get("fractal_memory") if isinstance(row.get("fractal_memory"), Mapping) else {}
    persistence = row.get("persistence") if isinstance(row.get("persistence"), Mapping) else {}
    median_return = fm.get("median_forward_return")
    return {
        "available": True,
        "observed_at": iso_z(observed),
        "regime": row.get("regime"),
        "risk_score": row.get("risk_score"),
        "p_up_4h": fm.get("p_up_4h"),
        "analogues_n": fm.get("analogues_n"),
        "mean_similarity": fm.get("mean_similarity"),
        "median_forward_return": median_return,
        "median_forward_return_atr": (
            float(median_return) / float(atr_value)
            if isinstance(median_return, (int, float)) and atr_value > 0
            else None
        ),
        "mean_hurst_q2": persistence.get("mean_hurst_q2"),
        "production_impact": bool(payload.get("production_impact")),
    }


def build_context(
    *,
    spot: Mapping[str, Any],
    rows_30m: Sequence[Bar],
    when: datetime,
    belief_state: Mapping[str, Any] | None = None,
    fse_public: Mapping[str, Any] | None = None,
    reference_mid: float | None = None,
) -> dict[str, Any]:
    rows = as_of(rows_30m, when)
    if len(rows) < 30:
        raise ValueError("insufficient EUR/USD 30m bars for contextual learning")
    mid = float(reference_mid if reference_mid is not None else rows[-1].close)
    atr_value = float(atr(rows, 26) or 0.0)
    if atr_value <= 0:
        raise ValueError("contextual learning requires positive ATR")

    closes = [float(row.close) for row in rows]
    ema20 = float(ema(closes, 20) or mid)
    moves: dict[str, float] = {}
    for hours in (3, 12, 24):
        prior = _lookback_price(rows, when, hours)
        moves[str(hours)] = 0.0 if prior is None else (mid - prior) / atr_value

    metadata = spot.get("metadata") if isinstance(spot.get("metadata"), Mapping) else {}
    candidate = metadata.get("candidate") if isinstance(metadata.get("candidate"), Mapping) else {}
    event = metadata.get("event_intelligence") if isinstance(metadata.get("event_intelligence"), Mapping) else {}
    event_score = event.get("score") if isinstance(event.get("score"), Mapping) else {}
    belief_macro = metadata.get("belief_macro") if isinstance(metadata.get("belief_macro"), Mapping) else {}
    belief = _belief_curve(belief_state, when)
    fse = _fse_context(fse_public, when, atr_value)

    score = spot.get("score")
    score_signed = (float(score) - 50.0) / 50.0 if isinstance(score, (int, float)) else None
    confidence = spot.get("confidence")
    horizons = belief.get("horizons") if isinstance(belief.get("horizons"), Mapping) else {}

    features: dict[str, float] = {
        "move_3h_atr": round(moves["3"], 6),
        "move_12h_atr": round(moves["12"], 6),
        "move_24h_atr": round(moves["24"], 6),
        "ema20_distance_atr": round((mid - ema20) / atr_value, 6),
    }
    optional = {
        "daily_score_signed": score_signed,
        "daily_confidence": confidence,
        "p_up_current": belief.get("current_p_up"),
        "p_up_delta": belief.get("delta_p_up"),
        "p_up_3h": (horizons.get("3") or {}).get("p_up") if isinstance(horizons.get("3"), Mapping) else None,
        "p_up_12h": (horizons.get("12") or {}).get("p_up") if isinstance(horizons.get("12"), Mapping) else None,
        "p_up_24h": (horizons.get("24") or {}).get("p_up") if isinstance(horizons.get("24"), Mapping) else None,
        "belief_confidence": belief.get("confidence"),
        "belief_macro_score": belief_macro.get("score"),
        "fse_p_up_4h": fse.get("p_up_4h"),
        "fse_risk_score": fse.get("risk_score"),
        "fse_hurst_q2": fse.get("mean_hurst_q2"),
        "fse_analogue_median_return_atr": fse.get("median_forward_return_atr"),
        "event_score": event_score.get("score_delta"),
        "event_confidence": event_score.get("confidence"),
    }
    for key, value in optional.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
            features[key] = round(float(value), 6)

    return {
        "observed_at": iso_z(when),
        "reference_mid": round(mid, 8),
        "atr_30m": round(atr_value, 8),
        "ema20": round(ema20, 8),
        "features": features,
        "belief_probability_curve": belief,
        "fse": fse,
        "event": {
            "available": bool(event),
            "decision_overlay": event.get("decision_overlay"),
            "score_delta": event_score.get("score_delta"),
            "confidence": event_score.get("confidence"),
            "dominant_event_scope": event_score.get("dominant_event_scope"),
        },
        "daily": {
            "direction": spot.get("direction"),
            "score": spot.get("score"),
            "confidence": spot.get("confidence"),
            "decision_source": metadata.get("decision_source"),
            "candidate": {
                "direction": candidate.get("direction"),
                "score": candidate.get("score"),
                "confidence": candidate.get("confidence"),
                "source": candidate.get("source"),
            },
        },
    }


def _authority_contract() -> dict[str, Any]:
    return {
        "decision_influence": True,
        "direction_decision_influence": False,
        "entry_timing_decision_influence": True,
        "trade_execution": False,
        "automatic_policy_mutation": True,
        "automatic_promotion": True,
        "automatic_rollback": True,
        "historical_backfill": False,
        "execution_owner": "Daily EURUSD lifecycle + EPE",
        "direction_owner": "Daily EURUSD direction engine",
    }


def _governance_contract() -> dict[str, Any]:
    return {
        "horizon_hours": HORIZON_HOURS,
        "pullback_depths_atr": list(PULLBACK_DEPTHS_ATR),
        "minimum_resolved_for_recommendation": MIN_RESOLVED_FOR_RECOMMENDATION,
        "minimum_effective_neighbors": MIN_EFFECTIVE_NEIGHBORS,
        "uncertainty_penalty": UNCERTAINTY_PENALTY,
        "minimum_policy_edge_r": MIN_POLICY_EDGE_R,
        "recency_half_life_days": RECENCY_HALF_LIFE_DAYS,
        "authority_levels": {
            level: {"minimum_evidence_confidence": threshold, "authority_fraction": fraction}
            for level, threshold, fraction in AUTHORITY_LEVELS
        },
        "production_policy_set": [
            "CONTINUATION_NOW",
            "PULLBACK_20_ATR",
            "PULLBACK_35_ATR",
            "PULLBACK_50_ATR",
            "FLAT",
        ],
        "research_only_policy_set": ["REVERSAL_NOW"],
        "execution_cost_model": "EPE fixed synthetic EURUSD spread 1.5 pips",
        "promotion_model": "continuous_context_local_evidence",
        "rollback_model": "automatic_context_local_evidence_decay_or_edge_loss",
    }


def _initial_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": None,
        "authority": _authority_contract(),
        "governance": _governance_contract(),
        "episodes": [],
        "latest_recommendation": None,
    }


def normalize_state(state: Mapping[str, Any] | None) -> dict[str, Any]:
    """Migrate v1 shadow state in-memory without fabricating historical evidence."""
    if not isinstance(state, Mapping):
        return _initial_state()
    version = str(state.get("schema_version") or "")
    if version not in {SCHEMA_VERSION, LEGACY_SCHEMA_VERSION}:
        raise ValueError(f"unsupported contextual policy learning schema: {version}")
    result = dict(state)
    result["schema_version"] = SCHEMA_VERSION
    result["authority"] = _authority_contract()
    result["governance"] = _governance_contract()
    result.setdefault("episodes", [])
    result.setdefault("latest_recommendation", None)
    return result
def _episode_id(kind: str, source_id: str, when: datetime) -> str:
    clean = iso_z(when).replace("-", "").replace(":", "")
    return f"{kind.lower()}:{source_id}:{clean}"


def _source_decision_points(
    spot: Mapping[str, Any],
    history: Mapping[str, Any],
) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    metadata = spot.get("metadata") if isinstance(spot.get("metadata"), Mapping) else {}
    position = metadata.get("position") if isinstance(metadata.get("position"), Mapping) else None
    if position and str(position.get("status") or "").upper() == "OPEN":
        opened = parse_time(position.get("opened_at"))
        direction = str(position.get("direction") or "").upper()
        trade_id = str(position.get("trade_id") or "")
        if opened and trade_id and direction in {"LONG", "SHORT"}:
            points.append({
                "kind": "ENTRY",
                "source_id": trade_id,
                "observed_at": opened,
                "direction": direction,
                "decision_source": position.get("decision_source"),
            })

    trades = [row for row in history.get("trades") or [] if isinstance(row, Mapping)]
    if trades:
        latest = max(trades, key=lambda row: str(row.get("closed_at") or ""))
        closed = parse_time(latest.get("closed_at"))
        direction = str(latest.get("direction") or "").upper()
        trade_id = str(latest.get("trade_id") or "")
        if closed and trade_id and direction in {"LONG", "SHORT"}:
            points.append({
                "kind": "POST_EXIT",
                "source_id": trade_id,
                "observed_at": closed,
                "direction": direction,
                "decision_source": latest.get("decision_source"),
                "exit_reason": latest.get("exit_reason"),
                "previous_r": latest.get("r_multiple"),
            })
    return points


def _risk_distance(reference_mid: float, atr_value: float) -> float:
    return max(float(atr_value) * 1.35, float(reference_mid) * 0.0027)


def capture_new_episodes(
    state: Mapping[str, Any],
    *,
    spot: Mapping[str, Any],
    history: Mapping[str, Any],
    rows_30m: Sequence[Bar],
    belief_state: Mapping[str, Any] | None,
    fse_public: Mapping[str, Any] | None,
    now: datetime,
) -> tuple[dict[str, Any], int]:
    result = dict(state)
    episodes = [dict(row) for row in result.get("episodes") or [] if isinstance(row, Mapping)]
    seen = {str(row.get("episode_id") or "") for row in episodes}
    added = 0
    for point in _source_decision_points(spot, history):
        observed = point["observed_at"]
        if observed > now or (now - observed).total_seconds() > 20 * 60:
            continue
        eid = _episode_id(point["kind"], point["source_id"], observed)
        if eid in seen:
            continue
        reference = price_at_or_before(rows_30m, observed)
        if reference is None:
            continue
        context = build_context(
            spot=spot,
            rows_30m=rows_30m,
            when=observed,
            belief_state=belief_state,
            fse_public=fse_public,
            reference_mid=reference,
        )
        atr_value = float(context["atr_30m"])
        episode = {
            "schema_version": EPISODE_SCHEMA,
            "episode_id": eid,
            "kind": point["kind"],
            "source_id": point["source_id"],
            "captured_at": iso_z(now),
            "market_observed_at": iso_z(observed),
            "target_at": iso_z(observed + timedelta(hours=HORIZON_HOURS)),
            "base_direction": point["direction"],
            "decision_source": point.get("decision_source"),
            "exit_reason": point.get("exit_reason"),
            "previous_r": point.get("previous_r"),
            "reference_mid": context["reference_mid"],
            "atr_30m": context["atr_30m"],
            "risk_distance": round(_risk_distance(float(context["reference_mid"]), atr_value), 8),
            "reward_risk": 1.8,
            "context": context,
            "policy_variants": {
                "CONTINUATION_NOW": {"family": "CONTINUATION_NOW"},
                **{
                    f"PULLBACK_{int(round(depth * 100)):02d}_ATR": {
                        "family": "CONTINUATION_PULLBACK",
                        "depth_atr": depth,
                    }
                    for depth in PULLBACK_DEPTHS_ATR
                },
                "REVERSAL_NOW": {"family": "REVERSAL"},
                "FLAT": {"family": "FLAT"},
            },
            "status": "PENDING",
            "settlement": None,
            "policy_change_applied": False,
        }
        episodes.append(episode)
        seen.add(eid)
        added += 1

    result["episodes"] = episodes[-MAX_EPISODES:]
    if added:
        result["updated_at"] = iso_z(now)
    return result, added


def _execution_side_values(direction: str, bar: Bar) -> tuple[float, float, float]:
    half = epe.SYNTHETIC_HALF_SPREAD_PRICE
    side = str(direction).upper()
    shift = -half if side == "LONG" else half
    high = float(bar.high if bar.high is not None else bar.close) + shift
    low = float(bar.low if bar.low is not None else bar.close) + shift
    close = float(bar.close) + shift
    return high, low, close


def _simulate_from_entry(
    *,
    direction: str,
    entry_mid: float,
    risk_distance: float,
    reward_risk: float,
    bars: Sequence[Bar],
    entered_at: datetime,
    skip_favorable_on_first_bar: bool = False,
) -> dict[str, Any]:
    fill, fill_side, levels = epe.synthetic_entry_price(direction, entry_mid)
    if direction == "LONG":
        stop = entry_mid - risk_distance
        target = entry_mid + risk_distance * reward_risk
    else:
        stop = entry_mid + risk_distance
        target = entry_mid - risk_distance * reward_risk
    stop = epe.fx_price_5(stop)
    target = epe.fx_price_5(target)

    sign = 1.0 if direction == "LONG" else -1.0
    mfe = 0.0
    mae = 0.0
    exit_price = None
    exit_reason = None
    exited_at = None

    relevant = [bar for bar in bars if bar.timestamp.astimezone(timezone.utc) >= entered_at]
    for index, bar in enumerate(relevant):
        high, low, close = _execution_side_values(direction, bar)
        if direction == "LONG":
            favorable = high - fill
            adverse = low - fill
            stop_hit = low <= stop
            target_hit = high >= target
        else:
            favorable = fill - low
            adverse = fill - high
            stop_hit = high >= stop
            target_hit = low <= target
        mfe = max(mfe, favorable / risk_distance)
        mae = min(mae, adverse / risk_distance)

        if index == 0 and skip_favorable_on_first_bar and target_hit and not stop_hit:
            target_hit = False
        if stop_hit and target_hit:
            exit_reason, exit_price = "STOP_LOSS_SAME_BAR_CONSERVATIVE", stop
        elif stop_hit:
            exit_reason, exit_price = "STOP_LOSS", stop
        elif target_hit:
            exit_reason, exit_price = "TAKE_PROFIT", target
        if exit_price is not None:
            exited_at = bar.timestamp.astimezone(timezone.utc)
            break

    if exit_price is None:
        if not relevant:
            return {"status": "UNRESOLVED", "reason": "no_forward_bars"}
        last = relevant[-1]
        exit_price, exit_side, _ = epe.synthetic_exit_price(direction, float(last.close))
        exit_reason = "HORIZON_EXIT"
        exited_at = last.timestamp.astimezone(timezone.utc)
    else:
        exit_side = "BID" if direction == "LONG" else "ASK"

    pnl = sign * (float(exit_price) - float(fill))
    net_r = pnl / risk_distance if risk_distance > 0 else 0.0
    return {
        "status": "RESOLVED",
        "direction": direction,
        "entry_mid": round(float(entry_mid), 8),
        "fill_price": round(float(fill), 5),
        "fill_side": fill_side,
        "stop": stop,
        "target": target,
        "exit_price": round(float(exit_price), 5),
        "exit_side": exit_side,
        "exit_reason": exit_reason,
        "entered_at": iso_z(entered_at),
        "exited_at": iso_z(exited_at),
        "net_r": round(net_r, 6),
        "mfe_r": round(mfe, 6),
        "mae_r": round(mae, 6),
        "synthetic_spread_pips": epe.SYNTHETIC_SPREAD_PIPS,
    }


def _settle_policy(
    episode: Mapping[str, Any],
    policy_id: str,
    policy: Mapping[str, Any],
    bars: Sequence[Bar],
) -> dict[str, Any]:
    observed = parse_time(episode.get("market_observed_at"))
    if observed is None:
        return {"status": "UNRESOLVED", "reason": "invalid_observed_at"}
    base = str(episode.get("base_direction") or "").upper()
    if base not in {"LONG", "SHORT"}:
        return {"status": "UNRESOLVED", "reason": "invalid_base_direction"}
    ref = float(episode["reference_mid"])
    atr_value = float(episode["atr_30m"])
    risk = float(episode["risk_distance"])
    rr = float(episode.get("reward_risk") or 1.8)

    family = str(policy.get("family") or policy_id)
    if family == "FLAT":
        return {
            "status": "RESOLVED",
            "direction": "FLAT",
            "net_r": 0.0,
            "mfe_r": 0.0,
            "mae_r": 0.0,
            "entry_status": "NO_TRADE",
            "synthetic_spread_pips": 0.0,
        }
    if family == "CONTINUATION_NOW":
        return _simulate_from_entry(
            direction=base,
            entry_mid=ref,
            risk_distance=risk,
            reward_risk=rr,
            bars=bars,
            entered_at=observed,
        )
    if family == "REVERSAL":
        opposite = "SHORT" if base == "LONG" else "LONG"
        return _simulate_from_entry(
            direction=opposite,
            entry_mid=ref,
            risk_distance=risk,
            reward_risk=rr,
            bars=bars,
            entered_at=observed,
        )
    if family == "CONTINUATION_PULLBACK":
        depth = float(policy.get("depth_atr") or 0.0)
        trigger = ref - depth * atr_value if base == "LONG" else ref + depth * atr_value
        trigger_bar = next((
            bar for bar in bars
            if bar.timestamp.astimezone(timezone.utc) >= observed
            and (
                float(bar.low if bar.low is not None else bar.close) <= trigger
                if base == "LONG"
                else float(bar.high if bar.high is not None else bar.close) >= trigger
            )
        ), None)
        if trigger_bar is None:
            return {
                "status": "RESOLVED",
                "direction": base,
                "entry_status": "NOT_TRIGGERED",
                "trigger_mid": round(trigger, 8),
                "net_r": 0.0,
                "mfe_r": 0.0,
                "mae_r": 0.0,
                "synthetic_spread_pips": 0.0,
            }
        result = _simulate_from_entry(
            direction=base,
            entry_mid=trigger,
            risk_distance=risk,
            reward_risk=rr,
            bars=bars,
            entered_at=trigger_bar.timestamp.astimezone(timezone.utc),
            skip_favorable_on_first_bar=True,
        )
        result["entry_status"] = "TRIGGERED"
        result["trigger_mid"] = round(trigger, 8)
        result["pullback_depth_atr"] = depth
        return result
    return {"status": "UNRESOLVED", "reason": "unknown_policy"}


def settle_due_episodes(
    state: Mapping[str, Any],
    *,
    bars_5m: Sequence[Bar],
    now: datetime,
) -> tuple[dict[str, Any], int]:
    result = dict(state)
    episodes = [dict(row) for row in result.get("episodes") or [] if isinstance(row, Mapping)]
    settled = 0
    for episode in episodes:
        if episode.get("status") != "PENDING":
            continue
        target = parse_time(episode.get("target_at"))
        observed = parse_time(episode.get("market_observed_at"))
        if target is None or observed is None or now < target:
            continue
        path = [
            bar for bar in bars_5m
            if observed <= bar.timestamp.astimezone(timezone.utc) <= target
        ]
        if not path:
            continue
        outcomes = {}
        all_resolved = True
        for policy_id, policy in (episode.get("policy_variants") or {}).items():
            outcome = _settle_policy(episode, str(policy_id), policy, path)
            outcomes[str(policy_id)] = outcome
            if outcome.get("status") != "RESOLVED":
                all_resolved = False
        if not all_resolved:
            continue
        episode["settlement"] = {
            "settled_at": iso_z(now),
            "target_at": iso_z(target),
            "bars": len(path),
            "outcomes": outcomes,
        }
        episode["status"] = "RESOLVED"
        settled += 1
    result["episodes"] = episodes
    if settled:
        result["updated_at"] = iso_z(now)
    return result, settled


def _feature_scales(episodes: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    values: dict[str, list[float]] = {key: [] for key in FEATURE_KEYS}
    for episode in episodes:
        context = episode.get("context") if isinstance(episode.get("context"), Mapping) else {}
        features = context.get("features") if isinstance(context.get("features"), Mapping) else {}
        for key in FEATURE_KEYS:
            value = features.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values[key].append(float(value))
    scales: dict[str, float] = {}
    for key, rows in values.items():
        if len(rows) >= 3:
            sd = statistics.pstdev(rows)
            scales[key] = max(sd, 0.05 if key.startswith("p_") or key.startswith("fse_") else 0.10)
        else:
            scales[key] = 0.25 if key.startswith("p_") or key.startswith("fse_") else 0.50
    return scales


def _similarity(current: Mapping[str, float], prior: Mapping[str, float], scales: Mapping[str, float]) -> float:
    terms = []
    for key in FEATURE_KEYS:
        if key not in current or key not in prior:
            continue
        scale = max(float(scales.get(key) or 1.0), 1e-6)
        z = (float(current[key]) - float(prior[key])) / scale
        terms.append(z * z)
    if len(terms) < 4:
        return 0.0
    return math.exp(-0.5 * sum(terms) / len(terms))


def _policy_outcomes(episode: Mapping[str, Any]) -> Mapping[str, Any]:
    settlement = episode.get("settlement") if isinstance(episode.get("settlement"), Mapping) else {}
    outcomes = settlement.get("outcomes") if isinstance(settlement.get("outcomes"), Mapping) else {}
    return outcomes


def _recency_weight(episode: Mapping[str, Any], current_context: Mapping[str, Any]) -> float:
    current_at = parse_time(current_context.get("observed_at"))
    prior_at = parse_time(episode.get("market_observed_at"))
    if current_at is None or prior_at is None or current_at <= prior_at:
        return 1.0
    age_days = (current_at - prior_at).total_seconds() / 86400.0
    return math.exp(-math.log(2.0) * age_days / RECENCY_HALF_LIFE_DAYS)


def _context_similarity(
    current_context: Mapping[str, Any],
    episode: Mapping[str, Any],
    scales: Mapping[str, float],
) -> float:
    context = episode.get("context") if isinstance(episode.get("context"), Mapping) else {}
    current_features = current_context.get("features") if isinstance(current_context.get("features"), Mapping) else {}
    prior_features = context.get("features") if isinstance(context.get("features"), Mapping) else {}
    similarity = _similarity(current_features, prior_features, scales)
    if similarity <= 0.0:
        return 0.0

    current_daily = current_context.get("daily") if isinstance(current_context.get("daily"), Mapping) else {}
    current_direction = str(current_daily.get("direction") or "").upper()
    prior_direction = str(episode.get("base_direction") or "").upper()
    if current_direction in {"LONG", "SHORT"} and prior_direction in {"LONG", "SHORT"} and current_direction != prior_direction:
        return 0.0

    current_fse = current_context.get("fse") if isinstance(current_context.get("fse"), Mapping) else {}
    prior_fse = context.get("fse") if isinstance(context.get("fse"), Mapping) else {}
    current_regime = str(current_fse.get("regime") or "").upper()
    prior_regime = str(prior_fse.get("regime") or "").upper()
    if current_regime and prior_regime and current_regime != prior_regime:
        similarity *= 0.35

    return similarity * _recency_weight(episode, current_context)


def _authority_from_evidence(*, n_eff: float, edge_r: float, mean_r: float, sd_r: float) -> dict[str, Any]:
    sample_strength = 1.0 - math.exp(-max(n_eff, 0.0) / 3.0)
    edge_strength = max(0.0, min(1.0, max(edge_r, 0.0) / 0.35))
    stability = 1.0 - min(1.0, max(sd_r, 0.0) / (abs(mean_r) + max(sd_r, 0.0) + 0.25))
    evidence_confidence = max(0.0, min(1.0, sample_strength * edge_strength * stability))

    level = "SHADOW"
    fraction = 0.0
    if edge_r >= MIN_POLICY_EDGE_R:
        for candidate_level, threshold, candidate_fraction in AUTHORITY_LEVELS:
            if evidence_confidence >= threshold:
                level = candidate_level
                fraction = candidate_fraction
                break
    return {
        "level": level,
        "authority_fraction": round(fraction, 4),
        "evidence_confidence": round(evidence_confidence, 6),
        "sample_strength": round(sample_strength, 6),
        "edge_strength": round(edge_strength, 6),
        "stability": round(stability, 6),
        "automatic_rollback": level == "SHADOW",
    }


def recommend_policy(state: Mapping[str, Any], current_context: Mapping[str, Any]) -> dict[str, Any]:
    resolved = [
        row for row in state.get("episodes") or []
        if isinstance(row, Mapping) and row.get("status") == "RESOLVED"
    ]
    if len(resolved) < MIN_RESOLVED_FOR_RECOMMENDATION:
        return {
            "status": "INSUFFICIENT_EVIDENCE",
            "resolved_episodes": len(resolved),
            "minimum_required": MIN_RESOLVED_FOR_RECOMMENDATION,
            "authority_level": "SHADOW",
            "authority_fraction": 0.0,
            "evidence_confidence": 0.0,
            "decision_influence": False,
            "automatic_rollback": True,
        }

    scales = _feature_scales(resolved)
    neighbors: list[tuple[float, Mapping[str, Any]]] = []
    for episode in resolved:
        similarity = _context_similarity(current_context, episode, scales)
        if similarity > 0.05:
            neighbors.append((similarity, episode))
    neighbors.sort(key=lambda item: item[0], reverse=True)
    neighbors = neighbors[:80]

    all_policy_ids = [
        "CONTINUATION_NOW",
        *(f"PULLBACK_{int(round(depth * 100)):02d}_ATR" for depth in PULLBACK_DEPTHS_ATR),
        "REVERSAL_NOW",
        "FLAT",
    ]
    production_policy_ids = [policy_id for policy_id in all_policy_ids if policy_id != "REVERSAL_NOW"]
    stats: dict[str, Any] = {}
    for policy_id in all_policy_ids:
        weighted: list[tuple[float, float]] = []
        for weight, episode in neighbors:
            outcome = _policy_outcomes(episode).get(policy_id)
            if not isinstance(outcome, Mapping):
                continue
            value = outcome.get("net_r")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                weighted.append((float(weight), float(value)))
        total_w = sum(w for w, _ in weighted)
        if total_w <= 0:
            continue
        mean = sum(w * value for w, value in weighted) / total_w
        variance = sum(w * (value - mean) ** 2 for w, value in weighted) / total_w
        sd = math.sqrt(max(0.0, variance))
        n_eff = total_w * total_w / max(sum(w * w for w, _ in weighted), 1e-9)
        se = sd / math.sqrt(max(n_eff, 1.0))
        lower = mean - UNCERTAINTY_PENALTY * se
        stats[policy_id] = {
            "expected_r": round(mean, 6),
            "weighted_mean_net_r": round(mean, 6),
            "weighted_sd_r": round(sd, 6),
            "effective_neighbors": round(n_eff, 3),
            "uncertainty_penalized_r": round(lower, 6),
            "raw_neighbors": len(weighted),
        }

    eligible = {
        key: value for key, value in stats.items()
        if key in production_policy_ids
        and float(value.get("effective_neighbors") or 0.0) >= MIN_EFFECTIVE_NEIGHBORS
    }
    if not eligible:
        return {
            "status": "INSUFFICIENT_SIMILAR_CONTEXT",
            "resolved_episodes": len(resolved),
            "neighbors": len(neighbors),
            "policy_stats": stats,
            "authority_level": "SHADOW",
            "authority_fraction": 0.0,
            "evidence_confidence": 0.0,
            "decision_influence": False,
            "automatic_rollback": True,
        }

    ranked = sorted(
        eligible.items(),
        key=lambda item: float(item[1]["uncertainty_penalized_r"]),
        reverse=True,
    )
    best_id, best = ranked[0]
    second_value = float(ranked[1][1]["uncertainty_penalized_r"]) if len(ranked) > 1 else 0.0
    edge = float(best["uncertainty_penalized_r"]) - second_value
    authority = _authority_from_evidence(
        n_eff=float(best.get("effective_neighbors") or 0.0),
        edge_r=edge,
        mean_r=float(best.get("weighted_mean_net_r") or 0.0),
        sd_r=float(best.get("weighted_sd_r") or 0.0),
    )
    status = "AUTONOMOUS_POLICY_EDGE" if authority["level"] != "SHADOW" else "NO_ROBUST_POLICY_EDGE"
    family = "CONTINUATION_PULLBACK" if best_id.startswith("PULLBACK_") else best_id

    research_ranked = sorted(
        ((key, value) for key, value in stats.items()),
        key=lambda item: float(item[1]["uncertainty_penalized_r"]),
        reverse=True,
    )
    research_best = research_ranked[0][0] if research_ranked else None

    return {
        "status": status,
        "recommended_policy_id": best_id,
        "recommended_family": family,
        "research_best_policy_id": research_best,
        "research_reversal_is_non_authoritative": research_best == "REVERSAL_NOW",
        "edge_vs_second_r": round(edge, 6),
        "expected_r": float(best.get("expected_r") or 0.0),
        "resolved_episodes": len(resolved),
        "neighbors": len(neighbors),
        "policy_stats": stats,
        "authority_level": authority["level"],
        "authority_fraction": authority["authority_fraction"],
        "evidence_confidence": authority["evidence_confidence"],
        "evidence_components": {
            "sample_strength": authority["sample_strength"],
            "edge_strength": authority["edge_strength"],
            "stability": authority["stability"],
        },
        "decision_influence": authority["level"] != "SHADOW",
        "automatic_promotion": True,
        "automatic_rollback": authority["automatic_rollback"],
        "direction_mutation_allowed": False,
    }
def public_summary(state: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    episodes = [row for row in state.get("episodes") or [] if isinstance(row, Mapping)]
    resolved = [row for row in episodes if row.get("status") == "RESOLVED"]
    pending = [row for row in episodes if row.get("status") == "PENDING"]
    return {
        "schema_version": PUBLIC_SCHEMA,
        "generated_at": str(state.get("updated_at") or iso_z(now)),
        "engine": "Daily Learning Loop — Contextual Entry Policy Learning",
        "mode": "PROSPECTIVE_CONTINUOUS_AUTONOMOUS_ENTRY_POLICY",
        "authority": dict(state.get("authority") or {}),
        "sample": {
            "episodes_total": len(episodes),
            "resolved": len(resolved),
            "pending": len(pending),
            "entry_points": sum(1 for row in episodes if row.get("kind") == "ENTRY"),
            "post_exit_points": sum(1 for row in episodes if row.get("kind") == "POST_EXIT"),
        },
        "policies": [
            "CONTINUATION_NOW",
            "CONTINUATION_PULLBACK",
            "REVERSAL",
            "FLAT",
        ],
        "pullback_variants_atr": list(PULLBACK_DEPTHS_ATR),
        "latest_recommendation": state.get("latest_recommendation"),
        "governance": dict(state.get("governance") or {}),
    }


def validate_state(state: Mapping[str, Any]) -> None:
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("invalid contextual policy learning schema")
    authority = state.get("authority") if isinstance(state.get("authority"), Mapping) else {}
    required_true = ("decision_influence", "entry_timing_decision_influence", "automatic_policy_mutation", "automatic_promotion", "automatic_rollback")
    for key in required_true:
        if authority.get(key) is not True:
            raise ValueError(f"contextual learner missing bounded authority: {key}")
    for key in ("direction_decision_influence", "trade_execution", "historical_backfill"):
        if authority.get(key) is not False:
            raise ValueError(f"contextual learner authority violation: {key}")
    seen: set[str] = set()
    for row in state.get("episodes") or []:
        if not isinstance(row, Mapping) or row.get("schema_version") != EPISODE_SCHEMA:
            raise ValueError("invalid contextual learning episode")
        eid = str(row.get("episode_id") or "")
        if not eid or eid in seen:
            raise ValueError("duplicate contextual learning episode")
        seen.add(eid)
        if row.get("policy_change_applied") is not False:
            raise ValueError("individual episode cannot directly mutate production policy")
        if row.get("status") == "RESOLVED":
            outcomes = _policy_outcomes(row)
            required = {
                "CONTINUATION_NOW",
                "REVERSAL_NOW",
                "FLAT",
                *(f"PULLBACK_{int(round(depth * 100)):02d}_ATR" for depth in PULLBACK_DEPTHS_ATR),
            }
            if not required.issubset(set(outcomes)):
                raise ValueError("resolved episode missing policy outcomes")
def run_cycle(
    *,
    spot_path: Path,
    history_path: Path,
    state_path: Path,
    public_path: Path,
    belief_state_path: Path | None = None,
    fse_path: Path | None = None,
    now: datetime | None = None,
    client: YahooChartClient | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, int]]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    spot = load_json(spot_path, {})
    history = load_json(history_path, {})
    if not isinstance(spot, Mapping) or not isinstance(history, Mapping):
        raise ValueError("Daily EUR/USD spot/history state is required")

    state = normalize_state(load_json(state_path))

    belief = load_json(belief_state_path, {}) if belief_state_path else {}
    fse = load_json(fse_path, {}) if fse_path else {}
    market = client or YahooChartClient(timeout=12)
    rows_30m = market.bars("EURUSD=X", "10d", "30m")
    rows_5m = market.bars("EURUSD=X", "5d", "5m")

    state, settled = settle_due_episodes(state, bars_5m=rows_5m, now=current)
    state, added = capture_new_episodes(
        state,
        spot=spot,
        history=history,
        rows_30m=rows_30m,
        belief_state=belief if isinstance(belief, Mapping) else None,
        fse_public=fse if isinstance(fse, Mapping) else None,
        now=current,
    )

    # Continuous authority is context-local, so the live recommendation must be
    # recomputed on every learner cycle even when no episode was added/settled.
    # Otherwise a migrated v1 state can keep a stale "minimum 20" recommendation
    # while production v1.8 already uses the v2 continuous evidence model.
    if rows_30m:
        context = build_context(
            spot=spot,
            rows_30m=rows_30m,
            when=min(current, rows_30m[-1].timestamp.astimezone(timezone.utc)),
            belief_state=belief if isinstance(belief, Mapping) else None,
            fse_public=fse if isinstance(fse, Mapping) else None,
        )
        state["latest_recommendation"] = recommend_policy(state, context)
        state["latest_context"] = context
        state["updated_at"] = iso_z(current)
    elif not state.get("updated_at"):
        state["updated_at"] = iso_z(current)
    validate_state(state)
    public = public_summary(state, current)
    atomic_json(state_path, state)
    atomic_json(public_path, public)
    return state, public, {"added": added, "settled": settled}


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily EUR/USD contextual entry policy learning")
    parser.add_argument("--spot", type=Path, required=True)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--public", type=Path, required=True)
    parser.add_argument("--belief-state", type=Path)
    parser.add_argument("--fse", type=Path)
    parser.add_argument("--now")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()

    if args.validate:
        state = load_json(args.state)
        if not isinstance(state, Mapping):
            raise SystemExit("contextual learning state missing")
        validate_state(state)
        print("EURUSD_CONTEXTUAL_POLICY_LEARNING_OK", len(state.get("episodes") or []))
        return 0

    now = parse_time(args.now) if args.now else None
    state, public, changed = run_cycle(
        spot_path=args.spot,
        history_path=args.history,
        state_path=args.state,
        public_path=args.public,
        belief_state_path=args.belief_state,
        fse_path=args.fse,
        now=now,
    )
    print("EURUSD_CONTEXTUAL_POLICY_EPISODES", len(state.get("episodes") or []))
    print("EURUSD_CONTEXTUAL_POLICY_ADDED", changed["added"])
    print("EURUSD_CONTEXTUAL_POLICY_SETTLED", changed["settled"])
    print("EURUSD_CONTEXTUAL_POLICY_STATUS", (public.get("latest_recommendation") or {}).get("status"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
