import test from "node:test";
import assert from "node:assert/strict";
import worker,{TRANSPORT_VERSION,resampleMinutes,phaseDescriptor,crossScaleAlignment,mergedPhaseMap,marketReference,validateMinuteSource,fetchMinuteBars,fetchJson,parseYahooBars} from "../src/index.js";

function syntheticBars(n=180) {
  const start=Date.parse("2026-10-07T10:00:00Z");
  let p=100;
  const rows=[];
  for (let i=0;i<n;i+=1) {
    const old=p;
    p*=Math.exp(0.0002+Math.sin(i/11)*0.0004);
    rows.push({
      timestamp:new Date(start+i*60_000).toISOString(),
      open:old,high:Math.max(old,p),low:Math.min(old,p),close:p,volume:1,
    });
  }
  return rows;
}

test("resampleMinutes produces aligned five-minute bars",()=>{
  const out=resampleMinutes(syntheticBars(20),5);
  assert.equal(out.length,4);
  assert.equal(out[0].timestamp,"2026-10-07T10:00:00.000Z");
  assert.equal(out[1].timestamp,"2026-10-07T10:05:00.000Z");
});

test("phaseDescriptor exposes the canonical direction contract",async()=>{
  const out=await phaseDescriptor(syntheticBars());
  assert.equal(out.available,true);
  assert.ok(["UP","DOWN","FLAT"].includes(out.direction));
  assert.ok(["CONSOLIDATION","DEVELOPMENT","EXPANSION","MATURATION","TRANSITION","REVERSAL"].includes(out.phase));
  assert.match(out.structure_id,/^FS-[0-9A-F]{8}$/);
});

test("crossScaleAlignment remains bounded and read-only data shaped",async()=>{
  const desc=await phaseDescriptor(syntheticBars());
  const phaseMap={};
  for (const tf of ["1m","5m","15m","1h","4h","1d","1w"]) {
    phaseMap[tf]={...desc,phase_progress:0.5,phase_memory:{top_similarity:0.8}};
  }
  const out=crossScaleAlignment(phaseMap);
  assert.ok(out.alignment_score>=0&&out.alignment_score<=1);
  assert.equal(out.cascade_state,"COHERENT");
  assert.equal(out.pairs.length,6);
});

test("fast descriptors remove inherited analogue memory and unavailable direction",async()=>{
  const base={phase_map:{"1h":{direction:"DOWN",structure_id:"OLD",phase_progress:.85,phase_memory:{p_up_remaining:.99}}}};
  const map=await mergedPhaseMap(base,syntheticBars(90));
  assert.equal(map["1h"].available,false);
  assert.equal(map["1h"].direction,undefined);
  assert.equal(map["1h"].structure_id,undefined);
  assert.equal(map["1h"].phase_memory,null);
  assert.equal(map["1h"].phase_progress,null);
});

test("SPY hourly buckets follow the NY session through DST",()=>{
  const start=Date.parse("2026-11-02T14:30:00Z");
  const bars=syntheticBars(90).map((row,i)=>({...row,timestamp:new Date(start+i*60000).toISOString()}));
  const out=resampleMinutes(bars,60,"SPX");
  assert.equal(out[0].timestamp,"2026-11-02T14:30:00.000Z");
  assert.equal(out[1].timestamp,"2026-11-02T15:30:00.000Z");
});

test("the final SPY hour closes with its real session and 30 completed minutes are complete",()=>{
  for(const [start,close] of [["2026-10-21T19:30:00Z","2026-10-21T20:00:00.000Z"],["2026-11-27T17:30:00Z","2026-11-27T18:00:00.000Z"]]){
    const rows=syntheticBars(30).map((row,i)=>({...row,timestamp:new Date(Date.parse(start)+i*60000).toISOString()}));
    const actual=resampleMinutes(rows,60,"SPX");
    assert.equal(actual.length,1);assert.equal(actual[0].closed_at,close);
    assert.equal(actual[0].is_partial,false);
    assert.equal(resampleMinutes(rows.slice(0,29),60,"SPX")[0].is_partial,true);
  }
});

test("session references handle FX weekends, DST, holidays and early closes",()=>{
  const cases=[
    ["BTCUSD","2026-10-24T12:00:00Z","OPEN","2026-10-24T12:00:00Z"],
    ["EURUSD","2026-10-24T12:00:00Z","CLOSED","2026-10-23T21:00:00Z"],
    ["EURUSD","2026-11-02T00:00:00Z","OPEN","2026-11-02T00:00:00Z"],
    ["SPX","2026-10-21T12:00:00Z","CLOSED","2026-10-20T20:00:00Z"],
    ["SPX","2026-12-25T17:00:00Z","CLOSED","2026-12-24T18:00:00Z"],
    ["SPX","2026-11-27T19:00:00Z","CLOSED","2026-11-27T18:00:00Z"],
  ];
  for(const [id,at,state,reference] of cases){
    assert.deepEqual(marketReference(id,Date.parse(at)),{market_state:state,reference:Date.parse(reference)});
  }
});

function minutePayload(now,offset=0){
  return {chart:{result:[{timestamp:Array.from({length:180},(_,i)=>(now-(180-i)*60000+offset)/1000),indicators:{quote:[{close:Array.from({length:180},(_,i)=>100+i/100)}]}}]}};
}

test("a stale HTTP 200 provider falls through to a fresh alternate",async(t)=>{
  const now=Date.parse("2026-10-21T12:00:00Z"),urls=[];
  t.mock.method(globalThis,"fetch",async(url,options)=>{
    urls.push(url);assert.ok(options.signal instanceof AbortSignal);
    return new Response(JSON.stringify(minutePayload(now,urls.length===1?-3600000:0)));
  });
  const {bars,sourceStatus}=await fetchMinuteBars("BTC-USD","BTCUSD",now);
  assert.equal(urls.length,2);assert.match(urls[1],/query2/);
  assert.equal(sourceStatus.market_state,"OPEN");
  assert.equal(sourceStatus.source_lag_minutes,0);
  assert.equal(bars.at(-1).timestamp,"2026-10-21T11:59:00.000Z");
});

test("future provider clocks are rejected and off-grid quotes cannot invent candles",()=>{
  const now=Date.parse("2026-10-21T12:00:00Z");
  assert.throws(()=>parseYahooBars(minutePayload(now,3600000),now),/future/);
  const payload={chart:{result:[{timestamp:[now/1000-60,now/1000-30,now/1000+15],indicators:{quote:[{close:[100,102,103]}]}}]}};
  const bars=parseYahooBars(payload,now);
  assert.equal(bars.length,1);assert.equal(bars[0].close,102);
  assert.throws(()=>validateMinuteSource(parseYahooBars(minutePayload(now),now),"BTCUSD",now+3600000),/stale/);
});

test("a forming candle with no explicit quote clock uses its canonical opening",()=>{
  const opened=Date.parse("2026-10-21T12:00:00Z"),now=opened+30000;
  const payload={chart:{result:[{timestamp:[opened/1000],indicators:{quote:[{close:[100]}]}}]}};
  assert.equal(parseYahooBars(payload,now)[0].source_observed_at,new Date(opened).toISOString());
  assert.equal(parseYahooBars(payload,opened+60000)[0].source_observed_at,new Date(opened+60000).toISOString());
});

test("bounded fetch cancels a stalled provider",async(t)=>{
  const keeper=setTimeout(()=>{},100);
  t.mock.method(globalThis,"fetch",(_url,options)=>new Promise((_resolve,reject)=>options.signal.addEventListener("abort",()=>reject(options.signal.reason),{once:true})));
  try {await assert.rejects(fetchJson("https://provider.invalid",5),/timeout/i);} finally {clearTimeout(keeper);}
});

test("health identifies the repaired transport and partial providers stay isolated",async(t)=>{
  assert.equal((await (await worker.fetch(new Request("https://example.test/health"),{},{})).json()).transport_version,TRANSPORT_VERSION);
  const now=Math.floor(Date.now()/60000)*60000;
  t.mock.method(globalThis,"fetch",async url=>{
    if(String(url).includes("fse_v2_public"))throw new Error("base unavailable");
    if(String(url).includes("BTC-USD"))return new Response(JSON.stringify(minutePayload(now)));
    return new Response("unavailable",{status:503});
  });
  const original=globalThis.caches;
  globalThis.caches={default:{match:async()=>null,put:async()=>{},delete:async()=>true}};
  t.after(()=>{if(original===undefined)delete globalThis.caches;else globalThis.caches=original;});
  const pending=[];
  const response=await worker.fetch(new Request("https://example.test/fse-fast"),{}, {waitUntil:p=>pending.push(p)});
  await Promise.all(pending);
  const result=await response.json();
  assert.equal(response.status,200);assert.equal(result.transport_version,TRANSPORT_VERSION);
  assert.deepEqual(result.instruments.map(x=>x.instrument),["BTCUSD"]);
  assert.ok(result.errors.EURUSD&&result.errors.SPX);
  assert.equal(result.production_impact,false);
  assert.ok(Object.values(result.authority).every(value=>value===false));
});
