#!/usr/bin/env python3
"""BriefRooms L3 autonomous experience-acquisition orchestrator.

Closes the production research-intent loop without granting code mutation,
probability override, trading authority, or automatic production promotion.
"""
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA="briefrooms-l3-experience-acquisition-v1"
MAX_ACTIVE=3
EXECUTABLE_TYPES={"reduce_uncertainty","refresh_evidence","resolve_contradiction","challenge_move","audit"}

def canon(x:Any)->str: return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str)
def stable(prefix:str,x:Any)->str: return prefix+"-"+hashlib.sha256(canon(x).encode()).hexdigest()[:24]
def load(path:Path)->dict[str,Any]:
    x=json.loads(path.read_text(encoding="utf-8")); 
    if not isinstance(x,dict): raise ValueError("object required")
    return x

def build(questions:Mapping[str,Any], prior:Mapping[str,Any]|None=None, now:str|None=None)->dict[str,Any]:
    if questions.get("mode")!="production": raise ValueError("production Question Engine input required")
    authority=questions.get("authority") or {}
    if authority.get("probability_override") is not False or authority.get("trade_execution") is not False:
        raise ValueError("Question Engine authority boundary violated")
    now=now or datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
    previous={str(x.get("question_id")):x for x in ((prior or {}).get("intents") or []) if isinstance(x,Mapping)}
    ranked=sorted((x for x in questions.get("questions",[]) if isinstance(x,Mapping)),
                  key=lambda x:(-float(x.get("expected_information_value") or 0),str(x.get("question_id") or "")))
    intents=[]
    for q in ranked[:MAX_ACTIVE]:
        qid=str(q.get("question_id") or ""); qtype=str(q.get("question_type") or "")
        if not qid or qtype not in EXECUTABLE_TYPES: continue
        route="approved_primary_source_research"
        if qtype in {"challenge_move","audit"}: route="prospective_challenger_research"
        prior_row=previous.get(qid,{})
        intent={
          "intent_id":stable("l3ri",{"question_id":qid,"belief_id":q.get("belief_id"),"question_type":qtype}),
          "question_id":qid,"belief_id":q.get("belief_id"),"question_type":qtype,"question":q.get("question"),
          "expected_information_value":float(q.get("expected_information_value") or 0),
          "route":route,"status":prior_row.get("status") or "READY_FOR_RESEARCH",
          "created_at":prior_row.get("created_at") or now,
          "preregistration":{
             "prospective_only":True,"historical_backfill":False,"dedupe_key":stable("dedupe",{"q":qid,"b":q.get("belief_id")}),
             "success_measure":"uncertainty_or_contradiction_reduction_with_independent_evidence",
             "falsification":"no_new_independent_evidence_or_no_epistemic_improvement"
          },
          "lineage":{"source_question_engine_contract":questions.get("contract_version"),"question_id":qid}
        }
        intents.append(intent)
    return {
      "schema_version":SCHEMA,"mode":"production_research_orchestration","generated_at":now,
      "authority":{"automatic_question_selection":True,"automatic_research_routing":True,"automatic_experiment_preregistration":True,
        "belief_probability_override":False,"engine_policy_writeback":False,"code_mutation":False,"trade_execution":False,
        "automatic_production_promotion":False},
      "budget":{"max_active_research_intents":MAX_ACTIVE},"intents":intents,
      "summary":{"selected":len(intents),"primary_source_research":sum(x["route"]=="approved_primary_source_research" for x in intents),
                 "prospective_challenger_research":sum(x["route"]=="prospective_challenger_research" for x in intents)}
    }

def main()->int:
    p=argparse.ArgumentParser(); p.add_argument("--questions",type=Path,required=True); p.add_argument("--prior",type=Path); p.add_argument("--output",type=Path,required=True)
    a=p.parse_args(); prior=load(a.prior) if a.prior and a.prior.exists() else {}
    out=build(load(a.questions),prior); a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(out,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(out["summary"],sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
