(()=>{"use strict";

const esc=s=>String(s??"").replace(/[&<>"]/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[m]));
const pct=v=>v==null?"—":(Number(v)*100).toFixed(1)+"%";
const num=(v,d=3)=>v==null||!Number.isFinite(Number(v))?"—":Number(v).toFixed(d);

const HYPOTHESES={
  "spx.trend.bullish":"Trend S&P 500 jest wzrostowy",
  "spx.breadth.healthy":"Breadth rynku USA pozostaje zdrowy",
  "spx.volatility.benign":"Zmienność rynku pozostaje umiarkowana",
  "spx.liquidity.supportive":"Płynność / kredyt wspierają rynek",
  "spx.financial_conditions.supportive":"Warunki finansowe wspierają rynek",
  "eurusd.trend.bullish":"Trend EUR/USD jest wzrostowy",
  "eurusd.us_rates_pressure.supportive":"Presja stóp USA wspiera EUR/USD",
  "eurusd.usd_environment.supportive":"Otoczenie USD wspiera EUR/USD",
  "btc.trend.bullish":"Trend BTC/USD jest wzrostowy",
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

const tabs=[...document.querySelectorAll("[data-tab]")];
tabs.forEach(btn=>btn.addEventListener("click",()=>{
  tabs.forEach(x=>x.classList.toggle("active",x===btn));
  document.querySelectorAll(".tab-panel").forEach(p=>p.classList.toggle("active",p.id==="tab-"+btn.dataset.tab));
}));

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
      '<div class="forecast">'+
        '<b>'+esc(instrument(x))+'</b>'+
        '<span class="hypothesis" title="'+esc(x.belief_id||"")+'">'+esc(hypothesis(x))+'</span>'+
        '<span class="probability">'+pct(x.probability)+'</span>'+
        '<span>'+pct(x.confidence)+'</span>'+
        '<span class="target">'+esc(target(x.target_at))+'</span>'+
        outcome(x)+
        '<span class="brier">'+num(x.brier_score,3)+'</span>'+
        status(x.status)+
      '</div>'
    ).join("");
}

function metric(rootId,m){
  const root=document.getElementById(rootId);if(!root)return;
  const cells=[["Forecasty",m.forecast_count],["Resolved",m.resolved_count],["Eligible",m.calibration_eligible],["Brier",m.brier_score],["ECE",m.ece],["Log loss",m.log_loss]];
  root.innerHTML=cells.map(([k,v])=>'<div class="metric"><small>'+k+'</small><b>'+(v==null?"—":esc(v))+'</b></div>').join("");
}

async function load(){
  try{
    const r=await fetch("/data/investments/decision_lab_public.json?v="+Date.now(),{cache:"no-store"});
    if(!r.ok)throw 0;
    const d=await r.json();
    forecasts(d.forecasts||[]);
    metric("metrics-summary",d.metrics||{});
    metric("metrics",d.metrics||{});
  }catch(_){
    forecasts([]);
    metric("metrics-summary",{});
    metric("metrics",{});
  }
}
load();
})();