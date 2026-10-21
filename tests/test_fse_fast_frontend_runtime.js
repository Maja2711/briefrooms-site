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

  function presentation(){
    const body=source.slice(source.indexOf("function fseSignal("),source.indexOf("function fseHseMatches("));
    const finite=v=>v!==null&&v!==undefined&&v!==""&&Number.isFinite(Number(v));
    return vm.runInNewContext(body+";({probability:fseSearchProbability,quality:fseSearchQuality,comparison:fseSearchComparison,cell:fseModelCell,signal:fseSignal,brier:fseBrierText});",{
      finite,Number,String,Array,esc:value=>String(value??"—"),num:(value,d=0)=>finite(value)?Number(value).toFixed(d):"—",pct:value=>finite(value)?(Number(value)*100).toFixed(1)+"%":"—",fseInstrumentLabel:value=>String(value),
    });
  }

  test(path+": abstention is explicit and never becomes a 50% or directional forecast",()=>{
    const view=presentation(),memory={available:false,status:"ABSTAIN",reason:"INSUFFICIENT_INDEPENDENT_EPISODES",raw_windows_n:180,independent_episodes_n:3,minimum_episodes:8};
    const cell=view.cell(null,{resolved_n:0},null,"deep",memory);
    assert.match(cell,path.endsWith("central-lab.js")?/Brak dobrego dopasowania/:/No reliable analogue/);
    assert(!cell.includes("50.0%"));assert(!cell.includes('class="fse-signal long"'));
    assert(!cell.includes('class="fse-signal short"'));assert(!cell.includes('class="fse-signal neutral"'));
    assert.equal(view.probability(memory,.72),null,"ABSTAIN must also defeat an inconsistent numeric P");
    assert.equal(view.probability({available:true,status:"MATCHED"},0),0,"An explicit zero probability is not missing");
    assert.match(view.quality(memory,null),/180/);assert.match(view.quality(memory,null),/3/);
  });

  test(path+": quality reasons are readable in the selected language",()=>{
    const view=presentation();
    const known=view.quality({available:false,status:"ABSTAIN",reason:"insufficient past quality calibration"},null);
    const unknown=view.quality({available:false,status:"ABSTAIN",reason:"future unpublished English reason"},null);
    if(path.endsWith("central-lab.js")){
      assert.match(known,/Za mało wcześniejszych danych/);
      assert(!known.includes("insufficient past quality calibration"));
      assert.match(unknown,/Dane nie potwierdzają/);
      assert(!unknown.includes("future unpublished English reason"));
    }else{
      assert(known.includes("insufficient past quality calibration"));
      assert(unknown.includes("future unpublished English reason"));
    }
  });

  test(path+": the shape-only ablation never fabricates a pair or baseline Brier",()=>{
    const view=presentation(),unavailable={available:false,status:"ABSTAIN",reason:"DISTANCE_GATE"},available={available:true,status:"FORECAST",independent_analogues_n:9,history_candidates:240};
    const output=view.comparison([{instrument:"EURUSD",memory:unavailable,simple_memory:available,feature_ablation:{full_p_up:null,simple_p_up:.61,paired_available:false}}]);
    assert.match(output,/AB-join/);assert.match(output,/61.0%/);assert(!output.includes("50.0%"));
    assert.match(output,path.endsWith("central-lab.js")?/Brak pełnej pary/:/No complete forecast pair/);
    assert.equal(view.brier({details:{kind:"feature_ablation"}},.0125),"Δ 0.0125");
    assert.equal(view.brier({details:{kind:"feature_ablation"}},null),"—");
  });

  test(path+": ASD2 keeps archived families accessible without treating them as current matches",()=>{
    const panelStart=source.indexOf("function fseDiscoveryPanel(d){"),panelEnd=source.indexOf("\nasync function fse(){",panelStart);
    const helpersStart=source.indexOf("function fseDiscoveryEnglishCopy(");
    const helper=helpersStart>=0?source.slice(helpersStart,panelStart):source.slice(source.indexOf("function fseDiscoveryShape("),panelStart);
    const render=vm.runInNewContext(helper+source.slice(panelStart,panelEnd)+";fseDiscoveryPanel;",{Date:Clock,Map,Set,Number,String,Array,Math,esc:value=>String(value??"—"),num:value=>value===null||value===undefined?"—":String(value),finite:value=>value!==null&&value!==undefined&&Number.isFinite(Number(value)),fseInstrumentLabel:value=>String(value)});
    const family={family_id:"ASD-NEW",methodology_version:"FSE-ASD-2.0",instrument:"EURUSD",name:"Current family",name_en:"Current family",description:"Current research",description_en:"Current research",window:32,historical_sample_n:100,historical_independent_n:7,prospective_n:20,independent_n:4,required_independent_n:40,holdout_n:2,evidence_status:"COLLECTING",application_status:"UNDETERMINED",applications:[]};
    const old={...family,family_id:"ASD-ARCHIVED",methodology_version:"FSE-ASD-1.0",application_status:"RESEARCH_CANDIDATE",applications:["Obsolete application"],applications_en:["Obsolete application"]};
    const result=render({schema_version:"FSE-ASD-1.0",methodology_version:"FSE-ASD-2.0",generated_at:new Date(now).toISOString(),families:[family],legacy_families:[old],legacy_evidence:{families:1,snapshots:12,resolutions:9,included_in_current_metrics:false},current_matches:[{instrument:"EURUSD",family_id:"ASD-ARCHIVED",status:"FROZEN_MATCH",p_up:.7},{instrument:"BTCUSD",family_id:null,status:"FROZEN_LOW_QUALITY",p_up:null,match_quality:{reason:"DISTANCE_GATE"}}]});
    assert(result.includes("ASD-NEW")&&result.includes("ASD-ARCHIVED"));
    assert(result.includes("fse-discovery-archive"));assert(!result.includes("fse-discovery-matches"));
    assert(!result.includes("Obsolete application"));assert(result.includes("100")&&result.includes("7"));
    assert.match(result,path.endsWith("central-lab.js")?/Brak dobrego dopasowania/:/No reliable analogue/);
    assert.match(result,path.endsWith("central-lab.js")?/Dopasowanie nie spełnia progu podobieństwa/:/DISTANCE GATE/);
  });

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

  test(path+": generic HSE claims map through explicit instrument rather than their prose",()=>{
    const start=source.indexOf("function fseHseMatches("),body=source.slice(start,source.indexOf("// Fast presentation selection",start));
    const methods=vm.runInNewContext(body+";({match:fseHseMatches,current:fseCurrentExperiments});",{String,Array});
    const metrics=["adaptive_ensemble_brier_improvement_vs_deep","adaptive_risk_improvement_vs_prior","full_brier_improvement_vs_simple"];
    const measurements=ids.flatMap(instrument=>metrics.map(metric_name=>({metric_name,details:{instrument,methodology_version:metric_name.includes("simple")?"FSE-PHASE-3.0":"FSE-ADAPTIVE-3.0"}})));
    const experiments=measurements.map(m=>({source_engine:"FSE",instrument:m.details.instrument,metric_name:m.metric_name,methodology_version:m.details.methodology_version,claim:"Generic prospective comparison without an instrument name",prospective_n:0}));
    assert.equal(methods.current(experiments,measurements).length,9);
    for(const measurement of measurements){
      const matching=experiments.filter(exp=>methods.match(exp,measurement));
      assert.equal(matching.length,1);assert.equal(matching[0].instrument,measurement.details.instrument);
    }
    const euro=measurements.find(m=>m.details.instrument==="EURUSD"),wrong={...experiments[0],instrument:"BTCUSD",claim:"Legacy prose explicitly mentions EURUSD"};
    assert.equal(methods.match(wrong,euro),false,"Explicit instrument must take precedence over legacy prose");
    const legacy={...experiments[0],claim:"Legacy FSE for EURUSD"};delete legacy.instrument;
    assert.equal(methods.match(legacy,euro),true,"Old public snapshots retain their claim-text fallback");
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
