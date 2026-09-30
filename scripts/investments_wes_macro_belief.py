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
    "eurusd.us_rates_pressure.supportive": 0.25,
}
MAX_AGE_HOURS = 8.0
MAX_SCORE_CONTRIBUTION = 10.0

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

def context(now: datetime) -> Dict[str, Any]:
    path=state_path()
    base={"enabled":True,"available":False,"score":0.0,"reason":"belief_state_unavailable","source":"belief_core"}
    if path is None or not path.exists(): return base
    state=_read(path)
    rows={str(x.get("belief_id")):x for x in state.get("beliefs",[]) if isinstance(x,Mapping)}
    evidence_rows={str(x.get("evidence_id")):x for x in state.get("evidence",[]) if isinstance(x,Mapping)}
    used=[]; weighted=0.0; weight_sum=0.0
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
        used.append({"belief_id":bid,"probability":round(p,6),"confidence":round(conf,6),"age_hours":round(age,3)})
    if weight_sum <= 0:
        return {**base,"reason":"no_fresh_eurusd_beliefs"}
    normalized=weighted/weight_sum
    score=max(-MAX_SCORE_CONTRIBUTION,min(MAX_SCORE_CONTRIBUTION,normalized*MAX_SCORE_CONTRIBUTION))
    return {"enabled":True,"available":True,"score":round(score,4),
            "direction":"long" if score>0 else "short" if score<0 else "neutral",
            "beliefs":used,"freshness_max_hours":MAX_AGE_HOURS,
            "score_cap":MAX_SCORE_CONTRIBUTION,"source":"belief_core",
            "rule":"fresh bounded EURUSD beliefs only; no standalone execution authority"}

def apply(macro: Dict[str, Any], belief: Dict[str, Any]) -> Dict[str, Any]:
    out=dict(macro); out["belief_core"]=belief
    if not belief.get("available") or out.get("data_quality")!="passed": return out
    base=float(out.get("score") or 0.0); cap=abs(float(out.get("score_cap") or 30.0)) or 30.0
    contribution=float(belief.get("score") or 0.0)
    out["price_macro_score_before_belief"]=round(base,4)
    out["belief_core_adjustment"]=round(contribution,4)
    out["score"]=round(max(-cap,min(cap,base+contribution)),4)
    out["direction"]="long" if out["score"]>0 else "short" if out["score"]<0 else "neutral"
    return out
