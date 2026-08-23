from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

from gse_discovery_common import (
    CATALOG_VERSION, FEATURE_KEYS, MIN_CLASSIFICATION_SCORE, PIPELINE_VERSION,
    STATE_VERSION, TARGET_VERIFIED_CLUSTERS, canonical_sha256, clamp, iso_z,
    parse_time, stable_id, tokenize,
)

def _dominant_scenario(row):
    scores=list(row.get("scenario_scores") or [])
    return str(scores[0].get("scenario_type")) if scores else str((row.get("scenario_types") or ["unknown"])[0])

def _dominant_actor(row):
    buckets=list(row.get("actor_buckets") or [])
    for x in ("russia_ukraine","iran","houthi_yemen","china_taiwan","grain_black_sea","israel_levant","belarus","venezuela","other"):
        if x in buckets: return x
    return str(buckets[0]) if buckets else "other"

def _window(scenario):
    return 5 if scenario=="sanctions_escalation" else 3 if scenario in {"middle_east_energy_escalation","russia_ukraine_black_sea_escalation","red_sea_shipping_disruption","china_taiwan_trade_escalation"} else 4

def _jaccard(left,right):
    a,b=tokenize(left),tokenize(right)
    return 0.0 if not a or not b else len(a&b)/len(a|b)

def cluster_verified_documents(rows: Sequence[Mapping[str,Any]]) -> List[Dict[str,Any]]:
    ordered=sorted((dict(r) for r in rows if r.get("discovery_verified")),key=lambda r:(r.get("event_at"),r.get("source_ref")))
    clusters=[]
    for row in ordered:
        at=parse_time(row["event_at"]); scenario=_dominant_scenario(row); actor=_dominant_actor(row); selected=None
        for cluster in reversed(clusters[-80:]):
            if cluster["dominant_scenario"]!=scenario or cluster["dominant_actor"]!=actor: continue
            if abs((at-parse_time(cluster["last_event_at"])).days)>_window(scenario): continue
            if _jaccard(str(row.get("title") or ""),str(cluster.get("representative_title") or ""))>=.12 or actor!="other": selected=cluster; break
        if selected is None:
            selected={"dominant_scenario":scenario,"dominant_actor":actor,"first_event_at":row["event_at"],"last_event_at":row["event_at"],"representative_title":row.get("title"),"documents":[]}; clusters.append(selected)
        selected["documents"].append(row)
        if at<parse_time(selected["first_event_at"]): selected["first_event_at"]=row["event_at"]
        if at>parse_time(selected["last_event_at"]): selected["last_event_at"]=row["event_at"]
        selected["representative_title"]=max(selected["documents"],key=lambda x:(float(x.get("verification_score") or 0),float(x.get("source_reliability") or 0),len(str(x.get("title") or "")))).get("title")
    out=[]
    for cluster in clusters:
        docs=list(cluster["documents"]); rep=max(docs,key=lambda x:(float(x.get("verification_score") or 0),float(x.get("source_reliability") or 0),len(str(x.get("title") or ""))))
        scores=defaultdict(float)
        for doc in docs:
            for item in doc.get("scenario_scores") or []: scores[str(item["scenario_type"])]=max(scores[str(item["scenario_type"])],float(item["score"]))
        scenarios=[name for name,score in sorted(scores.items(),key=lambda x:(-x[1],x[0])) if score>=MIN_CLASSIFICATION_SCORE]
        names=sorted({str(d.get("source_name")) for d in docs}); refs=sorted({str(d.get("source_ref")) for d in docs}); features={}
        for key in FEATURE_KEYS:
            vals=[float((d.get("features") or {}).get(key,0)) for d in docs]; features[key]=round(max(vals) if key in {"severity","surprise"} else sum(vals)/max(1,len(vals)),6)
        first=parse_time(cluster["first_event_at"]); cid=stable_id("gse-auto-cluster",cluster["dominant_scenario"],cluster["dominant_actor"],first.date().isoformat(),rep.get("title"))
        out.append({"cluster_id":cid,"event_cluster_id":cid,"event_at":iso_z(first),"last_event_at":cluster["last_event_at"],"label":rep.get("title"),"scenario_types":scenarios,"dominant_scenario":cluster["dominant_scenario"],"dominant_actor":cluster["dominant_actor"],"source":"; ".join(names),"source_ref":rep.get("source_ref"),"source_refs":refs,"source_reliability":round(max(float(d.get("source_reliability") or 0) for d in docs),6),"features":features,"supporting_document_count":len(docs),"distinct_source_count":len(names),"verification_score":round(max(float(d.get("verification_score") or 0) for d in docs),6),"verification_status":"machine_verified_primary_source_cluster","document_ids":sorted(str(d.get("candidate_id")) for d in docs),"market_outcomes_used_for_selection":False})
    return sorted(out,key=lambda r:(r["event_at"],r["cluster_id"]))

def _catalog_event(cluster,generated_at):
    return {"event_id":stable_id("gse-auto-event",cluster["cluster_id"]),"event_cluster_id":cluster["event_cluster_id"],"event_at":cluster["event_at"],"label":cluster["label"],"scenario_types":list(cluster["scenario_types"]),"source":cluster["source"],"source_ref":cluster["source_ref"],"anchor_rule":"machine_verified_primary_source_publication_date","source_reliability":cluster["source_reliability"],"features":dict(cluster["features"]),"verification":{"status":cluster["verification_status"],"verification_score":cluster["verification_score"],"supporting_document_count":cluster["supporting_document_count"],"distinct_source_count":cluster["distinct_source_count"],"source_refs":list(cluster["source_refs"]),"pipeline_version":PIPELINE_VERSION,"generated_at":iso_z(generated_at),"market_outcomes_used_for_selection":False},"notes":"Automatically discovered and machine-verified against official primary-source pages. Market outcomes were not used to discover, classify, verify or cluster this event."}

def merge_catalog(curated_catalog,clusters,*,generated_at):
    curated=[dict(x) for x in curated_catalog.get("events") or []]; curated_ids={str(x.get("event_cluster_id") or x.get("event_id")) for x in curated}; auto=[]
    for cluster in clusters:
        if str(cluster["event_cluster_id"]) not in curated_ids: auto.append(_catalog_event(cluster,generated_at))
    filtered=[]
    for event in auto:
        at=parse_time(event["event_at"]); scenarios=set(event.get("scenario_types") or []); redundant=False
        for existing in curated:
            try: delta=abs((at-parse_time(existing["event_at"])).days)
            except Exception: continue
            if delta<=1 and scenarios & set(existing.get("scenario_types") or []): redundant=True; break
        if not redundant: filtered.append(event)
    events=sorted(curated+filtered,key=lambda x:(x.get("event_at"),x.get("event_id"))); unique={str(x.get("event_cluster_id") or x.get("event_id")) for x in events}; counts=Counter()
    for e in events:
        for s in e.get("scenario_types") or []: counts[str(s)]+=1
    machine=[e for e in events if isinstance(e.get("verification"),Mapping) and str((e.get("verification") or {}).get("status","")).startswith("machine_verified")]
    return {"schema_version":CATALOG_VERSION,"catalog_version":f"auto-{generated_at.date().isoformat()}","generated_at":iso_z(generated_at),"base_catalog_version":curated_catalog.get("catalog_version"),"governance":{"curated_anchors_preserved":True,"automatic_discovery_enabled":True,"automatic_machine_verification_enabled":True,"market_outcomes_are_not_stored_in_catalog":True,"market_outcomes_used_for_event_selection":False,"future_events_must_not_enter_past_forecasts":True,"event_clusters_prevent_correlated_sample_inflation":True,"automatic_tuning_enabled":False,"decision_influence":False},"coverage":{"curated_event_n":len(curated),"auto_machine_verified_event_n":len(machine),"effective_event_n":len(events),"verified_cluster_n":len(unique),"target_verified_cluster_n":TARGET_VERIFIED_CLUSTERS,"target_met":len(unique)>=TARGET_VERIFIED_CLUSTERS,"scenario_counts":dict(sorted(counts.items()))},"events":events}

def build_state(*,now,source_diagnostics,verified_docs,clusters,catalog,full_backfill,candidate_added):
    by_source=Counter(str(x.get("source_name")) for x in verified_docs); by_scenario=Counter()
    for cluster in clusters:
        for s in cluster.get("scenario_types") or []: by_scenario[str(s)]+=1
    n=int((catalog.get("coverage") or {}).get("verified_cluster_n") or 0); non_sanctions=sum(1 for c in clusters if set(c.get("scenario_types") or [])-{"sanctions_escalation"}); families=sum(1 for _,count in by_scenario.items() if count>=5)
    return {"schema_version":STATE_VERSION,"mode":"shadow","updated_at":iso_z(now),"pipeline_version":PIPELINE_VERSION,"full_backfill_run":bool(full_backfill),"candidate_documents_added":int(candidate_added),"machine_verified_document_n":len(verified_docs),"auto_cluster_n":len(clusters),"effective_verified_cluster_n":n,"target_verified_cluster_n":TARGET_VERIFIED_CLUSTERS,"target_met":n>=TARGET_VERIFIED_CLUSTERS,"quality_balance_gate":{"non_sanctions_cluster_n":non_sanctions,"scenario_families_with_at_least_5_clusters":families,"gate_met":non_sanctions>=20 and families>=4},"by_source":dict(sorted(by_source.items())),"by_scenario":dict(sorted(by_scenario.items())),"source_diagnostics":list(source_diagnostics),"catalog_sha256":canonical_sha256(catalog),"controls":{"market_outcomes_used_for_event_selection":False,"automatic_tuning_enabled":False,"decision_engine_connected":False,"belief_core_connected":False,"trade_execution_enabled":False,"policy_output_enabled":False}}
