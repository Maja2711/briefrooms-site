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
const CACHE_KEY = new Request("https://briefrooms-fse-fast.internal/snapshot-v1");
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

export function parseYahooBars(payload) {
  const result=payload?.chart?.result?.[0];
  const timestamps=Array.isArray(result?.timestamp)?result.timestamp:[];
  const quote=result?.indicators?.quote?.[0]||{};
  const rows=[];
  for (let i=0;i<timestamps.length;i+=1) {
    const close=finiteNumber(quote.close?.[i]);
    const epoch=finiteNumber(timestamps[i]);
    if (close==null || epoch==null) continue;
    rows.push({
      timestamp:new Date(epoch*1000).toISOString(),
      close,
      open:finiteNumber(quote.open?.[i]),
      high:finiteNumber(quote.high?.[i]),
      low:finiteNumber(quote.low?.[i]),
      volume:finiteNumber(quote.volume?.[i]),
    });
  }
  return rows;
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

export function resampleMinutes(bars, minutes) {
  const bucketMs=minutes*60_000;
  const buckets=new Map();
  for (const bar of bars) {
    const ms=Date.parse(bar.timestamp);
    if (!Number.isFinite(ms)) continue;
    const key=Math.floor(ms/bucketMs)*bucketMs;
    if (!buckets.has(key)) buckets.set(key,[]);
    buckets.get(key).push(bar);
  }
  return [...buckets.keys()].sort((a,b)=>a-b).map((key)=>aggregateBars(buckets.get(key),key));
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

async function fetchJson(url) {
  const response=await fetch(url,{headers:{"cache-control":"no-cache","accept":"application/json"}});
  if (!response.ok) throw new Error(`http_${response.status}:${url}`);
  return response.json();
}

async function fetchMinuteBars(symbol) {
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
      const bars=parseYahooBars(await fetchJson(`${url}${url.includes("?")?"&":"?"}_=${Date.now()}`));
      if (bars.length<90) throw new Error(`insufficient_1m_bars_${bars.length}`);
      return bars;
    } catch (error) {
      errors.push(String(error?.message||error));
    }
  }
  throw new Error(`all_sources_failed:${errors.join("|")}`);
}

async function mergedPhaseMap(baseRow, minuteBars) {
  const base=baseRow?.phase_map||{};
  const scales={
    "1m":minuteBars,
    "5m":resampleMinutes(minuteBars,5),
    "15m":resampleMinutes(minuteBars,15),
    "1h":resampleMinutes(minuteBars,60),
  };
  const out={};
  for (const tf of SCALE_ORDER) {
    const prior={...(base[tf]||{})};
    if (FAST_SCALES.includes(tf)) {
      const bars=scales[tf];
      Object.assign(prior,await phaseDescriptor(bars));
      prior.bars=bars.length;
      prior.observed_at=bars.length?bars.at(-1).timestamp:null;
      prior.fast_refreshed=true;
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
  const ranked=Object.entries(phaseMap||{}).map(([tf,row])=>{
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
  const base=await fetchJson(env.FSE_V2_URL||"https://briefrooms.com/data/investments/fse_v2_public.json");
  const baseRows=new Map((Array.isArray(base?.instruments)?base.instruments:[]).map((x)=>[String(x.instrument||""),x]));
  const rows=[];
  const errors={};
  await Promise.all(Object.entries(INSTRUMENTS).map(async ([instrument,symbol])=>{
    try {
      const minuteBars=await fetchMinuteBars(symbol);
      const phaseMap=await mergedPhaseMap(baseRows.get(instrument)||{},minuteBars);
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
      });
    } catch (error) {
      errors[instrument]=String(error?.message||error);
    }
  }));
  rows.sort((a,b)=>Object.keys(INSTRUMENTS).indexOf(a.instrument)-Object.keys(INSTRUMENTS).indexOf(b.instrument));
  if (!rows.length) throw new Error(`no_fse_instruments:${JSON.stringify(errors)}`);
  return {
    schema_version:"briefrooms-fse-intraday-fast-edge-v1",
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
    const headers=new Headers(cached.headers);
    Object.entries(JSON_HEADERS).forEach(([k,v])=>headers.set(k,v));
    headers.set("x-fse-cache","HIT");
    return new Response(cached.body,{status:cached.status,headers});
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
      return new Response(JSON.stringify({ok:true,service:"briefrooms-fse-fast-edge",cadence_minutes:5,production_impact:false}),{headers:JSON_HEADERS});
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
