#!/usr/bin/env python3
"""DeepBook Predict shadow benchmark for BriefRooms Decision LAB.

Research-only. This module never writes Belief Core probabilities and has no
execution authority. It stores external market-implied probabilities so their
incremental information can be tested prospectively before any promotion.
"""
from __future__ import annotations
import json, math, os, urllib.request
from datetime import datetime, timezone
from pathlib import Path

OUT = Path("data/investments/deepbook_predict_shadow.json")
URL = os.getenv("DEEPBOOK_PREDICT_SHADOW_URL", "").strip()
UA = "BriefRooms-DeepBookShadow/1.0 (+research-only)"

def iso_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")

def clamp01(x):
    x=float(x)
    if not math.isfinite(x): raise ValueError("non-finite probability")
    return max(0.0,min(1.0,x))

def normalize_quote(row):
    """Normalize an upstream range-digital quote.

    DeepBook Predict 1x binary/range premium per unit notional is the model's
    risk-neutral event probability. Fees/all-in execution cost are deliberately
    excluded: this benchmark measures the pricing surface, not trading cost.
    """
    p=row.get("probability", row.get("premium_per_notional", row.get("mark_probability")))
    if p is None: raise ValueError("quote lacks probability/premium_per_notional")
    return {
      "asset": str(row.get("asset","BTC")).upper(),
      "expiry": row.get("expiry"),
      "lower": row.get("lower"),
      "higher": row.get("higher"),
      "probability": clamp01(p),
      "observed_at": row.get("observed_at") or iso_now(),
      "source_ref": row.get("source_ref"),
    }

def fetch():
    if not URL:
        return None, "unconfigured"
    req=urllib.request.Request(URL,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=20) as resp:
        payload=json.load(resp)
    rows=payload.get("quotes",payload) if isinstance(payload,(dict,list)) else []
    if not isinstance(rows,list): raise ValueError("expected list or {quotes:[...]}")
    return [normalize_quote(x) for x in rows if isinstance(x,dict)], "ok"

def load():
    if not OUT.exists(): return {"schema_version":"deepbook_predict_shadow_v1","snapshots":[]}
    return json.loads(OUT.read_text(encoding="utf-8"))

def main():
    rows,status=fetch()
    state=load()
    state["updated_at"]=iso_now()
    state["mode"]="shadow_research"
    state["visible_in_decision_lab"]=False
    state["belief_core_authority"]=False
    state["execution_authority"]=False
    state["automatic_promotion"]=False
    state["source_status"]=status
    if rows is not None:
        state.setdefault("snapshots",[]).append({"captured_at":iso_now(),"quotes":rows})
        state["snapshots"]=state["snapshots"][-5000:]
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(state,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":status,"quotes":0 if rows is None else len(rows),"output":str(OUT)}))

if __name__=="__main__":
    main()
