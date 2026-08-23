from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from gse_discovery_common import (
    DEFAULT_START_DATE, FULL_REFRESH_DAYS, MAX_DETAIL_FETCHES, MODE,
    RECENT_OVERLAP_DAYS, TARGET_VERIFIED_CLUSTERS, UTC, append_unique,
    iso_z, parse_time, read_json, read_jsonl, write_json,
)
from gse_discovery_cluster import build_state, cluster_verified_documents, merge_catalog
from gse_discovery_sources import HttpClient, discover_source_documents, verify_document


def _load_config(path: Path):
    payload=read_json(path,{})
    if not payload.get("sources"): raise ValueError("historical discovery source config is empty")
    return payload

def _verified(state_dir: Path):
    return [x for x in read_jsonl(state_dir/"gse_historical_event_candidates.jsonl") if x.get("discovery_verified")]

def run(state_dir: Path, curated_catalog_path: Path, source_config_path: Path, *, now: Optional[datetime]=None, force_full=False, client=None):
    now=(now or datetime.now(UTC)).astimezone(UTC); client=client or HttpClient(); curated=read_json(curated_catalog_path,{})
    if not curated.get("events"): raise ValueError("curated historical catalog is unavailable")
    config=_load_config(source_config_path); previous=read_json(state_dir/"gse_historical_discovery_state.json",{}); last_full=None
    if previous.get("last_full_backfill_at"):
        try: last_full=parse_time(previous["last_full_backfill_at"])
        except Exception: pass
    full=force_full or not bool(previous.get("target_met")) or last_full is None or last_full<now-timedelta(days=FULL_REFRESH_DAYS)
    configured_start=parse_time(str(config.get("start_date") or DEFAULT_START_DATE)); start_at=configured_start if full else max(configured_start,now-timedelta(days=int(config.get("recent_overlap_days") or RECENT_OVERLAP_DAYS)))
    discovered={}; diagnostics=[]
    for source in config.get("sources") or []:
        if source.get("enabled") is False: continue
        rows,diag=discover_source_documents(source,client,start_at=start_at,end_at=now); diagnostics.append(diag)
        for row in rows: discovered[str(row["source_ref"])]=row
    existing=read_jsonl(state_dir/"gse_historical_event_candidates.jsonl"); by_url={str(x.get("source_ref")):x for x in existing if x.get("source_ref")}; pending=[row for url,row in discovered.items() if not bool((by_url.get(url) or {}).get("discovery_verified"))]
    pending.sort(key=lambda row:max((float(x.get("score") or 0) for x in row.get("preclassification") or []),default=0),reverse=True)
    verified_new=[]
    for candidate in pending[:int(config.get("max_detail_fetches") or MAX_DETAIL_FETCHES)]:
        verified=verify_document(candidate,client,start_at=configured_start,end_at=now)
        if verified is not None: verified_new.append(verified)
    candidate_path=state_dir/"gse_historical_event_candidates.jsonl"; added=append_unique(candidate_path,verified_new,"candidate_id"); verified_docs=_verified(state_dir); clusters=cluster_verified_documents(verified_docs); catalog=merge_catalog(curated,clusters,generated_at=now)
    write_json(state_dir/"gse_historical_event_clusters.json",{"schema_version":"gse-historical-event-discovery-v1","generated_at":iso_z(now),"clusters":clusters}); write_json(state_dir/"gse_historical_event_catalog_effective.json",catalog)
    state=build_state(now=now,source_diagnostics=diagnostics,verified_docs=verified_docs,clusters=clusters,catalog=catalog,full_backfill=full,candidate_added=added)
    if full: state["last_full_backfill_at"]=iso_z(now)
    elif previous.get("last_full_backfill_at"): state["last_full_backfill_at"]=previous["last_full_backfill_at"]
    write_json(state_dir/"gse_historical_discovery_state.json",state); return state

def main():
    p=argparse.ArgumentParser(description="Discover, verify and cluster historical geopolitical events for GSE v2"); p.add_argument("--state-dir",required=True); p.add_argument("--curated-catalog",required=True); p.add_argument("--source-config",required=True); p.add_argument("--force-full",action="store_true"); p.add_argument("--require-target",action="store_true"); a=p.parse_args()
    state=run(Path(a.state_dir),Path(a.curated_catalog),Path(a.source_config),force_full=a.force_full); print(json.dumps({"mode":state["mode"],"target_met":state["target_met"],"effective_verified_cluster_n":state["effective_verified_cluster_n"],"quality_balance_gate":state["quality_balance_gate"],"by_source":state["by_source"],"by_scenario":state["by_scenario"]},ensure_ascii=False,sort_keys=True)); return 3 if a.require_target and not state["target_met"] else 0

if __name__=="__main__": raise SystemExit(main())
