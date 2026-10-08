#!/usr/bin/env python3
"""BriefRooms FSE — Fractal Structure Engine.

Research/shadow only. It separates structural risk from directional Fractal
Memory and has zero authority to change positions, sizing, stops or execution.
"""
from __future__ import annotations

import argparse, hashlib, json, math, os, statistics, urllib.parse, urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA="briefrooms-fractal-structure-engine-v1"
PUBLIC_SCHEMA="briefrooms-fse-public-v1"
METHODOLOGY_VERSION="briefrooms-fse-public-v1"
SNAPSHOT_SCHEMA="briefrooms-fse-snapshot-v1"
RESOLUTION_SCHEMA="briefrooms-fse-resolution-v1"
SNAPSHOTS_FILE="fse_snapshots.jsonl"
RESOLUTIONS_FILE="fse_resolutions.jsonl"
YAHOO_BASE="https://query1.finance.yahoo.com/v8/finance/chart"
USER_AGENT="BriefRooms-FSE/1.0 (+shadow-research)"

ZERO_AUTHORITY={
    "production_policy_writeback":False,
    "production_ranking_writeback":False,
    "production_sizing_writeback":False,
    "production_stop_writeback":False,
    "source_model_writeback":False,
    "trade_execution":False,
    "automatic_promotion":False,
}
DEFAULT_INSTRUMENTS={"EURUSD":"EURUSD=X","BTCUSD":"BTC-USD","SPX":"SPY"}
TIMEFRAMES={
    "5m":{"interval":"5m","range":"5d","minutes":5},
    "15m":{"interval":"15m","range":"1mo","minutes":15},
    "1h":{"interval":"60m","range":"2y","minutes":60},
    "1d":{"interval":"1d","range":"5y","minutes":1440},
}

def canonical(v:Any)->str:
    return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str)

def sha(v:Any)->str:
    return hashlib.sha256(canonical(v).encode()).hexdigest()

def clamp(v:float,lo:float=0.0,hi:float=1.0)->float:
    return max(lo,min(hi,float(v)))

def finite(v:Any)->float|None:
    try: x=float(v)
    except (TypeError,ValueError): return None
    return x if math.isfinite(x) else None

def parse_time(v:str)->datetime:
    dt=datetime.fromisoformat(str(v).replace("Z","+00:00"))
    if dt.tzinfo is None: raise ValueError("timezone required")
    return dt.astimezone(timezone.utc)

@dataclass(frozen=True)
class Bar:
    timestamp:datetime
    close:float
    open:float|None=None
    high:float|None=None
    low:float|None=None
    volume:float|None=None

class YahooChartClient:
    def __init__(self,timeout:int=20)->None: self.timeout=timeout
    def bars(self,symbol:str,range_:str,interval:str)->list[Bar]:
        enc=urllib.parse.quote(symbol,safe="")
        url=f"{YAHOO_BASE}/{enc}?range={range_}&interval={interval}&includePrePost=false&events=div%2Csplits"
        req=urllib.request.Request(url,headers={"User-Agent":USER_AGENT,"Accept":"application/json"})
        with urllib.request.urlopen(req,timeout=self.timeout) as resp: payload=json.load(resp)
        result=((payload.get("chart") or {}).get("result") or [None])[0]
        if not result: raise RuntimeError(f"no Yahoo chart for {symbol} {interval}")
        ts=result.get("timestamp") or []
        q=(((result.get("indicators") or {}).get("quote") or [{}])[0])
        op,hi,lo,cl,vol=[q.get(k) or [] for k in ("open","high","low","close","volume")]
        out=[]
        for i,t in enumerate(ts):
            if i>=len(cl) or cl[i] is None: continue
            def opt(a):
                return finite(a[i]) if i<len(a) and a[i] is not None else None
            out.append(Bar(datetime.fromtimestamp(int(t),tz=timezone.utc),float(cl[i]),opt(op),opt(hi),opt(lo),opt(vol)))
        if len(out)<20: raise RuntimeError(f"insufficient bars for {symbol} {interval}: {len(out)}")
        return out

def mean(xs:Sequence[float])->float: return statistics.fmean(xs) if xs else 0.0
def stdev(xs:Sequence[float])->float: return statistics.pstdev(xs) if len(xs)>=2 else 0.0

def quantile(xs:Sequence[float],q:float)->float:
    if not xs:return 0.0
    s=sorted(float(x) for x in xs); p=clamp(q)*(len(s)-1); a=int(math.floor(p)); b=int(math.ceil(p))
    return s[a] if a==b else s[a]*(b-p)+s[b]*(p-a)

def log_returns(closes:Sequence[float])->list[float]:
    return [math.log(b/a) for a,b in zip(closes,closes[1:]) if a>0 and b>0]

def slope(xs:Sequence[float],ys:Sequence[float])->float|None:
    if len(xs)!=len(ys) or len(xs)<2:return None
    mx,my=mean(xs),mean(ys); den=sum((x-mx)**2 for x in xs)
    return None if den<=0 else sum((x-mx)*(y-my) for x,y in zip(xs,ys))/den

def generalized_hurst(rs:Sequence[float],q:float)->float|None:
    if len(rs)<64:return None
    lx=[];ly=[]
    for lag in (1,2,4,8,16):
        agg=[sum(rs[i:i+lag]) for i in range(0,len(rs)-lag+1,lag)]
        if len(agg)<4:continue
        m=mean([abs(x)**q for x in agg])
        if m<=0:continue
        lx.append(math.log(lag));ly.append(math.log(m**(1.0/q)))
    return slope(lx,ly)

def excess_kurtosis(xs:Sequence[float])->float:
    if len(xs)<8:return 0.0
    m=mean(xs); m2=mean([(x-m)**2 for x in xs])
    return 0.0 if m2<=0 else mean([(x-m)**4 for x in xs])/(m2*m2)-3.0

def atr_fraction(bars:Sequence[Bar],period:int=14)->float:
    r=list(bars[-max(2,period+1):]); out=[]
    for p,c in zip(r,r[1:]):
        h=c.high if c.high is not None else c.close; l=c.low if c.low is not None else c.close
        if p.close>0: out.append(max(h-l,abs(h-p.close),abs(l-p.close))/p.close)
    return mean(out)

def resample_fixed(bars:Sequence[Bar],factor:int)->list[Bar]:
    r=list(bars); n=len(r)-len(r)%factor; out=[]
    for i in range(0,n,factor):
        b=r[i:i+factor]; hs=[x.high for x in b if x.high is not None]; ls=[x.low for x in b if x.low is not None]
        out.append(Bar(b[-1].timestamp,b[-1].close,b[0].open if b[0].open is not None else b[0].close,max(hs) if hs else max(x.close for x in b),min(ls) if ls else min(x.close for x in b),None))
    return out

def scale_features(bars:Sequence[Bar],minutes:int,window:int=256)->dict[str,Any]:
    r=list(bars[-window:]); rets=log_returns([x.close for x in r]); hs=[generalized_hurst(rets,q) for q in (1.0,2.0,3.0)]
    valid=[x for x in hs if x is not None]; ar=[abs(x) for x in rets]; med=quantile(ar,.5); q95=quantile(ar,.95)
    return {
        "bars":len(r),"minutes":minutes,
        "realized_vol_per_sqrt_minute":stdev(rets)/math.sqrt(max(minutes,1)),
        "hurst_q1":hs[0],"hurst_q2":hs[1],"hurst_q3":hs[2],
        "multifractality_proxy":max(valid)-min(valid) if len(valid)>=2 else 0.0,
        "excess_kurtosis":excess_kurtosis(rets),
        "tail_ratio_q95_median":q95/max(med,1e-12) if ar else 0.0,
        "atr_fraction":atr_fraction(r),
    }

def structural_risk(scales:Mapping[str,Mapping[str,Any]])->dict[str,Any]:
    rows=[v for v in scales.values() if int(v.get("bars") or 0)>=32]
    vols=[float(v.get("realized_vol_per_sqrt_minute") or 0) for v in rows if float(v.get("realized_vol_per_sqrt_minute") or 0)>0]
    hs=[float(v["hurst_q2"]) for v in rows if finite(v.get("hurst_q2")) is not None]
    mf=[max(0.0,float(v.get("multifractality_proxy") or 0)) for v in rows]
    ku=[max(0.0,float(v.get("excess_kurtosis") or 0)) for v in rows]
    tr=[max(0.0,float(v.get("tail_ratio_q95_median") or 0)) for v in rows]
    svi=clamp(stdev(vols)/max(mean(vols),1e-12)/1.25) if vols else 0.0
    hm=mean(hs) if hs else .5; hd=stdev(hs) if hs else 0.0
    pe=clamp(abs(hm-.5)/.22+hd/.18); mm=clamp(mean(mf)/.22) if mf else 0.0
    tails=clamp(.55*(mean(ku)/6.0)+.45*max(0.0,(mean(tr)-2.2)/3.0)) if rows else 0.0
    risk=clamp(.28*svi+.18*pe+.24*mm+.30*tails)
    if risk>=.70 or tails>=.85: regime="TURBULENT"
    elif svi>=.55 or hd>=.16: regime="TRANSITION"
    elif hm>=.58: regime="TRENDING"
    elif hm<=.42: regime="MEAN_REVERTING"
    else: regime="STABLE"
    geo={"STABLE":(1,1),"TRENDING":(.9,1.05),"MEAN_REVERTING":(.85,.95),"TRANSITION":(.7,1.1),"TURBULENT":(.5,1.25)}[regime]
    return {
        "risk_score":risk,"regime":regime,
        "components":{"scale_instability":svi,"persistence_extreme":pe,"multifractality":mm,"tails":tails},
        "persistence":{"mean_hurst_q2":hm,"dispersion":hd},
        "research_candidate":{"position_size_multiplier":geo[0],"stop_distance_multiplier":geo[1],"production_applied":False},
    }

def path_fingerprint(bars:Sequence[Bar],window:int=64,points:int=32)->list[float]:
    r=list(bars[-window:])
    if len(r)<16:return []
    c=[x.close for x in r]; den=max(atr_fraction(r)*c[0],abs(c[0])*1e-6,1e-12); z=[(x-c[0])/den for x in c]
    out=[]
    for i in range(points):
        p=i*(len(z)-1)/(points-1); a=int(math.floor(p)); b=int(math.ceil(p))
        out.append(z[a] if a==b else z[a]*(b-p)+z[b]*(p-a))
    return out

def similarity(a:Sequence[float],b:Sequence[float])->float:
    if not a or len(a)!=len(b):return 0.0
    return 1.0/(1.0+math.sqrt(mean([(x-y)**2 for x,y in zip(a,b)])))

def historical_analogues(hourly:Sequence[Bar],horizon_bars:int=4,top_k:int=50)->dict[str,Any]:
    r=list(hourly); cur=path_fingerprint(r)
    if len(r)<200 or not cur:return {"source":"HISTORICAL_BOOTSTRAP","analogues_n":0,"p_up":.5}
    cand=[]
    for end in range(64,len(r)-horizon_bars-8,8):
        fp=path_fingerprint(r[:end]); ref=r[end-1].close; fut=r[end:end+horizon_bars]
        if not fp or ref<=0 or len(fut)<horizon_bars:continue
        ret=fut[-1].close/ref-1; path=[x.close/ref-1 for x in fut]
        cand.append({"similarity":similarity(cur,fp),"return":ret,"min":min(path),"max":max(path)})
    cand.sort(key=lambda x:x["similarity"],reverse=True); c=cand[:top_k]
    if not c:return {"source":"HISTORICAL_BOOTSTRAP","analogues_n":0,"p_up":.5}
    w=[max(x["similarity"],1e-6)**3 for x in c]; ws=sum(w); pup=sum(a for a,x in zip(w,c) if x["return"]>0)/ws; up=pup>=.5
    adv=[-x["min"] if up else x["max"] for x in c]
    return {"source":"HISTORICAL_BOOTSTRAP","analogues_n":len(c),"p_up":pup,"top_similarity":c[0]["similarity"],"mean_similarity":mean([x["similarity"] for x in c]),"median_forward_return":statistics.median([x["return"] for x in c]),"median_adverse_excursion":statistics.median(adv)}

def feature_vector(scales:Mapping[str,Mapping[str,Any]],risk:Mapping[str,Any])->list[float]:
    out=[]
    for tf in ("5m","15m","1h","4h","1d"):
        x=scales.get(tf) or {}
        out += [float(x.get("hurst_q2") if finite(x.get("hurst_q2")) is not None else .5),float(x.get("multifractality_proxy") or 0),clamp(max(0,float(x.get("excess_kurtosis") or 0))/8),clamp(max(0,float(x.get("tail_ratio_q95_median") or 0)-2)/5)]
    out += [float(risk.get("risk_score") or 0),float((risk.get("components") or {}).get("scale_instability") or 0)]
    return out

def read_jsonl(path:Path)->list[dict[str,Any]]:
    if not path.exists():return []
    out=[]
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip(): out.append(json.loads(raw))
    return out

def append_chain(path:Path,schema:str,payload:Mapping[str,Any],id_key:str)->dict[str,Any]:
    rows=read_jsonl(path); body=dict(payload); body["schema_version"]=schema; body["previous_hash"]=rows[-1]["event_hash"] if rows else "GENESIS"
    body["event_hash"]=sha(body); path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("a",encoding="utf-8") as f: f.write(canonical(body)+"\n"); f.flush(); os.fsync(f.fileno())
    return body

def verify_chain(path:Path,schema:str,id_key:str)->dict[str,Any]:
    prev="GENESIS"; seen=set(); rows=read_jsonl(path)
    for i,row in enumerate(rows):
        if row.get("schema_version")!=schema:raise ValueError(f"schema mismatch {path}:{i}")
        rid=str(row.get(id_key) or "")
        if not rid or rid in seen or row.get("previous_hash")!=prev:raise ValueError(f"chain/id failure {path}:{i}")
        b=dict(row); stored=b.pop("event_hash",None)
        if stored!=sha(b):raise ValueError(f"hash failure {path}:{i}")
        seen.add(rid); prev=stored
    return {"ok":True,"events":len(rows),"head_hash":prev}

def memory_analogues(vector:Sequence[float],snaps:Sequence[Mapping[str,Any]],resmap:Mapping[str,Mapping[str,Any]],instrument:str)->dict[str,Any]|None:
    c=[]
    for s in snaps:
        if s.get("instrument")!=instrument:continue
        res=resmap.get(str(s.get("snapshot_id") or "")); v=s.get("feature_vector")
        if not res or not isinstance(v,list) or len(v)!=len(vector):continue
        sim=1/(1+math.sqrt(mean([(float(a)-float(b))**2 for a,b in zip(vector,v)])))
        c.append({"similarity":sim,"up":1 if res.get("outcome_up") else 0,"return":float(res.get("forward_return") or 0),"mae":float(res.get("mae_fraction") or 0)})
    if len(c)<12:return None
    c.sort(key=lambda x:x["similarity"],reverse=True); c=c[:40]; w=[x["similarity"]**3 for x in c]; ws=sum(w)
    return {"source":"PROSPECTIVE_MEMORY","analogues_n":len(c),"p_up":sum(a*x["up"] for a,x in zip(w,c))/ws,"top_similarity":c[0]["similarity"],"mean_similarity":mean([x["similarity"] for x in c]),"median_forward_return":statistics.median([x["return"] for x in c]),"median_adverse_excursion":statistics.median([x["mae"] for x in c])}

def fetch_instrument(client:YahooChartClient,symbol:str)->dict[str,list[Bar]]:
    d={tf:client.bars(symbol,str(s["range"]),str(s["interval"])) for tf,s in TIMEFRAMES.items()}; d["4h"]=resample_fixed(d["1h"],4); return d

def instrument_state(instrument:str,symbol:str,d:Mapping[str,Sequence[Bar]],snaps,resmap)->dict[str,Any]:
    scales={tf:scale_features(d[tf],240 if tf=="4h" else int(TIMEFRAMES[tf]["minutes"])) for tf in ("5m","15m","1h","4h","1d")}
    risk=structural_risk(scales); vec=feature_vector(scales,risk); mem=memory_analogues(vec,snaps,resmap,instrument) or historical_analogues(d["1h"]); pup=clamp(float(mem.get("p_up") or .5))
    return {"instrument":instrument,"symbol":symbol,"observed_at":d["1h"][-1].timestamp.isoformat().replace("+00:00","Z"),"reference_price":d["1h"][-1].close,"scales":scales,"structural_risk":risk,"fractal_memory":mem,"p_up_4h":pup,"forecast":"UP" if pup>=.55 else ("DOWN" if pup<=.45 else "NEUTRAL"),"feature_vector":vec,"atr_1h_fraction":float(scales["1h"].get("atr_fraction") or 0)}

def resolve_snapshots(state_dir:Path,hourly:Mapping[str,Sequence[Bar]],horizon_bars:int=4)->None:
    done={str(x.get("snapshot_id")) for x in read_jsonl(state_dir/RESOLUTIONS_FILE)}
    for s in read_jsonl(state_dir/SNAPSHOTS_FILE):
        sid=str(s.get("snapshot_id") or "")
        if not sid or sid in done:continue
        rows=[x for x in hourly.get(str(s.get("instrument")),[]) if x.timestamp>parse_time(str(s["observed_at"]))]
        if len(rows)<horizon_bars:continue
        path=rows[:horizon_bars]; bar=path[-1]; ref=float(s["reference_price"]); ret=bar.close/ref-1; up=1 if ret>0 else 0; p=clamp(float(s.get("p_up_4h") or .5)); brier=(p-up)**2
        large=1 if abs(ret)>=.75*max(float(s.get("atr_1h_fraction") or 0),1e-9) else 0; pr=clamp(float(s.get("risk_score") or 0)); rb=(pr-large)**2; prts=[x.close/ref-1 for x in path]
        rid="fseres-"+sha({"snapshot_id":sid,"resolved_at":bar.timestamp.isoformat()})[:24]
        append_chain(state_dir/RESOLUTIONS_FILE,RESOLUTION_SCHEMA,{"resolution_id":rid,"snapshot_id":sid,"instrument":s["instrument"],"observed_at":s["observed_at"],"horizon":f"{horizon_bars}x1h_market_bars","resolved_at":bar.timestamp.isoformat().replace("+00:00","Z"),"forward_return":ret,"outcome_up":bool(up),"mae_fraction":min(prts),"mfe_fraction":max(prts),"directional_brier":brier,"directional_brier_edge_vs_0_5":.25-brier,"large_move_event":bool(large),"risk_probability":pr,"risk_brier":rb,"risk_brier_edge_vs_0_5":.25-rb,"prospective_only":True,"authority":dict(ZERO_AUTHORITY)},"resolution_id")
        done.add(sid)

def create_snapshot(state_dir:Path,s:Mapping[str,Any])->None:
    rows=[x for x in read_jsonl(state_dir/SNAPSHOTS_FILE) if x.get("instrument")==s.get("instrument")]
    if rows and rows[-1].get("observed_at")==s.get("observed_at"):return
    sid="fsesnap-"+sha({"instrument":s["instrument"],"observed_at":s["observed_at"]})[:24]
    append_chain(state_dir/SNAPSHOTS_FILE,SNAPSHOT_SCHEMA,{"snapshot_id":sid,"instrument":s["instrument"],"symbol":s["symbol"],"observed_at":s["observed_at"],"reference_price":s["reference_price"],"risk_score":s["structural_risk"]["risk_score"],"regime":s["structural_risk"]["regime"],"p_up_4h":s["p_up_4h"],"analogue_source":s["fractal_memory"].get("source"),"analogue_n":s["fractal_memory"].get("analogues_n"),"atr_1h_fraction":s["atr_1h_fraction"],"feature_vector":list(s["feature_vector"]),"prospective_only":True,"authority":dict(ZERO_AUTHORITY)},"snapshot_id")

def verify(state_dir:Path)->dict[str,Any]:
    a=verify_chain(state_dir/SNAPSHOTS_FILE,SNAPSHOT_SCHEMA,"snapshot_id"); b=verify_chain(state_dir/RESOLUTIONS_FILE,RESOLUTION_SCHEMA,"resolution_id")
    for x in read_jsonl(state_dir/SNAPSHOTS_FILE)+read_jsonl(state_dir/RESOLUTIONS_FILE):
        if x.get("authority")!=ZERO_AUTHORITY:raise ValueError("FSE authority violation")
    return {"ok":True,"snapshots":a,"resolutions":b,"zero_authority":True}

def measurements(resolutions:Sequence[Mapping[str,Any]],instrument:str)->list[dict[str,Any]]:
    r=[x for x in resolutions if x.get("instrument")==instrument]; de=[float(x.get("directional_brier_edge_vs_0_5") or 0) for x in r]; re=[float(x.get("risk_brier_edge_vs_0_5") or 0) for x in r]
    return [
        {"proposal_key":f"fse-fractal-memory-{instrument.lower()}-4h-brier","claim":f"FSE Fractal Memory for {instrument} improves prospective 4x1h-market-bar directional Brier score versus a 50/50 baseline.","champion":"50/50 directional baseline","challenger":"FSE Fractal Memory","metric_name":"brier_improvement_vs_0_5","target_n":40,"success_mean_edge":.0025,"reject_mean_edge":-.0025,"counter":len(de),"total":sum(de),"details":{"instrument":instrument,"horizon":"4x1h_market_bars","kind":"directional_memory"}},
        {"proposal_key":f"fse-structural-risk-{instrument.lower()}-4h-brier","claim":f"FSE structural-risk probability for {instrument} improves prospective 4x1h-market-bar large-move Brier score versus a 50/50 baseline.","champion":"50/50 large-move baseline","challenger":"FSE Structural Risk","metric_name":"risk_brier_improvement_vs_0_5","target_n":40,"success_mean_edge":.0025,"reject_mean_edge":-.0025,"counter":len(re),"total":sum(re),"details":{"instrument":instrument,"horizon":"4x1h_market_bars","large_move_threshold":"0.75 * frozen ATR(1h)","kind":"risk_calibration"}},
    ]

def directional_performance(resolutions:Sequence[Mapping[str,Any]],snapshots:Sequence[Mapping[str,Any]],instrument:str,probability_field:str="p_up_4h",methodology_version:str|None=None,paired_base_field:str|None=None)->dict[str,Any]:
    """Accuracy of frozen, settled LONG/SHORT; neutral counts only in Brier."""
    snap_by={str(s.get("snapshot_id")):s for s in snapshots if s.get("instrument")==instrument and s.get("prospective_only") is True}
    briers=[]; base_briers=[]; signals=correct=0; seen=set()
    for outcome in resolutions:
        sid=str(outcome.get("snapshot_id") or "")
        s=snap_by.get(sid)
        if not s or sid in seen or outcome.get("instrument")!=instrument or outcome.get("prospective_only") is not True:continue
        if not isinstance(outcome.get("outcome_up"),bool):continue
        if methodology_version is not None and (s.get("methodology_version")!=methodology_version or outcome.get("methodology_version")!=methodology_version):continue
        p=finite(s.get(probability_field))
        base=finite(s.get(paired_base_field)) if paired_base_field else None
        if p is None or not 0<=p<=1:continue
        if paired_base_field and (base is None or not 0<=base<=1):continue
        seen.add(sid)
        y=int(outcome["outcome_up"])
        briers.append((p-y)**2)
        if paired_base_field:base_briers.append((base-y)**2)
        direction=True if p>=.55 else (False if p<=.45 else None)
        if direction is not None:
            signals+=1
            correct+=int(direction==outcome["outcome_up"])
    n=len(briers)
    brier=sum(briers)/n if n else None
    paired=sum(base_briers)/n if paired_base_field and n else None
    return {"horizon":"4x1h_market_bars","resolved_n":n,"signal_n":signals,"correct_n":correct,
            "accuracy":correct/signals if signals else None,
            "mean_brier":brier,"baseline_brier":.25,
            "brier_edge_vs_baseline":.25-brier if brier is not None else None,
            "paired_base_brier":paired,"delta_brier_vs_base":paired-brier if paired is not None else None}

def public_projection(states,resolutions,at:str,snapshots=None)->dict[str,Any]:
    inst=[]; ms=[]
    perf=[{"instrument":str(s["instrument"]),**directional_performance(resolutions,snapshots or [],str(s["instrument"]))} for s in states]
    for s in states:
        ms += measurements(resolutions,str(s["instrument"])); risk=s["structural_risk"]; mem=s["fractal_memory"]
        inst.append({"instrument":s["instrument"],"symbol":s["symbol"],"observed_at":s["observed_at"],"reference_price":s["reference_price"],"regime":risk["regime"],"risk_score":risk["risk_score"],"risk_components":risk["components"],"persistence":risk["persistence"],"research_risk_geometry":risk["research_candidate"],"fractal_memory":{"source":mem.get("source"),"analogues_n":mem.get("analogues_n"),"p_up_4h":s["p_up_4h"],"forecast":s["forecast"],"top_similarity":mem.get("top_similarity"),"mean_similarity":mem.get("mean_similarity"),"median_forward_return":mem.get("median_forward_return"),"median_adverse_excursion":mem.get("median_adverse_excursion")},"scale_summary":{tf:{k:v.get(k) for k in ("bars","hurst_q2","multifractality_proxy","excess_kurtosis","tail_ratio_q95_median","atr_fraction")} for tf,v in s["scales"].items()}})
    return {"schema_version":PUBLIC_SCHEMA,"methodology_version":METHODOLOGY_VERSION,"engine":"FSE — Fractal Structure Engine","module_id":"IN-09","mode":"SHADOW_ONLY","generated_at":at,"pipeline":"MULTISCALE MARKET GEOMETRY -> STRUCTURAL RISK + FRACTAL MEMORY -> PROSPECTIVE FREEZE -> FORWARD VERIFICATION","risk_definition":"Risk = f(scale, persistence, multifractality_proxy, tails)","memory_definition":"current state -> normalized structural fingerprint -> analogues -> forward distribution","instruments":inst,"hse_measurements":ms,"directional_performance":perf,"authority":dict(ZERO_AUTHORITY),"production_impact":False,"notes":["4h is resampled from 1h bars.","Multifractality is a generalized-Hurst spread proxy, not full MF-DFA.","Historical 1h analogues bootstrap the model; durable full-vector Fractal Memory takes over after enough prospective resolutions.","Sizing and stop multipliers are research candidates only."]}

def run_cycle(root:Path,state_dir:Path,public_path:Path,instruments:Mapping[str,str]|None=None,client:YahooChartClient|None=None,at:str|None=None)->dict[str,Any]:
    state_dir.mkdir(parents=True,exist_ok=True); instruments=dict(instruments or DEFAULT_INSTRUMENTS); client=client or YahooChartClient()
    snaps=read_jsonl(state_dir/SNAPSHOTS_FILE); res=read_jsonl(state_dir/RESOLUTIONS_FILE); resmap={str(x.get("snapshot_id")):x for x in res}; fetched={}; states=[]; errors={}
    for inst,symbol in instruments.items():
        try: d=fetch_instrument(client,symbol); fetched[inst]=d; states.append(instrument_state(inst,symbol,d,snaps,resmap))
        except Exception as e: errors[inst]=f"{type(e).__name__}: {e}"
    if not states:raise RuntimeError("FSE has no usable instruments: "+canonical(errors))
    resolve_snapshots(state_dir,{k:v["1h"] for k,v in fetched.items()}); res=read_jsonl(state_dir/RESOLUTIONS_FILE)
    for s in states:create_snapshot(state_dir,s)
    verify(state_dir); out=public_projection(states,res,at or datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),read_jsonl(state_dir/SNAPSHOTS_FILE)); out["source_status"]={"configured":len(instruments),"available":len(states),"errors":errors}
    public_path.parent.mkdir(parents=True,exist_ok=True); public_path.write_text(json.dumps(out,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8"); return out

def main()->int:
    p=argparse.ArgumentParser(); p.add_argument("--root",type=Path,default=Path(".")); p.add_argument("--state-dir",type=Path,required=True); p.add_argument("--public",type=Path,default=Path("data/investments/fse_public.json")); p.add_argument("--verify",action="store_true"); p.add_argument("--now"); a=p.parse_args()
    if a.verify: print(json.dumps(verify(a.state_dir),sort_keys=True)); return 0
    public=a.public if a.public.is_absolute() else a.root/a.public; out=run_cycle(a.root,a.state_dir,public,at=a.now); print(json.dumps({"engine":out["engine"],"instruments":len(out["instruments"]),"measurements":len(out["hse_measurements"]),"production_impact":out["production_impact"]},ensure_ascii=False)); return 0

if __name__=="__main__": raise SystemExit(main())
