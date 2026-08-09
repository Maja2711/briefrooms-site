#!/usr/bin/env python3
"""Research-only EUR/USD SMA vs EMA 30/60/100/200 comparison.

Compares identical weekly-entry signals with either SMA or EMA, plus hybrids:
SMA100/200 structural trend + EMA30/60 fast timing. Uses only completed bars
before Monday entry and Monday-Friday hourly outcomes. Never changes production.
"""
from __future__ import annotations
import json, math
from pathlib import Path
from statistics import fmean, median
from typing import Any, Dict, List
import pandas as pd
import yfinance as yf

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/investments/eurusd_sma_vs_ema_research.json'
SYMBOL='EURUSD=X'; TZ='Europe/Warsaw'; WINDOWS=(30,60,100,200)

def series(df,n):
    v=df[n]; return (v.iloc[:,0] if isinstance(v,pd.DataFrame) else v).astype(float)
def clean(df):
    if df is None or df.empty:return pd.DataFrame()
    out=pd.DataFrame({k.lower():series(df,k) for k in ('Open','High','Low','Close') if k in df}).dropna()
    idx=pd.DatetimeIndex(out.index); idx=idx.tz_localize('UTC') if idx.tz is None else idx
    out.index=idx.tz_convert(TZ); return out[~out.index.duplicated(keep='last')].sort_index()
def resample(df,rule):
    return df.resample(rule,label='right',closed='right').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
def features(df,kind):
    o=df.copy()
    for w in WINDOWS:
        o[f'ma{w}']=o.close.rolling(w).mean() if kind=='sma' else o.close.ewm(span=w,adjust=False,min_periods=w).mean()
    prev=o.close.shift(1); tr=pd.concat([o.high-o.low,(o.high-prev).abs(),(o.low-prev).abs()],axis=1).max(axis=1)
    o['atr14']=tr.rolling(14).mean(); o['ma30_prev']=o.ma30.shift(1); o['ma60_prev']=o.ma60.shift(1); o['close_prev']=o.close.shift(1)
    return o.dropna()
def snap(df,ts):
    e=df[df.index<ts]
    if e.empty:return None
    r=e.iloc[-1]; p=float(r.close); atr=max(float(r.atr14),1e-12); m={w:float(r[f'ma{w}']) for w in WINDOWS}
    near=min((abs(p-v),w,v) for w,v in m.items()); slow=1 if m[100]>m[200] else -1 if m[100]<m[200] else 0
    fast=1 if m[30]>m[60] else -1 if m[30]<m[60] else 0
    stack=1 if m[30]>m[60]>m[100]>m[200] else -1 if m[30]<m[60]<m[100]<m[200] else 0
    pa=1 if p>max(m.values()) else -1 if p<min(m.values()) else 0
    return {'slow':slow,'fast':fast,'stack':stack,'price_all':pa,
      'support_long':slow==1 and p>=near[2] and near[0]<=.35*atr and float(r.low)<=near[2]+.10*atr,
      'resistance_short':slow==-1 and p<=near[2] and near[0]<=.35*atr and float(r.high)>=near[2]-.10*atr,
      'reclaim_long':float(r.close_prev)<=float(r.ma30_prev) and p>m[30] and slow==1,
      'reclaim_short':float(r.close_prev)>=float(r.ma30_prev) and p<m[30] and slow==-1}
def summary(vals,side):
    x=vals if side=='long' else [-v for v in vals]
    if not x:return {'count':0,'mean_week_percent':None,'median_week_percent':None,'hit_rate':None}
    return {'count':len(x),'mean_week_percent':round(fmean(x),6),'median_week_percent':round(median(x),6),'hit_rate':round(sum(v>0 for v in x)/len(x),6)}
def collect(records,pred,side):return summary([r['ret'] for r in records if pred(r)],side)
def tests(records,key):
    f=lambda r:r[key]
    return {
      'full_stack':{s:collect(records,lambda r,s=s:f(r)['stack']==(1 if s=='long' else -1),s) for s in ('long','short')},
      'fast_with_slow':{s:collect(records,lambda r,s=s:f(r)['fast']==(1 if s=='long' else -1) and f(r)['slow']==(1 if s=='long' else -1),s) for s in ('long','short')},
      'price_vs_all':{s:collect(records,lambda r,s=s:f(r)['price_all']==(1 if s=='long' else -1),s) for s in ('long','short')},
      'support_resistance':{'long':collect(records,lambda r:f(r)['support_long'],'long'),'short':collect(records,lambda r:f(r)['resistance_short'],'short')},
      'reclaim30':{'long':collect(records,lambda r:f(r)['reclaim_long'],'long'),'short':collect(records,lambda r:f(r)['reclaim_short'],'short')}}
def edge(x):
    if not x or (x.get('count') or 0)<12 or x.get('hit_rate') is None:return None
    c=x['count']; return round((x['hit_rate']-.5)*math.sqrt(c)+max(min(x['mean_week_percent']/.15,1.5),-1.5)*.25,6)
def main():
    h1=clean(yf.download(SYMBOL,period='720d',interval='1h',progress=False,auto_adjust=False,prepost=True,threads=False))
    d1=clean(yf.download(SYMBOL,period='10y',interval='1d',progress=False,auto_adjust=False,threads=False))
    if h1.empty or d1.empty: raise SystemExit('price download failed')
    raw={'H1':h1,'D1':d1,'W1':resample(d1,'W-FRI')}
    frames={(tf,k):features(df,k) for tf,df in raw.items() for k in ('sma','ema')}
    records=[]
    for day in pd.date_range(h1.index.min().normalize(),h1.index.max().normalize(),freq='W-MON',tz=TZ):
        et=day+pd.Timedelta(hours=8); xt=day+pd.Timedelta(days=4,hours=22); er=h1[h1.index>=et]; xr=h1[h1.index>=xt]
        if er.empty or xr.empty or er.index[0]>et+pd.Timedelta(hours=3) or xr.index[0]>xt+pd.Timedelta(hours=4):continue
        ts=er.index[0]; rec={'week':day.date().isoformat(),'ret':(float(xr.iloc[0].close)/float(er.iloc[0].close)-1)*100}
        ok=True
        for tf in ('H1','D1','W1'):
            for k in ('sma','ema'):
                rec[f'{tf}_{k}']=snap(frames[(tf,k)],ts); ok &= rec[f'{tf}_{k}'] is not None
        if ok:
            # Hybrid: slow structure from SMA, fast/trigger/location from EMA.
            for tf in ('H1','D1','W1'):
                s,e=rec[f'{tf}_sma'],rec[f'{tf}_ema']; rec[f'{tf}_hybrid']={**e,'slow':s['slow'],'support_long':s['support_long'],'resistance_short':s['resistance_short']}
            records.append(rec)
    result={'symbol':SYMBOL,'sample_weeks':len(records),'sample_start':records[0]['week'] if records else None,'sample_end':records[-1]['week'] if records else None,
      'methodology':'same completed-bar weekly sample; SMA vs EMA vs hybrid SMA100/200 structure + EMA30/60 timing; no production changes', 'timeframes':{}}
    ranking=[]
    for tf in ('H1','D1','W1'):
      result['timeframes'][tf]={}
      for k in ('sma','ema','hybrid'):
        t=tests(records,f'{tf}_{k}'); result['timeframes'][tf][k]=t
        for test,sides in t.items():
          for side,x in sides.items():
            sc=edge(x)
            if sc is not None: ranking.append({'timeframe':tf,'average':k,'test':test,'side':side,'edge_score':sc,**x})
    result['ranking']=sorted(ranking,key=lambda x:x['edge_score'],reverse=True)
    result['top_20']=result['ranking'][:20]
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'sample_weeks':len(records),'top_10':result['ranking'][:10]},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
