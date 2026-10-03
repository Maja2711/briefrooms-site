#!/usr/bin/env python3
"""FSE v2 — Deep Fractal Memory Challenger.

Independent SHADOW_ONLY challenger to FSE v1. It searches the maximum practical
history exposed by the research feed, encodes multiscale geometry, freezes every
forecast before outcome, and has zero production authority.

Design influences: geometric scaling/fractal dimension (Falconer/Mandelbrot),
multifractal formalism, generalized Hurst/MF-DFA, wavelet multiresolution ideas,
and stochastic heavy-tail diagnostics. IFS/complex dynamics/L-systems are
intentionally not used as forecasting mechanisms.
"""
from __future__ import annotations

import argparse, bisect, json, math, os, statistics
from datetime import timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts import fractal_structure_engine as v1

SCHEMA="briefrooms-fse-v2-deep-memory-v1"
SNAPSHOT_SCHEMA="briefrooms-fse-v2-snapshot-v1"
RESOLUTION_SCHEMA="briefrooms-fse-v2-resolution-v1"
SNAPSHOTS_FILE="fse_v2_snapshots.jsonl"
RESOLUTIONS_FILE="fse_v2_resolutions.jsonl"
PUBLIC_SCHEMA="briefrooms-fse-v2-public-v1"
ZERO_AUTHORITY=dict(v1.ZERO_AUTHORITY)
Q_GRID=(-4.0,-2.0,-1.0,1.0,2.0,3.0,4.0)
HOURLY_RANGE="2y"   # Yahoo intraday maximum practical range for this feed
DAILY_RANGE="max"   # full daily history exposed by source
HORIZON=4
TOP_K=80

def mean(xs): return statistics.fmean(xs) if xs else 0.0
def stdev(xs): return statistics.pstdev(xs) if len(xs)>=2 else 0.0
def clamp(x,lo=0.0,hi=1.0): return max(lo,min(hi,float(x)))

def robust_scale(xs:Sequence[float])->float:
    if not xs:return 1.0
    med=statistics.median(xs)
    mad=statistics.median(abs(x-med) for x in xs)
    return max(1.4826*mad,1e-9)

def sample_entropy(xs:Sequence[float],m:int=2)->float:
    """Small, deterministic SampEn proxy; bounded sample keeps runtime stable."""
    x=list(xs[-256:])
    if len(x)<32:return 0.0
    r=.2*max(stdev(x),1e-12)
    def count(k):
        n=0
        for i in range(len(x)-k):
            for j in range(i+1,len(x)-k):
                if max(abs(x[i+t]-x[j+t]) for t in range(k))<=r:n+=1
        return n
    a,b=count(m+1),count(m)
    return -math.log(max(a,1)/max(b,1))

def dfa_alpha(rs:Sequence[float])->float:
    """Linear DFA scaling exponent across dyadic windows."""
    x=list(rs[-512:])
    if len(x)<64:return .5
    mu=mean(x); y=[]; acc=0.0
    for r in x:
        acc+=r-mu; y.append(acc)
    lx=[];ly=[]
    for s in (8,16,32,64,128):
        if s>len(y)//2:continue
        fs=[]
        for start in range(0,len(y)-s+1,s):
            seg=y[start:start+s]; n=len(seg)
            xx=list(range(n)); sl=v1.slope(xx,seg)
            if sl is None:continue
            intercept=mean(seg)-sl*mean(xx)
            fs.extend((seg[i]-(intercept+sl*i))**2 for i in range(n))
        if fs:
            f=math.sqrt(mean(fs))
            if f>0:lx.append(math.log(s));ly.append(math.log(f))
    out=v1.slope(lx,ly)
    return float(out) if out is not None and math.isfinite(out) else .5

def haar_scaling(rs:Sequence[float])->tuple[float,float]:
    """Haar multiresolution scaling proxy: slope + curvature of log energy."""
    x=list(rs[-512:])
    pts=[]
    for scale in (2,4,8,16,32,64):
        vals=[]
        for i in range(0,len(x)-scale+1,scale):
            half=scale//2
            vals.append(abs(mean(x[i:i+half])-mean(x[i+half:i+scale])))
        if vals and mean(vals)>0:pts.append((math.log(scale),math.log(mean(vals))))
    if len(pts)<3:return 0.0,0.0
    sl=v1.slope([p[0] for p in pts],[p[1] for p in pts]) or 0.0
    residual=[p[1]-(mean([q[1] for q in pts])+sl*(p[0]-mean([q[0] for q in pts]))) for p in pts]
    return float(sl),stdev(residual)

def generalized_hurst_grid(rs:Sequence[float])->dict[float,float]:
    """Positive and negative-q structure functions with zero protection."""
    x=list(rs[-512:]); out={}
    if len(x)<64:return {q:.5 for q in Q_GRID}
    for q in Q_GRID:
        lx=[];ly=[]
        for lag in (1,2,4,8,16,32):
            vals=[abs(sum(x[i:i+lag])) for i in range(0,len(x)-lag+1,lag)]
            vals=[max(v,1e-12) for v in vals]
            if len(vals)<4:continue
            if q==0:m=math.exp(mean([math.log(v) for v in vals]))
            else:
                m=mean([v**q for v in vals])
                if m<=0:continue
                m=m**(1.0/q)
            lx.append(math.log(lag));ly.append(math.log(max(m,1e-12)))
        h=v1.slope(lx,ly)
        out[q]=float(h) if h is not None and math.isfinite(h) else .5
    return out

def box_dimension_path(closes:Sequence[float])->float:
    """Box-counting dimension proxy of normalized price graph."""
    c=list(closes[-256:])
    if len(c)<32:return 1.0
    lo,hi=min(c),max(c); span=max(hi-lo,1e-12)
    y=[(v-lo)/span for v in c]
    logs=[]
    for boxes in (4,8,16,32):
        occupied=set()
        for i,val in enumerate(y):
            xi=min(boxes-1,int(i*boxes/len(y))); yi=min(boxes-1,int(val*boxes))
            occupied.add((xi,yi))
        if len(occupied)>1:logs.append((math.log(boxes),math.log(len(occupied))))
    sl=v1.slope([x for x,_ in logs],[y for _,y in logs]) if len(logs)>=2 else None
    return float(sl) if sl is not None else 1.0

def path_shape(bars:Sequence[v1.Bar],window:int=96,points:int=24)->list[float]:
    r=list(bars[-window:])
    if len(r)<window:return []
    c=[b.close for b in r]; ret=v1.log_returns(c); scale=robust_scale(ret)
    acc=[0.0]
    for z in ret:acc.append(acc[-1]+z/scale)
    out=[]
    for i in range(points):
        p=i*(len(acc)-1)/(points-1); a=int(math.floor(p)); b=int(math.ceil(p))
        out.append(acc[a] if a==b else acc[a]*(b-p)+acc[b]*(p-a))
    return out

def geometry_features(bars:Sequence[v1.Bar],window:int=512)->list[float]:
    r=list(bars[-window:]); closes=[b.close for b in r]; rs=v1.log_returns(closes)
    if len(rs)<64:return []
    hg=generalized_hurst_grid(rs)
    positive=[hg[q] for q in (1.0,2.0,3.0,4.0)]
    width=max(hg.values())-min(hg.values())
    asym=(hg[-4.0]-hg[-1.0])-(hg[1.0]-hg[4.0])
    wave_slope,wave_curv=haar_scaling(rs)
    absr=[abs(x) for x in rs]; med=v1.quantile(absr,.5); q95=v1.quantile(absr,.95); q99=v1.quantile(absr,.99)
    return [
        hg[-4.0],hg[-2.0],hg[-1.0],*positive,
        width,asym,dfa_alpha(rs),wave_slope,wave_curv,
        clamp(max(0.0,v1.excess_kurtosis(rs))/12.0),
        clamp((q95/max(med,1e-12)-2.0)/6.0),
        clamp((q99/max(q95,1e-12)-1.0)/4.0),
        clamp(sample_entropy(rs)/3.0),
        clamp((box_dimension_path(closes)-1.0)),
        math.tanh(mean(rs)/max(stdev(rs),1e-12)),
        clamp(stdev(rs)*100.0),
    ]

def daily_context(daily:Sequence[v1.Bar],at_ts)->list[float]:
    times=[b.timestamp for b in daily]
    idx=bisect.bisect_right(times,at_ts)
    if idx<128:return []
    return geometry_features(daily[:idx],window=min(512,idx))

def vector_distance(a:Sequence[float],b:Sequence[float])->float:
    if not a or len(a)!=len(b):return float("inf")
    # groups are already mostly bounded; robust cap prevents one feature dominating.
    return math.sqrt(mean([min((float(x)-float(y))**2,9.0) for x,y in zip(a,b)]))

def deep_historical_analogues(hourly:Sequence[v1.Bar],daily:Sequence[v1.Bar],top_k:int=TOP_K)->dict[str,Any]:
    """Search all eligible hourly history, conditioned on long daily geometry."""
    h=list(hourly); d=list(daily)
    current_path=path_shape(h); current_geo=geometry_features(h); current_daily=daily_context(d,h[-1].timestamp)
    cur=current_path+current_geo+current_daily
    if not current_path or not current_geo or not current_daily:
        return {"source":"DEEP_HISTORY","analogues_n":0,"p_up":.5,"history_candidates":0}
    cand=[]
    # 4h stride limits overlap while still searching the full eligible 1h archive.
    for end in range(512,len(h)-HORIZON-8,4):
        hist=h[:end]
        p=path_shape(hist); g=geometry_features(hist)
        dc=daily_context(d,h[end-1].timestamp)
        vec=p+g+dc
        if not p or not g or not dc or len(vec)!=len(cur):continue
        dist=vector_distance(cur,vec); sim=1/(1+dist)
        ref=h[end-1].close; fut=h[end:end+HORIZON]
        ret=fut[-1].close/ref-1
        path=[b.close/ref-1 for b in fut]
        cand.append({"similarity":sim,"return":ret,"mae":min(path),"mfe":max(path),"at":h[end-1].timestamp})
    cand.sort(key=lambda x:x["similarity"],reverse=True); selected=cand[:top_k]
    if not selected:return {"source":"DEEP_HISTORY","analogues_n":0,"p_up":.5,"history_candidates":0}
    # similarity softmax-like weighting; cap concentration by adding a floor.
    w=[max(x["similarity"],.05)**4 for x in selected]; ws=sum(w)
    pup=sum(wi for wi,x in zip(w,selected) if x["return"]>0)/ws
    eff=(sum(w)**2)/max(sum(x*x for x in w),1e-12)
    return {
        "source":"DEEP_HISTORY","analogues_n":len(selected),"history_candidates":len(cand),
        "p_up":pup,"top_similarity":selected[0]["similarity"],
        "mean_similarity":mean([x["similarity"] for x in selected]),
        "effective_analogues":eff,
        "median_forward_return":statistics.median(x["return"] for x in selected),
        "median_adverse_excursion":statistics.median(x["mae"] for x in selected),
        "median_favorable_excursion":statistics.median(x["mfe"] for x in selected),
        "history_start":h[0].timestamp.isoformat().replace("+00:00","Z"),
        "history_end":h[-1].timestamp.isoformat().replace("+00:00","Z"),
        "daily_history_start":d[0].timestamp.isoformat().replace("+00:00","Z") if d else None,
        "daily_history_bars":len(d),"hourly_history_bars":len(h),
        "fingerprint_dimensions":len(cur),
        "method":"ATR/robust-normalized path + generalized Hurst q[-4..4] + DFA + Haar multiresolution + tails + entropy + box dimension + full daily context",
    }

def fetch_deep(client:v1.YahooChartClient,symbol:str)->tuple[list[v1.Bar],list[v1.Bar]]:
    hourly=client.bars(symbol,HOURLY_RANGE,"60m")
    daily=client.bars(symbol,DAILY_RANGE,"1d")
    return hourly,daily

def read_jsonl(path:Path): return v1.read_jsonl(path)
def append_chain(path,schema,payload,id_key): return v1.append_chain(path,schema,payload,id_key)

def resolve(state_dir:Path,hourly_by:Mapping[str,Sequence[v1.Bar]]):
    done={str(x.get("snapshot_id")) for x in read_jsonl(state_dir/RESOLUTIONS_FILE)}
    for s in read_jsonl(state_dir/SNAPSHOTS_FILE):
        sid=str(s.get("snapshot_id") or "")
        if not sid or sid in done:continue
        future=[b for b in hourly_by.get(str(s.get("instrument")),[]) if b.timestamp>v1.parse_time(str(s["observed_at"]))]
        if len(future)<HORIZON:continue
        path=future[:HORIZON]; ref=float(s["reference_price"]); ret=path[-1].close/ref-1
        y=1 if ret>0 else 0; p=clamp(float(s["p_up_4h"])); brier=(p-y)**2
        rid="fsev2res-"+v1.sha({"snapshot_id":sid,"resolved_at":path[-1].timestamp.isoformat()})[:24]
        append_chain(state_dir/RESOLUTIONS_FILE,RESOLUTION_SCHEMA,{
            "resolution_id":rid,"snapshot_id":sid,"instrument":s["instrument"],
            "observed_at":s["observed_at"],"resolved_at":path[-1].timestamp.isoformat().replace("+00:00","Z"),
            "horizon":"4x1h_market_bars","forward_return":ret,"outcome_up":bool(y),
            "directional_brier":brier,"edge_vs_0_5":.25-brier,
            "mae_fraction":min(b.close/ref-1 for b in path),"mfe_fraction":max(b.close/ref-1 for b in path),
            "prospective_only":True,"authority":dict(ZERO_AUTHORITY)
        },"resolution_id"); done.add(sid)

def snapshot(state_dir:Path,row:Mapping[str,Any]):
    prior=[x for x in read_jsonl(state_dir/SNAPSHOTS_FILE) if x.get("instrument")==row["instrument"]]
    if prior and prior[-1].get("observed_at")==row["observed_at"]:return
    sid="fsev2snap-"+v1.sha({"instrument":row["instrument"],"observed_at":row["observed_at"]})[:24]
    append_chain(state_dir/SNAPSHOTS_FILE,SNAPSHOT_SCHEMA,{
        "snapshot_id":sid,"instrument":row["instrument"],"symbol":row["symbol"],
        "observed_at":row["observed_at"],"reference_price":row["reference_price"],
        "p_up_4h":row["memory"]["p_up"],"forecast":row["forecast"],
        "analogue_n":row["memory"]["analogues_n"],"history_candidates":row["memory"]["history_candidates"],
        "fingerprint_dimensions":row["memory"]["fingerprint_dimensions"],
        "prospective_only":True,"authority":dict(ZERO_AUTHORITY)
    },"snapshot_id")

def verify(state_dir:Path):
    a=v1.verify_chain(state_dir/SNAPSHOTS_FILE,SNAPSHOT_SCHEMA,"snapshot_id")
    b=v1.verify_chain(state_dir/RESOLUTIONS_FILE,RESOLUTION_SCHEMA,"resolution_id")
    for row in read_jsonl(state_dir/SNAPSHOTS_FILE)+read_jsonl(state_dir/RESOLUTIONS_FILE):
        if row.get("authority")!=ZERO_AUTHORITY:raise ValueError("FSE v2 authority violation")
    return {"ok":True,"snapshots":a,"resolutions":b,"zero_authority":True}

def metric(resolutions,instrument):
    rows=[x for x in resolutions if x.get("instrument")==instrument]
    edges=[float(x["edge_vs_0_5"]) for x in rows]
    return {"instrument":instrument,"counter":len(rows),"target_n":40,
            "total_edge":sum(edges),"mean_edge":mean(edges) if edges else None,
            "mean_brier":(.25-mean(edges)) if edges else None,
            "baseline_brier":.25,"status":"RUNNING_SHADOW" if len(rows)<40 else "READY_FOR_REVIEW"}

def run_cycle(root:Path,state_dir:Path,public:Path,instruments=None,client=None,at=None):
    instruments=instruments or v1.DEFAULT_INSTRUMENTS; client=client or v1.YahooChartClient()
    raw={}; states=[]; errors={}
    for iid,symbol in instruments.items():
        try:
            h,d=fetch_deep(client,symbol); raw[iid]=h
            mem=deep_historical_analogues(h,d)
            p=clamp(mem.get("p_up",.5))
            states.append({"instrument":iid,"symbol":symbol,
                "observed_at":h[-1].timestamp.isoformat().replace("+00:00","Z"),
                "reference_price":h[-1].close,"forecast":"UP" if p>=.55 else ("DOWN" if p<=.45 else "NEUTRAL"),
                "memory":mem})
        except Exception as e: errors[iid]=str(e)
    resolve(state_dir,raw)
    for row in states:snapshot(state_dir,row)
    check=verify(state_dir); resolutions=read_jsonl(state_dir/RESOLUTIONS_FILE)
    generated=at or __import__("datetime").datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
    out={"schema_version":PUBLIC_SCHEMA,"engine":"FSE v2 — Deep Fractal Memory","module_id":"IN-09-V2",
         "mode":"SHADOW_ONLY","production_impact":False,"authority":dict(ZERO_AUTHORITY),
         "generated_at":generated,"methodology_frozen":True,
         "source_policy":{"hourly_range":HOURLY_RANGE,"daily_range":DAILY_RANGE,"search":"all eligible history; 4h stride; future outcome excluded"},
         "instruments":states,"measurements":[metric(resolutions,i) for i in instruments],
         "state_verification":check,"errors":errors}
    public.parent.mkdir(parents=True,exist_ok=True); public.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--root",default="."); ap.add_argument("--state-dir",required=True)
    ap.add_argument("--public",default="data/investments/fse_v2_public.json"); ap.add_argument("--verify",action="store_true")
    a=ap.parse_args(); state=Path(a.state_dir)
    if a.verify: print(json.dumps(verify(state),indent=2)); return
    print(json.dumps(run_cycle(Path(a.root),state,Path(a.public)),ensure_ascii=False,indent=2))

if __name__=="__main__": main()
