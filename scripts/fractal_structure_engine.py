#!/usr/bin/env python3
"""BriefRooms FSE — Fractal Structure Engine.

Research/shadow only. It separates structural risk from directional Fractal
Memory and has zero authority to change positions, sizing, stops or execution.
"""
from __future__ import annotations

import argparse, hashlib, json, math, os, statistics, urllib.parse, urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, time
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

SCHEMA="briefrooms-fractal-structure-engine-v1"
PUBLIC_SCHEMA="briefrooms-fse-public-v1"
METHODOLOGY_VERSION="FSE-CORE-3.0"
SEARCH_MIN_EPISODES=8
SEARCH_WINDOW=64
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
    source_observed_at:datetime|None=None
    closed_at:datetime|None=None
    calendar_timezone:str|None=None
    session_closed_at:datetime|None=None

def bar_closed_at(bar:Bar,minutes:int=60)->datetime:
    """Known interval end; timestamp always denotes the canonical interval OPEN."""
    return bar.closed_at or bar.timestamp+timedelta(minutes=minutes)

def bar_is_closed(bar:Bar,as_of:datetime|str,minutes:int=60)->bool:
    cutoff=parse_time(as_of) if isinstance(as_of,str) else as_of
    return bar_closed_at(bar,minutes)<=cutoff

def _bucket_bounds(timestamp:datetime,minutes:int,symbol:str="",daily_timezone:str="UTC",session:tuple[datetime,datetime]|None=None)->tuple[datetime,datetime]|None:
    if symbol.upper()=="SPY":
        local=timestamp.astimezone(ZoneInfo("America/New_York"))
        if local.weekday()>=5:return None
        start=datetime.combine(local.date(),time(9,30),local.tzinfo)
        end=datetime.combine(local.date(),time(16),local.tzinfo)
        if session is not None:start,end=(t.astimezone(local.tzinfo) for t in session)
        if minutes>=1440:return start.astimezone(timezone.utc),end.astimezone(timezone.utc)
        # A regular-session closing quote belongs to the last real bar.
        if local==end:local-=timedelta(microseconds=1)
        if not start<=local<end:return None
        opened=start+timedelta(minutes=int((local-start).total_seconds()//(minutes*60))*minutes)
        return opened.astimezone(timezone.utc),min(opened+timedelta(minutes=minutes),end).astimezone(timezone.utc)
    if minutes>=1440:
        local=timestamp.astimezone(ZoneInfo(daily_timezone))
        opened=datetime.combine(local.date(),time(),local.tzinfo)
        return opened.astimezone(timezone.utc),(opened+timedelta(days=1)).astimezone(timezone.utc)
    seconds=minutes*60
    opened=datetime.fromtimestamp(int(timestamp.timestamp())//seconds*seconds,tz=timezone.utc)
    return opened,opened+timedelta(minutes=minutes)

def parse_yahoo_bars(payload:Mapping[str,Any],symbol:str,interval:str,as_of:datetime|str|None=None)->list[Bar]:
    """Normalize Yahoo bars without treating its trailing live quote as a candle.

    Off-grid quote rows may update an existing bucket only. OHLC fields and
    quote observation clocks remain distinct from canonical opening timestamps.
    """
    cutoff=parse_time(as_of) if isinstance(as_of,str) else (as_of or datetime.now(timezone.utc))
    result=((payload.get("chart") or {}).get("result") or [None])[0]
    if not result:raise RuntimeError(f"no Yahoo chart for {symbol} {interval}")
    minutes={"1m":1,"5m":5,"15m":15,"30m":30,"60m":60,"1h":60,"1d":1440}.get(interval)
    if minutes is None:raise ValueError("unsupported Yahoo interval: "+interval)
    meta=result.get("meta") or {}; tz="America/New_York" if symbol.upper()=="SPY" else str(meta.get("exchangeTimezoneName") or "UTC")
    sessions={}
    def session_rows(value):
        if isinstance(value,list):
            for row in value:session_rows(row)
        elif isinstance(value,dict):
            if finite(value.get("start")) is not None and finite(value.get("end")) is not None:
                start=datetime.fromtimestamp(float(value["start"]),timezone.utc); end=datetime.fromtimestamp(float(value["end"]),timezone.utc)
                if start<end:sessions[start.astimezone(ZoneInfo("America/New_York")).date()]=(start,end)
            elif "regular" in value:session_rows(value["regular"])
    if symbol.upper()=="SPY":
        session_rows(meta.get("tradingPeriods")); session_rows((meta.get("currentTradingPeriod") or {}).get("regular"))
    q=(((result.get("indicators") or {}).get("quote") or [{}])[0]); grouped={}
    raw_rows=[]
    for i,value in enumerate(result.get("timestamp") or []):
        stamp=finite(value)
        if stamp is None:continue
        try:observed=datetime.fromtimestamp(stamp,tz=timezone.utc)
        except (ValueError,OverflowError,OSError):continue
        if observed>cutoff+timedelta(seconds=60):continue
        def field(name):
            values=q.get(name) or []
            return values[i] if i<len(values) else None
        close=finite(field("close"))
        if close is None or close<=0:continue
        raw=[field(k) for k in ("open","high","low")]
        values=[finite(v) for v in raw]
        if any(v is not None and (n is None or n<=0) for v,n in zip(raw,values)):continue
        op,hi,lo=values
        if hi is not None and hi<max(close,op if op is not None else close):continue
        if lo is not None and lo>min(close,op if op is not None else close):continue
        if hi is not None and lo is not None and hi<lo:continue
        session=sessions.get(observed.astimezone(ZoneInfo("America/New_York")).date()) if symbol.upper()=="SPY" else None
        bounds=_bucket_bounds(observed,minutes,symbol,tz,session)
        if bounds is None:continue
        opened,closed=bounds
        # Without a provider quote clock, do not manufacture source freshness
        # from the time this HTTP request happened to finish.
        source=(closed if closed<=cutoff else opened) if observed==opened else observed
        volume=finite(field("volume")); volume=volume if volume is not None and volume>=0 else None
        session_close=(session[1] if session is not None else _bucket_bounds(observed,1440,symbol)[1]) if symbol.upper()=="SPY" else None
        raw_rows.append((observed,opened,closed,close,op,hi,lo,volume,source,session_close))
    # Deterministic even for out-of-order provider arrays. Regular bars precede
    # updates; no off-grid row can create an invented market interval.
    raw_rows.sort(key=lambda row:(row[1],row[0]!=row[1],row[0]))
    explicit_quotes={}
    for observed,opened,closed,close,op,hi,lo,volume,source,session_close in raw_rows:
        old=grouped.get(opened)
        if observed!=opened:
            if old is None:continue
            highs=[v for v in (old.high,hi,close) if v is not None]
            lows=[v for v in (old.low,lo,close) if v is not None]
            vols=[v for v in (old.volume,volume) if v is not None]
            grouped[opened]=Bar(opened,close,old.open,max(highs),min(lows),max(vols) if vols else None,source,closed,old.calendar_timezone,old.session_closed_at)
            explicit_quotes[opened]=observed
        else:
            grouped[opened]=Bar(opened,close,op,hi,lo,volume,source,closed,tz,session_close)
    # Some responses carry the last quote only in metadata. It can refresh a
    # real bucket, but cannot create a new candle with invented OHLC.
    quote_time=finite(meta.get("regularMarketTime")); quote_price=finite(meta.get("regularMarketPrice"))
    if quote_time is not None and quote_price is not None and quote_price>0:
        observed=datetime.fromtimestamp(quote_time,timezone.utc)
        session=sessions.get(observed.astimezone(ZoneInfo("America/New_York")).date()) if symbol.upper()=="SPY" else None
        bounds=_bucket_bounds(observed,minutes,symbol,tz,session)
        if bounds is not None and observed<=cutoff:
            opened,closed=bounds; old=grouped.get(opened)
            if old is not None and observed>=explicit_quotes.get(opened,opened):
                grouped[opened]=Bar(opened,quote_price,old.open,max(old.high if old.high is not None else old.close,quote_price),
                                    min(old.low if old.low is not None else old.close,quote_price),old.volume,observed,closed,old.calendar_timezone,old.session_closed_at)
    return [grouped[k] for k in sorted(grouped)]

class YahooChartClient:
    def __init__(self,timeout:int=20)->None: self.timeout=timeout
    def bars(self,symbol:str,range_:str,interval:str)->list[Bar]:
        enc=urllib.parse.quote(symbol,safe="")
        url=f"{YAHOO_BASE}/{enc}?range={range_}&interval={interval}&includePrePost=false&events=div%2Csplits"
        req=urllib.request.Request(url,headers={"User-Agent":USER_AGENT,"Accept":"application/json"})
        with urllib.request.urlopen(req,timeout=self.timeout) as resp: payload=json.load(resp)
        out=parse_yahoo_bars(payload,symbol,interval)
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

def resample_minutes(bars:Sequence[Bar],minutes:int,symbol:str|None=None)->list[Bar]:
    """Stable time/session buckets, retaining explicit close and quote clocks."""
    grouped={}; ends={}
    for bar in sorted(bars,key=lambda b:b.timestamp):
        effective_symbol=symbol or ("SPY" if bar.calendar_timezone=="America/New_York" and bar.session_closed_at is not None else "")
        session=None
        if effective_symbol.upper()=="SPY" and bar.session_closed_at is not None:
            local=bar.timestamp.astimezone(ZoneInfo("America/New_York"))
            session=(datetime.combine(local.date(),time(9,30),local.tzinfo),bar.session_closed_at)
        bounds=_bucket_bounds(bar.timestamp,minutes,effective_symbol,bar.calendar_timezone or "UTC",session)
        if bounds is None:continue
        opened,closed=bounds; grouped.setdefault(opened,[]).append(bar); ends[opened]=closed
    out=[]
    for opened,group in sorted(grouped.items()):
        highs=[b.high if b.high is not None else b.close for b in group]
        lows=[b.low if b.low is not None else b.close for b in group]
        sources=[b.source_observed_at or b.timestamp for b in group]
        volumes=[b.volume for b in group if b.volume is not None]
        out.append(Bar(opened,group[-1].close,group[0].open if group[0].open is not None else group[0].close,
                       max(highs),min(lows),sum(volumes) if len(volumes)==len(group) else None,max(sources),ends[opened],group[0].calendar_timezone,group[0].session_closed_at))
    return out

def resample_fixed(bars:Sequence[Bar],factor:int)->list[Bar]:
    """Compatibility wrapper; buckets are time-anchored, never input-index based."""
    rows=sorted(bars,key=lambda b:b.timestamp)
    gaps=[(b.timestamp-a.timestamp).total_seconds()/60 for a,b in zip(rows,rows[1:]) if b.timestamp>a.timestamp]
    base=int(min(gaps)) if gaps else 60
    return resample_minutes(rows,max(1,base)*factor)

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

def _search_distance(a:Sequence[float],b:Sequence[float])->float:
    if not a or len(a)!=len(b):return math.inf
    return math.sqrt(mean([(float(x)-float(y))**2 for x,y in zip(a,b)]))

def _independent_search_rows(rows:Sequence[Mapping[str,Any]],limit:int)->list[Mapping[str,Any]]:
    """Select whole feature-and-outcome episodes; nearby windows get one vote."""
    selected=[]
    for row in rows:
        if any(not (row["end"]<old["start"] or row["start"]>old["end"]) for old in selected):continue
        selected.append(row)
        if len(selected)>=limit:break
    return selected

def _quality_selected(rows:Sequence[Mapping[str,Any]],vector:Sequence[float],top_k:int)->tuple[list[Mapping[str,Any]],dict[str,Any]]:
    """Calibrate distance from historical independent episodes, never outcomes.

    The declared 0.75 ceiling is a research prior, not an optimized alpha claim.
    At most 64 past anchors bound the quadratic calibration cost.
    """
    anchors=_independent_search_rows(sorted(rows,key=lambda row:row["start"]),64)
    quality={"raw_windows_n":len(rows),"independent_episodes_n":len(anchors),"minimum_episodes":SEARCH_MIN_EPISODES,
             "available":False,"status":"ABSTAIN","quality_threshold":None,"reason":"insufficient independent episodes"}
    if len(anchors)<SEARCH_MIN_EPISODES:return [],quality
    distances=[min(_search_distance(a["vector"],b["vector"]) for b in anchors if b is not a) for a in anchors]
    threshold=min(.75,max(1e-6,quantile(distances,.90)*1.5))
    ranked=sorted((dict(row,distance=_search_distance(vector,row["vector"])) for row in rows),key=lambda row:(row["distance"],row["start"]))
    selected=_independent_search_rows([row for row in ranked if row["distance"]<=threshold],top_k)
    quality.update(quality_threshold=threshold,independent_analogues_n=len(selected),reason="poor analogue quality or insufficient independent matches")
    if len(selected)<SEARCH_MIN_EPISODES:return [],quality
    quality.update(available=True,status="MATCHED",reason="independent historical matches passed the frozen search-quality rule")
    return selected,quality

def historical_analogues(hourly:Sequence[Bar],horizon_bars:int=4,top_k:int=50)->dict[str,Any]:
    r=list(hourly); cur=path_fingerprint(r)
    empty={"source":"HISTORICAL_BOOTSTRAP","analogues_n":0,"p_up":None,"available":False,"status":"ABSTAIN","reason":"insufficient historical data"}
    if len(r)<200 or not cur:return empty
    cand=[]
    query_start=len(r)-SEARCH_WINDOW
    for end in range(SEARCH_WINDOW,query_start-horizon_bars+1,8):
        fp=path_fingerprint(r[:end]); ref=r[end-1].close; fut=r[end:end+horizon_bars]
        if not fp or ref<=0 or len(fut)<horizon_bars:continue
        ret=fut[-1].close/ref-1; path=[x.close/ref-1 for x in fut]
        cand.append({"vector":fp,"start":end-SEARCH_WINDOW,"end":end+horizon_bars-1,"similarity":similarity(cur,fp),"return":ret,"min":min(path),"max":max(path)})
    c,quality=_quality_selected(cand,cur,top_k)
    quality["query_exclusion_bars"]=SEARCH_WINDOW
    if not c:return dict(empty,**quality)
    w=[max(x["similarity"],1e-6)**3 for x in c]; ws=sum(w); pup=sum(a for a,x in zip(w,c) if x["return"]>0)/ws; up=pup>=.5
    adv=[-x["min"] if up else x["max"] for x in c]
    return {"source":"HISTORICAL_BOOTSTRAP","analogues_n":len(c),"p_up":pup,"top_similarity":c[0]["similarity"],"mean_similarity":mean([x["similarity"] for x in c]),"median_forward_return":statistics.median([x["return"] for x in c]),"median_adverse_excursion":statistics.median(adv),**quality}

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

def memory_analogues(vector:Sequence[float],snaps:Sequence[Mapping[str,Any]],resmap:Mapping[str,Mapping[str,Any]],instrument:str,as_of:datetime|None=None,query_start:datetime|None=None)->dict[str,Any]|None:
    c=[]
    for s in snaps:
        if s.get("instrument")!=instrument or s.get("methodology_version")!=METHODOLOGY_VERSION:continue
        res=resmap.get(str(s.get("snapshot_id") or "")); v=s.get("search_vector")
        if not res or res.get("methodology_version")!=METHODOLOGY_VERSION or not isinstance(v,list) or len(v)!=len(vector):continue
        if as_of is not None and parse_time(str(res["resolved_at"]))>as_of:continue
        if not s.get("search_window_start_at") or not res.get("outcome_closed_at"):continue
        start=parse_time(s["search_window_start_at"]); end=parse_time(res["outcome_closed_at"])
        if query_start is not None and end>=query_start:continue
        sim=1/(1+math.sqrt(mean([(float(a)-float(b))**2 for a,b in zip(vector,v)])))
        c.append({"vector":v,"start":start,"end":end,"similarity":sim,"up":1 if res.get("outcome_up") else 0,"return":float(res.get("forward_return") or 0),"mae":float(res.get("mae_fraction") or 0)})
    if len(c)<SEARCH_MIN_EPISODES:return None
    c,quality=_quality_selected(c,vector,40)
    if not c:return None  # A separate historical search may qualify; it must pass its own gate.
    w=[x["similarity"]**3 for x in c]; ws=sum(w)
    return {"source":"PROSPECTIVE_MEMORY","analogues_n":len(c),"p_up":sum(a*x["up"] for a,x in zip(w,c))/ws,"top_similarity":c[0]["similarity"],"mean_similarity":mean([x["similarity"] for x in c]),"median_forward_return":statistics.median([x["return"] for x in c]),"median_adverse_excursion":statistics.median([x["mae"] for x in c]),**quality}

def fetch_instrument(client:YahooChartClient,symbol:str)->dict[str,list[Bar]]:
    d={tf:client.bars(symbol,str(s["range"]),str(s["interval"])) for tf,s in TIMEFRAMES.items()}; d["4h"]=resample_minutes(d["1h"],240,symbol); return d

def instrument_state(instrument:str,symbol:str,d:Mapping[str,Sequence[Bar]],snaps,resmap)->dict[str,Any]:
    scales={tf:scale_features(d[tf],240 if tf=="4h" else int(TIMEFRAMES[tf]["minutes"])) for tf in ("5m","15m","1h","4h","1d")}
    risk=structural_risk(scales); vec=feature_vector(scales,risk); last=d["1h"][-1]
    query_start=d["1h"][-min(SEARCH_WINDOW,len(d["1h"]))].timestamp
    search_vector=path_fingerprint(d["1h"])
    mem=memory_analogues(search_vector,snaps,resmap,instrument,last.source_observed_at or last.timestamp,query_start=query_start) or historical_analogues(d["1h"])
    raw_p=finite(mem.get("p_up")); pup=clamp(raw_p) if raw_p is not None else None
    forecast="ABSTAIN" if pup is None else ("UP" if pup>=.55 else ("DOWN" if pup<=.45 else "NEUTRAL"))
    return {"instrument":instrument,"symbol":symbol,"observed_at":(last.source_observed_at or last.timestamp).isoformat().replace("+00:00","Z"),"bar_open_at":last.timestamp.isoformat().replace("+00:00","Z"),"search_window_start_at":query_start.isoformat(),"search_vector":search_vector,"reference_price":last.close,"scales":scales,"structural_risk":risk,"fractal_memory":mem,"p_up_4h":pup,"forecast":forecast,"feature_vector":vec,"atr_1h_fraction":float(scales["1h"].get("atr_fraction") or 0)}

def resolve_snapshots(state_dir:Path,hourly:Mapping[str,Sequence[Bar]],horizon_bars:int=4,as_of:str|datetime|None=None)->None:
    cutoff=parse_time(as_of) if isinstance(as_of,str) else (as_of or datetime.now(timezone.utc))
    done={str(x.get("snapshot_id")) for x in read_jsonl(state_dir/RESOLUTIONS_FILE)}
    for s in read_jsonl(state_dir/SNAPSHOTS_FILE):
        sid=str(s.get("snapshot_id") or "")
        if not sid or sid in done or s.get("methodology_version")!=METHODOLOGY_VERSION:continue
        opened=parse_time(str(s["bar_open_at"])); frozen=parse_time(str(s["frozen_at"]))
        rows=sorted((x for x in hourly.get(str(s.get("instrument")),[]) if x.timestamp>opened),key=lambda x:x.timestamp)
        if len(rows)<horizon_bars:continue
        path=rows[:horizon_bars]; bar=path[-1]; closed=bar_closed_at(bar)
        # Every target bar must begin after the real freeze, and the last must
        # close before scoring. Old feed backfills never become prospective.
        if path[0].timestamp<frozen or closed>cutoff:continue
        ref=float(s["reference_price"]); ret=bar.close/ref-1; up=1 if ret>0 else 0
        raw_p=finite(s.get("p_up_4h")); p=clamp(raw_p) if raw_p is not None else None; brier=(p-up)**2 if p is not None else None
        large=1 if abs(ret)>=.75*max(float(s.get("atr_1h_fraction") or 0),1e-9) else 0; pr=clamp(float(s.get("risk_score") or 0)); rb=(pr-large)**2; prts=[x.close/ref-1 for x in path]
        rid="fseres-"+sha({"methodology_version":METHODOLOGY_VERSION,"snapshot_id":sid,"outcome_closed_at":closed.isoformat()})[:24]
        append_chain(state_dir/RESOLUTIONS_FILE,RESOLUTION_SCHEMA,{"resolution_id":rid,"snapshot_id":sid,"instrument":s["instrument"],"observed_at":s["observed_at"],"bar_open_at":s["bar_open_at"],"frozen_at":s["frozen_at"],"horizon":f"{horizon_bars}x1h_market_bars","resolved_at":cutoff.isoformat().replace("+00:00","Z"),"outcome_closed_at":closed.isoformat().replace("+00:00","Z"),"methodology_version":METHODOLOGY_VERSION,"forward_return":ret,"outcome_up":bool(up),"mae_fraction":min(prts),"mfe_fraction":max(prts),"directional_brier":brier,"directional_brier_edge_vs_0_5":.25-brier if brier is not None else None,"large_move_event":bool(large),"risk_probability":pr,"risk_brier":rb,"risk_brier_edge_vs_0_5":.25-rb,"prospective_only":True,"authority":dict(ZERO_AUTHORITY)},"resolution_id")
        done.add(sid)

def create_snapshot(state_dir:Path,s:Mapping[str,Any],frozen_at:str|None=None)->None:
    rows=[x for x in read_jsonl(state_dir/SNAPSHOTS_FILE) if x.get("instrument")==s.get("instrument")]
    opened=str(s.get("bar_open_at") or s["observed_at"])
    if any(x.get("methodology_version")==METHODOLOGY_VERSION and x.get("bar_open_at")==opened for x in rows):return
    frozen=frozen_at or datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
    sid="fsesnap-"+sha({"methodology_version":METHODOLOGY_VERSION,"instrument":s["instrument"],"bar_open_at":opened})[:24]
    append_chain(state_dir/SNAPSHOTS_FILE,SNAPSHOT_SCHEMA,{"snapshot_id":sid,"instrument":s["instrument"],"symbol":s["symbol"],"observed_at":s["observed_at"],"bar_open_at":opened,"search_window_start_at":s.get("search_window_start_at"),"search_vector":list(s.get("search_vector") or []),"frozen_at":frozen,"methodology_version":METHODOLOGY_VERSION,"reference_price":s["reference_price"],"risk_score":s["structural_risk"]["risk_score"],"regime":s["structural_risk"]["regime"],"p_up_4h":s["p_up_4h"],"frozen_memory":dict(s["fractal_memory"]),"analogue_source":s["fractal_memory"].get("source"),"analogue_n":s["fractal_memory"].get("analogues_n"),"atr_1h_fraction":s["atr_1h_fraction"],"feature_vector":list(s["feature_vector"]),"prospective_only":True,"authority":dict(ZERO_AUTHORITY)},"snapshot_id")

def bind_frozen_forecast(state:dict[str,Any],snapshot:Mapping[str,Any])->None:
    """A live feature refresh cannot silently replace the immutable forecast."""
    state["latest_source_observed_at"]=state["observed_at"]
    state["live_features_observed_at"]=state["observed_at"]
    state["frozen_forecast"]={key:snapshot.get(key) for key in ("snapshot_id","methodology_version","bar_open_at","observed_at","frozen_at","reference_price","p_up_4h")}
    state["p_up_4h"]=finite(snapshot.get("p_up_4h"))
    state["forecast"]="ABSTAIN" if state["p_up_4h"] is None else ("UP" if state["p_up_4h"]>=.55 else ("DOWN" if state["p_up_4h"]<=.45 else "NEUTRAL"))
    state["fractal_memory"]=dict(snapshot.get("frozen_memory") or {"source":snapshot.get("analogue_source"),"analogues_n":snapshot.get("analogue_n")})

def verify(state_dir:Path)->dict[str,Any]:
    a=verify_chain(state_dir/SNAPSHOTS_FILE,SNAPSHOT_SCHEMA,"snapshot_id"); b=verify_chain(state_dir/RESOLUTIONS_FILE,RESOLUTION_SCHEMA,"resolution_id")
    snapshots=read_jsonl(state_dir/SNAPSHOTS_FILE); resolutions=read_jsonl(state_dir/RESOLUTIONS_FILE)
    for x in snapshots+resolutions:
        if x.get("authority")!=ZERO_AUTHORITY:raise ValueError("FSE authority violation")
    current={s["snapshot_id"]:s for s in snapshots if s.get("methodology_version")==METHODOLOGY_VERSION}
    for s in current.values():
        if parse_time(s["frozen_at"])<parse_time(s["bar_open_at"]):raise ValueError("FSE snapshot frozen before bar opened")
        if parse_time(s["observed_at"])>parse_time(s["frozen_at"]):raise ValueError("FSE source observed after freeze")
    settled=set()
    for r in resolutions:
        if r.get("methodology_version")!=METHODOLOGY_VERSION:continue
        sid=r.get("snapshot_id"); s=current.get(sid)
        if not s or sid in settled or s["instrument"]!=r.get("instrument"):raise ValueError("FSE resolution snapshot linkage failure")
        if not parse_time(s["frozen_at"])<parse_time(r["outcome_closed_at"])<=parse_time(r["resolved_at"]):raise ValueError("FSE resolution chronology failure")
        settled.add(sid)
    return {"ok":True,"snapshots":a,"resolutions":b,"zero_authority":True}

def measurements(resolutions:Sequence[Mapping[str,Any]],instrument:str)->list[dict[str,Any]]:
    r=[x for x in resolutions if x.get("instrument")==instrument and x.get("methodology_version")==METHODOLOGY_VERSION]; de=[float(x["directional_brier_edge_vs_0_5"]) for x in r if finite(x.get("directional_brier_edge_vs_0_5")) is not None]; re=[float(x["risk_brier_edge_vs_0_5"]) for x in r if finite(x.get("risk_brier_edge_vs_0_5")) is not None]
    return [
        {"proposal_key":f"fse-fractal-memory-{instrument.lower()}-4h-brier","claim":f"FSE Fractal Memory for {instrument} improves prospective 4x1h-market-bar directional Brier score versus a 50/50 baseline.","champion":"50/50 directional baseline","challenger":"FSE Fractal Memory","metric_name":"brier_improvement_vs_0_5","target_n":40,"success_mean_edge":.0025,"reject_mean_edge":-.0025,"counter":len(de),"total":sum(de),"details":{"instrument":instrument,"horizon":"4x1h_market_bars","kind":"directional_memory","methodology_version":METHODOLOGY_VERSION}},
        {"proposal_key":f"fse-structural-risk-{instrument.lower()}-4h-brier","claim":f"FSE structural-risk probability for {instrument} improves prospective 4x1h-market-bar large-move Brier score versus a 50/50 baseline.","champion":"50/50 large-move baseline","challenger":"FSE Structural Risk","metric_name":"risk_brier_improvement_vs_0_5","target_n":40,"success_mean_edge":.0025,"reject_mean_edge":-.0025,"counter":len(re),"total":sum(re),"details":{"instrument":instrument,"horizon":"4x1h_market_bars","large_move_threshold":"0.75 * frozen ATR(1h)","kind":"risk_calibration","methodology_version":METHODOLOGY_VERSION}},
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
    perf=[{"instrument":str(s["instrument"]),**directional_performance(resolutions,snapshots or [],str(s["instrument"]),methodology_version=METHODOLOGY_VERSION)} for s in states]
    for s in states:
        ms += measurements(resolutions,str(s["instrument"])); risk=s["structural_risk"]; mem=s["fractal_memory"]
        inst.append({"instrument":s["instrument"],"symbol":s["symbol"],"observed_at":s["observed_at"],"latest_source_observed_at":s.get("latest_source_observed_at",s["observed_at"]),"live_features_observed_at":s.get("live_features_observed_at",s["observed_at"]),"reference_price":s["reference_price"],"frozen_forecast":s.get("frozen_forecast"),"regime":risk["regime"],"risk_score":risk["risk_score"],"risk_components":risk["components"],"persistence":risk["persistence"],"research_risk_geometry":risk["research_candidate"],"fractal_memory":{**{k:mem.get(k) for k in ("available","status","reason","raw_windows_n","independent_episodes_n","independent_analogues_n","minimum_episodes","quality_threshold","query_exclusion_bars")},"source":mem.get("source"),"analogues_n":mem.get("analogues_n"),"p_up_4h":s["p_up_4h"],"forecast":s["forecast"],"top_similarity":mem.get("top_similarity"),"mean_similarity":mem.get("mean_similarity"),"median_forward_return":mem.get("median_forward_return"),"median_adverse_excursion":mem.get("median_adverse_excursion")},"scale_summary":{tf:{k:v.get(k) for k in ("bars","hurst_q2","multifractality_proxy","excess_kurtosis","tail_ratio_q95_median","atr_fraction")} for tf,v in s["scales"].items()}})
    legacy={"snapshots":sum(s.get("methodology_version")!=METHODOLOGY_VERSION for s in snapshots or []),"resolutions":sum(r.get("methodology_version")!=METHODOLOGY_VERSION for r in resolutions),"included_in_current_metrics":False}
    return {"schema_version":PUBLIC_SCHEMA,"methodology_version":METHODOLOGY_VERSION,"engine":"FSE — Fractal Structure Engine","module_id":"IN-09","mode":"SHADOW_ONLY","generated_at":at,"legacy_evidence":legacy,"pipeline":"MULTISCALE MARKET GEOMETRY -> STRUCTURAL RISK + FRACTAL MEMORY -> PROSPECTIVE FREEZE -> FORWARD VERIFICATION","risk_definition":"Risk = f(scale, persistence, multifractality_proxy, tails)","memory_definition":"current state -> normalized structural fingerprint -> analogues -> forward distribution","instruments":inst,"hse_measurements":ms,"directional_performance":perf,"authority":dict(ZERO_AUTHORITY),"production_impact":False,"notes":["4h is resampled from fixed time/session buckets of 1h bars.","Multifractality is a generalized-Hurst spread proxy, not full MF-DFA.","Historical 1h analogues bootstrap the model; durable 64-bar shape memory uses the same retrieval vector after enough independent prospective episodes; multiscale geometry remains descriptive context.","Legacy cohorts remain immutable and excluded from current metrics.","Sizing and stop multipliers are research candidates only."]}

def run_cycle(root:Path,state_dir:Path,public_path:Path,instruments:Mapping[str,str]|None=None,client:YahooChartClient|None=None,at:str|None=None)->dict[str,Any]:
    state_dir.mkdir(parents=True,exist_ok=True); instruments=dict(instruments or DEFAULT_INSTRUMENTS); client=client or YahooChartClient()
    verify(state_dir)
    snaps=read_jsonl(state_dir/SNAPSHOTS_FILE); res=read_jsonl(state_dir/RESOLUTIONS_FILE); resmap={str(x.get("snapshot_id")):x for x in res}; fetched={}; states=[]; errors={}
    for inst,symbol in instruments.items():
        try: d=fetch_instrument(client,symbol); fetched[inst]=d; states.append(instrument_state(inst,symbol,d,snaps,resmap))
        except Exception as e: errors[inst]=f"{type(e).__name__}: {e}"
    if not states:raise RuntimeError("FSE has no usable instruments: "+canonical(errors))
    frozen_at=at or datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
    resolve_snapshots(state_dir,{k:v["1h"] for k,v in fetched.items()},as_of=frozen_at); res=read_jsonl(state_dir/RESOLUTIONS_FILE)
    for s in states:create_snapshot(state_dir,s,frozen_at=frozen_at)
    frozen_rows=read_jsonl(state_dir/SNAPSHOTS_FILE)
    for s in states:
        same=next(row for row in reversed(frozen_rows) if row.get("instrument")==s["instrument"] and row.get("methodology_version")==METHODOLOGY_VERSION and row.get("bar_open_at")==s["bar_open_at"])
        bind_frozen_forecast(s,same)
    verify(state_dir); out=public_projection(states,res,frozen_at,frozen_rows); out["source_status"]={"configured":len(instruments),"available":len(states),"errors":errors}
    public_path.parent.mkdir(parents=True,exist_ok=True); public_path.write_text(json.dumps(out,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8"); return out

def main()->int:
    p=argparse.ArgumentParser(); p.add_argument("--root",type=Path,default=Path(".")); p.add_argument("--state-dir",type=Path,required=True); p.add_argument("--public",type=Path,default=Path("data/investments/fse_public.json")); p.add_argument("--verify",action="store_true"); p.add_argument("--now"); a=p.parse_args()
    if a.verify: print(json.dumps(verify(a.state_dir),sort_keys=True)); return 0
    public=a.public if a.public.is_absolute() else a.root/a.public; out=run_cycle(a.root,a.state_dir,public,at=a.now); print(json.dumps({"engine":out["engine"],"instruments":len(out["instruments"]),"measurements":len(out["hse_measurements"]),"production_impact":out["production_impact"]},ensure_ascii=False)); return 0

if __name__=="__main__": raise SystemExit(main())
