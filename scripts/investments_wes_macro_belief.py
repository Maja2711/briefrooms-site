#!/usr/bin/env python3
"""Bounded Belief Core -> WES EUR/USD macro bridge.

Belief Core remains the evidence authority. WES consumes only fresh EUR/USD
belief state and never parses macro releases itself.
"""
from __future__ import annotations
import json, os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

BELIEF_IDS = {
    "eurusd.trend.bullish": 0.45,
    "eurusd.usd_environment.supportive": 0.30,
    "eurusd.us_rates_pressure.supportive": 0.15,
    "eurusd.macro_surprise.supportive": 0.20,
    "eurusd.policy_differential.supportive": 0.10,
}
MAX_AGE_HOURS = 8.0
MAX_SCORE_CONTRIBUTION = 10.0
FAST_CONTEXT_PATH = Path("data/investments/eurusd_macro_fast_context.json")

def _dt(v: Any) -> Optional[datetime]:
    if not v: return None
    try:
        d=datetime.fromisoformat(str(v).replace("Z","+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except ValueError: return None

def _read(path: Path) -> Dict[str, Any]:
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return {}

def state_path() -> Optional[Path]:
    raw=os.environ.get("BELIEF_CORE_STATE","").strip()
    return Path(raw) if raw else None

def _fast_context(now: datetime) -> Dict[str, Any]:
    payload=_read(FAST_CONTEXT_PATH)
    if not payload:
        return {}
    event=payload.get("event") if isinstance(payload.get("event"),Mapping) else None
    if not event:
        return {}
    event_at=_dt(event.get("event_at"))
    if event_at is None:
        return {}
    hours=(event_at-now.astimezone(timezone.utc)).total_seconds()/3600.0
    if hours < -1.0 or hours > 2.0:
        return {}
    out=dict(payload)
    out["hours_until"]=round(hours,4)
    out["phase_now"]="PRE_RELEASE" if hours >= 0 else "POST_RELEASE"
    return out

def _calendar_context(path: Path, now: datetime) -> Dict[str, Any]:
    observations=path.parent / "observations.jsonl"
    upcoming=[]
    if observations.exists():
        try:
            for line in observations.read_text(encoding="utf-8").splitlines():
                if not line.strip(): continue
                row=json.loads(line)
                if row.get("adapter") != "macro_event_calendar" or row.get("metric") != "scheduled_macro_event": continue
                meta=row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
                if str(meta.get("importance") or "") != "high": continue
                event_at=_dt(meta.get("event_at"))
                if event_at is None: continue
                hours=(event_at-now.astimezone(timezone.utc)).total_seconds()/3600.0
                if -1.0 <= hours <= 24.0:
                    upcoming.append({"title":meta.get("title"),"event_at":meta.get("event_at"),"hours_until":round(hours,3),"source":row.get("source"),"source_ref":row.get("source_ref")})
        except (OSError, json.JSONDecodeError):
            return {"available":False,"status":"calendar_observations_unreadable","events":[]}
    upcoming.sort(key=lambda x:x["hours_until"])
    imminent=any(0.0 <= float(x["hours_until"]) <= 1.0 for x in upcoming)
    return {"available":bool(upcoming),"status":"high_impact_event_imminent" if imminent else "calendar_clear_or_not_imminent","imminent":imminent,"events":upcoming[:8],"source":"belief_macro_calendar_adapter"}


def context(now: datetime) -> Dict[str, Any]:
    path=state_path()
    fast=_fast_context(now)
    base={
        "enabled":True,
        "available":False,
        "score":0.0,
        "reason":"belief_state_unavailable",
        "source":"belief_core",
        "fast_context":fast,
    }
    if path is None or not path.exists():
        fast_score=float(fast.get("eurusd_score") or 0.0) if fast else 0.0
        if fast and fast.get("llm") and abs(fast_score) > 0.0:
            return {
                **base,
                "available":True,
                "score":round(max(-MAX_SCORE_CONTRIBUTION,min(MAX_SCORE_CONTRIBUTION,fast_score)),4),
                "direction":"long" if fast_score>0 else "short" if fast_score<0 else "neutral",
                "reason":"macro_fast_lane_only",
                "source":"macro_fast_lane",
            }
        return base
    state=_read(path)
    calendar=_calendar_context(path, now)
    rows={str(x.get("belief_id")):x for x in state.get("beliefs",[]) if isinstance(x,Mapping)}
    evidence_rows={str(x.get("evidence_id")):x for x in state.get("evidence",[]) if isinstance(x,Mapping)}
    used=[]; weighted=0.0; weight_sum=0.0
    latest_macro_evidence_at=None
    now_utc=now.astimezone(timezone.utc)
    for bid,w in BELIEF_IDS.items():
        row=rows.get(bid)
        if not row: continue
        evidence_ids=list(row.get("representative_evidence_ids") or [])
        evidence_times=[_dt((evidence_rows.get(str(eid)) or {}).get("observed_at")) for eid in evidence_ids]
        evidence_times=[x for x in evidence_times if x is not None]
        updated=max(evidence_times) if evidence_times else None
        if updated is None: continue
        age=(now_utc-updated).total_seconds()/3600.0
        if age < -0.1 or age > MAX_AGE_HOURS: continue
        try: p=float(row.get("probability")); conf=float(row.get("confidence"))
        except (TypeError,ValueError): continue
        directional=(p-0.5)*2.0
        effective=directional*max(0.0,min(1.0,conf))
        weighted += w*effective; weight_sum += w
        used.append({
            "belief_id":bid,
            "probability":round(p,6),
            "confidence":round(conf,6),
            "age_hours":round(age,3),
            "updated_at":updated.isoformat().replace("+00:00","Z"),
        })
        if bid in {"eurusd.macro_surprise.supportive","eurusd.policy_differential.supportive"}:
            if latest_macro_evidence_at is None or updated > latest_macro_evidence_at:
                latest_macro_evidence_at=updated
    recent_events=[
        row for row in (calendar.get("events") or [])
        if isinstance(row,Mapping)
        and -0.5 <= float(row.get("hours_until") or 999.0) < 0.0
    ]
    latest_recent_event=None
    if recent_events:
        latest_recent_event=max(recent_events,key=lambda row: float(row.get("hours_until") or -999.0))
    recent_event_at=_dt(latest_recent_event.get("event_at")) if latest_recent_event else None
    fast_post_ready=bool(
        fast
        and fast.get("phase_now") == "POST_RELEASE"
        and fast.get("post_release_ready") is True
        and fast.get("llm")
    )
    post_release_macro_evidence_fresh=bool(
        (
            recent_event_at is not None
            and latest_macro_evidence_at is not None
            and latest_macro_evidence_at >= recent_event_at
        )
        or fast_post_ready
    )
    release_context={
        "recent_high_impact_event":dict(latest_recent_event) if latest_recent_event else None,
        "latest_macro_evidence_at":(
            latest_macro_evidence_at.isoformat().replace("+00:00","Z")
            if latest_macro_evidence_at is not None else None
        ),
        "post_release_macro_evidence_fresh":post_release_macro_evidence_fresh,
        "post_release_guard_minutes":30,
        "fast_lane_post_release_ready":fast_post_ready,
    }
    fast_score=float(fast.get("eurusd_score") or 0.0) if fast and fast.get("llm") else 0.0
    if weight_sum <= 0:
        if abs(fast_score) > 0.0:
            score=max(-MAX_SCORE_CONTRIBUTION,min(MAX_SCORE_CONTRIBUTION,fast_score))
            return {**base,"available":True,"reason":"macro_fast_lane_only","score":round(score,4),
                    "direction":"long" if score>0 else "short" if score<0 else "neutral",
                    "beliefs":[],"freshness_max_hours":MAX_AGE_HOURS,
                    "score_cap":MAX_SCORE_CONTRIBUTION,"source":"macro_fast_lane",
                    "macro_calendar":calendar,"release_context":release_context,
                    "rule":"sourced EURUSD macro fast lane; no standalone execution authority"}
        return {**base,"reason":"no_fresh_eurusd_beliefs","macro_calendar":calendar,"release_context":release_context}
    normalized=weighted/weight_sum
    belief_score=max(-MAX_SCORE_CONTRIBUTION,min(MAX_SCORE_CONTRIBUTION,normalized*MAX_SCORE_CONTRIBUTION))
    score=fast_score if abs(fast_score) >= abs(belief_score) else belief_score
    score=max(-MAX_SCORE_CONTRIBUTION,min(MAX_SCORE_CONTRIBUTION,score))
    return {"enabled":True,"available":True,"score":round(score,4),
            "direction":"long" if score>0 else "short" if score<0 else "neutral",
            "beliefs":used,"freshness_max_hours":MAX_AGE_HOURS,
            "score_cap":MAX_SCORE_CONTRIBUTION,
            "source":"belief_core+macro_fast_lane" if abs(fast_score)>0.0 else "belief_core",
            "macro_calendar":calendar,
            "release_context":release_context,
            "fast_context":fast,
            "rule":"fresh bounded EURUSD beliefs with event fast lane; no standalone execution authority"}

def apply(macro: Dict[str, Any], belief: Dict[str, Any]) -> Dict[str, Any]:
    out=dict(macro); out["belief_core"]=belief; out["belief_macro_calendar"]=belief.get("macro_calendar") or {}
    if not belief.get("available") or out.get("data_quality")!="passed": return out
    base=float(out.get("score") or 0.0); cap=abs(float(out.get("score_cap") or 30.0)) or 30.0
    contribution=float(belief.get("score") or 0.0)
    out["price_macro_score_before_belief"]=round(base,4)
    out["belief_core_adjustment"]=round(contribution,4)
    out["score"]=round(max(-cap,min(cap,base+contribution)),4)
    out["direction"]="long" if out["score"]>0 else "short" if out["score"]<0 else "neutral"
    return out
