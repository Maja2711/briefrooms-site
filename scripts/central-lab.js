(()=>{'use strict';
const esc=v=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const finite=v=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v));
const num=(v,d=0)=>finite(v)?new Intl.NumberFormat('pl-PL',{maximumFractionDigits:d}).format(Number(v)):'—';
const pct=(v,d=1)=>finite(v)?num(Number(v)*100,d)+'%':'—';
const get=async u=>{const r=await fetch(u+'?lab='+Date.now(),{cache:'no-store'});if(!r.ok)throw Error(r.status);return r.json()};
const chip=s=>'<span class="central-chip">'+esc(String(s||'—').replaceAll('_',' '))+'</span>';
function researchGate(status){
  const s=String(status||'').toLowerCase();
  if(s==='promoted'||s==='promotion_candidate')return {stage:5,next:'manual production review',reason:'pełny pipeline badawczy zaliczony'};
  if(s.includes('prospective'))return {stage:4,next:'prospective shadow',reason:'zbieranie wymaganej próby prospective'};
  if(s.includes('regime'))return {stage:3,next:'regime',reason:'wymagana stabilność między reżimami'};
  if(s.includes('holdout'))return {stage:2,next:'frozen holdout',reason:'wymagany niezależny frozen holdout'};
  if(s.includes('walk'))return {stage:1,next:'walk-forward',reason:'wymagany stabilny walk-forward'};
  if(s==='rejected_discovery')return {stage:0,next:'walk-forward',reason:'odrzucony na discovery — kryteria jakości niezaliczone'};
  return {stage:0,next:'walk-forward',reason:'za mało danych, aby zaliczyć discovery'};
}
function candidateReason(x){
  const g=researchGate(x.status),h=x.holdout_metrics||{},n=Number(h.count||0),pf=Number(h.profit_factor),mean=Number(h.mean_net_percent);
  if(String(x.status)==='insufficient_data')return n?g.reason+' (holdout n='+n+')':g.reason;
  if(String(x.status)==='rejected_discovery'){
    const bits=[]; if(Number.isFinite(mean))bits.push('mean '+num(mean,3)+'%'); if(Number.isFinite(pf))bits.push('PF '+num(pf,2));
    return g.reason+(bits.length?' · '+bits.join(' · '):'');
  }
  return g.reason;
}
async function strategy(){const root=document.querySelector('#central-strategy-lab');if(!root)return;try{const r=await get('/data/investments/research_lab_report.json');const statuses=r.status_counts||{},top=r.top_candidates||[];
const counts={candidate:Number(r.candidate_count||0),walk:0,holdout:0,regime:0,shadow:0,promotion:Number(r.promotion_registry_count??(r.promotion_registry||[]).length??0)};
Object.entries(statuses).forEach(([s,n])=>{const stage=researchGate(s).stage,v=Number(n||0);if(stage>=1)counts.walk+=v;if(stage>=2)counts.holdout+=v;if(stage>=3)counts.regime+=v;if(stage>=4)counts.shadow+=v;});
const funnel=[['Candidates',counts.candidate],['Walk-forward pass',counts.walk],['Frozen holdout pass',counts.holdout],['Regime pass',counts.regime],['Prospective shadow',counts.shadow],['Promotion candidates',counts.promotion]];
const topRows=top.slice().sort((a,b)=>{const ga=researchGate(a.status),gb=researchGate(b.status);if(gb.stage!==ga.stage)return gb.stage-ga.stage;const an=Number(a.holdout_metrics?.count||0),bn=Number(b.holdout_metrics?.count||0);if(bn!==an)return bn-an;return Number(b.holdout_metrics?.mean_net_percent||-999)-Number(a.holdout_metrics?.mean_net_percent||-999)}).slice(0,10);
root.innerHTML='<article class="panel central-head"><header><div><h2>Autonomous Strategy Research</h2><p>Izolowany silnik generowania i walidacji kandydatów strategii. Centralny LAB tylko odczytuje jego kanoniczny raport.</p></div>'+chip(r.execution_loop_closed?'LOOP CLOSED':'CHECK LOOP')+'</header><div class="central-stats"><div><small>Cykl research</small><strong>'+num(r.cycle)+'</strong></div><div><small>Evidence cycle</small><strong>'+num(r.evidence_cycle)+'</strong></div><div><small>Kandydaci</small><strong>'+num(r.candidate_count)+'</strong></div><div><small>Promotion registry</small><strong>'+num(counts.promotion)+'</strong></div></div>'+
'<h3 class="central-subtitle">Lejek walidacji</h3><div class="research-funnel">'+funnel.map((x,i)=>'<div><small>'+esc(x[0])+'</small><strong>'+num(x[1])+'</strong>'+(i<funnel.length-1?'<span>→</span>':'')+'</div>').join('')+'</div>'+
'<p class="central-note">Lejek pokazuje wyłącznie bramki potwierdzone przez status w kanonicznym raporcie. Brak statusu pass nie jest interpretowany jako zaliczenie.</p>'+
'<h3 class="central-subtitle">TOP kandydatów najbliżej kolejnej bramki</h3><div class="central-table-wrap"><table class="central-table research-candidates"><thead><tr><th>Kandydat</th><th>Setup</th><th>Status</th><th>Następna bramka</th><th>Holdout n</th><th>Hit rate</th><th>PF</th><th>Mean net</th><th>Max DD</th><th>Dlaczego nie przeszedł</th></tr></thead><tbody>'+topRows.map(x=>{const h=x.holdout_metrics||{},s=x.spec||{},g=researchGate(x.status);return '<tr><td><strong>'+esc(x.candidate_id)+'</strong></td><td>'+esc((s.instrument_id||'—').toUpperCase()+' · '+(s.side||'—')+' · '+(s.timeframe||'—'))+'<small>'+esc(s.rule||'—')+'</small></td><td>'+chip(x.status)+'</td><td><b>'+esc(g.next)+'</b></td><td>'+num(h.count)+'</td><td>'+pct(h.hit_rate)+'</td><td>'+num(h.profit_factor,2)+'</td><td>'+num(h.mean_net_percent,3)+'%</td><td>'+num(h.max_drawdown_percent,2)+'%</td><td>'+esc(candidateReason(x))+'</td></tr>'}).join('')+'</tbody></table></div>'+
'<p class="central-note">Pipeline pozostaje niezależny: candidate → walk-forward → frozen holdout → regime → prospective shadow. Brak automatycznej promocji do produkcji.</p></article>';}catch(e){root.innerHTML='<div class="central-error">Strategy Research jest chwilowo niedostępny.</div>'}}
async function brace(){const root=document.querySelector('#central-brace-lab');if(!root)return;try{const r=await get('/data/public/brace_spx_platform_public.json');const f=r.frozen_track||{},a=r.adaptive_research||{},p=r.promotion_gate||{};root.innerHTML='<article class="panel central-head"><header><div><h2>BRACE-SPX</h2><p>Frozen G6 i Adaptive Research pozostają jednym odseparowanym pipeline’em S&P 500. Tutaj widzisz jego stan bez przenoszenia backendu.</p></div>'+chip(p.status)+'</header><div class="central-stats"><div><small>Frozen G6</small><strong>'+num(f.observations_collected)+' / '+num(f.warmup_required)+'</strong></div><div><small>Research cycles</small><strong>'+num(a.research_cycles)+'</strong></div><div><small>Challengery</small><strong>'+num(a.active_challengers)+'</strong></div><div><small>Prospective N</small><strong>'+num(a.initial_challenger_prospective_n)+'</strong></div></div><div class="central-actions"><a href="/pl/inwestycje/brace-spx-lab.html">Pełny widok BRACE-SPX</a></div></article>';}catch(e){root.innerHTML='<div class="central-error">BRACE-SPX jest chwilowo niedostępny.</div>'}}
function metric(m){if(!m||m.value==null)return'—';return m.unit==='fraction'?pct(m.value,1):m.unit==='percent'?num(m.value,2)+'%':num(m.value,3)}
function domainLink(x){if(x.id==='gse-v2-learning-lab')return '<a href="/pl/geopolityka.html">Otwórz w Geopolityce</a>';if(x.id==='eurusd-abc-live-shadow'||x.id==='eurusd-x-adaptive-shadow')return '<a href="/pl/inwestycje/daily-trading.html">Otwórz w Daily Trading</a>';return''}
async function registry(){const root=document.querySelector('#central-registry-lab');if(!root)return;try{const [r,e,abc,x]=await Promise.all([get('/data/investments/experiment_registry.json'),get('/data/investments/experience_store_public.json'),get('/data/investments/eurusd_abc_public_pl.json').catch(()=>({})),get('/data/investments/eurusd_x_public_pl.json').catch(()=>({}))]);const experiments=[...(r.experiments||[])];if(x?.engine&&!experiments.some(v=>v.id==='eurusd-x-adaptive-shadow')){const xp=x?.performance?.champion||{},xn=Number(xp.n||0),xc=x?.adaptive_layer?.champion||{};experiments.push({id:'eurusd-x-adaptive-shadow',name:'EURUSD X Adaptive Shadow',family:'EURUSD',category:'trading',sample_count:xn,minimum_sample:30,primary_metric:{value:xp.profit_factor,unit:'ratio'},status:xn>=30?'RUNNING':'INSUFFICIENT_DATA',version:xc.version||'X-001'});}const rows=experiments.map(x=>'<tr'+(x.id==='eurusd-x-adaptive-shadow'?' class="central-x-row"':'')+'><td><strong>'+esc(x.name)+'</strong><small>'+esc(x.family)+'</small></td><td>'+esc(x.category)+'</td><td>'+num(x.sample_count)+' / '+num(x.minimum_sample)+'</td><td>'+metric(x.primary_metric)+'</td><td>'+chip(x.status)+'</td><td>'+domainLink(x)+'</td></tr>').join('');const s=e.summary||{},overall=e.overall_evidence||{},tp=overall.trading_performance||{};
const abcMap={'eurusd-abc-a':'A','eurusd-abc-b':'B','eurusd-abc-c':'C'};
const baseEngineRows=(e.engines||[]).map(x=>{const ev=x.evidence||{},perf=ev.trading_performance||{},arm=abcMap[x.engine],derived=arm?abc?.trade_comparison?.arms?.[arm]?.derived_net_2pip:null,useDerived=derived&&Number(derived.sample_size||0)>0;const netSample=useDerived?Number(derived.sample_size):Number(perf.settled_with_return||0),tradeN=Number(perf.n_trades||0),hit=useDerived?derived.hit_rate:perf.hit_rate,pf=useDerived?derived.profit_factor:perf.profit_factor,exp=useDerived?derived.mean_net_return_fraction:perf.expectancy_return_fraction,measure=useDerived?'DERIVED · 2 PIPS':perf.status;return '<tr><td><strong>'+esc(x.label||x.engine)+'</strong><small>'+esc(x.engine)+'</small></td><td>'+num(x.experience_count)+'</td><td>'+num(x.settled_count)+'</td><td>'+num(x.pending_count)+'</td><td>'+num(tradeN)+'</td><td>'+num(netSample)+' / '+num(ev.minimum_sample)+'</td><td>'+pct(hit,1)+'</td><td>'+num(pf,2)+'</td><td>'+pct(exp,2)+'</td><td>'+num(perf.average_r_multiple,3)+'</td><td>'+chip(measure)+'</td><td>'+chip(ev.assessment)+'</td></tr>'}).join('');
const xs=x?.sample||{},xa=x?.adaptive_layer||{},xc=xa.champion||{},xch=xa.challenger||{},xp=x?.performance?.champion||{},xn=Number(xp.n||0),xResolved=Number(xs.resolved||0),xCaptures=Number(xs.captures||0),xAssessment=xn>=30?'RUNNING':'INSUFFICIENT_DATA';
const xRow=x?.engine?'<tr class="central-x-row"><td><strong>EURUSD X</strong><small>'+esc(xc.version||'X-001')+(xch.version?' · challenger '+esc(xch.version):'')+'</small></td><td>'+num(xCaptures)+'</td><td>'+num(xResolved)+'</td><td>'+num(Math.max(0,xCaptures-xResolved))+'</td><td>'+num(xn)+'</td><td>'+num(xn)+' / 30</td><td>'+pct(xp.hit_rate,1)+'</td><td>'+num(xp.profit_factor,2)+'</td><td>'+(finite(xp.expectancy_pips)?num(xp.expectancy_pips,2)+' pips':'—')+'</td><td>—</td><td>'+chip('SHADOW · 2 PIPS')+'</td><td>'+chip(xAssessment)+'</td></tr>':'';
const engineRows=baseEngineRows+xRow;
const recent=(e.recent_experiences||[]).slice(0,12).map(x=>'<tr><td>'+esc(x.engine_label||x.engine)+'</td><td><strong>'+esc(x.instrument||'—')+'</strong></td><td>'+esc(x.action)+'</td><td>'+pct(x.confidence_fraction,1)+'</td><td>'+chip(x.status)+'</td><td>'+pct(x.return_fraction,2)+(x.return_basis?' <small>'+esc(x.return_basis)+'</small>':'')+'</td><td>'+esc(x.exit_reason||'—')+'</td></tr>').join('');
root.innerHTML='<article class="panel central-head"><header><div><h2>Experiment Registry</h2><p>Jedno miejsce nadzoru. GSE v2 oraz EUR/USD A/B/C i X pozostają w swoich domenach; Registry pokazuje tylko ich kanoniczny status.</p></div>'+chip(experiments.length+' EXPERIMENTS')+'</header><div class="central-table-wrap"><table class="central-table"><thead><tr><th>Eksperyment</th><th>Typ</th><th>Próba</th><th>Wynik</th><th>Status</th><th>Domena</th></tr></thead><tbody>'+rows+'</tbody></table></div></article>'+
'<article class="panel central-head"><header><div><h2>Experience Store</h2><p>Wspólna pamięć evidence z prospektywnych decyzji i późniejszych wyników. Tylko odczyt — bez writebacku, strojenia i wpływu na produkcję.</p></div>'+chip(s.assessment)+'</header><div class="central-stats"><div><small>Doświadczenia</small><strong>'+num(s.experience_count)+'</strong></div><div><small>Rozliczone</small><strong>'+num(s.settled_count)+'</strong></div><div><small>Oczekujące</small><strong>'+num(s.pending_count)+'</strong></div><div><small>Silniki</small><strong>'+num(s.engine_count)+'</strong></div><div><small>Źródła</small><strong>'+num(s.source_count)+'</strong></div><div><small>Formal alpha</small><strong>'+esc(s.formal_alpha_status)+'</strong></div></div>'+
'<h3 class="central-subtitle">Evidence ogółem</h3><div class="central-stats"><div><small>Próba evidence</small><strong>'+num(overall.sample_size)+' / '+num(overall.minimum_sample)+'</strong></div><div><small>Hit rate</small><strong>'+pct(tp.hit_rate,1)+'</strong></div><div><small>Profit factor</small><strong>'+num(tp.profit_factor,2)+'</strong></div><div><small>Expectancy / trade</small><strong>'+pct(tp.expectancy_return_fraction,2)+'</strong></div><div><small>Max drawdown</small><strong>'+pct(tp.max_drawdown_fraction,1)+'</strong></div><div><small>Sharpe / trade</small><strong>'+num(tp.sharpe_per_trade,2)+'</strong></div></div>'+
'<h3 class="central-subtitle">Evidence per silnik</h3><div class="central-table-wrap"><table class="central-table"><thead><tr><th>Silnik</th><th>Dośw.</th><th>Rozl.</th><th>Pending</th><th>Trade n</th><th>Net n / próg</th><th>Hit rate net</th><th>PF net</th><th>Expectancy net</th><th>Śr. R</th><th>Pomiar net</th><th>Ocena</th></tr></thead><tbody>'+engineRows+'</tbody></table></div>'+
'<h3 class="central-subtitle">Ostatnie doświadczenia</h3><div class="central-table-wrap"><table class="central-table"><thead><tr><th>Silnik</th><th>Instrument</th><th>Akcja</th><th>Confidence</th><th>Status</th><th>Zwrot</th><th>Wyjście</th></tr></thead><tbody>'+recent+'</tbody></table></div>'+
'<p class="central-note">Dla EUR/USD A/B/C centralny LAB wykorzystuje pełne prospektywne virtual trades i koszt 2 pips round-trip. EURUSD X jest czytany bezpośrednio z własnej projekcji shadow i pokazuje metryki aktualnego Championa po tym samym koszcie 2 pips. Żaden z tych odczytów nie ma writebacku ani prawa zmiany produkcji. Źródła: '+esc((e.sources||[]).map(x=>x.label).join(' · '))+' · EURUSD X · wygenerowano '+esc(e.generated_at||'—')+'.</p></article>';}catch(e){root.innerHTML='<div class="central-error">Registry / Experience Store są chwilowo niedostępne.</div>'}}

function shadowStatus(s){
  const key=String(s||'NO DATA').toUpperCase();
  return '<span class="shadow-engine-status '+key.toLowerCase().replaceAll(' ','-')+'">'+esc(key)+'</span>';
}
function shadowWhen(value){
  if(!value)return '—';
  const d=new Date(value);
  if(Number.isNaN(d.valueOf()))return esc(value);
  return d.toLocaleString('pl-PL',{dateStyle:'short',timeStyle:'short'});
}
async function shadows(){
  const root=document.querySelector('#central-shadow-engines');
  if(!root)return;
  try{
    const r=await get('/data/investments/shadow_engines_public.json');
    const s=r.summary||{},coverage=r.coverage||{},engines=Array.isArray(r.engines)?r.engines:[];
    const rows=engines.map(x=>'<tr>'+
      '<td><strong>'+esc(x.name)+'</strong><small>'+esc(x.workflow||'—')+'</small></td>'+
      '<td>'+shadowStatus(x.status)+'<small>'+esc(x.status_reason||'')+'</small></td>'+
      '<td>'+shadowWhen(x.last_run_at)+'</td>'+
      '<td><b>'+num(x.observations)+'</b><small>'+esc(x.observation_label||'obserwacje')+'</small></td>'+
      '<td><b>'+esc(x.champion||'—')+'</b></td>'+
      '<td><b>'+esc(x.challenger||'—')+'</b></td>'+
      '<td><a href="'+esc(x.domain||'#')+'">'+esc(x.domain_label||'Otwórz')+'</a></td>'+
    '</tr>').join('');
    const coverageChip=coverage.complete?'<span class="shadow-coverage ok">COVERAGE OK</span>':'<span class="shadow-coverage error">UNMAPPED SHADOW</span>';
    const unmapped=Array.isArray(coverage.unmapped_shadow_workflows)&&coverage.unmapped_shadow_workflows.length
      ? '<p class="shadow-coverage-warning">Niewpięte workflow shadow: '+esc(coverage.unmapped_shadow_workflows.join(' · '))+'</p>' : '';
    root.innerHTML='<article class="panel central-head shadow-observatory"><header><div><h2>Shadow Engines</h2><p>Automatyczny nadzór nad logicznymi silnikami działającymi w trybie shadow. Status pochodzi z ostatniego GitHub Actions run, a liczba obserwacji oraz Champion/Challenger z kanonicznych stanów danego silnika.</p></div>'+coverageChip+'</header>'+
      '<div class="central-stats shadow-summary">'+
        '<div><small>Silniki</small><strong>'+num(s.total)+'</strong></div>'+
        '<div><small>RUNNING</small><strong>'+num(s.RUNNING)+'</strong></div>'+
        '<div><small>IDLE</small><strong>'+num(s.IDLE)+'</strong></div>'+
        '<div><small>ERROR</small><strong>'+num(s.ERROR)+'</strong></div>'+
        '<div><small>NO DATA</small><strong>'+num(s["NO DATA"])+'</strong></div>'+
      '</div>'+unmapped+
      '<div class="central-table-wrap"><table class="central-table shadow-engine-table"><thead><tr><th>Shadow engine</th><th>Status</th><th>Ostatni run</th><th>Obserwacje</th><th>Champion</th><th>Challenger</th><th>Domena</th></tr></thead><tbody>'+rows+'</tbody></table></div>'+
      '<p class="central-note">Sekcja jest wyłącznie obserwatorium. Nie ma prawa do promocji, writebacku, zmiany parametrów ani wykonywania transakcji. Wygenerowano '+shadowWhen(r.generated_at)+'.</p></article>';
  }catch(e){
    root.innerHTML='<div class="central-error">Shadow Engines Observatory jest chwilowo niedostępny.</div>';
  }
}
function start(){strategy();brace();shadows();registry()}document.readyState==='loading'?document.addEventListener('DOMContentLoaded',start,{once:true}):start();})();