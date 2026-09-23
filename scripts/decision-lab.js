(()=>{"use strict";

const esc=s=>String(s??"").replace(/[&<>"]/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[m]));
const pct=v=>v==null?"—":(Number(v)*100).toFixed(1)+"%";
const pp=v=>v==null?"—":((Number(v)*100)>=0?"+":"")+(Number(v)*100).toFixed(1)+" pp";
const num=(v,d=3)=>v==null||!Number.isFinite(Number(v))?"—":Number(v).toFixed(d);
const bits=v=>v==null?"—":(Number(v)>=0?"+":"")+Number(v).toFixed(1)+" bit";

const HYPOTHESES={
  "spx.trend.bullish":"S&P 500 będzie wyżej w momencie Target niż w chwili prognozy",
  "spx.breadth.healthy":"Breadth rynku USA pozostaje zdrowy",
  "spx.volatility.benign":"Zmienność rynku pozostaje umiarkowana",
  "spx.liquidity.supportive":"Płynność / kredyt wspierają rynek",
  "spx.financial_conditions.supportive":"Warunki finansowe wspierają rynek",
  "eurusd.trend.bullish":"EUR/USD będzie wyżej w momencie Target niż w chwili prognozy",
  "eurusd.us_rates_pressure.supportive":"Presja stóp USA wspiera EUR/USD",
  "eurusd.usd_environment.supportive":"Otoczenie USD wspiera EUR/USD",
  "btc.trend.bullish":"BTC/USD będzie wyżej w momencie Target niż w chwili prognozy",
  "btc.volatility.benign":"Zmienność BTC pozostaje umiarkowana",
  "btc.liquidity.supportive":"Płynność wspiera BTC",
  "btc.usd_environment.supportive":"Otoczenie USD wspiera BTC"
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
  return d.toLocaleString("pl-PL",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"});
}
function probabilityMeaning(x){
  const p=Number(x?.probability);
  if(!Number.isFinite(p)) return "";
  const positive=p>=.5;
  const label=positive?"bardziej TAK":"bardziej NIE";
  return label+" · "+(Math.max(p,1-p)*100).toFixed(1)+"%";
}
function target(value){
  if(!value) return "—";
  const d=new Date(value);
  if(Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString("pl-PL",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"});
}
function outcome(x){
  if(String(x?.status||"").toUpperCase()!=="RESOLVED") return '<span class="outcome pending">—</span>';
  return x.outcome===true
    ? '<span class="outcome yes" title="Hipoteza potwierdzona">TAK</span>'
    : '<span class="outcome no" title="Hipoteza niepotwierdzona">NIE</span>';
}
function status(value){
  const v=String(value||"").toUpperCase();
  if(v==="RESOLVED") return '<span class="forecast-status resolved">ROZLICZONA</span>';
  if(v==="OPEN") return '<span class="forecast-status open">OTWARTA</span>';
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

let MULTI_PATHS=[]; let HORIZON_AGG=[]; const HORIZON_MIN_SAMPLE=30;
function forecasts(items){
  const root=document.getElementById("forecast-list");
  if(!root)return;
  if(!items.length){
    root.innerHTML='<p class="muted">Brak opublikowanych forecastów. Model nie tworzy danych demonstracyjnych.</p>';
    return;
  }
  root.innerHTML=
    '<div class="forecast head">'+
      '<span>Instrument</span><span>Hipoteza</span><span>P</span><span>Confidence</span>'+
      '<span>Target</span><span>Wynik</span><span>Brier</span><span>Status</span>'+
    '</div>'+
    items.slice(0,20).map(x=>
      '<button class="forecast forecast-click" type="button" data-forecast-id="'+esc(x.forecast_id||'')+'">'+
        '<b>'+esc(instrument(x))+'</b>'+
        '<span class="hypothesis" title="'+esc(x.belief_id||"")+'">'+esc(hypothesis(x))+'</span>'+
        '<span class="probability" title="'+esc(probabilityMeaning(x))+'">'+pct(x.probability)+'<small>'+esc(probabilityMeaning(x))+'</small></span>'+
        '<span>'+pct(x.confidence)+'</span>'+
        '<span class="target" title="Forecast: '+esc(forecastTime(x.forecast_at))+' · Target: '+esc(target(x.target_at))+'">'+esc(target(x.target_at))+'<small>od '+esc(forecastTime(x.forecast_at))+'</small></span>'+
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

function showHorizonPath(row){
  let box=document.getElementById("horizon-path");
  if(!box){ box=document.createElement("div"); box.id="horizon-path"; box.className="horizon-path"; document.getElementById("forecast-list").after(box); }
  const path=MULTI_PATHS.find(x=>x.belief_id===row.belief_id && x.forecast_at===row.forecast_at);
  if(!path){ box.innerHTML='<b>Analiza forecastu</b><span class="muted">Badanie 3H / 12H / 24H / 3D / 5D rozpoczyna się dla nowych forecastów.</span>'; return; }
  box.innerHTML='<div class="horizon-title"><b>Analiza forecastu · '+esc(instrument(row))+'</b><span>P zamrożone: '+pct(row.probability)+' · '+esc(forecastTime(row.forecast_at))+'</span></div>'+
    '<div class="horizon-grid">'+path.horizons.map(h=>'<div><b>'+esc(h.horizon_label)+'</b><span>'+esc(h.status==='RESOLVED'?(h.outcome?'TAK':'NIE'):'oczekuje')+'</span><small>Brier '+num(h.brier_score,3)+'</small></div>').join('')+'</div>'+
    horizonAggregateHtml();
}
function horizonAggregateHtml(){
  const ready=HORIZON_AGG.filter(x=>Number(x.n)>=HORIZON_MIN_SAMPLE && x.mean_brier!=null);
  if(!ready.length) return '<p class="muted">Agregat horyzontów pojawi się po min. '+HORIZON_MIN_SAMPLE+' rozliczonych obserwacjach na horyzont.</p>';
  return '<div class="horizon-aggregate"><b>Agregat Brier</b>'+ready.map(x=>'<span>'+esc(x.horizon)+' · n='+esc(x.n)+' · '+num(x.mean_brier,3)+'</span>').join('')+'</div>';
}

function metric(rootId,m){
  const root=document.getElementById(rootId);if(!root)return;
  const cells=[["Forecasty",m.forecast_count],["Resolved",m.resolved_count],["Eligible",m.calibration_eligible],["Brier",m.brier_score],["ECE",m.ece],["Log loss",m.log_loss]];
  root.innerHTML=cells.map(([k,v])=>'<div class="metric"><small>'+k+'</small><b>'+(v==null?"—":esc(v))+'</b></div>').join("");
}

function patternMetrics(meta){
  const root=document.getElementById("pattern-metrics");if(!root)return;
  const s=meta?.sample||{};
  const cells=[
    ["Verified",s.eligible_verified_forecasts],["Grupy",s.groups_mined],["Patterny",s.patterns_published],
    ["Replicated",s.replicated],["OOS pass",s.oos_pass],["Unstable",s.unstable]
  ];
  root.innerHTML=cells.map(([k,v])=>'<div class="metric"><small>'+esc(k)+'</small><b>'+(v==null?"—":esc(v))+'</b></div>').join("");
}

function patternDetail(x){
  const root=document.getElementById("pattern-detail");if(!root)return;
  if(!x){
    root.innerHTML='<div class="pattern-empty"><b>Wybierz pattern</b><span>Kliknij rekord w tabeli, aby zobaczyć discovery, holdout, regimes, residual i możliwy modifier.</span></div>';
    return;
  }
  const d=x.discovery||{}, h=x.holdout||{};
  const outcomeText=(HYPOTHESES[x.belief_id]||x.belief_id||"Outcome")+' → '+(x.expected_outcome===false?"NIE":"TAK");
  const regimes=Object.entries(x.regimes||{});
  const modifier=x.possible_modifier;
  root.innerHTML=
    '<div class="pattern-detail-head"><div><span class="eyebrow">ARIS-PATTERN-1</span><h3>'+esc(x.pattern_id||"—")+'</h3></div>'+patternStatus(x.status)+'</div>'+
    '<div class="pattern-detail-grid">'+
      '<section><small>Pattern</small><div class="pattern-atoms">'+(x.atom_labels||[]).map(a=>'<span>'+esc(a)+'</span>').join('')+'</div></section>'+
      '<section><small>Outcome</small><b>'+esc(outcomeText)+'</b><span>horizon '+esc(x.horizon_bucket||"—")+' · mediana '+num(x.horizon_hours_median,1)+'h</span></section>'+
      '<section><small>Discovery</small><b>'+esc(d.successes??"—")+' / '+esc(d.n??"—")+'</b><span>P '+pct(d.success_rate)+' · baseline '+pct(d.baseline)+' · lift '+pp(d.lift)+'</span></section>'+
      '<section><small>Holdout / OOS</small><b>'+esc(h.successes??"—")+' / '+esc(h.n??"—")+'</b><span>P '+pct(h.success_rate)+' · lift '+pp(h.lift)+'</span></section>'+
      '<section><small>MDL gain</small><b>'+bits(d.mdl_gain_bits)+'</b><span>Model opłaca się dopiero po koszcie jego opisu.</span></section>'+
      '<section><small>Residual / exceptions</small><b>'+esc(x.residual_exceptions??0)+' obserwacji</b><span>Wyjątki pozostają jawne; ARIS ich nie usuwa.</span></section>'+
    '</div>'+
    '<div class="pattern-subgrid">'+
      '<section><h4>Regimes</h4>'+(regimes.length?regimes.map(([name,r])=>'<div class="regime-row"><span>'+esc(name.replaceAll("_"," "))+'</span><b>'+pct(r.success_rate)+'</b><small>n='+esc(r.n)+'</small></div>').join(''):'<p class="muted">Brak wystarczającego rozbicia.</p>')+'</section>'+
      '<section><h4>Possible modifier</h4>'+(modifier?'<div class="modifier"><b>'+esc(modifier.label)+'</b><span>częstszy w wyjątkach o '+pp(modifier.enrichment)+'</span></div>':'<p class="muted">Brak stabilnego modifiera w obecnej próbce.</p>')+'</section>'+
    '</div>'+
    '<div class="causal-warning"><b>CAUSAL STATUS: ASSOCIATION ONLY</b><span>Pattern jest powtarzalną relacją statystyczną. Nie jest dowodem związku przyczynowego i nie ma authority do zmiany decyzji lub wykonania transakcji.</span></div>';
}

function patterns(items,meta){
  patternMetrics(meta||{});
  const root=document.getElementById("pattern-list");if(!root)return;
  const rows=Array.isArray(items)?items:[];
  if(!rows.length){
    root.innerHTML='<div class="pattern-empty"><b>Brak patternów spełniających obecne kryteria.</b><span>ARIS publikuje tylko wzorce z dodatnim MDL gain odkryte na danych frozen-before-outcome. Nie generujemy danych demonstracyjnych.</span></div>';
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
    const r=await fetch("/data/investments/decision_lab_public.json?v="+Date.now(),{cache:"no-store"});
    if(!r.ok)throw 0;
    const d=await r.json();
    MULTI_PATHS=d.multihorizon_paths||[]; HORIZON_AGG=d.horizon_aggregate||[]; forecasts(d.forecasts||[]);
    metric("metrics-summary",d.metrics||{});
    metric("metrics",d.metrics||{});
    patterns(d.aris_patterns||[],d.aris_pattern_meta||{});
  }catch(_){
    forecasts([]);
    metric("metrics-summary",{});
    metric("metrics",{});
    patterns([],{});
  }
}
load();
})();