"""EURUSD Daily paper: as-of H1/D1/W1 strategy and volatility snapshot.

Belief Core alone owns direction; W1 has context only. No trade routing.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from math import isfinite
from statistics import median
from typing import Any, Mapping

PIP = 0.0001
VERSION = "MTF_H1_REGIME_V1"

def evaluate(h1_halves, daily_bars, h1, d1, direction, at):
    base = {"version": VERSION, "available": False, "h1_regime": "UNVERIFIED",
            "strategy": "NO_NEW_ADMISSION", "direction_authority": False,
            "w1": {"available": False, "trend": "UNVERIFIED"},
            "h1_volatility": {"available": False},
            "d1": {"trend": "UNVERIFIED", "strong": False}}
    if at.tzinfo is None:
        return {**base, "reason": "no_utc_clock"}
    now = at.astimezone(timezone.utc)
    hours = defaultdict(dict)
    try:
        for b in h1_halves:
            t = b.timestamp.astimezone(timezone.utc)
            if t.minute not in (0,30) or t.second or t.microsecond or t + timedelta(minutes=30) > now:
                continue
            if not all(isfinite(float(v)) and float(v) > 0 for v in (b.open,b.high,b.low,b.close)):
                continue
            if float(b.low) > min(float(b.open),float(b.close)) or float(b.high) < max(float(b.open),float(b.close)):
                continue
            hours[t.replace(minute=0,second=0,microsecond=0)][t.minute] = b
    except (ValueError,TypeError,AttributeError):
        return {**base, "reason": "invalid_h1_source"}
    complete = []
    for t,pair in sorted(hours.items()):
        if set(pair) != {0,30} or t+timedelta(hours=1)>now:
            continue
        complete.append({"end":t+timedelta(hours=1),"high":max(float(pair[0].high),float(pair[30].high)),
                         "low":min(float(pair[0].low),float(pair[30].low)),"close":float(pair[30].close)})
    if len(complete)<34 or (now-complete[-1]["end"]).total_seconds()>5400:
        return {**base, "reason": "missing_or_stale_closed_h1", "completed_h1":len(complete)}
    span = complete[-24:]
    hl = sorted((row["high"]-row["low"])/PIP for row in span)
    avg = sum(hl)/len(hl)
    if not .5<=avg<=200:
        return {**base, "reason": "implausible_h1_range"}
    prior=complete[-25]["close"]
    true_ranges=[]
    for row in span:
        true_ranges.append((max(row["high"],prior)-min(row["low"],prior))/PIP)
        prior=row["close"]
    vol={"available":True,"mean_high_low_pips":round(avg,3),
         "median_high_low_pips":round(median(hl),3),
         "p75_high_low_pips":round(hl[int(.75*(len(hl)-1))],3),
         "atr_true_range_pips":round(sum(true_ranges)/len(true_ranges),3),
         "sample_closed_hours":24,"last_completed_h1":complete[-1]["end"].isoformat(),
         "last_h1_high":complete[-1]["high"],"last_h1_low":complete[-1]["low"],
         "executable_spread_proven":False}
    valid_days=[]
    try:
        for b in sorted(daily_bars,key=lambda x:x.timestamp):
            t=b.timestamp.astimezone(timezone.utc)
            day=t.replace(hour=0,minute=0,second=0,microsecond=0)
            if day+timedelta(days=1)<=now and isfinite(float(b.close)) and float(b.close)>0:
                valid_days.append((day,float(b.close)))
    except (ValueError,TypeError,AttributeError):
        pass
    weeks=defaultdict(list)
    for day,close in valid_days:
        monday=day-timedelta(days=day.weekday())
        weeks[monday].append(close)
    w1_closes=[(week+timedelta(days=7),v[-1]) for week,v in sorted(weeks.items())
               if week+timedelta(days=7)<=now and len(v)>=3]
    weekview=base["w1"]
    if len(w1_closes)>=8 and (now-w1_closes[-1][0]).days<=14:
        values=[v for _,v in w1_closes]
        current=sum(values[-4:])/4
        previous=sum(values[-5:-1])/4
        delta=(current-previous)/PIP
        wtrend=("UP" if delta>2 and values[-1]>current else
                "DOWN" if delta< -2 and values[-1]<current else "MIXED")
        weekview={"available":True,"trend":wtrend,"last_closed_w1":w1_closes[-1][0].isoformat(),
                  "sma4_change_pips":round(delta,3),"veto_authority":False}
    dtrend=str(d1.get("trend") or "UNVERIFIED") if d1.get("available") is True else "UNVERIFIED"
    slope=(float(d1.get("middle_slope") or 0)/PIP) if dtrend!="UNVERIFIED" else 0
    pb=(float(d1.get("percent_b") or .5)) if dtrend!="UNVERIFIED" else .5
    strong=dtrend=="UP" and slope>=1.5 and pb>=.65 or dtrend=="DOWN" and slope<=-1.5 and pb<=.35
    daily={"trend":dtrend,"strong":bool(strong),"middle_slope_pips_per_day":round(slope,3),
           "last_completed_d1":d1.get("last_completed_d1")}
    regime="RANGE" if h1.get("range_structure") is True else (
        "STRONG_TREND" if h1.get("strong_trend_confirmed") is True else
        "TREND" if h1.get("regime")=="TREND" and h1.get("allowed") is True else "TRANSITION")
    opposing=bool(strong and direction in ("LONG","SHORT") and
                  dtrend!=("UP" if direction=="LONG" else "DOWN"))
    if direction not in ("LONG","SHORT"):
        strategy="NO_BELIEF_SIGNAL"
    elif regime=="RANGE":
        strategy="RANGE_EDGE" if (h1.get("range_edge_entry") or {}).get("allowed") is True else "WAIT_RANGE_EDGE"
    elif regime=="STRONG_TREND":
        strategy="WAIT_D1_CONFLICT" if opposing else "STRONG_TREND"
    elif regime=="TREND":
        strategy="WAIT_D1_CONFLICT" if opposing else "TREND_CONTINUATION"
    else:
        strategy="WAIT_TRANSITION"
    return {**base,"available":True,"h1_regime":regime,"strategy":strategy,
            "direction":direction,"d1":daily,"w1":weekview,"h1_volatility":vol,
            "block_new_entry":bool(h1.get("available") is True and strategy.startswith("WAIT_")),
            "position_management":"TREND_H1_VOLATILITY" if strategy=="STRONG_TREND" else
                                  "RANGE_H1_REVERSION" if strategy=="RANGE_EDGE" else "STANDARD_R_PACE",
            "roles":{"H1":"PRIMARY","D1":"SECONDARY","W1":"OVERVIEW_ONLY"}}


# Predeclared, conservative location limits inside the closed-H1 Bollinger band.
# The H1 trigger is deliberately *not* a signal to sell below its lower band
# or buy above its upper band simply because D1 has touched an outer band.
H1_TREND_SHORT_MIN_PERCENT_B = 0.12
H1_TREND_LONG_MAX_PERCENT_B = 0.88
H1_PRIORITY_MAX_AGE_MINUTES = 90


def strong_h1_entry_priority(h1, mtf, d1, d1_touch, direction, observed_at):
    """Replace D1 50% retracement by H1 location only for a verified strong trend.

    The touch memory is NEVER erased. If H1 ceases to be strongly aligned,
    the original D1 cooldown remains authoritative. Other admission, Belief,
    calendar, re-entry, EPE and risk requirements remain untouched.
    """
    base = {
        "schema_version": "eurusd-daily-h1-strong-trend-primary-v1",
        "timeframe": "H1",
        "mode": "H1_PRIMARY_STRONG_TREND",
        "active": False,
        "replaces_d1_touch_veto": False,
        "entry_location_allowed": False,
        "status": "INELIGIBLE",
        "reason": "strong_h1_evidence_not_qualified",
        "short_min_percent_b": H1_TREND_SHORT_MIN_PERCENT_B,
        "long_max_percent_b": H1_TREND_LONG_MAX_PERCENT_B,
        "direction_authority": False,
        "execution_authority": False,
        "risk_override_authority": False,
        "d1_touch_state_changed": False,
    }
    if direction not in ("LONG", "SHORT") or observed_at.tzinfo is None:
        return base
    if (h1.get("available") is not True
            or h1.get("allowed") is not True
            or h1.get("strong_trend_confirmed") is not True
            or h1.get("exhaustion") is True
            or h1.get("range_regime") is True
            or mtf.get("available") is not True
            or mtf.get("strategy") != "STRONG_TREND"
            or mtf.get("block_new_entry") is True
            or mtf.get("direction") != direction
            or d1.get("available") is not True
            or d1.get("trend") != ("UP" if direction == "LONG" else "DOWN")
            or d1_touch.get("available") is not True
            or d1_touch.get("processing_error")):
        return base
    try:
        h1_at = datetime.fromisoformat(str(h1["last_closed_h1"]).replace("Z", "+00:00"))
        mtf_at = datetime.fromisoformat(str(mtf["h1_volatility"]["last_completed_h1"]).replace("Z", "+00:00"))
        d1_at = datetime.fromisoformat(str(d1["last_completed_d1"]).replace("Z", "+00:00"))
        touch_d1_at = datetime.fromisoformat(str(d1_touch["last_completed_d1"]).replace("Z", "+00:00"))
        now = observed_at.astimezone(timezone.utc)
        if (any(x.tzinfo is None for x in (h1_at, mtf_at, d1_at, touch_d1_at))
                or h1_at != mtf_at
                or d1_at != touch_d1_at
                or not 0 <= (now - h1_at).total_seconds() <= H1_PRIORITY_MAX_AGE_MINUTES * 60
                or not 0 <= (now - d1_at).total_seconds() <= 4 * 86400):
            return {**base, "reason": "h1_d1_timestamp_mismatch_or_stale"}
        position = float(h1["percent_b"])
        if not isfinite(position):
            raise ValueError("non-finite H1 position")
    except (ValueError, TypeError, KeyError, OverflowError):
        return {**base, "reason": "invalid_h1_location_or_source_time"}

    # An extreme H1 band position is *not* a qualified trend entry even if
    # the higher-timeframe trend and Belief agree. Wait for a modest H1 pullback,
    # rather than demanding the much larger D1 50% return.
    location_ok = (position >= H1_TREND_SHORT_MIN_PERCENT_B if direction == "SHORT"
                   else position <= H1_TREND_LONG_MAX_PERCENT_B)
    return {
        **base,
        "active": True,
        "replaces_d1_touch_veto": True,
        "entry_location_allowed": location_ok,
        "status": "H1_PRIMARY_READY" if location_ok else "H1_PRIMARY_WAIT_PULLBACK",
        "reason": "strong_h1_trend_location_confirmed" if location_ok else "h1_outer_band_overextension",
        "h1_percent_b": position,
        "h1_closed_at": h1_at.astimezone(timezone.utc).isoformat(),
        "d1_closed_at": d1_at.astimezone(timezone.utc).isoformat(),
        "d1_trend_context": d1.get("trend"),
        "d1_touch_preserved": True,
    }



# Diagnostic support-vote thresholds are predeclared, not fitted to the
# small loss-only recent cohort. The four groups are diverse evidence families,
# not statistically independent variables; historical association is not causal.
MIN_NON_BAND_FACTOR_CONFIRMATIONS = 3
MULTIFACTOR_INDEPENDENT_GROUPS = (
    "BELIEF_QUALITY", "H1_RAW_PRICE_ACTION", "HIGHER_TIMEFRAME",
    "PROSPECTIVE_REWARD_RISK",
)


def multifactor_entry_review(final, h1, d1, mtf, risk_plan, h1_priority,
                             reversal, direction, entry, stop, target, at):
    """One auditable, non-directional strong-H1 admission review.

    Bollinger location NEVER contributes to the independent evidence quorum.
    Its pre-existing anti-chase/outer-band safety is separate and preserved.
    No estimated accuracy, learned weights, or historical outcome leaks.
    """
    result = {
        "schema_version": "eurusd-daily-multifactor-entry-v1",
        "mode": "FIXED_UNCALIBRATED_PROSPECTIVE_BASELINE",
        "as_of": at.astimezone(timezone.utc).isoformat() if at.tzinfo else None,
        "native_direction": direction,
        "regime": mtf.get("strategy"),
        "minimum_non_band_confirmations": MIN_NON_BAND_FACTOR_CONFIRMATIONS,
        "non_band_factor_groups": list(MULTIFACTOR_INDEPENDENT_GROUPS),
        "bollinger_vote_in_non_band_quorum": False,
        "direction_authority": False, "execution_authority": False,
        "change_existing_position": False, "automatic_promotion": False,
        "factors": {}, "enforce": False, "status": "NOT_EVALUATED",
        "non_band_passed": 0, "non_band_available": 0,
        "evidence_quality": "UNVERIFIED",
    }
    if direction not in {"LONG", "SHORT"}:
        return {**result, "status": "NO_NATIVE_DIRECTION"}
    sign = 1 if direction == "LONG" else -1
    factor_rows = {}
    used = final.get("used_beliefs") or []
    try:
        score = float(final["score"])
        confidence = float(final["confidence"])
        coverage = float(final["coverage_weight"])
        ready = all(isfinite(x) for x in (score, confidence, coverage))
        if not ready:
            score, confidence, coverage = None, None, None
        belief_confirmed = (ready and abs(score - 50.0) >= 4.0
                            and confidence >= .30 and coverage >= .55
                            and isinstance(used, list) and len(used) >= 3
                            and (score > 50 if sign == 1 else score < 50))
    except (ValueError, KeyError, TypeError):
        ready, belief_confirmed, score, confidence, coverage = False, False, None, None, None
    factor_rows["BELIEF_QUALITY"] = {
        "available": ready and isinstance(used, list) and len(used) > 0,
        "confirmed": bool(belief_confirmed),
        "source": "NATIVE_BELIEF_FIRST",
        "score": score, "confidence": confidence, "coverage": coverage,
        "qualified_beliefs": len(used) if isinstance(used, list) else 0,
        "criterion": "score_distance_4_confidence_0.30_coverage_0.55_3_beliefs",
    }
    raw = h1.get("raw_h1_price_action") or {}
    try:
        impulse = float(raw["directional_3h_pips"])
        aligned = int(raw["aligned_intervals_3h"])
        raw_ready = (raw.get("direction") == direction
                     and raw.get("source") == "COMPLETED_H1_RAW_CLOSES"
                     and isfinite(impulse) and 0 <= aligned <= 3)
        if not isfinite(impulse):
            impulse = None
    except (ValueError, TypeError, KeyError):
        impulse, aligned, raw_ready = None, None, False
    factor_rows["H1_RAW_PRICE_ACTION"] = {
        "available": raw_ready,
        "confirmed": bool(raw_ready and impulse >= 1.0 and aligned >= 1),
        "source": "LAST_3_COMPLETED_H1_CLOSES",
        "directional_3h_pips": impulse, "aligned_intervals_3h": aligned,
        "criterion": "directional_3h_move_at_least_1pip_and_one_aligned_hour",
    }
    expected = "UP" if direction == "LONG" else "DOWN"
    w1 = mtf.get("w1") or {}
    d1_ok = d1.get("available") is True and d1.get("trend") in {"UP", "DOWN", "MIXED"}
    w1_available = w1.get("available") is True and w1.get("trend") in {"UP", "DOWN", "MIXED"}
    factor_rows["HIGHER_TIMEFRAME"] = {
        "available": d1_ok,
        "confirmed": bool(d1_ok and d1.get("trend") == expected
                          and (not w1_available or w1.get("trend") in {expected, "MIXED"})),
        "source": "COMPLETED_D1_AND_COMPLETED_W1_CONTEXT",
        "d1_trend": d1.get("trend"), "w1_trend": w1.get("trend"),
        "w1_verified": w1_available,
        "criterion": "d1_aligned_and_no_verified_opposite_w1",
    }
    try:
        e, sl, tp = float(entry), float(stop), float(target)
        risk = sign * (e - sl) / PIP
        reward = sign * (tp - e) / PIP
        rr = reward / risk if risk > 0 else None
        rr_available = (all(isfinite(x) for x in (e, sl, tp, risk, reward))
                        and risk > 0 and reward > 0 and rr is not None)
    except (ValueError, TypeError, ZeroDivisionError):
        risk, reward, rr, rr_available = None, None, None, False
    factor_rows["PROSPECTIVE_REWARD_RISK"] = {
        "available": bool(rr_available),
        "confirmed": bool(rr_available and risk >= 5.0 and rr >= 1.20
                          and risk_plan.get("allowed") is not False),
        "source": "DECISION_TIME_INITIAL_RISK_GEOMETRY_NOT_EXECUTION",
        "initial_risk_pips": risk if rr_available else None,
        "initial_reward_pips": reward if rr_available else None,
        "reward_risk": round(rr, 4) if rr_available else None,
        "criterion": "risk_at_least_5pips_reward_to_risk_at_least_1.20",
    }
    h1_qualified = h1.get("available") is True and h1.get("allowed") is True
    factor_rows["BOLLINGER_H1_LOCATION"] = {
        "available": h1_qualified and h1_priority.get("active") is True,
        "confirmed": bool(h1_priority.get("entry_location_allowed") is True),
        "source": "COMPLETED_H1_BOLLINGER",
        "percent_b": h1.get("percent_b"),
        "criterion": "qualified_h1_primary_entry_location",
        "role": "SUPPORT_ONLY_NOT_AN_INDEPENDENT_VOTE",
    }
    factor_rows["OPPOSITE_REVERSAL_RISK"] = {
        "available": reversal.get("status") != "UNVERIFIED",
        "confirmed": reversal.get("h1_reversal_confirmed") is not True,
        "source": "H1_BOTH_SIDES_D1_CONTEXT",
        "opposing_hypothesis": reversal.get("hypothesis"),
        "role": "CONTRADICTION_WARNING_NOT_DIRECTION_AUTHORITY",
    }
    passed = sum(factor_rows[x]["available"] and factor_rows[x]["confirmed"]
                 for x in MULTIFACTOR_INDEPENDENT_GROUPS)
    available = sum(factor_rows[x]["available"] for x in MULTIFACTOR_INDEPENDENT_GROUPS)
    active = mtf.get("strategy") == "STRONG_TREND" and h1_qualified
    # Strong H1 must NEVER silently fall through because a newly required
    # source is missing: incomplete multifactor proof is a fail-closed veto.
    enforce = bool(active)
    complete = available == len(MULTIFACTOR_INDEPENDENT_GROUPS)
    status = ("MULTIFACTOR_EVIDENCE_UNVERIFIED" if active and not complete
              else "WAIT_MULTIFACTOR" if active and passed < MIN_NON_BAND_FACTOR_CONFIRMATIONS
              else "MULTIFACTOR_CONFIRMED" if active
              else "OBSERVATION_ONLY_OTHER_REGIME")
    return {
        **result, "factors": factor_rows,
        "enforce": enforce, "status": status,
        "non_band_passed": passed, "non_band_available": available,
        "evidence_quality": "COMPLETE" if available == 4 else "PARTIAL",
        "technical_context_may_veto": True,
        "promotion_eligibility": "NOT_CALIBRATED_NO_AUTOMATIC_PROMOTION",
        "model_success_rate": None,
    }


def geometry(policy, entry, stop, target, direction, h1):
    """Entry-only prospective sizing: never increase original hard SL distance."""
    unchanged={"applied":False,"allowed":True,"reason":"legacy_geometry"}
    name=policy.get("strategy")
    if name not in ("STRONG_TREND","RANGE_EDGE") or not policy.get("available"):
        return unchanged
    try:
        sign=1 if direction=="LONG" else -1
        start=float(entry); risk=sign*(start-float(stop))/PIP
        reward=sign*(float(target)-start)/PIP
        vol=policy["h1_volatility"]
        avg=float(vol["mean_high_low_pips"])
        typical=float(vol["median_high_low_pips"])
        if risk<5 or reward<=0 or not all(isfinite(x) for x in (start,risk,reward,avg,typical)):
            raise ValueError("invalid_input")
        if name=="STRONG_TREND":
            new_risk=min(risk,max(10.,1.5*avg,1.2*float(vol["atr_true_range_pips"])))
            new_reward=min(reward,1.8*new_risk)
        else:
            band=float(h1["lower"] if sign==1 else h1["upper"])
            midpoint=float(h1["middle"])
            width=float(h1["upper"])-float(h1["lower"])
            new_risk=min(risk,max(6.,1.15*typical,abs(start-band)/PIP+.6*avg))
            new_reward=min(reward,sign*(midpoint+sign*.20*width-start)/PIP)
            if new_reward<max(7.,1.05*new_risk):
                return {**unchanged,"allowed":False,"reason":"range_reward_below_volatility_risk"}
        proposed_stop=round(start-sign*new_risk*PIP,5)
        proposed_target=round(start+sign*new_reward*PIP,5)
        if sign*(start-proposed_stop)/PIP > risk+.051:
            raise ValueError("hard_stop_widened")
        return {"applied":True,"allowed":True,"strategy":name,
                "stop":proposed_stop,"target":proposed_target,
                "new_risk_pips":round(new_risk,3),
                "new_reward_pips":round(new_reward,3),
                "previous_max_risk_pips":round(risk,3),
                "hard_stop_widened":False}
    except (TypeError,ValueError,KeyError,ZeroDivisionError):
        return {**unchanged,"allowed":False,"reason":"invalid_volatility_geometry"}


def review_band_reversal(
    native_direction: str,
    h1_long: Mapping[str, Any],
    h1_short: Mapping[str, Any],
    d1: Mapping[str, Any],
    observed_at: datetime,
) -> dict[str, Any]:
    """Compare both closed-H1 directions with an extended D1 band, audit-only.

    A downtrend touching its lower D1 band is a LONG *hypothesis*, never a
    trading instruction. The opposing H1 evaluation is independent of the
    native Belief direction and cannot vote in Belief, override admission or
    manufacture a paper fill. All evidence is as-of the current decision.
    """
    if observed_at.tzinfo is None:
        raise ValueError("reversal review needs timezone-aware decision time")
    result: dict[str, Any] = {
        "schema_version": "eurusd-daily-band-reversal-review-v1",
        "mode": "RESEARCH_ONLY",
        "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
        "native_belief_direction": native_direction,
        "hypothesis": None,
        "status": "UNVERIFIED",
        "direction_authority": False,
        "admission_authority": False,
        "execution_authority": False,
        "historical_fill_inference": False,
        "automatic_promotion": False,
        "requires_new_belief_decision": True,
    }
    if (d1.get("available") is not True or h1_long.get("available") is not True
            or h1_short.get("available") is not True):
        return {**result, "reason": "missing_verified_d1_or_both_h1_directions"}
    h1_long_at, h1_short_at = h1_long.get("last_closed_h1"), h1_short.get("last_closed_h1")
    d1_at = d1.get("last_completed_d1")
    try:
        long_stamp = datetime.fromisoformat(str(h1_long_at).replace("Z", "+00:00"))
        short_stamp = datetime.fromisoformat(str(h1_short_at).replace("Z", "+00:00"))
        daily_stamp = datetime.fromisoformat(str(d1_at).replace("Z", "+00:00"))
        if (long_stamp.tzinfo is None or short_stamp.tzinfo is None
                or daily_stamp.tzinfo is None or long_stamp != short_stamp
                or long_stamp > observed_at or daily_stamp > observed_at):
            raise ValueError("invalid_source_time")
    except (ValueError, TypeError):
        return {**result, "reason": "inconsistent_or_future_h1_d1_timestamps"}
    result.update(
        status="NO_D1_BAND_EXTENSION",
        reason="no_directional_d1_outer_band_extension",
        source_last_completed_h1=long_stamp.astimezone(timezone.utc).isoformat(),
        source_last_completed_d1=daily_stamp.astimezone(timezone.utc).isoformat(),
        d1_trend=d1.get("trend"),
        d1_percent_b=d1.get("percent_b"),
        h1_long_allowed=h1_long.get("allowed") is True,
        h1_short_allowed=h1_short.get("allowed") is True,
    )
    extended_down = d1.get("trend") == "DOWN" and d1.get("near_lower_band") is True
    extended_up = d1.get("trend") == "UP" and d1.get("near_upper_band") is True
    if not extended_down and not extended_up:
        return result
    hypothesis = "LONG" if extended_down else "SHORT"
    reversal_h1 = h1_long if extended_down else h1_short
    prior_h1 = h1_short if extended_down else h1_long
    h1_rejection = prior_h1.get("exhaustion") is True
    h1_confirmed = reversal_h1.get("allowed") is True
    status = ("H1_REVERSAL_CONFIRMED_RECHECK_BELIEF" if h1_confirmed
              else "H1_EXHAUSTION_BUT_REVERSAL_UNCONFIRMED" if h1_rejection
              else "D1_EXTENSION_WITHOUT_H1_REVERSAL")
    return {
        **result,
        "hypothesis": hypothesis,
        "status": status,
        "reason": ("native_belief_must_independently_support_reversal_before_entry"
                   if h1_confirmed else "no_verified_opposite_h1_admission"),
        "native_belief_supports_hypothesis": native_direction == hypothesis,
        "h1_reversal_confirmed": h1_confirmed,
        "h1_prior_direction_exhaustion": h1_rejection,
        "h1_reversal_reason": reversal_h1.get("reason"),
        "h1_prior_direction_reason": prior_h1.get("reason"),
        "h1_percent_b": reversal_h1.get("percent_b"),
        "d1_close": d1.get("close"),
        "d1_lower": d1.get("lower"),
        "d1_upper": d1.get("upper"),
    }
