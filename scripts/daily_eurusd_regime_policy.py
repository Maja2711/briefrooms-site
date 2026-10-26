"""EURUSD Daily paper: as-of H1/D1/W1 strategy and volatility snapshot.

Belief Core alone owns direction; W1 has context only. No trade routing.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from math import isfinite
from statistics import median

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
