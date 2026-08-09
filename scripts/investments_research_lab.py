#!/usr/bin/env python3
"""Autonomous governed research lab for weekly-position hypotheses.

The lab searches known indicator families, mutates parameters/combinations, records hypotheses,
and promotes NOTHING directly to production. It can only write a promotion registry candidate
after evidence gates are met; runtime impact additionally requires an implemented live activation rule.
"""
from __future__ import annotations
import hashlib, itertools, json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
POLICY=ROOT/'data/investments/research_lab_policy.json'
MA=ROOT/'data/investments/eurusd_sma_vs_ema_research.json'
DECISION=ROOT/'data/investments/eurusd_ma_research_decision.json'
STATE=ROOT/'data/investments/research_lab_state.json'
REGISTRY=ROOT/'data/investments/research_lab_promotion_registry.json'
REPORT=ROOT/'data/investments/research_lab_report.json'
TZ=ZoneInfo('Europe/Warsaw')

def read(path,default):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except Exception:return default

def write(path,obj):path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def cid(payload):return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()[:16]

def generated_hypotheses(policy):
    fam=list((policy.get('search') or {}).get('indicator_families') or [])
    tfs=list((policy.get('search') or {}).get('timeframes') or [])
    maxn=int((policy.get('search') or {}).get('max_candidates_per_cycle') or 120)
    rows=[]
    # First generation: single families and cross-timeframe pairs. Later cycles mutate periods/thresholds.
    for tf,f in itertools.product(tfs,fam):
        spec={'instrument_id':'eurusd','timeframes':[tf],'families':[f],'generation_type':'library_single'}
        rows.append({'candidate_id':cid(spec),'spec':spec,'status':'queued_needs_backtest'})
    if (policy.get('search') or {}).get('allow_cross_timeframe_combinations',True):
        for tf1,tf2 in itertools.combinations(tfs,2):
            for f1,f2 in itertools.combinations(fam,2):
                spec={'instrument_id':'eurusd','timeframes':[tf1,tf2],'families':[f1,f2],'generation_type':'cross_timeframe_composite'}
                rows.append({'candidate_id':cid(spec),'spec':spec,'status':'queued_needs_backtest'})
                if len(rows)>=maxn: return rows[:maxn]
    return rows[:maxn]

def evidence_candidates(policy):
    r=read(MA,{})
    if not r:return []
    target=policy.get('target') or {}; min_trades=int(target.get('minimum_total_trades') or 60)
    min_wr=float(target.get('minimum_promotable_win_rate') or .58); min_mean=float(target.get('minimum_mean_week_percent') or .02)
    out=[]
    # Existing SMA/EMA study is treated as discovery evidence only, never as holdout proof.
    for tf,avg,test,side in [('H1','ema','price_vs_all','long'),('H1','ema','price_vs_all','short'),('W1','hybrid','fast_with_slow','long')]:
        row=((((r.get('timeframes') or {}).get(tf) or {}).get(avg) or {}).get(test) or {}).get(side) or {}
        spec={'instrument_id':'eurusd','timeframes':[tf],'families':[avg,test],'side':side,'generation_type':'observed_edge'}
        discovery_pass=int(row.get('count') or 0)>=min_trades and float(row.get('hit_rate') or 0)>=min_wr and float(row.get('mean_week_percent') or 0)>min_mean
        out.append({'candidate_id':cid(spec),'spec':spec,'discovery_metrics':row,'discovery_pass':discovery_pass,
                    'status':'needs_walk_forward_holdout' if discovery_pass else 'rejected_discovery',
                    'runtime_activation':'not_implemented','runtime_adjustment_points':0.0})
    return out

def main():
    p=read(POLICY,{})
    now=datetime.now(TZ).isoformat(timespec='seconds')
    old=read(STATE,{'cycle':0,'known_candidates':{}}); cycle=int(old.get('cycle') or 0)+1
    generated=generated_hypotheses(p); evidence=evidence_candidates(p)
    known=dict(old.get('known_candidates') or {})
    for row in generated+evidence: known[row['candidate_id']]={**known.get(row['candidate_id'],{}),**row,'last_seen_cycle':cycle}
    # Promotion registry is deliberately empty of runtime-active entries until holdout+WF+live activation are implemented.
    registry={'version':'1.0.0','generated_at':now,'rule':'only_holdout_walk_forward_passed_candidates_with_live_activation_may_affect_runtime',
              'candidates':[x for x in evidence if x.get('status') in {'approved_for_shadow','approved_for_paper'} and x.get('runtime_activation')!='not_implemented']}
    state={'version':'1.0.0','cycle':cycle,'updated_at':now,'known_candidates':known,'queue':[x['candidate_id'] for x in generated if x['status']=='queued_needs_backtest']}
    report={'version':'1.0.0','generated_at':now,'cycle':cycle,'generated_this_cycle':len(generated),'evidence_candidates':evidence,
            'promotion_registry_count':len(registry['candidates']),'aspirational_win_rate':(p.get('target') or {}).get('aspirational_win_rate'),
            'governance':'research_can_iterate_autonomously_but_production_promotion_requires_holdout_walk_forward_costs_regime_stability_and_live_activation'}
    write(STATE,state);write(REGISTRY,registry);write(REPORT,report)
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
