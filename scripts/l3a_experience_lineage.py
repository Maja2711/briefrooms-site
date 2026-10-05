#!/usr/bin/env python3
"""L3-A lineage helpers: question/research -> evidence -> belief delta -> forecast -> verification."""
from __future__ import annotations
import hashlib, json
from typing import Any, Mapping, Sequence

SCHEMA="briefrooms-l3a-lineage-v1"
def _id(prefix:str,x:Any)->str:
 return prefix+"-"+hashlib.sha256(json.dumps(x,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()[:24]

def match_intents(intents:Mapping[str,Any], belief_ids:Sequence[str])->list[dict[str,Any]]:
 wanted=set(map(str,belief_ids)); out=[]
 for row in intents.get("intents",[]) or []:
  if isinstance(row,Mapping) and str(row.get("belief_id")) in wanted and row.get("status") in {"READY_FOR_RESEARCH","RESEARCHING","EVIDENCE_FOUND"}:
   out.append(dict(row))
 return out

def attribution(intent:Mapping[str,Any], *, evidence_ids:Sequence[str], p_before:float|None, p_after:float|None, forecast_id:str|None=None)->dict[str,Any]:
 pb=None if p_before is None else round(float(p_before),6); pa=None if p_after is None else round(float(p_after),6)
 return {"schema_version":SCHEMA,"attribution_id":_id("l3attr",{"intent":intent.get("intent_id"),"evidence":sorted(evidence_ids),"after":pa}),
  "intent_id":intent.get("intent_id"),"question_id":intent.get("question_id"),"belief_id":intent.get("belief_id"),
  "evidence_ids":sorted(set(map(str,evidence_ids))),"p_before":pb,"p_after":pa,
  "delta_p":None if pb is None or pa is None else round(pa-pb,6),"forecast_id":forecast_id,
  "future_metrics":{"verification_id":None,"outcome":None,"brier_score":None,"log_loss":None,"brier_gain_vs_pre_research":None}}

def forecast_metadata(intent:Mapping[str,Any], attr:Mapping[str,Any])->dict[str,Any]:
 return {"l3a":True,"l3_intent_id":intent.get("intent_id"),"l3_question_id":intent.get("question_id"),
         "l3_attribution_id":attr.get("attribution_id"),"l3_p_before":attr.get("p_before"),"l3_p_after":attr.get("p_after"),
         "l3_delta_p":attr.get("delta_p"),"l3_evidence_ids":list(attr.get("evidence_ids") or [])}

def settle(attr:Mapping[str,Any], verification:Mapping[str,Any])->dict[str,Any]:
 out=dict(attr); fm=dict(out.get("future_metrics") or {}); y=1.0 if bool(verification["outcome"]) else 0.0
 pb=out.get("p_before"); baseline=None if pb is None else round((float(pb)-y)**2,6)
 actual=float(verification["brier_score"])
 fm.update({"verification_id":verification.get("verification_id"),"outcome":bool(verification["outcome"]),"brier_score":actual,
            "log_loss":verification.get("log_loss"),"pre_research_counterfactual_brier":baseline,
            "brier_gain_vs_pre_research":None if baseline is None else round(baseline-actual,6)})
 out["forecast_id"]=verification.get("forecast_id") or out.get("forecast_id"); out["future_metrics"]=fm
 out["experience_value_status"]="POSITIVE" if fm["brier_gain_vs_pre_research"] is not None and fm["brier_gain_vs_pre_research"]>0 else "NON_POSITIVE"
 return out
