#!/usr/bin/env python3
"""Settle L3-A experience value from real canonical Belief Core Verification rows."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping

from belief_core import BeliefCore
from l3a_experience_lineage import settle
from learning_ledger import append_event
from l3a_research_executor import EXPERIENCE_FILE, EXPERIENCE_SCHEMA, LEDGER_FILE
try:
    import provenance_contract as provenance
except ImportError:
    from scripts import provenance_contract as provenance

SCHEMA="briefrooms-l3a-settlement-v1"

def _load(path: Path) -> dict[str,Any]:
    payload=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload,dict):
        raise ValueError("object required")
    return payload

def _save(path: Path,payload: Mapping[str,Any]) -> None:
    tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    os.replace(tmp,path)

def settle_state(state_dir: Path) -> dict[str,Any]:
    experience_path=state_dir/EXPERIENCE_FILE
    if not experience_path.exists():
        return {"schema_version":SCHEMA,"settled":0,"waiting":0,"records":0}
    state=_load(experience_path)
    if state.get("schema_version") != EXPERIENCE_SCHEMA:
        raise ValueError("unsupported L3-A experience schema")
    core=BeliefCore(state_dir)
    by_forecast={str(v.forecast_id):v for v in core.verifications.values() if v.forecast_id}
    rows=[]
    settled_count=0
    waiting=0
    for row in state.get("records",[]) or []:
        if not isinstance(row,Mapping):
            continue
        current=dict(row)
        fid=str(current.get("forecast_id") or "")
        already=((current.get("future_metrics") or {}).get("verification_id"))
        if fid and not already:
            verification=by_forecast.get(fid)
            if verification is not None:
                current=settle(current,verification.to_dict())
                current["research_result_status"]="SETTLED"
                current.pop("provenance",None)
                settlement_id=f"l3a-settlement:{current.get('attempt_id')}:{verification.verification_id}"
                current=provenance.attach_native(
                    current,
                    artifact_id=settlement_id,
                    artifact_type="l3a_experience_settlement",
                    engine_id="l3a",
                    engine_version=SCHEMA,
                    created_at=verification.verified_at,
                    authority="verification",
                    parent_artifact_ids=[
                        str(value)
                        for value in (current.get("attempt_id"),current.get("attribution_id"),fid)
                        if value
                    ],
                    evidence_ids=list(current.get("evidence_ids") or []),
                    belief_ids=[str(current.get("belief_id"))] if current.get("belief_id") else [],
                    forecast_id=fid,
                    verification_id=verification.verification_id,
                    prospective=True,
                    domain_provenance={
                        "experience_value_status":current.get("experience_value_status"),
                        "native_write_time":True,
                    },
                )
                append_event(
                    state_dir/LEDGER_FILE,
                    event_type="learning_observation",
                    occurred_at=verification.verified_at,
                    subject_id=str(current.get("question_id") or current.get("intent_id")),
                    source_ref=str(current.get("intent_id") or ""),
                    payload={
                        "kind":"l3a_experience_settlement",
                        "attempt_id":current.get("attempt_id"),
                        "attribution_id":current.get("attribution_id"),
                        "intent_id":current.get("intent_id"),
                        "question_id":current.get("question_id"),
                        "belief_id":current.get("belief_id"),
                        "forecast_id":fid,
                        "verification_id":verification.verification_id,
                        "outcome":verification.outcome,
                        "brier_score":verification.brier_score,
                        "log_loss":verification.log_loss,
                        "pre_research_counterfactual_brier":current["future_metrics"].get("pre_research_counterfactual_brier"),
                        "brier_gain_vs_pre_research":current["future_metrics"].get("brier_gain_vs_pre_research"),
                        "experience_value_status":current.get("experience_value_status"),
                    },
                )
                settled_count+=1
            else:
                waiting+=1
        elif fid and not already:
            waiting+=1
        rows.append(current)
    state["records"]=rows
    _save(experience_path,state)
    summary={"schema_version":SCHEMA,"settled":settled_count,"waiting":waiting,"records":len(rows)}
    _save(state_dir/"L3A_SETTLEMENT_SUMMARY.json",summary)
    return summary

def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument("--state-dir",type=Path,required=True)
    p.add_argument("--output",type=Path)
    a=p.parse_args()
    out=settle_state(a.state_dir)
    if a.output:
        _save(a.output,out)
    print(json.dumps(out,sort_keys=True))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
