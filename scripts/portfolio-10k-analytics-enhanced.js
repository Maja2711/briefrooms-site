(()=>{
'use strict';
const lang=window.BR_PORTFOLIO_10K?.lang==='en'?'en':'pl';
const locale=lang==='en'?'en-US':'pl-PL';
const currency=lang==='en'?'USD':'PLN';
const $=s=>document.querySelector(s);
const n=v=>Number.isFinite(Number(v))?Number(v):null;
const pct=(v,d=2)=>v===null||!Number.isFinite(v)?'—':`${v>0?'+':''}${(v*100).toLocaleString(locale,{minimumFractionDigits:d,maximumFractionDigits:d})}%`;
const money=v=>v===null||!Number.isFinite(v)?'—':new Intl.NumberFormat(locale,{style:'currency',currency,maximumFractionDigits:2}).format(v);
const mean=a=>a.length?a.reduce((s,x)=>s+x,0)/a.length:null;
const stdev=a=>{if(a.length<2)return null;const m=mean(a);return Math.sqrt(a.reduce((s,x)=>s+(x-m)**2,0)/(a.length-1))};
const esc=value=>String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
function sourceUrl(){return '/data/investments/portfolio_10k.json?v='+Date.now()}
function launchLabel(data){
 const raw=data.launch_date||data.entry_date||'2026-07-01';
 const value=/^\d{4}-\d{2}-\d{2}$/.test(String(raw))?`${raw}T12:00:00Z`:String(raw);
 const date=new Date(value);
 if(!Number.isFinite(date.getTime()))return lang==='pl'?'Start: lipiec 2026':'Started: July 2026';
 const monthYear=new Intl.DateTimeFormat(lang==='pl'?'pl-PL':'en-US',{month:'long',year:'numeric',timeZone:'UTC'}).format(date);
 return lang==='pl'?`Start: ${monthYear}`:`Started: ${monthYear}`;
}
function renderLaunchCopy(data){
 const text=launchLabel(data);
 const overview=$('#portfolio-launch-label');
 const portfolio=$('#portfolio-launch-note');
 if(overview)overview.textContent=text;
 if(portfolio)portfolio.textContent=text;
}
function computedPortfolioValue(data){
 const cash=n(lang==='en'&&data.base_currency==='USD'?(data.cash_usd??data.cash_pln):data.cash_pln)||0;
 const positions=(data.positions||[]).filter(p=>p.status==='active').reduce((sum,p)=>{
  const value=lang==='en'&&data.base_currency==='USD'?(p.current_value_usd??p.current_value_pln):p.current_value_pln;
  return sum+(n(value)||0);
 },0);
 return positions+cash;
}
function valueOf(data){
 const explicit=lang==='en'&&data.base_currency==='USD'?n(data.total_value_usd??data.total_value_pln):n(data.total_value_pln);
 return explicit??computedPortfolioValue(data);
}
function startOf(data){if(lang==='en'&&data.base_currency==='USD')return n(data.starting_capital_usd??data.starting_capital_pln);return n(data.starting_capital_pln)}
function cashOf(data){if(lang==='en'&&data.base_currency==='USD')return n(data.cash_usd??data.cash_pln);return n(data.cash_pln)}
function snapshotValue(s,data){if(lang==='en'&&data.base_currency==='USD')return n(s.total_value_usd??s.total_value_pln??s.value_usd??s.value_pln);return n(s.total_value_pln??s.value_pln??s.portfolio_value_pln??s.value)}
function snapshotTime(s){const raw=s.timestamp_utc||s.timestamp||s.date||s.as_of||s.created_at;const t=raw?new Date(raw).getTime():NaN;return Number.isFinite(t)?t:null}
function metrics(data){
 const start=startOf(data),value=valueOf(data),snaps=(data.snapshots||[]).map(s=>({t:snapshotTime(s),v:snapshotValue(s,data)})).filter(x=>Number.isFinite(x.t)&&x.v!==null).sort((a,b)=>a.t-b.t);
 const ret=start&&value?value/start-1:null,profit=start&&value?value-start:null;
 let maxDD=null,peak=null;for(const x of snaps){peak=peak===null?x.v:Math.max(peak,x.v);const dd=peak?x.v/peak-1:null;if(dd!==null&&(maxDD===null||dd<maxDD))maxDD=dd}
 const daily=[];const byDay=new Map();for(const x of snaps)byDay.set(new Date(x.t).toISOString().slice(0,10),x.v);const vals=[...byDay.values()];for(let i=1;i<vals.length;i++)if(vals[i-1]>0)daily.push(vals[i]/vals[i-1]-1);
 const vol=stdev(daily);const annVol=vol===null?null:vol*Math.sqrt(252);const sharpe=vol&&daily.length>1?mean(daily)/vol*Math.sqrt(252):null;
 const first=snaps[0]?.t,last=snaps.at(-1)?.t,days=first&&last?Math.max(1,(last-first)/86400000):null;const annualized=ret!==null&&days?Math.pow(1+ret,365/days)-1:null;
 const bench=n(data.benchmark_return_percent??data.benchmark?.return_percent);const alpha=ret!==null&&bench!==null?ret-bench:null;
 const active=(data.positions||[]).filter(p=>p.status==='active');const profitable=active.length?active.filter(p=>n(p.pnl_percent)>0).length/active.length:null;
 const weights=active.map(p=>n(p.current_weight??p.target_weight)).filter(x=>x!==null);const top3=weights.length?[...weights].sort((a,b)=>b-a).slice(0,3).reduce((s,x)=>s+x,0):null;
 const total=active.reduce((s,p)=>s+(n(lang==='en'&&data.base_currency==='USD'?(p.current_value_usd??p.current_value_pln):p.current_value_pln)||0),0)+(cashOf(data)||0);
 return{ret,profit,maxDD,annVol,sharpe,annualized,alpha,profitable,top3,total,days,dailyCount:daily.length};
}
function miniChartSeries(data){
 const start=startOf(data),current=valueOf(data),launchRaw=data.launch_date||data.entry_date;
 const launchTime=launchRaw?new Date(launchRaw).getTime():Date.now()-86400000;
 const source=Array.isArray(data.snapshots)?data.snapshots:[];
 const points=source.map(s=>({t:snapshotTime(s),v:snapshotValue(s,data)})).filter(x=>x.t!==null&&x.v!==null&&x.v>0).sort((a,b)=>a.t-b.t);
 if(start&&start>0&&!points.some(x=>Math.abs(x.v-start)<0.01))points.unshift({t:Number.isFinite(launchTime)?launchTime:Date.now()-86400000,v:start});
 if(current&&current>0){const now=data.last_updated_at?new Date(data.last_updated_at).getTime():Date.now();const last=points.at(-1);if(!last||Math.abs(last.v-current)>0.01)points.push({t:Number.isFinite(now)?now:Date.now(),v:current});}
 return points.length>=2?points:[];
}
function renderMiniChart(data){
 const host=$('#mini-chart');if(!host)return;
 const points=miniChartSeries(data);if(points.length<2){host.innerHTML='';return;}
 const W=600,H=120,pad=8,values=points.map(x=>x.v),min=Math.min(...values),max=Math.max(...values),span=Math.max(max-min,Math.max(max,1)*0.005);
 const x=i=>pad+(W-pad*2)*(i/(points.length-1));
 const y=v=>H-pad-(H-pad*2)*((v-(min-span*.08))/(span*1.16));
 const line=points.map((p,i)=>`${i?'L':'M'}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ');
 const area=`${line} L${x(points.length-1).toFixed(1)},${H} L${x(0).toFixed(1)},${H} Z`;
 const start=points[0].v,end=points.at(-1).v,up=end>=start;
 const stroke=up?'#15964d':'#d64d5f',gradientId=up?'miniPortfolioUp':'miniPortfolioDown';
 const started=launchLabel(data);
 const valuePath=lang==='pl'?`Rzeczywista ścieżka wartości portfela: ${money(start)} → ${money(end)}`:`Actual portfolio value path: ${money(start)} → ${money(end)}`;
 const label=`${started} · ${valuePath}`;
 host.style.position='relative';
 host.innerHTML=`<span class="mini-chart-launch" style="position:absolute;left:6px;top:2px;z-index:2;padding:3px 7px;border:1px solid rgba(18,32,57,.12);border-radius:999px;background:rgba(255,255,255,.9);color:#526079;font-size:9px;font-weight:800;line-height:1.2;pointer-events:none">${esc(started)}</span><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(label)}"><defs><linearGradient id="${gradientId}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${stroke}" stop-opacity=".24"/><stop offset="1" stop-color="${stroke}" stop-opacity="0"/></linearGradient></defs><path fill="url(#${gradientId})" d="${area}"/><path fill="none" stroke="${stroke}" stroke-width="3" vector-effect="non-scaling-stroke" d="${line}"/></svg>`;
 host.title=label;
}
const labels=lang==='pl'?{
 profit:'Zysk / strata',return:'Zwrot od startu',annualized:'Zwrot annualizowany',drawdown:'Maks. obsunięcie',vol:'Zmienność annualizowana',sharpe:'Sharpe',alpha:'Alpha vs benchmark',profitable:'Zyskowne pozycje',top3:'Koncentracja Top 3',note:'Wskaźniki liczone z opublikowanych snapshotów. Przy krótkiej historii wartości annualizowane i Sharpe są orientacyjne.'
}:{profit:'Profit / loss',return:'Return since launch',annualized:'Annualised return',drawdown:'Max drawdown',vol:'Annualised volatility',sharpe:'Sharpe ratio',alpha:'Alpha vs benchmark',profitable:'Profitable positions',top3:'Top-3 concentration',note:'Metrics are calculated from published snapshots. With a short history, annualised return and Sharpe are indicative.'};
function card(label,value,sub='',cls=''){return `<article class="kpi"><small>${label}</small><strong class="${cls}">${value}</strong><span>${sub}</span></article>`}
async function enhance(){
 const host=$('#kpis');
 try{const r=await fetch(sourceUrl(),{cache:'no-store'});if(!r.ok)return;const data=await r.json();renderLaunchCopy(data);renderMiniChart(data);if(!host)return;const m=metrics(data);const tone=v=>v>0?'positive':v<0?'negative':'neutral';
 host.innerHTML=[card(labels.profit,money(m.profit),labels.return,tone(m.profit)),card(labels.return,pct(m.ret),currency,tone(m.ret)),card(labels.annualized,pct(m.annualized),m.days?`${Math.round(m.days)} d`:'—',tone(m.annualized)),card(labels.drawdown,pct(m.maxDD),lang==='pl'?'od szczytu':'from peak',tone(m.maxDD)),card(labels.vol,pct(m.annVol),`${m.dailyCount} ${lang==='pl'?'zmian dziennych':'daily changes'}`),card(labels.sharpe,m.sharpe===null?'—':m.sharpe.toFixed(2),lang==='pl'?'stopa wolna od ryzyka = 0':'risk-free rate = 0',tone(m.sharpe)),card(labels.alpha,pct(m.alpha),lang==='pl'?'portfel − benchmark':'portfolio − benchmark',tone(m.alpha)),card(labels.profitable,pct(m.profitable,0),`${(data.positions||[]).filter(p=>p.status==='active').length} ${lang==='pl'?'pozycji':'positions'}`),card(labels.top3,pct(m.top3,0),lang==='pl'?'udział 3 największych':'share of 3 largest')].join('');
 if(!$('.analytics-explainer'))host.insertAdjacentHTML('afterend',`<div class="analytics-explainer"><b>${lang==='pl'?'Jak czytać wskaźniki':'How to read the metrics'}:</b> ${labels.note}</div>`);
 }catch(_){/* retain base analytics */}
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>setTimeout(enhance,700));else setTimeout(enhance,700);
})();
