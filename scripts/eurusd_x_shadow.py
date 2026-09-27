#!/usr/bin/env python3
"""EUR/USD X adaptive shadow research engine.

Fixed core:
- MA30/60/100/200 on 1H/1D/1W/1M
- classic daily Pivot
- Bollinger(20, 2.5 sigma) on 1H/1D
- BriefRooms Belief Core

Only the interpretation layer may adapt. Fixed technical parameters are immutable.
Champion/challenger calibration is prospective and can roll back to the last champion.
"""
from __future__ import annotations
import argparse, json, math
from datetime import datetime, timezone, timedelta
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Mapping, Sequence

from belief_market_data_adapter import Bar, YahooChartClient
from daily_eurusd_experiment import belief_snapshot

SCHEMA="eurusd-x-shadow-v1"
ENGINE="EURUSD_X"
PAIR="EURUSD=X"
MA_PERIODS=(30,60,100,200)
BOLL_WINDOW=20
BOLL_SIGMA=2.5
ROUNDTRIP_PIPS=2.0
PIP=0.0001
CALIBRATION_BLOCK=5
MIN_PROMOTION_N=5
LOOKBACK=20

def iso(dt:datetime)->str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00","Z")
def parse(s:str)->datetime:
    return datetime.fromisoformat(s.replace("Z","+00:00")).astimezone(timezone.utc)
def clamp(x:float)->float: return max(-1.0,min(1.0,x))
def avg(xs): return mean(xs) if xs else 0.0
def sma(rows:Sequence[Bar],n:int)->float:
    if len(rows)<n: raise ValueError(f"need {n} bars")
    return mean(x.close for x in rows[-n:])
def slope(rows:Sequence[Bar],n:int)->float:
    if len(rows)<n+1:return 0.0
    now=sma(rows,n); prev=mean(x.close for x in rows[-n-1:-1])
    return 0.0 if prev==0 else now/prev-1.0
def bb(rows:Sequence[Bar])->dict[str,float]:
    xs=[x.close for x in rows[-BOLL_WINDOW:]]
    if len(xs)<BOLL_WINDOW: raise ValueError("insufficient bollinger history")
    mid=mean(xs); sd=pstdev(xs); upper=mid+BOLL_SIGMA*sd; lower=mid-BOLL_SIGMA*sd
    px=xs[-1]; z=0.0 if sd==0 else (px-mid)/sd
    return {"mid":mid,"upper":upper,"lower":lower,"z":z,"width":0.0 if mid==0 else (upper-lower)/mid}
def pivot(daily:Sequence[Bar])->dict[str,float]:
    if len(daily)<2: raise ValueError("insufficient daily bars")
    b=daily[-2]
    if b.high is None or b.low is None: raise ValueError("daily OHLC unavailable")
    p=(b.high+b.low+b.close)/3
    return {"p":p,"r1":2*p-b.low,"s1":2*p-b.high,"r2":p+(b.high-b.low),"s2":p-(b.high-b.low)}
def tf_state(rows:Sequence[Bar])->dict[str,Any]:
    px=rows[-1].close
    mas={str(n):sma(rows,n) for n in MA_PERIODS}
    slopes={str(n):slope(rows,n) for n in MA_PERIODS}
    pos={str(n):(1 if px>mas[str(n)] else -1 if px<mas[str(n)] else 0) for n in MA_PERIODS}
    signed=clamp(0.75*avg(list(pos.values()))+0.25*avg([1 if x>0 else -1 if x<0 else 0 for x in slopes.values()]))
    return {"price":px,"ma":mas,"slope":slopes,"position":pos,"signed":round(signed,6)}
def experimental_signals(h1:Sequence[Bar], states:Mapping[str,Any], b1:Mapping[str,float], bd:Mapping[str,float])->dict[str,float]:
    # Additive research features. They never mutate the fixed core.
    consensus=clamp(avg([float(x["signed"]) for x in states.values()]))
    disagreement=clamp((float(states["1H"]["signed"])-float(states["1D"]["signed"]))/2.0)
    reentry=clamp(-float(b1["z"])/BOLL_SIGMA) if abs(float(b1["z"]))>=2.0 else 0.0
    closes=[x.close for x in h1[-12:]]
    accel=0.0
    if len(closes)>=12 and closes[-7] and closes[-12]:
        recent=closes[-1]/closes[-6]-1.0
        prior=closes[-7]/closes[-12]-1.0
        accel=clamp((recent-prior)/0.004)
    ma30=float(states["1H"]["ma"]["30"]); ma200=float(states["1H"]["ma"]["200"]); px=float(states["1H"]["price"])
    spread=0.0 if px==0 else clamp(((ma30-ma200)/px)/0.01)
    return {"tf_consensus":round(consensus,6),"h1_d1_disagreement":round(disagreement,6),
            "bb_1h_extreme_reentry":round(reentry,6),"h1_momentum_acceleration":round(accel,6),
            "h1_ma30_ma200_spread":round(spread,6)}

def technical_state(h1,d1,w1,m1)->dict[str,Any]:
    states={"1H":tf_state(h1),"1D":tf_state(d1),"1W":tf_state(w1),"1M":tf_state(m1)}
    # Equal contribution keeps the core transparent; adaptation happens only above this layer.
    ma_signed=avg([x["signed"] for x in states.values()])
    b1=bb(h1); bd=bb(d1); pv=pivot(d1); px=h1[-1].close
    # Bollinger contributes momentum only inside 2.5σ; outside 2.5σ is treated as exhaustion pressure.
    def bb_sig(x):
        z=x["z"]
        return clamp(z/BOLL_SIGMA) if abs(z)<=BOLL_SIGMA else clamp((2*BOLL_SIGMA-abs(z))/BOLL_SIGMA)*(1 if z>0 else -1)
    pivot_sig=1.0 if px>pv["p"] else -1.0 if px<pv["p"] else 0.0
    signed=clamp(0.70*ma_signed+0.20*avg([bb_sig(b1),bb_sig(bd)])+0.10*pivot_sig)
    return {"timeframes":states,"bollinger":{"1H":b1,"1D":bd},"pivot":pv,"signed":round(signed,6),
            "experimental":experimental_signals(h1,states,b1,bd)}
def decision(comp:Mapping[str,float],setup:Mapping[str,Any])->dict[str,Any]:
    tw=float(setup["technical_weight"]); bw=float(setup["belief_weight"]); th=float(setup["threshold"])
    ew=float(setup.get("experimental_weight",0.0) or 0.0); feature=setup.get("experimental_feature")
    extra=float((comp.get("experimental") or {}).get(feature,0.0)) if feature else 0.0
    score=clamp(tw*float(comp["technical"])+bw*float(comp["belief"])+ew*extra)
    side="LONG" if score>=th else "SHORT" if score<=-th else "NO_TRADE"
    return {"side":side,"score":round(score,6),"confidence":round(abs(score),6),"setup_version":setup["version"]}
def setup_metrics(captures:list[dict],key:str,version:str)->dict[str,Any]:
    vals=[]
    for c in captures:
        if c.get("status")!="RESOLVED": continue
        d=(c.get("decisions") or {}).get(key) or {}
        if d.get("setup_version")!=version or d.get("side")=="NO_TRADE": continue
        r=d.get("net_return_pips")
        if r is not None: vals.append(float(r))
    wins=[x for x in vals if x>0]; losses=[x for x in vals if x<0]
    pf=(sum(wins)/abs(sum(losses))) if losses else (999.0 if wins else None)
    return {"n":len(vals),"expectancy_pips":round(avg(vals),3) if vals else None,"profit_factor":round(pf,3) if pf is not None else None,"hit_rate":round(len(wins)/len(vals),4) if vals else None}
def propose(state:dict)->dict[str,Any]:
    champ=state["champion"]; recent=[c for c in state["captures"] if c.get("status")=="RESOLVED"][-LOOKBACK:]
    tech=[]; belief=[]
    for c in recent:
        raw=c.get("raw_return_pips")
        if raw is None or raw==0: continue
        y=1 if raw>0 else -1
        comp=c.get("components") or {}
        tech.append(y*float(comp.get("technical",0)))
        belief.append(y*float(comp.get("belief",0)))
    feature_scores={}
    for name in ("tf_consensus","h1_d1_disagreement","bb_1h_extreme_reentry","h1_momentum_acceleration","h1_ma30_ma200_spread"):
        vals=[]
        for row in recent:
            raw=row.get("raw_return_pips")
            val=((row.get("components") or {}).get("experimental") or {}).get(name)
            if raw is None or raw==0 or val is None: continue
            vals.append((1 if raw>0 else -1)*float(val))
        feature_scores[name]=avg(vals)
    best_feature=max(feature_scores,key=feature_scores.get) if feature_scores else None
    best_score=feature_scores.get(best_feature,0.0) if best_feature else 0.0
    delta=0.05 if avg(tech)>avg(belief)+0.03 else -0.05 if avg(belief)>avg(tech)+0.03 else 0.0
    tw=max(0.35,min(0.80,float(champ["technical_weight"])+delta)); bw=1.0-tw
    ew=0.0
    if best_feature and best_score>0.05:
        ew=0.10; tw=round(tw*0.90,4); bw=round(bw*0.90,4)
    seq=int(state.get("setup_sequence",1))+1; state["setup_sequence"]=seq
    return {"version":f"X-{seq:03d}","technical_weight":round(tw,4),"belief_weight":round(bw,4),
            "experimental_feature":best_feature if ew else None,"experimental_weight":ew,
            "threshold":champ["threshold"],"created_at":state["updated_at"],
            "reason":"experimental_feature_challenger" if ew else "recent_component_reliability_shift",
            "research_scores":feature_scores}
def calibrate(state:dict)->None:
    ch=state.get("challenger")
    if ch:
        cm=setup_metrics(state["captures"],"champion",state["champion"]["version"])
        xm=setup_metrics(state["captures"],"challenger",ch["version"])
        if xm["n"]>=MIN_PROMOTION_N:
            better=(xm["expectancy_pips"] or -999)>(cm["expectancy_pips"] or -999)+0.5 and (xm["profit_factor"] or 0)>=(cm["profit_factor"] or 0)
            event={"at":state["updated_at"],"champion":state["champion"]["version"],"challenger":ch["version"],"champion_metrics":cm,"challenger_metrics":xm}
            if better:
                state["checkpoints"].append(dict(state["champion"]))
                state["champion"]=dict(ch); event["action"]="PROMOTE_CHALLENGER"
            else:
                event["action"]="ROLLBACK_KEEP_CHAMPION"
            state["calibration_log"].append(event); state["challenger"]=None
    resolved=sum(1 for c in state["captures"] if c.get("status")=="RESOLVED")
    last=int(state.get("last_proposal_resolved",0))
    if state.get("challenger") is None and resolved-last>=CALIBRATION_BLOCK:
        state["challenger"]=propose(state); state["last_proposal_resolved"]=resolved
def resolve(state:dict,h1:Sequence[Bar],now:datetime)->None:
    for c in state["captures"]:
        if c.get("status")!="OPEN": continue
        t0=parse(c["observed_at"])
        if now<t0+timedelta(hours=24): continue
        future=[b for b in h1 if b.timestamp>=t0+timedelta(hours=24)]
        if not future: continue
        exit_px=future[0].close; entry=float(c["reference_price"]); raw=(exit_px-entry)/PIP
        c["status"]="RESOLVED"; c["resolved_at"]=iso(future[0].timestamp); c["exit_price"]=exit_px; c["raw_return_pips"]=round(raw,3)
        for key,d in (c.get("decisions") or {}).items():
            side=d.get("side")
            if side=="NO_TRADE": d["net_return_pips"]=None
            else:
                signed=raw*(1 if side=="LONG" else -1)
                d["net_return_pips"]=round(signed-ROUNDTRIP_PIPS,3)
def load(path:Path)->dict:
    if path.exists(): return json.loads(path.read_text(encoding="utf-8"))
    now=iso(datetime.now(timezone.utc))
    return {"schema_version":SCHEMA,"mode":"research_shadow","created_at":now,"updated_at":now,"setup_sequence":1,
      "champion":{"version":"X-001","technical_weight":0.65,"belief_weight":0.35,"threshold":0.15,"created_at":now},
      "challenger":None,"checkpoints":[],"calibration_log":[],"last_proposal_resolved":0,"captures":[]}
def public(state:dict,tech:dict,belief:dict)->dict:
    champ=state["champion"]; ch=state.get("challenger")
    return {"schema_version":SCHEMA,"engine":"EURUSD X","mode":"SHADOW ONLY","generated_at":state["updated_at"],
      "fixed_core":{"ma_periods":list(MA_PERIODS),"ma_timeframes":["1H","1D","1W","1M"],"pivot":"classic_daily","bollinger":{"window":BOLL_WINDOW,"sigma":BOLL_SIGMA,"timeframes":["1H","1D"]},"belief_core":"BriefRooms Belief Core"},
      "adaptive_layer":{"calibration_block_resolved":CALIBRATION_BLOCK,"champion":champ,"challenger":ch,"rollback":"automatic_keep_last_champion","transaction_cost_pips_roundtrip":ROUNDTRIP_PIPS},
      "discovery_layer":{"enabled":True,"candidate_features":["tf_consensus","h1_d1_disagreement","bb_1h_extreme_reentry","h1_momentum_acceleration","h1_ma30_ma200_spread"],"policy":"new technical features/strategies/anomalies are versioned challengers; fixed core is never mutated"},
      "current":{"technical_signed":tech.get("signed"),"belief_signed":belief.get("signed_score"),"reference_price":tech.get("timeframes",{}).get("1H",{}).get("price")},
      "performance":{"champion":setup_metrics(state["captures"],"champion",champ["version"]),"challenger":setup_metrics(state["captures"],"challenger",ch["version"]) if ch else None},
      "sample":{"captures":len(state["captures"]),"resolved":sum(c.get("status")=="RESOLVED" for c in state["captures"])},
      "last_calibration":state["calibration_log"][-1] if state["calibration_log"] else None}
def run(state_path:Path,public_path:Path,belief_path:Path|None)->dict:
    client=YahooChartClient(timeout=20)
    h1=client.bars(PAIR,"3mo","1h"); d1=client.bars(PAIR,"2y","1d"); w1=client.bars(PAIR,"10y","1wk"); m1=client.bars(PAIR,"20y","1mo")
    now=datetime.now(timezone.utc); state=load(state_path); state["updated_at"]=iso(now)
    resolve(state,h1,now); calibrate(state)
    tech=technical_state(h1,d1,w1,m1)
    bp=json.loads(belief_path.read_text(encoding="utf-8")) if belief_path and belief_path.exists() else None
    belief=belief_snapshot(bp,h1[-1].timestamp)
    if not belief.get("available"): belief={"available":False,"signed_score":0.0,"reason":belief.get("reason")}
    # one capture per new H1 market observation
    observed=h1[-1].timestamp
    if not state["captures"] or state["captures"][-1].get("observed_at")!=iso(observed):
        comp={"technical":float(tech["signed"]),"belief":float(belief.get("signed_score") or 0.0),"experimental":dict(tech.get("experimental") or {})}
        decisions={"champion":decision(comp,state["champion"])}
        if state.get("challenger"): decisions["challenger"]=decision(comp,state["challenger"])
        state["captures"].append({"capture_id":f"x-{int(observed.timestamp())}","observed_at":iso(observed),"reference_price":h1[-1].close,"components":comp,"decisions":decisions,"status":"OPEN"})
        state["captures"]=state["captures"][-500:]
    state_path.parent.mkdir(parents=True,exist_ok=True); public_path.parent.mkdir(parents=True,exist_ok=True)
    state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    out=public(state,tech,belief); public_path.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return out
def main():
    p=argparse.ArgumentParser(); p.add_argument("--state",type=Path,required=True); p.add_argument("--public",type=Path,required=True); p.add_argument("--belief-state",type=Path)
    a=p.parse_args(); out=run(a.state,a.public,a.belief_state); print(json.dumps({"engine":out["engine"],"sample":out["sample"],"champion":out["adaptive_layer"]["champion"]["version"]}))
if __name__=="__main__": main()
