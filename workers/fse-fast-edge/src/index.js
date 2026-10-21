const SCALE_ORDER = ["1m","5m","15m","1h","4h","1d","1w"];
const FAST_SCALES = ["1m","5m","15m","1h"];
const INSTRUMENTS = {
  EURUSD: "EURUSD=X",
  BTCUSD: "BTC-USD",
  SPX: "SPY",
};
const ZERO_AUTHORITY = {
  production_policy_writeback: false,
  production_ranking_writeback: false,
  production_sizing_writeback: false,
  production_stop_writeback: false,
  source_model_writeback: false,
  trade_execution: false,
  automatic_promotion: false,
};
export const TRANSPORT_VERSION = "FSE-FAST-2.0";
const CACHE_KEY = new Request("https://briefrooms-fse-fast.internal/snapshot-v2");
const SOURCE_STALE_MS = 12 * 60_000;
const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "access-control-allow-origin": "*",
  "cache-control": "no-store",
};

export function finiteNumber(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === "string" && value.trim() === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

export function mean(values) {
  return values.length ? values.reduce((a,b)=>a+b,0) / values.length : 0;
}

export function stdev(values) {
  if (values.length < 2) return 0;
  const m = mean(values);
  return Math.sqrt(values.reduce((acc,x)=>acc+(x-m)**2,0) / values.length);
}

export function clamp(value, lo=0, hi=1) {
  return Math.max(lo, Math.min(hi, Number(value)));
}

export function logReturns(closes) {
  const out=[];
  for (let i=1;i<closes.length;i+=1) {
    const a=Number(closes[i-1]), b=Number(closes[i]);
    if (a>0 && b>0) out.push(Math.log(b/a));
  }
  return out;
}

function pyRound(value) {
  const floor=Math.floor(value);
  const frac=value-floor;
  if (Math.abs(frac-0.5)<1e-12) return floor%2===0?floor:floor+1;
  return Math.round(value);
}

async function sha256Hex(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b)=>b.toString(16).padStart(2,"0")).join("");
}

function canonicalSignature(signature) {
  return JSON.stringify({
    curvature_bucket: signature.curvature_bucket,
    direction: signature.direction,
    efficiency_bucket: signature.efficiency_bucket,
    phase: signature.phase,
    vol_bucket: signature.vol_bucket,
  });
}

export function parseYahooBars(payload, now=Date.now(), instrument=null) {
  const result=payload?.chart?.result?.[0];
  const timestamps=Array.isArray(result?.timestamp)?result.timestamp:[];
  const quote=result?.indicators?.quote?.[0]||{};
  const rows=new Map();
  const ordered=timestamps.map((stamp,i)=>[finiteNumber(stamp),i]).filter(([stamp])=>stamp!==null).sort((a,b)=>a[0]-b[0]);
  for (const [,i] of ordered) {
    const close=finiteNumber(quote.close?.[i]);
    const epoch=finiteNumber(timestamps[i]);
    if (close==null || close<=0 || epoch==null) continue;
    if (epoch*1000>now+60_000) throw new Error("future_source_clock");
    const sourceMs=epoch*1000;
    let effectiveMs=sourceMs;
    if (instrument==="SPX") {
      const local=nyClock(sourceMs),session=spySession(new Date(Date.UTC(local.year,local.month-1,local.day)));
      if(!session||sourceMs<session.open||sourceMs>session.close)continue;
      if(sourceMs===session.close)effectiveMs-=1;
    }
    const bucket=Math.floor(effectiveMs/60_000)*60_000;
    const old=rows.get(bucket);
    if (sourceMs!==bucket) {
      // A provider's off-grid live quote can update a known minute only.
      if (!old) continue;
      rows.set(bucket,{...old,close,high:Math.max(old.high??old.close,close),low:Math.min(old.low??old.close,close),source_observed_at:new Date(epoch*1000).toISOString()});
      continue;
    }
    const open=finiteNumber(quote.open?.[i]),high=finiteNumber(quote.high?.[i]),low=finiteNumber(quote.low?.[i]);
    if([open,high,low].some(x=>x!==null&&x<=0)||(high!==null&&high<Math.max(open??close,close))||(low!==null&&low>Math.min(open??close,close)))continue;
    rows.set(bucket,{
      timestamp:new Date(bucket).toISOString(),
      source_observed_at:new Date(bucket+60_000<=now?bucket+60_000:bucket).toISOString(),
      closed_at:new Date(bucket+60_000).toISOString(),
      close,
      open,
      high,
      low,
      volume:finiteNumber(quote.volume?.[i]),
    });
  }
  return [...rows.entries()].sort((a,b)=>a[0]-b[0]).map(([,row])=>row);
}

function nyClock(ms) {
  const parts=new Intl.DateTimeFormat("en-US",{timeZone:"America/New_York",year:"numeric",month:"numeric",day:"numeric",hour:"numeric",minute:"numeric",hourCycle:"h23"}).formatToParts(new Date(ms));
  const out=Object.fromEntries(parts.filter(x=>x.type!=="literal").map(x=>[x.type,Number(x.value)]));
  out.weekday=new Date(Date.UTC(out.year,out.month-1,out.day)).getUTCDay();
  return out;
}

function nyInstant(year,month,day,hour,minute=0) {
  const guess=Date.UTC(year,month-1,day,hour,minute);
  const local=nyClock(guess);
  return guess+(guess-Date.UTC(local.year,local.month-1,local.day,local.hour,local.minute));
}

function observedHoliday(year,month,day) {
  const d=new Date(Date.UTC(year,month-1,day));
  d.setUTCDate(d.getUTCDate()+(d.getUTCDay()===6?-1:d.getUTCDay()===0?1:0));
  return d.toISOString().slice(0,10);
}

function easterSunday(year) {
  const a=year%19,b=Math.floor(year/100),c=year%100,d=Math.floor(b/4),e=b%4,f=Math.floor((b+8)/25),g=Math.floor((b-f+1)/3);
  const h=(19*a+b-d-g+15)%30,i=Math.floor(c/4),k=c%4,l=(32+2*e+2*i-h-k)%7,m=Math.floor((a+11*h+22*l)/451);
  return new Date(Date.UTC(year,Math.floor((h+l-7*m+114)/31)-1,(h+l-7*m+114)%31+1));
}

function nthWeekday(year,month,weekday,n) {
  const first=new Date(Date.UTC(year,month-1,1));
  return 1+(weekday-first.getUTCDay()+7)%7+(n-1)*7;
}

function spySession(date) {
  const year=date.getUTCFullYear(),month=date.getUTCMonth()+1,day=date.getUTCDate();
  if ([0,6].includes(date.getUTCDay())) return null;
  const key=date.toISOString().slice(0,10),goodFriday=easterSunday(year);goodFriday.setUTCDate(goodFriday.getUTCDate()-2);
  const memorial=new Date(Date.UTC(year,5,0));memorial.setUTCDate(memorial.getUTCDate()-(memorial.getUTCDay()+1)%7);
  const holidays=new Set([
    new Date(Date.UTC(year,0,1+(new Date(Date.UTC(year,0,1)).getUTCDay()===0?1:0))).toISOString().slice(0,10),observedHoliday(year,6,19),observedHoliday(year,7,4),observedHoliday(year,12,25),
    new Date(Date.UTC(year,0,nthWeekday(year,1,1,3))).toISOString().slice(0,10),
    new Date(Date.UTC(year,1,nthWeekday(year,2,1,3))).toISOString().slice(0,10),
    memorial.toISOString().slice(0,10),goodFriday.toISOString().slice(0,10),
    new Date(Date.UTC(year,8,nthWeekday(year,9,1,1))).toISOString().slice(0,10),
    new Date(Date.UTC(year,10,nthWeekday(year,11,4,4))).toISOString().slice(0,10),
  ]);
  if (holidays.has(key)) return null;
  const thanksgiving=nthWeekday(year,11,4,4);
  const early=(month===11&&day===thanksgiving+1)||(month===12&&day===24)||(month===7&&day===3);
  return {open:nyInstant(year,month,day,9,30),close:nyInstant(year,month,day,early?13:16)};
}

export function marketReference(instrument,now=Date.now()) {
  if (instrument==="BTCUSD") return {market_state:"OPEN",reference:now};
  const local=nyClock(now),date=new Date(Date.UTC(local.year,local.month-1,local.day));
  if (instrument==="EURUSD") {
    const minute=local.hour*60+local.minute;
    if ((local.weekday>=1&&local.weekday<=4)||(local.weekday===5&&minute<17*60)||(local.weekday===0&&minute>=17*60)) return {market_state:"OPEN",reference:now};
    const sinceFriday=(local.weekday-5+7)%7;
    date.setUTCDate(date.getUTCDate()-sinceFriday);
    return {market_state:"CLOSED",reference:nyInstant(date.getUTCFullYear(),date.getUTCMonth()+1,date.getUTCDate(),17)};
  }
  if (instrument!=="SPX") throw new Error("unknown_instrument");
  for (let i=0;i<10;i+=1) {
    const session=spySession(date);
    if (session&&now>=session.open) return {market_state:now<session.close?"OPEN":"CLOSED",reference:Math.min(now,session.close)};
    date.setUTCDate(date.getUTCDate()-1);
  }
  throw new Error("market_session_unavailable");
}

export function validateMinuteSource(bars,instrument,now=Date.now()) {
  if (bars.length<90) throw new Error(`insufficient_1m_bars_${bars.length}`);
  const last=bars.at(-1),lastMs=Date.parse(last.source_observed_at||last.timestamp);
  if (!Number.isFinite(lastMs)||lastMs>now+60_000) throw new Error("future_or_invalid_source_clock");
  const {market_state,reference}=marketReference(instrument,now);
  const lag=Math.max(0,reference-(lastMs+60_000));
  if (lag>SOURCE_STALE_MS) throw new Error(`stale_1m_source_${Math.round(lag/60_000)}m`);
  return {market_state,source_reference_at:new Date(reference).toISOString(),source_age_minutes:Math.max(0,(now-lastMs)/60_000),source_lag_minutes:lag/60_000};
}

function aggregateBars(rows, timestampMs) {
  const closes=rows.map((x)=>x.close);
  const highs=rows.map((x)=>x.high).filter((x)=>x!=null);
  const lows=rows.map((x)=>x.low).filter((x)=>x!=null);
  return {
    timestamp:new Date(timestampMs).toISOString(),
    close:rows.at(-1).close,
    open:rows[0].open ?? rows[0].close,
    high:highs.length?Math.max(...highs):Math.max(...closes),
    low:lows.length?Math.min(...lows):Math.min(...closes),
    volume:rows.reduce((acc,x)=>acc+Number(x.volume||0),0),
  };
}

export function resampleMinutes(bars, minutes, instrument=null) {
  const bucketMs=minutes*60_000;
  const buckets=new Map();
  for (const bar of bars) {
    const ms=Date.parse(bar.timestamp);
    if (!Number.isFinite(ms)) continue;
    const local=instrument==="SPX"?nyClock(ms):null;
    const session=local?spySession(new Date(Date.UTC(local.year,local.month-1,local.day))):null;
    if (local&&(!session||ms<session.open||ms>=session.close)) continue;
    const anchor=local?nyInstant(local.year,local.month,local.day,9,30):0;
    const key=Math.floor((ms-anchor)/bucketMs)*bucketMs+anchor;
    if (!buckets.has(key)) buckets.set(key,[]);
    buckets.get(key).push(bar);
  }
  return [...buckets.keys()].sort((a,b)=>a-b).map((key)=>{
    const rows=buckets.get(key),bar=aggregateBars(rows,key);
    bar.source_observed_at=rows.at(-1).source_observed_at||rows.at(-1).timestamp;
    const local=instrument==="SPX"?nyClock(key):null;
    const session=local?spySession(new Date(Date.UTC(local.year,local.month-1,local.day))):null;
    const closedMs=session?Math.min(key+bucketMs,session.close):key+bucketMs;
    bar.closed_at=new Date(closedMs).toISOString();
    const expected=Math.max(1,Math.round((closedMs-key)/60_000));
    bar.is_partial=rows.length<expected;
    return bar;
  });
}

export async function phaseDescriptor(bars, window=64) {
  const r=bars.slice(-window);
  if (r.length<16) return {available:false,phase:"INSUFFICIENT_DATA",confidence:0};
  const closes=r.map((b)=>Number(b.close));
  const rs=logReturns(closes);
  const n=rs.length;
  const recentN=Math.max(6,Math.floor(n/4));
  const recent=rs.slice(-recentN);
  let prior=rs.slice(-2*recentN,-recentN);
  if (!prior.length) prior=rs.slice(0,-recentN);
  const net=rs.reduce((a,b)=>a+b,0);
  const gross=rs.reduce((a,b)=>a+Math.abs(b),0);
  const efficiency=clamp(Math.abs(net)/Math.max(gross,1e-12));
  const sigma=Math.max(stdev(rs),1e-12);
  const trendZ=net/(sigma*Math.sqrt(Math.max(n,1)));
  const volRatio=stdev(recent)/Math.max(stdev(prior),1e-12);
  const half=Math.max(8,Math.floor(closes.length/2));
  const early=logReturns(closes.slice(0,half));
  const late=logReturns(closes.slice(-half));
  const curvature=Math.tanh((mean(late)-mean(early))/sigma);
  const lateZ=recent.reduce((a,b)=>a+b,0)/(sigma*Math.sqrt(Math.max(recent.length,1)));
  const direction=trendZ>=0.75?"UP":(trendZ<=-0.75?"DOWN":"FLAT");
  const reversal=direction!=="FLAT"&&((direction==="UP"&&lateZ<-0.45)||(direction==="DOWN"&&lateZ>0.45));
  let phase;
  if (reversal) phase="REVERSAL";
  else if (efficiency>=0.52&&volRatio>=1.00) phase="EXPANSION";
  else if (efficiency>=0.38&&volRatio<0.82) phase="MATURATION";
  else if (efficiency<=0.24&&volRatio<=1.02) phase="CONSOLIDATION";
  else if (volRatio>=1.25) phase="TRANSITION";
  else phase="DEVELOPMENT";
  const confidence=clamp(
    0.40*Math.min(Math.abs(trendZ)/2,1)
    +0.30*Math.min(Math.abs(Math.log(Math.max(volRatio,1e-9)))/0.7,1)
    +0.30*Math.abs(curvature)
  );
  const signature={
    direction,
    phase,
    efficiency_bucket:pyRound(efficiency*10),
    vol_bucket:pyRound(clamp(volRatio/2)*10),
    curvature_bucket:pyRound((curvature+1)*5),
  };
  const digest=await sha256Hex(canonicalSignature(signature));
  return {
    available:true,
    structure_id:"FS-"+digest.slice(0,8).toUpperCase(),
    phase,
    direction,
    confidence,
    efficiency,
    trend_z:trendZ,
    volatility_ratio:volRatio,
    curvature,
  };
}

export async function fetchJson(url,timeoutMs=4000) {
  const response=await fetch(url,{signal:AbortSignal.timeout(timeoutMs),headers:{"cache-control":"no-cache","accept":"application/json"}});
  if (!response.ok) throw new Error(`http_${response.status}:${url}`);
  return response.json();
}

export async function fetchMinuteBars(symbol,instrument,now=Date.now()) {
  const enc=encodeURIComponent(symbol);
  const direct1=`https://query1.finance.yahoo.com/v8/finance/chart/${enc}?range=7d&interval=1m&includePrePost=false&events=div%2Csplits`;
  const direct2=`https://query2.finance.yahoo.com/v8/finance/chart/${enc}?range=7d&interval=1m&includePrePost=false&events=div%2Csplits`;
  const candidates=[
    direct1,
    direct2,
    `https://proxy.cors.dev/${direct1}`,
    `https://api.allorigins.win/raw?url=${encodeURIComponent(direct1)}`,
  ];
  const errors=[];
  for (const url of candidates) {
    try {
      const bars=parseYahooBars(await fetchJson(`${url}${url.includes("?")?"&":"?"}_=${Date.now()}`),now,instrument);
      const sourceStatus=validateMinuteSource(bars,instrument,now);
      return {bars,sourceStatus};
    } catch (error) {
      errors.push(String(error?.message||error));
    }
  }
  throw new Error(`all_sources_failed:${errors.join("|")}`);
}

export async function mergedPhaseMap(baseRow, minuteBars, instrument=null) {
  const base=baseRow?.phase_map||{};
  const scales={
    "1m":minuteBars,
    "5m":resampleMinutes(minuteBars,5,instrument),
    "15m":resampleMinutes(minuteBars,15,instrument),
    "1h":resampleMinutes(minuteBars,60,instrument),
  };
  const out={};
  for (const tf of SCALE_ORDER) {
    let prior={...(base[tf]||{})};
    if (FAST_SCALES.includes(tf)) {
      const bars=scales[tf];
      prior=await phaseDescriptor(bars);
      prior.bars=bars.length;
      prior.observed_at=bars.length?bars.at(-1).timestamp:null;
      prior.fast_refreshed=true;
      prior.provisional=true;
      prior.source_observed_at=bars.at(-1)?.source_observed_at||prior.observed_at;
      prior.phase_progress=null;
      prior.phase_memory=null;
    } else {
      prior.fast_refreshed=false;
    }
    out[tf]=prior;
  }
  return out;
}

export function crossScaleAlignment(phaseMap) {
  const pairs=[];
  for (let i=0;i<SCALE_ORDER.length-1;i+=1) {
    const fast=SCALE_ORDER[i],slow=SCALE_ORDER[i+1];
    const a=phaseMap?.[fast]||{},b=phaseMap?.[slow]||{};
    if (!a.available||!b.available) continue;
    const da=a.direction,db=b.direction;
    const directionScore=(da==="FLAT"||db==="FLAT")?0.5:(da===db?1:0);
    const shapeDist=Math.sqrt(mean([
      (Number(a.efficiency||0)-Number(b.efficiency||0))**2,
      (Math.tanh(Math.log(Math.max(Number(a.volatility_ratio||1),1e-9)))-Math.tanh(Math.log(Math.max(Number(b.volatility_ratio||1),1e-9))))**2,
      (Number(a.curvature||0)-Number(b.curvature||0))**2,
    ]));
    const shapeScore=clamp(1-shapeDist/1.25);
    const conf=Math.sqrt(clamp(Number(a.confidence||0))*clamp(Number(b.confidence||0)));
    const pa=a.phase_progress,pb=b.phase_progress;
    const lead=(pa==null||pb==null)?null:clamp(0.5+(Number(pa)-Number(pb))/0.8);
    const score=0.45*directionScore+0.40*shapeScore+0.15*conf;
    pairs.push({fast,slow,alignment:score,fast_lead:lead});
  }
  const alignment=pairs.length?mean(pairs.map((x)=>x.alignment)):0;
  const leads=pairs.filter((x)=>x.fast_lead!=null).map((x)=>Number(x.fast_lead));
  const ranked=Object.entries(phaseMap||{}).filter(([,row])=>row?.available===true).map(([tf,row])=>{
    const mem=row?.phase_memory||{};
    return [Number(row?.confidence||0)*Number(mem?.top_similarity||0),tf];
  });
  let dominantScale=null,best=-Infinity;
  for (const [quality,tf] of ranked) {
    if (quality>best||(quality===best&&String(tf)>String(dominantScale||""))) {best=quality;dominantScale=tf;}
  }
  return {
    alignment_score:alignment,
    cascade_state:alignment>=0.70?"COHERENT":(alignment>=0.50?"MIXED":"FRACTURED"),
    fast_scale_lead_score:leads.length?mean(leads):null,
    dominant_scale:dominantScale,
    pairs,
  };
}

async function buildSnapshot(env) {
  const base=await fetchJson(env.FSE_V2_URL||"https://briefrooms.com/data/investments/fse_v2_public.json").catch(()=>({}));
  const baseRows=new Map((Array.isArray(base?.instruments)?base.instruments:[]).map((x)=>[String(x.instrument||""),x]));
  const rows=[];
  const errors={};
  await Promise.all(Object.entries(INSTRUMENTS).map(async ([instrument,symbol])=>{
    try {
      const {bars:minuteBars,sourceStatus}=await fetchMinuteBars(symbol,instrument);
      const phaseMap=await mergedPhaseMap(baseRows.get(instrument)||{},minuteBars,instrument);
      const observed={};
      for (const tf of FAST_SCALES) observed[tf]=phaseMap?.[tf]?.observed_at||null;
      const latest=Object.values(observed).filter(Boolean).sort().at(-1)||null;
      rows.push({
        instrument,
        symbol,
        source:"Yahoo Chart 7d/1m via Cloudflare Edge",
        phase_map:phaseMap,
        cross_scale_alignment:crossScaleAlignment(phaseMap),
        fast_observed_at:observed,
        latest_fast_observed_at:latest,
        source_status:sourceStatus,
      });
    } catch (error) {
      errors[instrument]=String(error?.message||error);
    }
  }));
  rows.sort((a,b)=>Object.keys(INSTRUMENTS).indexOf(a.instrument)-Object.keys(INSTRUMENTS).indexOf(b.instrument));
  if (!rows.length) throw new Error(`no_fse_instruments:${JSON.stringify(errors)}`);
  return {
    schema_version:"briefrooms-fse-intraday-fast-edge-v1",
    transport_version:TRANSPORT_VERSION,
    engine:"FSE Intraday Fast Edge Projection",
    module_id:"IN-09",
    component_id:"FSE-INTRADAY-FAST-EDGE",
    mode:"SHADOW_ONLY",
    production_impact:false,
    authority:{...ZERO_AUTHORITY},
    generated_at:new Date().toISOString(),
    cadence_minutes:5,
    stale_after_minutes:12,
    source_policy:{
      source:"Yahoo Chart secondary research feed",
      range:"7d",
      interval:"1m",
      derived_scales:["5m","15m","1h"],
      slow_context:"4h/1d/1w retained from latest published FSE-PHASE snapshot",
      runtime:"Cloudflare Worker edge projection; GitHub static snapshot remains fallback",
    },
    instruments:rows,
    errors,
    notes:[
      "Read-only presentation freshness path; no HSE2 evidence or durable FSE state is written.",
      "No production authority, execution, probability writeback, sizing writeback or stop writeback.",
    ],
  };
}

async function responseForSnapshot(env, ctx) {
  const cache=caches.default;
  const cached=await cache.match(CACHE_KEY);
  if (cached) {
    try {
      const cachedPayload=await cached.clone().json();
      const generatedMs=Date.parse(String(cachedPayload?.generated_at||""));
      const ageMs=Number.isFinite(generatedMs)?Date.now()-generatedMs:Infinity;
      if (cachedPayload.transport_version===TRANSPORT_VERSION && ageMs>=-60_000 && ageMs<=5*60_000) {
        const headers=new Headers(cached.headers);
        Object.entries(JSON_HEADERS).forEach(([k,v])=>headers.set(k,v));
        headers.set("x-fse-cache","HIT_FRESH");
        headers.set("x-fse-age-seconds",String(Math.max(0,Math.round(ageMs/1000))));
        return new Response(cached.body,{status:cached.status,headers});
      }
      await cache.delete(CACHE_KEY);
    } catch {
      await cache.delete(CACHE_KEY);
    }
  }
  const snapshot=await buildSnapshot(env);
  const body=JSON.stringify(snapshot);
  const cacheResponse=new Response(body,{headers:{"content-type":"application/json; charset=utf-8","cache-control":"public,max-age=180"}});
  ctx?.waitUntil?.(cache.put(CACHE_KEY,cacheResponse.clone()));
  return new Response(body,{headers:{...JSON_HEADERS,"x-fse-cache":"MISS"}});
}

export default {
  async fetch(request,env,ctx) {
    const url=new URL(request.url);
    if (request.method==="OPTIONS") return new Response(null,{status:204,headers:JSON_HEADERS});
    if (url.pathname==="/health") {
      return new Response(JSON.stringify({ok:true,service:"briefrooms-fse-fast-edge",transport_version:TRANSPORT_VERSION,cadence_minutes:5,production_impact:false}),{headers:JSON_HEADERS});
    }
    if (url.pathname!=="/fse-fast") return new Response(JSON.stringify({error:"not_found"}),{status:404,headers:JSON_HEADERS});
    try {
      return await responseForSnapshot(env,ctx);
    } catch (error) {
      return new Response(JSON.stringify({error:"fse_fast_edge_unavailable",detail:String(error?.message||error)}),{status:503,headers:JSON_HEADERS});
    }
  },

  async scheduled(_controller,env,ctx) {
    ctx.waitUntil((async()=>{
      try {
        const snapshot=await buildSnapshot(env);
        const response=new Response(JSON.stringify(snapshot),{headers:{"content-type":"application/json; charset=utf-8","cache-control":"public,max-age=180"}});
        await caches.default.put(CACHE_KEY,response);
      } catch (error) {
        console.error("FSE_FAST_EDGE_SCHEDULE_FAIL",String(error?.stack||error));
      }
    })());
  },
};
