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

def _fast_calendar_context(fast: Mapping[str, Any]) -> Dict[str, Any]:
    event=fast.get("event") if isinstance(fast.get("event"),Mapping) else None
    if not event:
        return {"available":False,"status":"calendar_clear_or_not_imminent","imminent":False,"events":[],"source":"macro_fast_lane"}
    try:
        hours=float(fast.get("hours_until"))
    except (TypeError,ValueError):
        return {"available":False,"status":"calendar_clear_or_not_imminent","imminent":False,"events":[],"source":"macro_fast_lane"}
    row={
        "title":event.get("title"),
        "event_at":event.get("event_at"),
        "hours_until":round(hours,3),
        "source":event.get("source"),
        "source_ref":event.get("source_ref"),
    }
    imminent=0.0 <= hours <= 1.0
    return {
        "available":True,
        "status":"high_impact_event_imminent" if imminent else "calendar_clear_or_not_imminent",
        "imminent":imminent,
        "events":[row],
        "source":"macro_fast_lane",
    }


def _merge_calendar(primary: Mapping[str, Any], fast: Mapping[str, Any]) -> Dict[str, Any]:
    fast_calendar=_fast_calendar_context(fast)
    rows=[]
    seen=set()
    for source in (primary,fast_calendar):
        for row in source.get("events") or []:
            if not isinstance(row,Mapping):
                continue
            key=(str(row.get("event_at") or ""),str(row.get("title") or ""))
            if key in seen:
                continue
            seen.add(key)
            rows.append(dict(row))
    rows.sort(key=lambda row: float(row.get("hours_until") or 999.0))
    imminent=any(0.0 <= float(row.get("hours_until") or 999.0) <= 1.0 for row in rows)
    return {
        "available":bool(rows),
        "status":"high_impact_event_imminent" if imminent else "calendar_clear_or_not_imminent",
        "imminent":imminent,
        "events":rows[:8],
        "source":"belief_macro_calendar_adapter+macro_fast_lane" if primary.get("events") and fast_calendar.get("events") else (
            primary.get("source") if primary.get("events") else fast_calendar.get("source")
        ),
    }


def _release_context(
    calendar: Mapping[str, Any],
    fast: Mapping[str, Any],
    latest_macro_evidence_at: Optional[datetime],
) -> Dict[str, Any]:
    release_context=_release_context(calendar,fast,latest_macro_evidence_at)
    fast_score=float(fast.get("eurusd_score") or 0.0) if fast and fast.get("llm") else 0.0
        if fast and fast.get("llm") and abs(fast_score) > 0.0:
            return {
                **base,
                "available":True,
                "score":round(max(-MAX_SCORE_CONTRIBUTION,min(MAX_SCORE_CONTRIBUTION,fast_score)),4),
                "direction":"long" if fast_score>0 else "short" if fast_score<0 else "neutral",
                "reason":"macro_fast_lane_only",
                "source":"macro_fast_lane",
                "macro_calendar":calendar,
                "release_context":release_context,
            }
        return {**base,"macro_calendar":calendar,"release_context":release_context}
    state=_read(path)
    calendar=_merge_calendar(_calendar_context(path, now),fast)
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
