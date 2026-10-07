#!/usr/bin/env python3
"""BriefRooms L3-A research-intent attribution and prospective forecast capture.

Research collection itself is performed by existing approved Belief adapters.
This module diff-checks the pre/post Belief state, attributes only newly ingested
Evidence to the triggering research intent, records P_before/P_after, and freezes
an L3-A forecast using the canonical Belief Core forecast/verification contract.
It has no direct probability, policy, sizing, promotion, or execution authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from belief_core import BeliefCore, iso_z, parse_time
from belief_core_live import (
    BELIEFS,
    YahooChartClient,
    fetch_snapshot,
    horizon_target_plan,
    outcome_spec,
    required_symbols,
)
from l3a_experience_lineage import attribution, forecast_metadata
try:
    import provenance_contract as provenance
except ImportError:
    from scripts import provenance_contract as provenance
from learning_ledger import append_event

SCHEMA = "briefrooms-l3a-executor-v1"
EXPERIENCE_SCHEMA = "briefrooms-l3a-experience-state-v1"
EXPERIENCE_FILE = "L3A_EXPERIENCE_STATE.json"
LEDGER_FILE = "l3a_learning_ledger.jsonl"

def _stable(prefix: str, payload: Any) -> str:
    raw=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str)
    return prefix+"-"+hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

def _load(path: Path) -> dict[str,Any]:
    payload=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload,dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload

def _belief_probabilities(state: Mapping[str,Any]) -> dict[str,float]:
    out={}
    for row in state.get("beliefs",[]) or []:
        if isinstance(row,Mapping) and row.get("belief_id") is not None and row.get("probability") is not None:
            out[str(row["belief_id"])]=float(row["probability"])
    return out

def _evidence_by_belief(state: Mapping[str,Any]) -> dict[str,set[str]]:
    out: dict[str,set[str]]={}
    for row in state.get("evidence",[]) or []:
        if not isinstance(row,Mapping):
            continue
        bid=str(row.get("belief_id") or "")
        eid=str(row.get("evidence_id") or "")
        if bid and eid:
            out.setdefault(bid,set()).add(eid)
    return out

def new_evidence_ids(before: Mapping[str,Any], after: Mapping[str,Any], belief_id: str) -> list[str]:
    a=_evidence_by_belief(before).get(str(belief_id),set())
    b=_evidence_by_belief(after).get(str(belief_id),set())
    return sorted(b-a)

def _experience_state(path: Path) -> dict[str,Any]:
    if not path.exists():
        return {"schema_version":EXPERIENCE_SCHEMA,"records":[]}
    payload=_load(path)
    if payload.get("schema_version") != EXPERIENCE_SCHEMA:
        raise ValueError("unsupported L3-A experience state schema")
    if not isinstance(payload.get("records"),list):
        raise ValueError("L3-A experience state records must be a list")
    return payload

def _save_json(path: Path, payload: Mapping[str,Any]) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    os.replace(tmp,path)

def _record_attempt(state_dir: Path, record: Mapping[str,Any]) -> None:
    raw=dict(record)
    raw.pop("provenance",None)
    key=str(raw["attempt_id"])
    stored=provenance.attach_native(
        raw,
        artifact_id=key,
        artifact_type="l3a_research_attempt",
        engine_id="l3a",
        engine_version=SCHEMA,
        created_at=str(raw["attempted_at"]),
        authority="research",
        parent_artifact_ids=[
            str(value)
            for value in (raw.get("question_id"),raw.get("intent_id"),raw.get("attribution_id"))
            if value
        ],
        source_ids=[str(raw.get("route"))] if raw.get("route") else [],
        evidence_ids=list(raw.get("evidence_ids") or []),
        belief_ids=[str(raw.get("belief_id"))] if raw.get("belief_id") else [],
        forecast_id=raw.get("forecast_id"),
        prospective=True,
        domain_provenance={
            "research_result_status":raw.get("research_result_status"),
            "native_write_time":True,
        },
    )
    path=state_dir/EXPERIENCE_FILE
    state=_experience_state(path)
    records=list(state.get("records") or [])
    existing={str(x.get("attempt_id")):i for i,x in enumerate(records) if isinstance(x,Mapping)}
    if key in existing:
        records[existing[key]]=stored
    else:
        records.append(stored)
    state["records"]=records[-2000:]
    state["updated_at"]=str(stored.get("attempted_at") or "")
    _save_json(path,state)
    append_event(
        state_dir/LEDGER_FILE,
        event_type="learning_observation",
        occurred_at=str(stored["attempted_at"]),
        subject_id=str(stored.get("question_id") or stored.get("intent_id")),
        source_ref=str(stored.get("intent_id") or ""),
        payload=stored,
    )

def execute(
    intents: Mapping[str,Any],
    before_state: Mapping[str,Any],
    state_dir: Path,
    *,
    now: Optional[datetime]=None,
    client: Optional[YahooChartClient]=None,
    run_id: Optional[str]=None,
) -> dict[str,Any]:
    authority=intents.get("authority") or {}
    if authority.get("belief_probability_override") is not False or authority.get("trade_execution") is not False:
        raise RuntimeError("unsafe L3 intent authority")
    when=now or datetime.now(timezone.utc)
    run_id=str(run_id or os.environ.get("GITHUB_RUN_ID") or iso_z(when))

    core=BeliefCore(state_dir)
    core.register_beliefs(BELIEFS)
    core.recompute(when)
    after_state=_load(state_dir/"state.json")
    p_before=_belief_probabilities(before_state)
    p_after={bid:float(row.probability) for bid,row in core.beliefs.items()}

    snapshot=None
    snapshot_error=None
    try:
        snapshot=fetch_snapshot(client or YahooChartClient())
    except Exception as exc:
        snapshot_error=f"{type(exc).__name__}:{exc}"

    results=[]
    for intent in intents.get("intents",[]) or []:
        if not isinstance(intent,Mapping):
            continue
        iid=str(intent.get("intent_id") or "")
        qid=str(intent.get("question_id") or "")
        bid=str(intent.get("belief_id") or "")
        route=str(intent.get("route") or "")
        attempt_id=_stable("l3attempt",{"intent_id":iid,"run_id":run_id})
        base={
            "schema_version":SCHEMA,
            "attempt_id":attempt_id,
            "run_id":run_id,
            "attempted_at":iso_z(when),
            "intent_id":iid,
            "question_id":qid,
            "question_type":intent.get("question_type"),
            "question":intent.get("question"),
            "belief_id":bid,
            "route":route,
            "expected_information_value":intent.get("expected_information_value"),
            "authority":{
                "probability_override":False,
                "engine_policy_writeback":False,
                "code_mutation":False,
                "trade_execution":False,
                "automatic_production_promotion":False,
            },
        }
        if route not in {"approved_primary_source_research","approved_market_evidence_research"}:
            rec={**base,"research_result_status":"WAITING_FOR_CAPABILITY","evidence_ids":[],"forecast_id":None}
            _record_attempt(state_dir,rec); results.append(rec); continue

        evidence_ids=new_evidence_ids(before_state,after_state,bid)
        if not evidence_ids:
            rec={**base,"research_result_status":"NO_NEW_EVIDENCE","evidence_ids":[],"forecast_id":None,
                 "p_before":p_before.get(bid),"p_after":p_after.get(bid),"delta_p":None}
            _record_attempt(state_dir,rec); results.append(rec); continue

        if bid not in p_before or bid not in p_after:
            rec={**base,"research_result_status":"UNATTRIBUTABLE_BELIEF_STATE","evidence_ids":evidence_ids,"forecast_id":None,
                 "p_before":p_before.get(bid),"p_after":p_after.get(bid),"delta_p":None}
            _record_attempt(state_dir,rec); results.append(rec); continue

        attr=attribution(intent,evidence_ids=evidence_ids,p_before=p_before[bid],p_after=p_after[bid],forecast_id=None)
        if snapshot is None:
            rec={**base,**attr,"research_result_status":"WAITING_FOR_OUTCOME_CONTRACT","forecast_id":None,
                 "outcome_contract_error":snapshot_error}
            _record_attempt(state_dir,rec); results.append(rec); continue

        try:
            spec=outcome_spec(bid,snapshot)
            symbols=required_symbols(spec)
            if not all(symbol in snapshot.bars for symbol in symbols):
                raise KeyError("missing outcome-contract market symbols")
            definition=core.definitions[bid]
            plan=horizon_target_plan(spec,when,float(definition.horizon_hours))
        except Exception as exc:
            rec={**base,**attr,"research_result_status":"WAITING_FOR_OUTCOME_CONTRACT","forecast_id":None,
                 "outcome_contract_error":f"{type(exc).__name__}:{exc}"}
            _record_attempt(state_dir,rec); results.append(rec); continue

        forecast_id="l3af-"+str(attr["attribution_id"]).split("-",1)[-1]
        meta={
            **forecast_metadata(intent,attr),
            "l3a_attempt_id":attempt_id,
            "l3a_research_route":route,
            "l3a_research_result_status":"EVIDENCE_GAIN",
            "outcome_spec":spec,
            "adapter_contract":"Observation->Evidence/v1",
            "research_horizon_hours":float(definition.horizon_hours),
            "research_horizon_label":plan["label"],
            "research_horizon_basis":plan["basis"],
            "calibration_horizon_bucket":plan["calibration_bucket"],
            "elapsed_nominal_target_at":plan["elapsed_nominal_target_at"],
            "session_equivalent_count":plan["session_equivalent_count"],
            "shadow_only":True,
            "trade_execution_enabled":False,
            "policy_output_enabled":False,
            "automatic_tuning":False,
        }
        forecast=core.capture_forecast(
            bid,
            as_of=when,
            target_at=plan["target"],
            regime="l3a_research",
            forecast_id=forecast_id,
            metadata=meta,
        )
        attr=attribution(intent,evidence_ids=evidence_ids,p_before=p_before[bid],p_after=p_after[bid],forecast_id=forecast.forecast_id)
        rec={**base,**attr,"research_result_status":"WAITING_OUTCOME",
             "forecast_at":forecast.forecast_at,"target_at":forecast.target_at,
             "experience_value_status":"WAITING_OUTCOME"}
        _record_attempt(state_dir,rec); results.append(rec)

    core.save()
    summary={
        "selected":len(results),
        "waiting_outcome":sum(r.get("research_result_status")=="WAITING_OUTCOME" for r in results),
        "no_new_evidence":sum(r.get("research_result_status")=="NO_NEW_EVIDENCE" for r in results),
        "waiting_for_capability":sum(r.get("research_result_status")=="WAITING_FOR_CAPABILITY" for r in results),
        "waiting_for_outcome_contract":sum(r.get("research_result_status")=="WAITING_FOR_OUTCOME_CONTRACT" for r in results),
    }
    return {"schema_version":SCHEMA,"generated_at":iso_z(when),"summary":summary,"results":results}

def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument("--intents",type=Path,required=True)
    p.add_argument("--before-state",type=Path,required=True)
    p.add_argument("--state-dir",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--now")
    args=p.parse_args()
    when=parse_time(args.now) if args.now else datetime.now(timezone.utc)
    out=execute(_load(args.intents),_load(args.before_state),args.state_dir,now=when)
    _save_json(args.output,out)
    print(json.dumps(out["summary"],sort_keys=True))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
