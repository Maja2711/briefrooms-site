#!/usr/bin/env python3
"""BriefRooms Belief Question Engine v1 — read-only research-question selection."""
from __future__ import annotations
import hashlib, json, math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

CONTRACT_VERSION="belief-question-engine-v1"
MAX_QUESTIONS_PER_RUN=5

@dataclass(frozen=True)
class ResearchQuestion:
    question_id:str; belief_id:str; question_type:str; question:str
    trigger_reasons:tuple[str,...]; priority:float; uncertainty:float
    contradiction:float; freshness_gap:float; impact:float
    redundancy_penalty:float; expected_information_value:float
    status:str="production_candidate"

def clamp(x:float)->float: return max(0.0,min(1.0,float(x)))
def sid(*parts:Any)->str:
    return "q-"+hashlib.sha256("|".join(map(str,parts)).encode()).hexdigest()[:20]

def candidate(state:Mapping[str,Any], belief_id:str)->ResearchQuestion|None:
    p=clamp(state.get("probability",.5)); conf=clamp(state.get("confidence",0))
    contra=clamp(state.get("contradiction",0)); fresh=clamp(state.get("freshness",0))
    reasons=tuple(state.get("drilldown_reasons") or ())
    uncertainty=clamp((1-conf)*.55 + (1-abs(p-.5)*2)*.20 + contra*.25)
    freshness_gap=1-fresh
    impact=1.0 if "high_impact" in reasons else .70
    if "large_probability_delta" in reasons: impact=max(impact,.90)
    if not reasons and uncertainty < .45 and freshness_gap < .45: return None
    if contra>=.55:
        qtype="resolve_contradiction"
        text=f"What new independent evidence would best discriminate between the strongest supporting and opposing explanations for {state.get('topic',belief_id)}?"
    elif conf<.50:
        qtype="reduce_uncertainty"
        text=f"What missing evidence would most reduce uncertainty about {state.get('topic',belief_id)}?"
    elif freshness_gap>.50:
        qtype="refresh_evidence"
        text=f"What is the freshest authoritative evidence that could materially confirm or falsify {state.get('topic',belief_id)}?"
    elif "large_probability_delta" in reasons:
        qtype="challenge_move"
        text=f"What independent evidence could falsify the recent large probability move in {state.get('topic',belief_id)}?"
    else:
        qtype="audit"
        text=f"What evidence is missing to make the current assessment of {state.get('topic',belief_id)} auditable and decision-useful?"
    raw=clamp(.42*uncertainty+.23*contra+.15*freshness_gap+.20*impact)
    redundancy=.10 if len(state.get("dominant_support_evidence_ids") or ())>=3 and len(state.get("dominant_opposition_evidence_ids") or ())>=3 else 0.0
    eiv=clamp(raw-redundancy)
    return ResearchQuestion(sid(belief_id,state.get("state_id"),qtype),belief_id,qtype,text,reasons,round(eiv,6),round(uncertainty,6),round(contra,6),round(freshness_gap,6),round(impact,6),round(redundancy,6),round(eiv,6))

def build(epistemic:Mapping[str,Any])->dict[str,Any]:
    rows=[]
    for bid,state in (epistemic.get("states") or {}).items():
        if isinstance(state,Mapping):
            q=candidate(state,str(bid))
            if q: rows.append(q)
    rows.sort(key=lambda q:(-q.priority,q.question_id))
    selected=rows[:MAX_QUESTIONS_PER_RUN]
    return {"contract_version":CONTRACT_VERSION,"mode":"production","source_contract":epistemic.get("contract_version"),
      "authority":{"belief_core_writeback":False,"probability_override":False,"trade_execution":False,"automatic_research":True,"automatic_tuning":False},
      "selection_policy":{"objective":"expected_information_value_proxy","max_questions_per_run":MAX_QUESTIONS_PER_RUN,
      "principle":"reduce uncertainty/contradiction with independent evidence; penalize redundancy"},
      "candidate_count":len(rows),"selected_count":len(selected),"questions":[asdict(x) for x in selected]}

def main()->int:
    import argparse
    ap=argparse.ArgumentParser(); ap.add_argument("--epistemic-state",type=Path,required=True); ap.add_argument("--output",type=Path,required=True)
    a=ap.parse_args(); payload=build(json.loads(a.epistemic_state.read_text(encoding="utf-8")))
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"candidates":payload["candidate_count"],"selected":payload["selected_count"]})); return 0
if __name__=="__main__": raise SystemExit(main())
