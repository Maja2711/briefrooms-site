"""TR-03 post-loss execution safety; no direction or state-write authority.

The daily candle is the source D1 interval (timestamps in UTC). Levels use
completed D1 bars only. The current impulse is reconstructed from complete
1m bars, never from the future high/low of an unfinished D1 candle.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

SCHEMA = "eurusd-post-loss-technical-admission-v1"
MIN_RETRACEMENT = .30
STRONG_LONG_SCORE = 60.0
STRONG_SHORT_SCORE = 40.0
MIN_CONFIDENCE = .50
MA_PERIODS = (60, 100, 200)
MIN_ALIGNED_BELIEFS = 3
ANCHOR = "eurusd.trend.bullish"
ZONE_ATR = .10
MAX_ENTRY_DISTANCE_ATR = .25
MAX_MARKET_AGE_SECONDS = 180.0
FIRST_HOUR_SECONDS = 3600.0
EPS = 1e-10

# These are initial criteria, not immutable production thresholds. Only the
# local learner's prospectively approved version is supplied by the writer.
DEFAULT_CRITERIA = {
    "minimum_retracement": .30, "minimum_score_distance": 10.0,
    "minimum_confidence": .50, "minimum_aligned_beliefs": 3,
    "support_mode": "D1_LEVEL", "ma_periods": [60, 100, 200],
    "zone_atr": .10, "maximum_entry_distance_atr": .25,
    "confirmation_minutes": 5, "minimum_wait_minutes": 0,
    "require_post_loss_trend": True, "require_material_change": True,
    "material_score_improvement": 3.0, "material_confidence_improvement": .08,
}


def normalize_criteria(value: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result = dict(DEFAULT_CRITERIA) if value is None else dict(value)
    if set(result) != set(DEFAULT_CRITERIA):
        raise ValueError("reentry_criteria_contract_mismatch")
    for key, bounds in {
        "minimum_retracement": (0, 1), "minimum_score_distance": (0, 50),
        "minimum_confidence": (0, 1), "zone_atr": (.01, 1),
        "maximum_entry_distance_atr": (.02, 2),
        "material_score_improvement": (0, 50), "material_confidence_improvement": (0, 1),
    }.items():
        number = _number(result[key])
        if not bounds[0] <= number <= bounds[1]:
            raise ValueError("reentry_criteria_out_of_domain:" + key)
        result[key] = number
    for key, low, high in (("minimum_aligned_beliefs", 1, 5), ("minimum_wait_minutes", 0, 1440)):
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError("invalid_reentry_integer:" + key)
    if result["confirmation_minutes"] not in (1, 5, 15) or isinstance(result["confirmation_minutes"], bool) or not isinstance(result["confirmation_minutes"], int):
        raise ValueError("invalid_confirmation_interval")
    if result["support_mode"] not in {"D1_LEVEL", "PRICE_REJECTION"}:
        raise ValueError("invalid_support_definition")
    periods = result["ma_periods"]
    if (not isinstance(periods, list) or not 1 <= len(periods) <= 8
            or any(isinstance(p, bool) or not isinstance(p, int) or not 2 <= p <= 400 for p in periods)
            or periods != sorted(set(periods))):
        raise ValueError("invalid_ma_periods")
    result["ma_periods"] = list(periods)
    if result["maximum_entry_distance_atr"] <= result["zone_atr"]:
        raise ValueError("entry_zone_must_have_positive_width")
    if any(not isinstance(result[key], bool) for key in ("require_post_loss_trend", "require_material_change")):
        raise ValueError("invalid_reentry_requirement")
    return result


def thesis_allows(guard: Mapping[str, Any], criteria: Mapping[str, Any]) -> bool:
    if not guard.get("blocked"):
        return True
    if guard.get("reason") not in {
        "recent_same_thesis_loss_without_material_new_evidence",
        "recent_same_direction_loss_without_material_new_evidence",
    }:
        return False
    if criteria["require_material_change"] is False:
        return True
    change = guard.get("change") or {}
    score = change.get("directional_score_strength_improvement")
    confidence = change.get("confidence_improvement")
    return ((score is not None and _number(score) >= criteria["material_score_improvement"] - EPS)
            or (confidence is not None and _number(confidence) >= criteria["material_confidence_improvement"] - EPS))



def _time(value: Any) -> datetime:
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("timezone_required")
    return stamp.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _number(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("invalid_number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non_finite_number")
    return number


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def latest_loss(history: Mapping[str, Any] | None, direction: str, now: datetime) -> Mapping[str, Any] | None:
    """Every negative result counts; source-family labels cannot bypass this gate."""
    choices = []
    for trade in (history or {}).get("trades") or []:
        if not isinstance(trade, Mapping) or str(trade.get("direction", "")).upper() != direction:
            continue
        instrument = str(trade.get("instrument") or "EUR/USD").upper().replace("_", "/")
        if instrument not in {"EUR/USD", "EURUSD", "EURUSD=X"}:
            continue
        closed = _time(trade.get("closed_at"))
        if closed < now - timedelta(hours=36):
            continue
        result = _number(trade.get("r_multiple"))
        if result < 0:
            if closed > now:
                raise ValueError("loss_timestamp_in_future")
            choices.append((closed, str(trade.get("trade_id") or ""), trade))
    return max(choices, key=lambda item: (item[0], item[1]))[2] if choices else None


def _bars(raw: Sequence[Any], now: datetime, *, minutes: int | None = None) -> list[dict[str, Any]]:
    result = {}
    for item in raw or []:
        def get(key: str) -> Any:
            return item.get(key) if isinstance(item, Mapping) else getattr(item, key, None)
        start = _time(get("timestamp"))
        if start > now or (minutes is not None and start + timedelta(minutes=minutes) > now):
            continue
        prices = {key: _number(get(key)) for key in ("open", "high", "low", "close")}
        if (min(prices.values()) <= 0 or prices["low"] > min(prices["open"], prices["close"]) + EPS
                or prices["high"] + EPS < max(prices["open"], prices["close"])
                or prices["low"] > prices["high"]):
            raise ValueError("invalid_ohlc")
        row = {"timestamp": _iso(start), **prices}
        if start in result and result[start] != row:
            raise ValueError("conflicting_duplicate_bar")
        result[start] = row
    return [result[key] for key in sorted(result)]


def _strong_signal(payload: Mapping[str, Any], loss_at: datetime, now: datetime, criteria: Mapping[str, Any]) -> dict[str, Any]:
    final = (payload.get("metadata") or {}).get("final_decision") or {}
    direction = str(payload.get("direction") or "")
    score, confidence = _number(payload.get("score")), _number(payload.get("confidence"))
    consumer = final.get("epistemic_consumer") or {}
    aligned = []
    anchor_fresh = False
    anchor_present = False
    for belief in final.get("used_beliefs") or []:
        probability = _number(belief.get("probability"))
        observed = _time(belief.get("observed_at"))
        agrees = probability >= .52 if direction == "LONG" else probability <= .48
        if agrees and 0 <= probability <= 1 and observed <= now:
            aligned.append(str(belief.get("belief_id")))
            if belief.get("belief_id") == ANCHOR:
                anchor_present = True
                if loss_at < observed:
                    anchor_fresh = True
    long_score = 50 + criteria["minimum_score_distance"]
    short_score = 50 - criteria["minimum_score_distance"]
    score_ok = score >= long_score if direction == "LONG" else score <= short_score
    authoritative = (
        final.get("decision_source") == "NATIVE_BELIEF_FIRST"
        and final.get("direction") == direction
        and final.get("epistemic_aggregate_authoritative") is True
        and consumer.get("available") is True
        and consumer.get("contract") == "epistemic-consumer-interface-v1"
        and consumer.get("aggregate_authoritative") is True
        and abs(_number(final.get("score")) - score) <= EPS
        and abs(_number(final.get("confidence")) - confidence) <= EPS
    )
    return {
        "passed": bool(authoritative and 0 <= score <= 100 and 0 <= confidence <= 1
                       and score_ok and confidence >= criteria["minimum_confidence"]
                       and len(set(aligned)) >= criteria["minimum_aligned_beliefs"] and anchor_present
                       and (anchor_fresh or not criteria["require_post_loss_trend"])),
        "score": score, "confidence": confidence, "authoritative": authoritative,
        "aligned_belief_ids": sorted(set(aligned)), "post_loss_trend_evidence": anchor_fresh,
        "thresholds": {"long_score": long_score, "short_score": short_score,
                       "confidence": criteria["minimum_confidence"], "aligned_beliefs": criteria["minimum_aligned_beliefs"],
                       "require_post_loss_trend": criteria["require_post_loss_trend"]},
    }


def _atr(rows: Sequence[Mapping[str, Any]]) -> float:
    if len(rows) < 15:
        raise ValueError("insufficient_completed_d1_for_atr")
    values = [max(b["high"] - b["low"], abs(b["high"] - a["close"]), abs(b["low"] - a["close"]))
              for a, b in zip(rows[-15:-1], rows[-14:])]
    value = sum(values) / 14
    if value <= 0:
        raise ValueError("daily_atr_unavailable")
    return value


def _levels(rows: Sequence[Mapping[str, Any]], direction: str, zone: float, periods: Sequence[int] = MA_PERIODS) -> list[dict[str, Any]]:
    levels = []
    for period in periods:
        if len(rows) >= period:
            levels.append({"kind": "SMA_D1", "period": period,
                           "price": sum(row["close"] for row in rows[-period:]) / period,
                           "source_last_completed_at": rows[-1]["timestamp"]})
    # An alternative structural level needs two separately confirmed D1 pivots.
    # Each pivot uses two completed bars on each side, all known before admission.
    scoped = list(rows[-64:])
    side = "low" if direction == "LONG" else "high"
    pivots = []
    for index in range(2, len(scoped) - 2):
        value = scoped[index][side]
        neighbours = [scoped[j][side] for j in (index - 2, index - 1, index + 1, index + 2)]
        extreme = value < min(neighbours) if direction == "LONG" else value > max(neighbours)
        if extreme:
            pivots.append((index, value, scoped[index]["timestamp"]))
    for index, price, stamp in pivots:
        peers = [(j, value, at) for j, value, at in pivots if abs(value - price) <= zone and abs(j - index) >= 3]
        if peers:
            levels.append({"kind": "RETESTED_D1_SWING", "price": price,
                           "confirmed_pivot_at": stamp, "independent_retests": len(peers),
                           "other_pivot_at": [at for _, _, at in peers]})
    return levels


def _five_minute(rows: Sequence[Mapping[str, Any]], loss_at: datetime, minutes: int = 5) -> list[dict[str, Any]]:
    groups: dict[int, list[Mapping[str, Any]]] = {}
    for row in rows:
        start = _time(row["timestamp"])
        bucket = int(start.timestamp()) // (60 * minutes) * (60 * minutes)
        groups.setdefault(bucket, []).append(row)
    result = []
    for epoch, chunk in sorted(groups.items()):
        start = datetime.fromtimestamp(epoch, timezone.utc)
        if start <= loss_at or len(chunk) != minutes:
            continue
        if any(_time(row["timestamp"]) != start + timedelta(minutes=i) for i, row in enumerate(chunk)):
            continue
        result.append({"timestamp": _iso(start), "end": _iso(start + timedelta(minutes=minutes)),
                       "open": chunk[0]["open"], "close": chunk[-1]["close"],
                       "high": max(row["high"] for row in chunk), "low": min(row["low"] for row in chunk)})
    return result


def evaluate(payload: Mapping[str, Any], history: Mapping[str, Any] | None, *,
             observed_at: datetime, market_context: Mapping[str, Any] | None = None,
             criteria: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Can only veto a native candidate. Never creates direction or modifies history."""
    now = _time(observed_at)
    result: dict[str, Any] = {
        "schema_version": SCHEMA, "enabled": True, "required": False, "allowed": True,
        "status": "NOT_APPLICABLE", "reason": "no_recent_same_direction_loss",
        "direction_mutation_allowed": False, "history_rewritten": False,
    }

    def block(reason: str) -> dict[str, Any]:
        return {**result, "allowed": False, "status": "BLOCKED", "reason": reason}

    if str(payload.get("instrument") or "") != "EUR/USD":
        return block("unsupported_instrument")
    direction = str(payload.get("direction") or "")
    if direction not in {"LONG", "SHORT"}:
        result["reason"] = "no_directional_candidate"
        return result
    try:
        criteria = normalize_criteria(criteria)
        result.update(criteria=criteria, criteria_sha256=_digest(criteria))
        loss = latest_loss(history, direction, now)
        if loss is None:
            return result
        result.update(required=True, prior_trade_id=loss.get("trade_id"),
                      prior_closed_at=loss.get("closed_at"), prior_r_multiple=loss.get("r_multiple"))
        loss_at = _time(loss["closed_at"])
        context = market_context or {}
        daily = _bars(context.get("daily_bars") or [], now)
        active = [row for row in daily if _time(row["timestamp"]) <= now < _time(row["timestamp"]) + timedelta(days=1)]
        if not active:
            return block("post_loss_daily_context_unavailable")
        session = active[-1]
        start, end = _time(session["timestamp"]), _time(session["timestamp"]) + timedelta(days=1)
        if start.minute or start.second:
            return block("invalid_d1_session_boundary")
        result["session"] = {"start": _iso(start), "end": _iso(end), "open": session["open"],
                             "basis": "SOURCE_D1_OPEN_TO_PRE_CORRECTION_EXTREME"}
        # The same D1 candle remains protected beyond the first hour. A loss just
        # before a session rollover is also protected for its complete first hour.
        if loss_at < start and (now - loss_at).total_seconds() > FIRST_HOUR_SECONDS:
            return {**result, "required": False, "reason": "loss_outside_current_d1_and_first_hour"}
        if (now - loss_at).total_seconds() < criteria["minimum_wait_minutes"] * 60:
            return block("post_loss_learned_wait_not_complete")
        signal = _strong_signal(payload, loss_at, now, criteria)
        result["strong_signal"] = signal
        if not signal["passed"]:
            return block("post_loss_strong_native_signal_required")
        completed = [row for row in daily if _time(row["timestamp"]) + timedelta(days=1) <= start]
        if any((_time(b["timestamp"]) - _time(a["timestamp"])).total_seconds() < 20 * 3600
               for a, b in zip(completed, completed[1:])):
            return block("invalid_d1_cadence")
        if not completed or start - (_time(completed[-1]["timestamp"]) + timedelta(days=1)) > timedelta(days=4):
            return block("completed_d1_history_stale")
        atr = _atr(completed)
        minute = [row for row in _bars(context.get("minute_bars") or [], now, minutes=1)
                  if start <= _time(row["timestamp"]) < end]
        if not minute or _time(minute[0]["timestamp"]) != start:
            return block("incomplete_current_d1_intraday_path")
        if abs(minute[0]["open"] - session["open"]) > .0001 + EPS:
            return block("daily_intraday_open_mismatch")
        if any((_time(b["timestamp"]) - _time(a["timestamp"])).total_seconds() != 60
               for a, b in zip(minute, minute[1:])):
            return block("incomplete_current_d1_intraday_path")
        if (now - _time(minute[-1]["timestamp"]) - timedelta(minutes=1)).total_seconds() > MAX_MARKET_AGE_SECONDS:
            return block("post_loss_market_path_stale")
        groups = _five_minute(minute, loss_at, criteria["confirmation_minutes"])
        confirmation_reason = "post_loss_completed_" + str(criteria["confirmation_minutes"]) + "m_confirmation_required"
        if len(groups) < 2:
            return block(confirmation_reason)
        touch, confirmation = groups[-2:]
        if touch["end"] != confirmation["timestamp"]:
            return block(confirmation_reason)
        before = [row for row in minute if _time(row["timestamp"]) < _time(touch["timestamp"])]
        if not before:
            return block("daily_impulse_unavailable")
        extreme = max(row["high"] for row in before) if direction == "LONG" else min(row["low"] for row in before)
        impulse = extreme - session["open"] if direction == "LONG" else session["open"] - extreme
        if impulse <= EPS:
            return block("directional_daily_impulse_unavailable")
        retracement = ((extreme - touch["low"]) / impulse if direction == "LONG"
                       else (touch["high"] - extreme) / impulse)
        result["correction"] = {"fraction": round(retracement, 8), "minimum": criteria["minimum_retracement"],
                                "extreme": extreme, "impulse": impulse, "touch": dict(touch)}
        if retracement + EPS < criteria["minimum_retracement"]:
            return block("daily_retracement_below_30_percent" if criteria["minimum_retracement"] == .30
                         else "daily_retracement_below_learned_threshold")
        since_touch = [row for row in minute if _time(row["timestamp"]) >= _time(touch["timestamp"])]
        changed = (max(row["high"] for row in since_touch) > extreme + EPS if direction == "LONG"
                   else min(row["low"] for row in since_touch) < extreme - EPS)
        if changed:
            return block("daily_impulse_changed_after_correction")
        zone = max(.0001, criteria["zone_atr"] * atr)
        entry_distance = criteria["maximum_entry_distance_atr"] * atr
        levels = _levels(completed, direction, zone, criteria["ma_periods"])
        if criteria["support_mode"] == "PRICE_REJECTION":
            levels = [{"kind": "CONFIRMED_INTRADAY_REJECTION",
                       "price": touch["low"] if direction == "LONG" else touch["high"]}]
        for level in levels:
            price = level["price"]
            if direction == "LONG":
                held = (price - zone - EPS <= touch["low"] <= price + zone + EPS
                        and touch["close"] >= price - EPS
                        and min(row["low"] for row in since_touch) >= price - zone - EPS
                        and confirmation["close"] > max(touch["high"], price + zone) + EPS
                        and confirmation["close"] > confirmation["open"])
                lower, upper = price + zone, min(price + entry_distance, extreme)
            else:
                held = (price - zone - EPS <= touch["high"] <= price + zone + EPS
                        and touch["close"] <= price + EPS
                        and max(row["high"] for row in since_touch) <= price + zone + EPS
                        and confirmation["close"] < min(touch["low"], price - zone) - EPS
                        and confirmation["close"] < confirmation["open"])
                lower, upper = max(price - entry_distance, extreme), price - zone
            if held and lower - EPS <= minute[-1]["close"] <= upper + EPS:
                result.update(
                    allowed=True, status="ALLOWED", reason="daily_correction_support_and_strong_signal_confirmed",
                    support={**level, "side": "SUPPORT" if direction == "LONG" else "RESISTANCE",
                             "zone_half_width": zone, "atr14_d1": atr},
                    confirmation=dict(confirmation),
                    entry_zone={"minimum": lower, "maximum": upper},
                    market_observed_at=_iso(_time(minute[-1]["timestamp"]) + timedelta(minutes=1)),
                    source="Yahoo Finance EURUSD=X D1 and completed 1m OHLC",
                    source_sha256=_digest({"completed_d1": completed, "active_d1_open": session["open"],
                                           "session_start": session["timestamp"], "completed_1m": minute}),
                )
                return result
        return block("post_loss_strong_support_or_held_retest_missing")
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return block("post_loss_invalid_market_or_signal_context:" + type(exc).__name__)


def check_execution(proof: Mapping[str, Any], execution: Mapping[str, Any]) -> str | None:
    """Recheck price and session after EPE; a stale quote cannot reuse a bounce."""
    if proof.get("required") is not True:
        return None
    if proof.get("schema_version") != SCHEMA or proof.get("allowed") is not True:
        return "post_loss_technical_admission_not_verified"
    try:
        if _digest(normalize_criteria(proof.get("criteria"))) != proof.get("criteria_sha256"):
            return "post_loss_execution_criteria_proof_invalid"
        price = _number(execution.get("fill_price"))
        zone = proof["entry_zone"]
        if not _number(zone["minimum"]) - EPS <= price <= _number(zone["maximum"]) + EPS:
            return "post_loss_execution_left_verified_support_zone"
        verified = _time(execution.get("verified_at"))
        source = _time((execution.get("selected_quote") or {}).get("timestamp"))
        session = proof["session"]
        confirmed = _time(proof["confirmation"]["end"])
        if not _time(session["start"]) <= verified < _time(session["end"]):
            return "post_loss_daily_session_changed_before_execution"
        if source < confirmed or source > verified or verified < confirmed:
            return "post_loss_execution_quote_predates_confirmation"
    except (ValueError, TypeError, KeyError, OverflowError):
        return "post_loss_execution_proof_missing"
    return None
