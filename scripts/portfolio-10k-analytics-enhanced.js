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
function sourceUrl(){return '/data/investments/portfolio_10k.json?v='+Date.now()}
function valueOf(data){if(lang==='en'&&data.base_currency==='USD')return n(data.total_value_usd??data.total_value_pln);return n(data.total_value_pln)}
function startOf(data){if(lang==='en'&&data.base_currency==='USD')return n(data.starting_capital_usd??data.starting_capital_pln);return n(data.starting_capital_pln)}
function cashOf(data){if(lang==='en'&&data.base_currency==='USD')return n(data.cash_usd??data.cash_pln);return n(data.cash_pln)}
function snapshotValue(s,data){if(lang==='en'&&data.base_currency==='USD')return n(s.total_value_usd??s.total_value_pln);return n(s.total_value_pln)}
function metrics(data){
 const start=startOf(data),value=valueOf(data),snaps=(data.snapshots||[]).map(s=>({t:new Date(s.timestamp_utc||s.date).getTime(),v:snapshotValue(s,data)})).filter(x=>Number.isFinite(x.t)&&x.v!==null).sort((a,b)=>a.t-b.t);
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
const labels=lang==='pl'?{
 profit:'Zysk / strata',return:'Zwrot od startu',annualized:'Zwrot annualizowany',drawdown:'Maks. obsunięcie',vol:'Zmienność annualizowana',sharpe:'Sharpe',alpha:'Alpha vs benchmark',profitable:'Zyskowne pozycje',top3:'Koncentracja Top 3',note:'Wskaźniki liczone z opublikowanych snapshotów. Przy krótkiej historii wartości annualizowane i Sharpe są orientacyjne.'
}:{profit:'Profit / loss',return:'Return since launch',annualized:'Annualised return',drawdown:'Max drawdown',vol:'Annualised volatility',sharpe:'Sharpe ratio',alpha:'Alpha vs benchmark',profitable:'Profitable positions',top3:'Top-3 concentration',note:'Metrics are calculated from published snapshots. With a short history, annualised return and Sharpe are indicative.'};
function card(label,value,sub='',cls=''){return `<article class="kpi"><small>${label}</small><strong class="${cls}">${value}</strong><span>${sub}</span></article>`}
async function enhance(){
 const host=$('#kpis');if(!host)return;
 try{const r=await fetch(sourceUrl(),{cache:'no-store'});if(!r.ok)return;const data=await r.json();const m=metrics(data);const tone=v=>v>0?'positive':v<0?'negative':'neutral';
 host.innerHTML=[card(labels.profit,money(m.profit),labels.return,tone(m.profit)),card(labels.return,pct(m.ret),currency,tone(m.ret)),card(labels.annualized,pct(m.annualized),m.days?`${Math.round(m.days)} d`:'—',tone(m.annualized)),card(labels.drawdown,pct(m.maxDD),lang==='pl'?'od szczytu':'from peak',tone(m.maxDD)),card(labels.vol,pct(m.annVol),`${m.dailyCount} ${lang==='pl'?'zmian dziennych':'daily changes'}`),card(labels.sharpe,m.sharpe===null?'—':m.sharpe.toFixed(2),lang==='pl'?'stopa wolna od ryzyka = 0':'risk-free rate = 0',tone(m.sharpe)),card(labels.alpha,pct(m.alpha),lang==='pl'?'portfel − benchmark':'portfolio − benchmark',tone(m.alpha)),card(labels.profitable,pct(m.profitable,0),`${(data.positions||[]).filter(p=>p.status==='active').length} ${lang==='pl'?'pozycji':'positions'}`),card(labels.top3,pct(m.top3,0),lang==='pl'?'udział 3 największych':'share of 3 largest')].join('');
 if(!$('.analytics-explainer'))host.insertAdjacentHTML('afterend',`<div class="analytics-explainer"><b>${lang==='pl'?'Jak czytać wskaźniki':'How to read the metrics'}:</b> ${labels.note}</div>`);
 }catch(_){/* retain base analytics */}
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>setTimeout(enhance,700));else setTimeout(enhance,700);
})();