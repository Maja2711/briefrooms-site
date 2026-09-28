(()=>{"use strict";

const esc=s=>String(s??"").replace(/[&<>"]/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[m]));
const pct=v=>v==null?"—":(Number(v)*100).toFixed(1)+"%";
const pp=v=>v==null?"—":((Number(v)*100)>=0?"+":"")+(Number(v)*100).toFixed(1)+" pp";
const num=(v,d=3)=>v==null||!Number.isFinite(Number(v))?"—":Number(v).toFixed(d);
const bits=v=>v==null?"—":(Number(v)>=0?"+":"")+Number(v).toFixed(1)+" bit";

const HYPOTHESES={
  "spx.trend.bullish":"S&P 500 will be higher at Target than at forecast time",
  "spx.breadth.healthy":"US market breadth remains healthy",
  "spx.volatility.benign":"Market volatility remains moderate",
  "spx.liquidity.supportive":"Liquidity / credit support the market",
  "spx.financial_conditions.supportive":"Financial conditions support the market",
  "eurusd.trend.bullish":"EUR/USD will be higher at Target than at forecast time",
  "eurusd.us_rates_pressure.supportive":"US rates pressure supports EUR/USD",
  "eurusd.usd_environment.supportive":"USD environment supports EUR/USD",
  "btc.trend.bullish":"BTC/USD will be higher at Target than at forecast time",
  "btc.volatility.benign":"BTC volatility remains moderate",
  "btc.liquidity.supportive":"Liquidity supports BTC",
  "btc.usd_environment.supportive":"USD environment supports BTC"
};

function instrument(x){
  const id=String(x?.belief_id||"");
  if(id.startsWith("eurusd.")) return "EUR/USD";
  if(id.startsWith("btc.")) return "BTC/USD";
  if(id.startsWith("spx.")) return "S&P 500";
  return String(x?.entity||"—");
}
function hypothesis(x){
  const id=String(x?.belief_id||"");
  return HYPOTHESES[id]||id||"—";
}
function forecastTime(value){
  if(!value) return "—";
  const d=new Date(value);
  if(Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString("en-US",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"});
}
function probabilityMeaning(x){
  const p=Number(x?.probability);
  if(!Number.isFinite(p)) return "";
  const positive=p>=.5;
  const label=positive?"leans YES":"leans NO";
  return label+" · "+(Math.max(p,1-p)*100).toFixed(1)+"%";
}
function target(value){
  if(!value) return "—";
  const d=new Date(value);
  if(Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString("en-US",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"});
}
function marketCalendar(x){
  const name=instrument(x), raw=x?.target_at;
  if(!raw) return {state:"UNKNOWN",label:"no target",detail:""};
  const d=new Date(raw); if(Number.isNaN(d.getTime())) return {state:"UNKNOWN",label:"no calendar",detail:""};
  if(name==="BTC/USD") return {state:"OPEN",label:"24/7",detail:"Market operates 24/7."};
  const day=d.getUTCDay(), mins=d.getUTCHours()*60+d.getUTCMinutes();
  if(name==="EUR/USD"){
    const closed=day===6 || (day===0 && mins<22*60) || (day===5 && mins>=22*60);
    return closed
      ? {state:"CLOSED",label:"FX closed",detail:"Settlement: first available quote after the FX market reopens."}
      : {state:"OPEN",label:"FX open",detail:"Target falls during FX trading hours."};
  }
  if(name==="S&P 500"){
    const weekend=day===0||day===6;
    return weekend
      ? {state:"CLOSED",label:"US market closed",detail:"Settlement: first available quote after the US market reopens."}
      : {state:"SESSION",label:"US calendar",detail:"Settlement uses the first available quote after Target; holidays and missing data remain fail-closed."};
  }
  return {state:"UNKNOWN",label:"calendar n/a",detail:""};
}
function calendarBadge(x){
  const m=marketCalendar(x), cls=m.state==="CLOSED"?"closed":m.state==="OPEN"?"open":"session";
  return '<small class="market-calendar '+cls+'" title="'+esc(m.detail)+'">'+esc(m.label)+'</small>';
}
function outcome(x){
  if(String(x?.status||"").toUpperCase()!=="RESOLVED") return '<span class="outcome pending">—</span>';
  return x.outcome===true
    ? '<span class="outcome yes" title="Hypothesis confirmed">YES</span>'
    : '<span class="outcome no" title="Hypothesis not confirmed">NO</span>';
}
function status(value){
  const v=String(value||"").toUpperCase();
  if(v==="RESOLVED") return '<span class="forecast-status resolved">RESOLVED</span>';
  if(v==="OPEN") return '<span class="forecast-status open">OPEN</span>';
  return '<span class="forecast-status">'+esc(v||"—")+'</span>';
}
function patternStatus(value){
  const v=String(value||"DISCOVERY").toUpperCase();
  const cls=v==="REPLICATED"?"replicated":v==="OOS PASS"?"oos":v==="UNSTABLE"?"unstable":"discovery";
  return '<span class="pattern-status '+cls+'">'+esc(v)+'</span>';
}

const tabs=[...document.querySelectorAll("[data-tab]")];
tabs.forEach(btn=>btn.addEventListener("click",()=>{
  tabs.forEach(x=>x.classList.toggle("active",x===btn));
  document.querySelectorAll(".tab-panel").forEach(p=>p.classList.toggle("active",p.id==="tab-"+btn.dataset.tab));
}));

let MULTI_PATHS=[]; let HORIZON_AGG=[]; let HYPOTHESIS_BRIER={}; const HORIZON_MIN_SAMPLE=30;

function deriveMarketView(forecasts){
  const cfg=[["BTC/USD","btc.","btc.trend.bullish"],["EUR/USD","eurusd.","eurusd.trend.bullish"],["S&P 500","spx.","spx.trend.bullish"]];
  const rows=Array.isArray(forecasts)?forecasts:[];
  return cfg.map(([instrument,prefix,trendId])=>{
    const rel=rows.filter(x=>String(x.belief_id||"").startsWith(prefix)).sort((a,b)=>String(b.forecast_at||"").localeCompare(String(a.forecast_at||"")));
    const trendRows=rel.filter(x=>x.belief_id===trendId);
    const trend=trendRows[0]; if(!trend)return null;
    const p=Number(trend.probability);
    const direction=p>=.55?"up":p<=.45?"down":"neutral";
    const latestByBelief={};
    rel.forEach(x=>{if(!latestByBelief[x.belief_id])latestByBelief[x.belief_id]=x;});
    const envRows=Object.values(latestByBelief).filter(x=>x.belief_id!==trendId);
    const env=envRows.length?envRows.reduce((s,x)=>s+Number(x.probability||.5),0)/envRows.length:.5;
    const sentiment=env>=.55?"positive":env<=.45?"negative":"neutral";
    const prev=trendRows[1]?Number(trendRows[1].probability):null;
    const delta=prev==null?null:p-prev;
    const hb=HYPOTHESIS_BRIER[trendId]||{};
    return {instrument,trend_belief_id:trendId,as_of:trend.forecast_at,target_at:trend.target_at,horizon_label:trend.horizon_label,
      direction,direction_label:{up:"BULLISH",down:"BEARISH",neutral:"NEUTRALNY"}[direction],trend_probability:p,
      evidence_confidence:trend.confidence,sentiment,sentiment_label:{positive:"POSITIVE",negative:"NEGATIVE",neutral:"NEUTRALNE"}[sentiment],
      environment_score:env,environment_components:envRows.length,probability_change:delta,
      probability_movement:delta==null||Math.abs(delta)<.015?"stable":delta>0?"rising":"falling",
      quality:{n:hb.count??hb.n,mean_brier:hb.mean_brier,skill_vs_base_rate:hb.brier_skill_score_vs_base_rate,sample_sufficient:!!hb.sample_sufficient},
      method:"deterministic_briefrooms_beliefs_v1_client_fallback",llm_authority:false};
  }).filter(Boolean);
}

function marketView(items){
  const root=document.getElementById("market-view"); if(!root)return;
  if(!Array.isArray(items)||!items.length){root.innerHTML='<p class="muted">No current Market View.</p>';return;}
  const arrow=x=>x==="up"?"↑":x==="down"?"↓":"→";
  const move=x=>x==="rising"?"P rising ↑":x==="falling"?"P falling ↓":"P stable →";
  root.innerHTML=items.map(x=>{
    const q=x.quality||{};
    const quality=q.sample_sufficient?(q.skill_vs_base_rate==null?"sample ≥30":"skill "+pct(q.skill_vs_base_rate)):"quality under study";
    return '<div class="market-card '+esc(x.direction)+'"><div class="market-card-head"><b>'+esc(x.instrument)+'</b><small>'+esc(x.horizon_label||"")+'</small></div>'+
      '<div class="market-direction"><strong>'+arrow(x.direction)+' '+esc(x.direction_label)+'</strong><span>'+pct(x.trend_probability)+'</span></div>'+
      '<div class="market-facts"><span>Sentiment <b>'+esc(x.sentiment_label)+'</b></span><span>'+esc(move(x.probability_movement))+'</span><span>'+esc(quality)+'</span></div>'+
      '<small class="market-asof">as of '+esc(forecastTime(x.as_of))+' · '+esc(x.environment_components)+' environment signals</small></div>';
  }).join("");
}

function contractValue(values){
  if(!values||typeof values!=="object") return null;
  const entries=Object.entries(values).filter(([,v])=>Number.isFinite(Number(v)));
  if(!entries.length) return null;
  return entries.map(([k,v])=>entries.length>1?k+" "+num(v,5):num(v,5)).join(" · ");
}
function t0Line(x){
  const values=x?.t0_values;
  if(!values||typeof values!=="object") return '<small class="contract-mark t0">T0: —</small>';
  const entries=Object.entries(values).filter(([,v])=>Number.isFinite(Number(v)));
  if(!entries.length) return '<small class="contract-mark t0">T0: —</small>';
  const full=entries.map(([k,v])=>k+" "+num(v,5)).join(" · ");
  if(entries.length===1) return '<small class="contract-mark t0">T0: '+esc(num(entries[0][1],5))+'</small>';
  return '<small class="contract-mark t0 frozen-inputs" title="'+esc(full)+'">T0: '+esc(entries.length)+' frozen inputs ⓘ</small>';
}
function t1Line(x){
  const v=contractValue(x?.t1_values);
  const waiting=x?.forecast_contract_version && String(x?.status||"").toUpperCase()!=="RESOLVED";
  return '<small class="contract-mark t1">T1: '+esc(v||(waiting?"pending":"—"))+'</small>';
}

function forecasts(items){
  const root=document.getElementById("forecast-list");
  if(!root)return;
  if(!items.length){
    root.innerHTML='<p class="muted">No published forecasts. The model does not generate demonstration data.</p>';
    return;
  }
  root.innerHTML=
    '<div class="forecast head">'+
      '<span>Instrument</span><span>Hypothesis</span><span>P</span><span>Confidence</span>'+
      '<span>Forecast date</span><span>Target</span><span>Outcome</span><span>Brier</span><span>Status</span>'+
    '</div>'+
    items.slice(0,20).map(x=>
      '<button class="forecast forecast-click" type="button" data-forecast-id="'+esc(x.forecast_id||'')+'">'+
        '<b>'+esc(instrument(x))+'</b>'+
        '<span class="hypothesis" title="'+esc(x.belief_id||"")+'">'+esc(hypothesis(x))+'</span>'+
        '<span class="probability" title="'+esc(probabilityMeaning(x))+'">'+pct(x.probability)+'<small>'+esc(probabilityMeaning(x))+'</small></span>'+
        '<span>'+pct(x.confidence)+'</span>'+
        '<span class="target forecast-origin" title="Forecast: '+esc(forecastTime(x.forecast_at))+'">'+esc(forecastTime(x.forecast_at))+t0Line(x)+'</span>'+
        '<span class="target" title="Target: '+esc(target(x.target_at))+' · '+esc(marketCalendar(x).detail)+'">'+esc(target(x.target_at))+t1Line(x)+calendarBadge(x)+'</span>'+
        outcome(x)+
        '<span class="brier">'+num(x.brier_score,3)+'</span>'+
        status(x.status)+
      '</button>'
    ).join("");
  root.querySelectorAll("[data-forecast-id]").forEach(btn=>btn.addEventListener("click",()=>{
    const row=items.find(x=>String(x.forecast_id)===btn.dataset.forecastId);
    if(row) showHorizonPath(row);
  }));
}


let HISTORY_LOADED=false,HISTORY_ROWS=[],HISTORY_VISIBLE=50;

function historyRowHtml(x){
  return '<div class="forecast forecast-history-row">'+
    '<b>'+esc(instrument(x))+'</b>'+
    '<span class="hypothesis" title="'+esc(x.belief_id||"")+'">'+esc(hypothesis(x))+'</span>'+
    '<span class="probability" title="'+esc(probabilityMeaning(x))+'">'+pct(x.probability)+'<small>'+esc(probabilityMeaning(x))+'</small></span>'+
    '<span>'+pct(x.confidence)+'</span>'+
    '<span class="target forecast-origin" title="Forecast: '+esc(forecastTime(x.forecast_at))+'">'+esc(forecastTime(x.forecast_at))+t0Line(x)+'</span>'+
    '<span class="target" title="Target: '+esc(target(x.target_at))+'">'+esc(target(x.target_at))+t1Line(x)+'</span>'+
    outcome(x)+
    '<span class="brier">'+num(x.brier_score,3)+'</span>'+
    status(x.status)+
  '</div>';
}

function renderForecastHistory(){
  const root=document.getElementById("forecast-history-body"); if(!root)return;
  const count=document.getElementById("forecast-history-count");
  if(count) count.textContent=HISTORY_LOADED?HISTORY_ROWS.length+" records":"—";
  if(!HISTORY_LOADED){root.innerHTML='<p class="muted">Loading 30D history…</p>';return;}
  if(!HISTORY_ROWS.length){root.innerHTML='<p class="muted">No forecasts in the last 30 days.</p>';return;}
  const visible=HISTORY_ROWS.slice(0,HISTORY_VISIBLE);
  root.innerHTML=
    '<div class="forecast-list history-list">'+
      '<div class="forecast head"><span>Instrument</span><span>Hypothesis</span><span>P</span><span>Confidence</span><span>Forecast date</span><span>Target</span><span>Outcome</span><span>Brier</span><span>Status</span></div>'+
      visible.map(historyRowHtml).join("")+
    '</div>'+
    (HISTORY_VISIBLE<HISTORY_ROWS.length?'<button class="history-more" id="forecast-history-more" type="button">Show more · '+esc(HISTORY_ROWS.length-HISTORY_VISIBLE)+' remaining</button>':'')+
    '<p class="muted history-private-note">Only the rolling last 30 days are published to the frontend. Older history is not sent to the browser and remains in BriefRooms private analytical state.</p>';
  const more=document.getElementById("forecast-history-more");
  if(more) more.addEventListener("click",()=>{HISTORY_VISIBLE+=50;renderForecastHistory();});
}

async function loadForecastHistory(){
  if(HISTORY_LOADED)return;
  const root=document.getElementById("forecast-history-body");
  if(root) root.innerHTML='<p class="muted">Loading 30D history…</p>';
  try{
    const r=await fetch("/data/investments/decision_lab_history_30d.json?v="+Date.now(),{cache:"no-store"});
    if(!r.ok)throw new Error("history unavailable");
    const d=await r.json();
    if(d?.schema_version!=="briefrooms_decision_lab_history_v1")throw new Error("history schema");
    HISTORY_ROWS=Array.isArray(d.rows)?d.rows:[];
    HISTORY_LOADED=true;
    HISTORY_VISIBLE=50;
    renderForecastHistory();
  }catch(_){
    if(root) root.innerHTML='<p class="muted">30D history is temporarily unavailable.</p>';
  }
}

function setupForecastHistory(){
  const details=document.getElementById("forecast-history");
  const label=document.getElementById("forecast-history-toggle-label");
  if(!details)return;
  details.addEventListener("toggle",()=>{
    if(label) label.textContent=details.open?"Hide history":"Show history";
    if(details.open&&!HISTORY_LOADED) loadForecastHistory();
  });
}

function showHorizonPath(row){
  let box=document.getElementById("horizon-path");
  if(!box){
    box=document.createElement("div"); box.id="horizon-path"; box.className="horizon-path";
    const root=document.getElementById("forecast-list");
    root.parentNode.insertBefore(box,root.nextSibling);
  }
  const path=MULTI_PATHS.find(x=>x.belief_id===row.belief_id && x.forecast_at===row.forecast_at);
  const hb=HYPOTHESIS_BRIER[row.belief_id]||null;
  const hn=hb ? (hb.count ?? hb.n) : null;
  const hasDetails=hb && hb.period_start && hb.period_end && hb.direction_accuracy!=null && hb.brier_skill_score_vs_50_50!=null;
  const hypothesisBlock='<div class="hypothesis-brier"><b>Quality of this hypothesis</b><span>'+
    (hb ? 'n='+esc(hn)+' · Brier '+num(hb.mean_brier,3) : 'no resolved sample')+
    '</span><small>'+esc(row.belief_id||"—")+'</small>'+
    (hasDetails ? '<div class="hypothesis-details">Period: '+esc(forecastTime(hb.period_start))+' → '+esc(forecastTime(hb.period_end))+
      ' · direction accuracy: '+pct(hb.direction_accuracy)+
      ' · benchmark 50/50: '+num(hb.benchmark_brier_50_50,3)+
      ' · Brier Skill: '+pct(hb.brier_skill_score_vs_50_50)+
      (hb.sample_sufficient?' · sample ≥30':' · small sample')+
      '</div>' : '')+'</div>';
  const pathBlock=path
    ? '<div class="horizon-grid">'+path.horizons.map(h=>'<div><b>'+esc(h.horizon_label)+'</b><span>'+esc(h.status==='RESOLVED'?(h.outcome?'TAK':'NIE'):'pending')+'</span><small>Brier '+num(h.brier_score,3)+'</small></div>').join('')+'</div>'+horizonAggregateHtml()
    : '<p class="muted">The 3H / 12H / 24H / 3D / 5D path applies to new forecasts created after the research launch. This older record is not rewritten retroactively.</p>';
  const fmtValues=v=>v&&typeof v==='object'?Object.entries(v).map(([k,val])=>esc(k)+'='+num(val,5)).join(' · '):'—';
  const legacyT0=contractValue(row.t0_values);
  const contract=row.forecast_contract_version
    ? '<div class="forecast-contract"><b>Frozen Forecast Contract</b><span>Model: '+esc(row.model_freeze_version||"—")+'</span><span>T0: '+esc(forecastTime(row.t0_at||row.forecast_at))+' · '+fmtValues(row.t0_values)+'</span><span>Horizon: '+esc(row.horizon_label||"—")+' · Nominal target: '+esc(target(row.nominal_target_at||row.target_at))+'</span><span>Settlement: '+esc(row.settlement_rule||"—")+' · calendar '+esc(row.market_calendar||"—")+'</span><span>T1: '+esc(forecastTime(row.settled_at))+' · '+fmtValues(row.t1_values)+'</span><small>SHADOW ONLY · no automatic promotion and no authority to write to production</small></div>'
    : legacyT0
      ? '<div class="forecast-contract legacy"><b>Legacy forecast · frozen T0</b><span>T0: '+esc(forecastTime(row.t0_at||row.forecast_at))+' · '+fmtValues(row.t0_values)+'</span><span>T0 source: frozen outcome_spec.reference stored before Target — no after-the-fact reconstruction.</span><span>T1: '+esc(forecastTime(row.settled_at))+' · '+fmtValues(row.t1_values)+'</span></div>'
      : '<div class="forecast-contract legacy"><b>Legacy forecast</b><span>No preserved T0 value; it is not reconstructed after the fact.</span></div>';
  box.innerHTML='<div class="horizon-title"><b>Forecast analysis · '+esc(instrument(row))+'</b><span>Frozen P: '+pct(row.probability)+' · '+esc(forecastTime(row.forecast_at))+'</span></div>'+hypothesisBlock+contract+pathBlock;
  box.scrollIntoView({behavior:"smooth",block:"nearest"});
}
function horizonAggregateHtml(){
  const ready=HORIZON_AGG.filter(x=>Number(x.n)>=HORIZON_MIN_SAMPLE && x.mean_brier!=null);
  if(!ready.length) return '<p class="muted">Horizon aggregate will appear after at least '+HORIZON_MIN_SAMPLE+' resolved observations per horizon.</p>';
  return '<div class="horizon-aggregate"><b>Brier aggregate</b>'+ready.map(x=>'<span>'+esc(x.horizon)+' · n='+esc(x.n)+' · '+num(x.mean_brier,3)+'</span>').join('')+'</div>';
}

function metric(rootId,m){
  const root=document.getElementById(rootId);if(!root)return;
  const cells=rootId==="metrics" ? [["Forecasts",m.forecast_count],["Resolved",m.resolved_count],["Brier",m.brier_score],["ECE",m.ece],["Log loss",m.log_loss],["Brier Skill",m.brier_skill==null?"—":pct(m.brier_skill)]] : [["Forecasts",m.forecast_count],["Resolved",m.resolved_count],["Eligible",m.calibration_eligible],["Brier",m.brier_score],["ECE",m.ece],["Log loss",m.log_loss]];
  root.innerHTML=cells.map(([k,v])=>'<div class="metric"><small>'+k+'</small><b>'+(v==null?"—":esc(v))+'</b></div>').join("");
}

function calibrationCurve(a){
  const root=document.getElementById("calibration-curve"); if(!root)return;
  const rows=a?.curve||[]; if(!rows.length){root.innerHTML='<p class="muted">No data available for the calibration curve.</p>';return;}
  const w=720,h=310,pad=42, sx=v=>pad+Number(v)*(w-pad*2), sy=v=>h-pad-Number(v)*(h-pad*2);
  const points=rows.map(x=>sx(x.mean_predicted)+','+sy(x.observed_rate)).join(' ');
  root.innerHTML='<svg viewBox="0 0 '+w+' '+h+'" role="img" aria-label="Calibration Curve">'+
    '<line class="cal-axis" x1="'+pad+'" y1="'+(h-pad)+'" x2="'+(w-pad)+'" y2="'+(h-pad)+'"/><line class="cal-axis" x1="'+pad+'" y1="'+pad+'" x2="'+pad+'" y2="'+(h-pad)+'"/>'+
    '<line class="cal-perfect" x1="'+pad+'" y1="'+(h-pad)+'" x2="'+(w-pad)+'" y2="'+pad+'"/>'+
    '<polyline class="cal-line" points="'+points+'"/>'+
    rows.map(x=>'<g><circle class="cal-dot" cx="'+sx(x.mean_predicted)+'" cy="'+sy(x.observed_rate)+'" r="'+Math.min(9,4+Math.sqrt(x.n))+'"/><title>'+esc(x.range)+' · n='+x.n+' · P '+pct(x.mean_predicted)+' · observed '+pct(x.observed_rate)+'</title></g>').join('')+
    '<text x="'+pad+'" y="'+(h-10)+'">0%</text><text x="'+(w-pad-22)+'" y="'+(h-10)+'">100% P</text><text x="5" y="'+(pad+4)+'">100%</text><text x="8" y="'+(h-pad)+'">0%</text></svg>'+
    '<div class="cal-bin-list">'+rows.map(x=>'<span><b>'+esc(x.range)+'</b> P '+pct(x.mean_predicted)+' → '+pct(x.observed_rate)+' <small>n='+x.n+'</small></span>').join('')+'</div>';
}
let CAL_ANALYTICS={};
function calibrationBreakdown(kind){
  const root=document.getElementById("calibration-breakdown");if(!root)return;
  const rawRows=CAL_ANALYTICS?.breakdown?.[kind]||[];
  const rows=kind==="horizon"?rawRows.filter(x=>String(x?.segment||"").trim().toUpperCase()!=="0H"):rawRows;
  root.innerHTML='<div class="cal-table"><div class="cal-row head"><span>Segment</span><span>n</span><span>Brier</span><span>ECE</span><span>Skill vs 50/50</span></div>'+
    rows.map(x=>'<div class="cal-row"><b>'+esc(x.segment)+'</b><span>'+x.n+'</span><span>'+num(x.brier,3)+'</span><span>'+num(x.ece,3)+'</span><span class="'+(Number(x.brier_skill_vs_50_50)>=0?'skill-pos':'skill-neg')+'">'+pct(x.brier_skill_vs_50_50)+'</span></div>').join('')+'</div>';
}
function setupBreakdown(){
  document.querySelectorAll("[data-breakdown]").forEach(btn=>btn.addEventListener("click",()=>{
    document.querySelectorAll("[data-breakdown]").forEach(x=>x.classList.toggle("active",x===btn));
    calibrationBreakdown(btn.dataset.breakdown);
  }));
}
function rollingCalibration(a){
  const root=document.getElementById("rolling-calibration");if(!root)return;
  const rows=a?.rolling?.points||[];if(!rows.length){root.innerHTML='<p class="muted">Rolling metrics pojawią się po '+esc(a?.rolling?.window||50)+' rozliczonych forecastach.</p>';return;}
  const w=720,h=270,pad=42, max=Math.max(.35,...rows.flatMap(x=>[Number(x.brier)||0,Number(x.ece)||0]));
  const sx=i=>pad+(rows.length===1?0:i/(rows.length-1))*(w-pad*2), sy=v=>h-pad-(Number(v)/max)*(h-pad*2);
  const line=k=>rows.map((x,i)=>sx(i)+','+sy(x[k])).join(' ');
  root.innerHTML='<svg viewBox="0 0 '+w+' '+h+'" role="img" aria-label="Rolling Brier i ECE"><line class="cal-axis" x1="'+pad+'" y1="'+(h-pad)+'" x2="'+(w-pad)+'" y2="'+(h-pad)+'"/><polyline class="rolling-brier" points="'+line("brier")+'"/><polyline class="rolling-ece" points="'+line("ece")+'"/></svg><div class="rolling-legend"><span><i class="brier-key"></i>Brier</span><span><i class="ece-key"></i>ECE</span><small>okno '+esc(a.rolling.window)+' forecastów</small></div>';
}
function calibrationResults(a){
  CAL_ANALYTICS=a||{}; calibrationCurve(CAL_ANALYTICS); calibrationBreakdown("instrument"); rollingCalibration(CAL_ANALYTICS); setupBreakdown();
}
function v3Candidates(payload){
  const root=document.getElementById("v3-candidate-table"); if(!root)return;
  const rows=payload?.candidates||[], gov=payload?.governance||{}, summary=payload?.summary||{};
  if(!rows.length){root.innerHTML='<p class="muted">No v3 Candidate registry available.</p>';return;}
  const sample=x=>esc(x.sample_n??0)+(Number(x.open_n||0)>0?' <small>(+'+esc(x.open_n)+' open)</small>':'');
  const summaryHtml='<div class="candidate-summary"><b>'+esc(summary.wired??0)+' active SHADOW</b><span>'+esc(summary.waiting_for_real_source??0)+' waiting for real sources</span><span>'+esc(summary.open_count??0)+' open forecasts</span><span>'+esc(summary.resolved_count??0)+' resolved</span></div>';
  root.innerHTML=summaryHtml+'<div class="cal-table v3-table"><div class="cal-row head"><span>Candidate belief</span><span>n</span><span>Brier</span><span>ECE</span><span>Log loss</span><span>Incremental</span><span>Status / decision</span></div>'+
    rows.map(x=>'<div class="cal-row"><b>'+esc(x.belief_id)+'</b><span>'+sample(x)+'</span><span>'+num(x.brier,3)+'</span><span>'+num(x.ece,3)+'</span><span>'+num(x.log_loss,3)+'</span><span>'+esc(x.incremental_information==null?'—':x.incremental_information)+'</span><span><b>'+esc(x.production_recommendation)+'</b><small>'+esc(x.review_status)+'</small></span></div>').join('')+'</div>'+
    '<p class="muted">n = resolved forecasts. Gate: n≥'+esc(gov.minimum_sample_for_review||50)+' → calibration → incremental information → manual decision. Automatic promotion: OFF.</p>';
}

function closedLoopStatus(loop){
  const root=document.getElementById("closed-loop-status"); if(!root)return;
  const badge=document.getElementById("closed-loop-badge");
  const data=loop||{};

  if(data.schema_version==="briefrooms-evolution-controller-public-v1"){
    const s=data.summary||{}, candidates=Array.isArray(data.candidates)?data.candidates:[], gates=Array.isArray(data.promotion_gates)?data.promotion_gates:[], versions=data.production_versions||{};
    const beliefSource=data.source_status?.belief_calibration||{}, scans=beliefSource.scan_status||{};
    const activeVersions=Object.values(versions).flatMap(x=>Array.isArray(x)?x:[]).filter(x=>x?.status==="ACTIVE");
    const running=candidates.filter(x=>["OOS_RUNNING","READY_FOR_OOS","PROMOTION_ELIGIBLE"].includes(x.status));
    const rollbacks=Array.isArray(data.rollback_events)?data.rollback_events:[];
    let headline="MONITORING",tone="neutral";
    if(activeVersions.length){headline="EVOLUTION IN PRODUCTION";tone="good";}
    else if(running.length){headline="CHALLENGER · OOS";tone="test";}
    else if(rollbacks.length){headline="ROLLBACK / RETIREMENT";tone="warn";}
    if(badge){badge.textContent=headline;badge.className="status shadow loop-"+tone;}

    const cards=[
      ["Candidates",s.candidates_total??0],
      ["OOS / ready",s.oos_running??0],
      ["Active versions",s.active_production_versions??0],
      ["Hypotheses",s.hypotheses??0]
    ];
    const statusLabel=x=>String(x||"—").replaceAll("_"," ").toUpperCase();
    const candidateRows=candidates.slice(0,12).map(x=>{
      const gate=gates.find(g=>g.candidate_id===x.candidate_id)||{};
      const detail=[
        gate.minimum_sample?("OOS: "+(gate.observed_sample??0)+" / "+gate.minimum_sample):null,
        (gate.blockers||[]).length?("blockers: "+gate.blockers.join(", ")):null,
        x.promotion_route?("route: "+x.promotion_route):null
      ].filter(Boolean);
      return '<div class="loop-row"><div><b>'+esc(x.component_id||x.candidate_id)+'</b><small>'+esc(x.candidate_type||"candidate")+' · '+esc(x.source_module||"—")+' → '+esc(x.target_module||"—")+'</small></div><span class="loop-state '+esc(String(x.status||"").toLowerCase())+'">'+esc(statusLabel(x.status))+'</span><div class="loop-detail"><span>'+esc(detail.join(" · ")||"no additional data")+'</span></div></div>';
    }).join("");
    const scanRows=Object.entries(scans).map(([scope,x])=>'<div class="loop-row"><div><b>'+esc(scope==="__GLOBAL__"?"GLOBAL calibration":scope)+'</b><small>Belief calibration scan</small></div><span class="loop-state '+esc(String(x?.status||"").toLowerCase())+'">'+esc(statusLabel(x?.status||"—"))+'</span><div class="loop-detail"><span>discovery n: <b>'+esc(x?.last_discovery_n??"—")+'</b> · next search: <b>'+esc(x?.rediscovery_after_n??"—")+'</b> · prospective: <b>'+esc(x?.prospective_n??0)+'</b></span></div></div>').join("");
    const lifecycleRows=candidateRows+scanRows;
    root.innerHTML=
      '<div class="closed-loop-grid">'+cards.map(([k,v])=>'<div class="loop-card"><small>'+esc(k)+'</small><b>'+esc(v)+'</b></div>').join("")+'</div>'+
      '<div class="loop-governance"><b>Evolution Controller</b><span>prospective/OOS</span><span>segment safety</span><span>versioning</span><span>rollback + retirement</span><span>execution: OFF</span></div>'+
      '<div class="loop-list">'+(lifecycleRows||'<div class="loop-empty"><b>No active candidate.</b><span>The Controller monitors Belief, v3, Evidence Patterns, Experience Store and trading regret.</span></div>')+'</div>'+
      '<p class="muted loop-foot">Production mutation is performed only by the authorized component owner. Stock Trading remains owned by Stock Trading Component Promotion.</p>';
    return;
  }

  const gates=data.gates||{}, control=data.global_control_metrics||{};
  const challengers=Object.entries(data.challengers||{}).map(([id,row])=>({id,...(row||{})}));
  const active=Array.isArray(data.active_production_overrides)?data.active_production_overrides:[];
  if(badge){badge.textContent=active.length?"NEW PRODUCTION CALIBRATION":challengers.some(x=>x.status==="prospective_shadow")?"NEW CALIBRATION · OOS TEST":"MONITORING";badge.className="status shadow";}
  root.innerHTML='<div class="closed-loop-grid"><div class="loop-card"><small>v2 history</small><b>'+(control.n==null?"—":esc(control.n))+'</b></div><div class="loop-card"><small>Control Brier</small><b>'+num(control.brier,3)+'</b></div><div class="loop-card"><small>Control ECE</small><b>'+(control.ece==null?"—":pct(control.ece))+'</b></div><div class="loop-card"><small>Active calibrations</small><b>'+esc(active.length)+'</b></div></div><p class="muted loop-foot">Local Belief loop is waiting for Evolution Controller state.</p>';
}
function patternMetrics(meta){
  const root=document.getElementById("pattern-metrics");if(!root)return;
  const s=meta?.sample||{};
  const cells=[
    ["Verified",s.eligible_verified_forecasts],["Groups",s.groups_mined],["Patterns",s.patterns_published],
    ["Replicated",s.replicated],["OOS pass",s.oos_pass],["Unstable",s.unstable]
  ];
  root.innerHTML=cells.map(([k,v])=>'<div class="metric"><small>'+esc(k)+'</small><b>'+(v==null?"—":esc(v))+'</b></div>').join("");
}

function patternDetail(x){
  const root=document.getElementById("pattern-detail");if(!root)return;
  if(!x){
    root.innerHTML='<div class="pattern-empty"><b>Select a pattern</b><span>Click a table row to view discovery, holdout, regimes, residual and a possible modifier.</span></div>';
    return;
  }
  const d=x.discovery||{}, h=x.holdout||{};
  const outcomeText=(HYPOTHESES[x.belief_id]||x.belief_id||"Outcome")+' → '+(x.expected_outcome===false?"NIE":"TAK");
  const regimes=Object.entries(x.regimes||{});
  const modifier=x.possible_modifier;
  root.innerHTML=
    '<div class="pattern-detail-head"><div><span class="eyebrow">BRIEFROOMS PATTERN DISCOVERY</span><h3>'+esc(x.pattern_id||"—")+'</h3></div>'+patternStatus(x.status)+'</div>'+
    '<div class="pattern-detail-grid">'+
      '<section><small>Pattern</small><div class="pattern-atoms">'+(x.atom_labels||[]).map(a=>'<span>'+esc(a)+'</span>').join('')+'</div></section>'+
      '<section><small>Outcome</small><b>'+esc(outcomeText)+'</b><span>horizon '+esc(x.horizon_bucket||"—")+' · median '+num(x.horizon_hours_median,1)+'h</span></section>'+
      '<section><small>Discovery</small><b>'+esc(d.successes??"—")+' / '+esc(d.n??"—")+'</b><span>P '+pct(d.success_rate)+' · baseline '+pct(d.baseline)+' · lift '+pp(d.lift)+'</span></section>'+
      '<section><small>Holdout / OOS</small><b>'+esc(h.successes??"—")+' / '+esc(h.n??"—")+'</b><span>P '+pct(h.success_rate)+' · lift '+pp(h.lift)+'</span></section>'+
      '<section><small>MDL gain</small><b>'+bits(d.mdl_gain_bits)+'</b><span>A model is useful only after accounting for the cost of its description.</span></section>'+
      '<section><small>Residual / exceptions</small><b>'+esc(x.residual_exceptions??0)+' observations</b><span>Exceptions remain explicit; the system does not remove them.</span></section>'+
    '</div>'+
    '<div class="pattern-subgrid">'+
      '<section><h4>Regimes</h4>'+(regimes.length?regimes.map(([name,r])=>'<div class="regime-row"><span>'+esc(name.replaceAll("_"," "))+'</span><b>'+pct(r.success_rate)+'</b><small>n='+esc(r.n)+'</small></div>').join(''):'<p class="muted">Insufficient breakdown.</p>')+'</section>'+
      '<section><h4>Possible modifier</h4>'+(modifier?'<div class="modifier"><b>'+esc(modifier.label)+'</b><span>more frequent among exceptions by '+pp(modifier.enrichment)+'</span></div>':'<p class="muted">No stable modifier in the current sample.</p>')+'</section>'+
    '</div>'+
    '<div class="causal-warning"><b>CAUSAL STATUS: ASSOCIATION ONLY</b><span>The pattern is a repeatable statistical relationship. It is not evidence of causality and has no authority to change decisions or execute trades.</span></div>';
}

function patterns(items,meta){
  patternMetrics(meta||{});
  const root=document.getElementById("pattern-list");if(!root)return;
  const rows=Array.isArray(items)?items:[];
  if(!rows.length){
    root.innerHTML='<div class="pattern-empty"><b>No patterns meet the current criteria.</b><span>Pattern Discovery publishes only patterns with positive MDL gain discovered on frozen-before-outcome data. We do not generate demonstration data.</span></div>';
    patternDetail(null);
    return;
  }
  root.innerHTML=
    '<div class="pattern-row head"><span>Pattern</span><span>N</span><span>P(outcome)</span><span>Baseline</span><span>Lift</span><span>MDL gain</span><span>OOS</span><span>Status</span></div>'+
    rows.map((x,i)=>
      '<button class="pattern-row" type="button" data-pattern-index="'+i+'">'+
        '<b>'+esc(x.pattern_id||"—")+'</b>'+
        '<span>'+esc(x.discovery?.n??"—")+'</span>'+
        '<span class="probability">'+pct(x.discovery?.success_rate)+'</span>'+
        '<span>'+pct(x.discovery?.baseline)+'</span>'+
        '<span>'+pp(x.discovery?.lift)+'</span>'+
        '<span>'+bits(x.discovery?.mdl_gain_bits)+'</span>'+
        '<span>'+pct(x.holdout?.success_rate)+'</span>'+
        patternStatus(x.status)+
      '</button>'
    ).join("");
  root.querySelectorAll("[data-pattern-index]").forEach(btn=>btn.addEventListener("click",()=>{
    root.querySelectorAll(".pattern-row[data-pattern-index]").forEach(x=>x.classList.toggle("selected",x===btn));
    patternDetail(rows[Number(btn.dataset.patternIndex)]);
  }));
  const first=root.querySelector("[data-pattern-index]");
  if(first){first.classList.add("selected");patternDetail(rows[0]);}
}

async function load(){
  try{
    const stamp=Date.now();
    const [r,evolutionResponse]=await Promise.all([
      fetch("/data/investments/decision_lab_public.json?v="+stamp,{cache:"no-store"}),
      fetch("/data/investments/evolution_controller_public.json?v="+stamp,{cache:"no-store"}).catch(()=>null)
    ]);
    if(!r.ok)throw 0;
    const d=await r.json();
    let evolution={};
    if(evolutionResponse&&evolutionResponse.ok){
      try{evolution=await evolutionResponse.json();}catch(_){}
    }
    MULTI_PATHS=d.multihorizon_paths||[]; HORIZON_AGG=d.horizon_aggregate||[]; HYPOTHESIS_BRIER=d.hypothesis_brier||{}; const forecastRows=d.forecasts||[]; const view=(Array.isArray(d.market_view)&&d.market_view.length)?d.market_view:deriveMarketView(forecastRows); marketView(view); forecasts(forecastRows);
    metric("metrics-summary",d.metrics||{});
    metric("metrics",d.metrics||{});
    calibrationResults(d.calibration_analytics||{});
    v3Candidates(d.belief_core_v3_candidates||{});
    closedLoopStatus((evolution&&evolution.schema_version)?evolution:((d.evolution_controller&&d.evolution_controller.schema_version)?d.evolution_controller:(d.closed_loop||{})));
    patterns(d.evidence_patterns||[],d.evidence_pattern_meta||{});
  }catch(_){
    forecasts([]);
    metric("metrics-summary",{});
    metric("metrics",{});
    calibrationResults({});
    v3Candidates({});
    closedLoopStatus({});
    patterns([],{});
  }
}
setupForecastHistory();
load();
})();