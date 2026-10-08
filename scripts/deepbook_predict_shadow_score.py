#!/usr/bin/env python3
"""Settle and score DeepBook Predict Shadow observations.

Read-only benchmark. Settlement uses the first Coinbase BTC-USD 1-minute candle
whose close is at/after expiry. It never writes to Belief Core, trading policy,
execution, or promotion state.
"""
from __future__ import annotations
import json, math, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

P=Path("data/investments/deepbook_predict_shadow.json")
UA="BriefRooms-DeepBookValidation/1.0"
GRACE_MS=60_000

def brier(p,y): return (float(p)-float(y))**2
def iso_ms(ms): return datetime.fromtimestamp(ms/1000,timezone.utc).isoformat().replace("+00:00","Z")

def coinbase_settlement(expiry_ms):
    start=(expiry_ms//60_000)*60_000
    end=start+120_000
    qs=urllib.parse.urlencode({"start":iso_ms(start),"end":iso_ms(end),"granularity":60})
    req=urllib.request.Request("https://api.exchange.coinbase.com/products/BTC-USD/candles?"+qs,
        headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=20) as resp:
        rows=json.load(resp)
    candles=sorted((r for r in rows if isinstance(r,list) and len(r)>=5),key=lambda r:r[0])
    for r in candles:
        candle_start_ms=int(r[0])*1000
        candle_end_ms=candle_start_ms+60_000
        if candle_end_ms>=expiry_ms:
            return {"price":float(r[4]),"observed_at":iso_ms(candle_end_ms),
                    "source":"coinbase_exchange_BTC-USD_1m_close",
                    "rule":"first_1m_candle_close_at_or_after_expiry"}
    return None

def unique_expiries(state):
    out=set()
    for batch in state.get("snapshots",[]):
        for q in batch.get("quotes",[]):
            if q.get("up_probability") is not None and q.get("expiry_ms") is not None:
                out.add(int(q["expiry_ms"]))
    return sorted(out)

def settle_due(state,now_ms=None):
    now_ms=int(now_ms if now_ms is not None else time.time()*1000)
    settlements=state.setdefault("settlements",{})
    errors=state.setdefault("settlement_errors",{})
    added=0
    for expiry in unique_expiries(state):
        key=str(expiry)
        if key in settlements or expiry+GRACE_MS>now_ms: continue
        try:
            row=coinbase_settlement(expiry)
            if row:
                settlements[key]=row; errors.pop(key,None); added+=1
            else:
                errors[key]="no_candle"
        except Exception as e:
            errors[key]=str(e)[:240]
    return added

def settlement_price(value):
    if isinstance(value,dict): return value.get("price")
    return value

def score(state):
    settlements=state.get("settlements",{})
    out=[]
    for batch in state.get("snapshots",[]):
        captured=batch.get("captured_at")
        for q in batch.get("quotes",[]):
            if q.get("up_probability") is None: continue
            raw=settlements.get(str(q.get("expiry_ms")))
            px=settlement_price(raw)
            if px is None: continue
            p=float(q["up_probability"]); strike=float(q["strike"])
            y=int(float(px)>strike); predicted=int(p>=0.5)
            row={"captured_at":captured,"market_id":q.get("market_id"),
                "expiry_ms":q["expiry_ms"],"horizon_ms":q.get("horizon_ms"),
                "strike":strike,"settlement_price":float(px),"p":p,"outcome":y,
                "hit":predicted==y,"brier":brier(p,y)}
            comp=q.get("brs_same_contract") or {}
            cp=comp.get("probability")
            if comp.get("status")=="FROZEN_AT_T0" and cp is not None:
                cp=float(cp)
                row["brs_same_contract_probability"]=cp
                row["brs_same_contract_hit"]=int(cp>=0.5)==y
                row["brs_same_contract_brier"]=brier(cp,y)
                row["brs_same_contract_methodology_version"]=comp.get("methodology_version")
                row["brs_source_forecast_at"]=comp.get("source_forecast_at")
                row["brs_source_age_ms"]=comp.get("source_age_ms")
                # Frozen ex-ante blend; evaluated prospectively only.
                bp=0.9*p+0.1*cp
                row["blend_90_deepbook_10_brs_probability"]=bp
                row["blend_90_deepbook_10_brs_hit"]=int(bp>=0.5)==y
                row["blend_90_deepbook_10_brs_brier"]=brier(bp,y)
            out.append(row)
    return out

def summary(rows,p_key="p",hit_key="hit",brier_key="brier"):
    valid=[r for r in rows if r.get(p_key) is not None and r.get(hit_key) is not None and r.get(brier_key) is not None]
    n=len(valid)
    if not n: return {"n":0,"accuracy":None,"brier":None,"baseline_50_brier":None,"brier_skill_vs_50":None}
    mean=sum(float(x[brier_key]) for x in valid)/n
    return {"n":n,"accuracy":sum(bool(x[hit_key]) for x in valid)/n,"brier":mean,
            "baseline_50_brier":0.25,"brier_skill_vs_50":1-mean/0.25}

def main():
    s=json.loads(P.read_text()) if P.exists() else {}
    added=settle_due(s)
    rows=score(s); metrics=summary(rows)
    brs_metrics=summary(rows,"brs_same_contract_probability","brs_same_contract_hit","brs_same_contract_brier")
    blend_metrics=summary(rows,"blend_90_deepbook_10_brs_probability","blend_90_deepbook_10_brs_hit","blend_90_deepbook_10_brs_brier")
    s["scores"]=rows
    s["validation"]={
        **metrics,
        "settled_expiries":len(s.get("settlements",{})),
        "settlement_source":"Coinbase Exchange BTC-USD 1m",
        "settlement_rule":"first 1m candle close at/after expiry",
        "comparison":{
            "baseline_50_50":{"n":metrics["n"],"brier":0.25 if metrics["n"] else None},
            "briefrooms_same_contract_shadow":{
              "status":"PROSPECTIVE" if brs_metrics["n"] else "AWAITING_NEW_FROZEN_OBSERVATIONS",
              "methodology_version":"brs-btc-same-contract-v1",
              "deepbook":metrics,
              "brs":brs_metrics,
              "blend_90_deepbook_10_brs":blend_metrics,
              "same_contract":True,
              "same_expiry":True,
              "historical_backfill":False,
              "frontend_visible":False
            }
        },
        "learning":False,"writeback":False,"automatic_promotion":False
    }
    s["deepbook_brier_mean"]=metrics["brier"]
    s["validation_updated_at"]=datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
    P.write_text(json.dumps(s,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"settlements_added":added,**metrics,"brs_same_contract":brs_metrics,"blend":blend_metrics}))
if __name__=="__main__":
    main()
