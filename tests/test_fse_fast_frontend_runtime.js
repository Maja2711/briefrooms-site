import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const now=Date.parse("2026-10-21T12:00:00Z");
class Clock extends Date {static now(){return now;}}
const ids=["EURUSD","BTCUSD","SPX"];
function row(instrument,at=now-60000,direction="UP"){
  return {instrument,latest_fast_observed_at:new Date(at).toISOString(),
    source_status:{market_state:"OPEN",source_reference_at:new Date(now).toISOString(),source_lag_minutes:Math.max(0,(now-at-60000)/60000),source_age_minutes:(now-at)/60000},
    phase_map:Object.fromEntries(["1m","5m","15m","1h"].map(tf=>[tf,{available:true,direction,confidence:.7,efficiency:.4,volatility_ratio:1,curvature:0,observed_at:new Date(at).toISOString(),source_observed_at:new Date(at).toISOString(),phase_memory:{p_up_remaining:.99},phase_progress:.85}]))};
}
function payload(rows=ids.map(id=>row(id)),generated=now){
  return {schema_version:"briefrooms-fse-intraday-fast-edge-v1",transport_version:"FSE-FAST-2.0",mode:"SHADOW_ONLY",production_impact:false,generated_at:new Date(generated).toISOString(),instruments:rows};
}
for(const path of ["scripts/central-lab.js","scripts/central-lab-runtime-en.js"]){
  const source=fs.readFileSync(path,"utf8");
  const block=source.slice(source.indexOf("// Fast presentation selection"),source.indexOf("const FSE_TREND_TFS="));
  function api(get=async()=>null){return vm.runInNewContext(block+";({load:fseLoadFast,merge:fseMergeProjection,fresh:fseFastRowFresh,valid:fseProjectionValid});",{Date:Clock,Map,Number,String,Array,Infinity,finite:v=>v!==null&&v!==undefined&&Number.isFinite(Number(v)),get});}

  test(path+": historical HSE support cannot label or inflate the current methodology",()=>{
    const start=source.indexOf("function fseHseMatches(");
    const body=source.slice(start,source.indexOf("// Fast presentation selection",start));
    const methods=vm.runInNewContext(body+";({match:fseHseMatches,current:fseCurrentExperiments});",{String,Array});
    const measurements=[
      {metric_name:"core",details:{instrument:"EURUSD",methodology_version:"FSE-CORE-2.0"}},
      {metric_name:"phase",details:{instrument:"BTCUSD",methodology_version:"FSE-PHASE-2.0"}},
      {metric_name:"adaptive",details:{instrument:"SPX",methodology_version:"FSE-ADAPTIVE-2.0"}},
    ];
    const current=measurements.map((m,i)=>({source_engine:"FSE",metric_name:m.metric_name,claim:"FSE for "+m.details.instrument,methodology_version:m.details.methodology_version,status:"RUNNING_SHADOW",prospective_n:i+1}));
    const historical=current.map(x=>({...x,methodology_version:x.methodology_version.replace("2.0","1.0"),status:"SUPPORTED",prospective_n:40}));
    historical.push({...current[0],methodology_version:undefined,status:"SUPPORTED",prospective_n:40});
    const selected=methods.current([...historical,...current],measurements);
    assert.equal(selected.length,3);
    assert.equal(selected.reduce((total,x)=>total+x.prospective_n,0),6);
    for(const m of measurements){
      assert.equal(selected.find(x=>methods.match(x,m)).status,"RUNNING_SHADOW");
      assert.equal(historical.some(x=>methods.match(x,m)),false);
    }
    assert.ok(source.includes("fseCurrentExperiments(hse.experiments,allMeasurements)"));
    assert.ok(source.includes("experiments.find(x=>fseHseMatches(x,m))"));
  });

  test(path+": stale/partial HTTP 200 recovers from the static provider",async()=>{
    const urls=[],fallback=payload();
    const instance=api(async url=>{urls.push(url);return url.includes("fse-fast")?payload([row("EURUSD",now-86400000)],now-86400000):fallback;});
    const candidates=await instance.load();
    assert.equal(urls.length,2);assert.match(urls[1],/fse_intraday/);
    const merged=instance.merge([],candidates,now);
    assert.equal(merged.length,3);
    assert.ok(merged.every(value=>value._fast_meta.fresh));
  });

  test(path+": unavailable scales, schema errors and future clocks cannot vote",async()=>{
    const instance=api(),future=payload([row("EURUSD",now+3600000)],now+3600000);
    assert.equal(instance.valid(future,now),false);
    assert.equal(instance.valid({...payload(),schema_version:"wrong"},now),false);
    assert.equal(instance.fresh(payload([row("EURUSD",now+3600000)]),row("EURUSD",now+3600000),now),false);
    const unavailable=row("EURUSD");unavailable.phase_map["1h"]={available:false,direction:"DOWN"};
    const merged=instance.merge([{instrument:"EURUSD",phase_map:{}}],[payload([unavailable])],now);
    assert.equal(merged[0].phase_map["1h"],undefined);
    const trendBlock=source.slice(source.indexOf("const FSE_TREND_TFS="),source.indexOf("function fseBarClock"));
    const score=vm.runInNewContext(trendBlock+";fseTrendScore;",{Number,String});
    assert.equal(score({"1h":{available:false,direction:"DOWN"}}),0);
  });

  test(path+": older responses cannot overwrite newer scales or cached projections",()=>{
    const instance=api();
    const base={instrument:"EURUSD",phase_map:{"1h":{available:true,direction:"UP",observed_at:new Date(now).toISOString()}}};
    const older=payload([row("EURUSD",now-60000,"DOWN")]);
    const result=instance.merge([base],[older],now)[0];
    assert.equal(result.phase_map["1h"].direction,"UP");
    assert.equal(result.phase_map["5m"].phase_memory,null);
    assert.equal(result.phase_map["5m"].phase_progress,null);
    const next=instance.merge([{instrument:"EURUSD",phase_map:{}}],[],now)[0];
    assert.equal(next.phase_map["1h"].direction,"UP");
    assert.equal(next._fast_meta.fresh,false);
  });

  test(path+": a closed session can be current without concealing a stale live source",()=>{
    const instance=api(),at=now-16*3600000,closed=row("SPX",at);
    closed.source_status={market_state:"CLOSED",source_reference_at:new Date(at+60000).toISOString(),source_lag_minutes:0,source_age_minutes:960};
    assert.equal(instance.fresh(payload([closed]),closed,now),true);
    closed.source_status.market_state="OPEN";
    assert.equal(instance.fresh(payload([closed]),closed,now),false);
  });
}
