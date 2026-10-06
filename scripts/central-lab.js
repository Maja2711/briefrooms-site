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
function hsePanel(hse){
  if(!hse||hse.engine!=='Hypothesis Shadow Engine 2.0')return '';
  const s=hse.summary||{},experiments=Array.isArray(hse.experiments)?hse.experiments:[],lessons=Array.isArray(hse.recent_lessons)?hse.recent_lessons:[];
  const rows=experiments.slice().sort((a,b)=>String(a.source_engine||'').localeCompare(String(b.source_engine||''))).map(x=>
    '<tr><td><strong>'+esc(x.source_engine||'—')+'</strong><small>'+esc(x.experiment_id||'')+'</small></td>'+
    '<td>'+esc(x.claim||'—')+'</td>'+
    '<td><b>'+esc(x.champion||'—')+'</b><small>vs '+esc(x.challenger||'—')+'</small></td>'+
    '<td>'+num(x.prospective_n)+' / '+num(x.target_n)+'</td>'+
    '<td>'+shadowStatus(x.status)+'</td>'+
    '<td>'+shadowWhen(x.last_evidence_at||x.frozen_at)+'</td></tr>'
  ).join('');
  const lessonRows=lessons.slice().reverse().slice(0,8).map(x=>
    '<tr><td><strong>'+esc(x.source_engine||'—')+'</strong></td><td>'+shadowStatus(x.verdict)+'</td><td>'+esc(x.statement||'—')+'</td></tr>'
  ).join('');
  return '<article class="panel central-head hse2-panel"><header><div><h2>Hypothesis Shadow Engine 2.0</h2><p>Siedem źródeł → zamrożona hipoteza → wyłącznie forward evidence → jedna formalna ocena fixed-N → LESSON. Zero production authority.</p></div>'+chip((s.running_shadow||0)+' ACTIVE')+'</header>'+
    '<div class="central-stats"><div><small>Źródła</small><strong>'+num(s.sources_available)+' / '+num(s.sources_configured)+'</strong></div><div><small>Eksperymenty</small><strong>'+num(s.experiments_total)+'</strong></div><div><small>Forward evidence N</small><strong>'+num(s.prospective_evidence_n)+'</strong></div><div><small>Lessons</small><strong>'+num(s.lessons_total)+'</strong></div></div>'+
    '<h3 class="central-subtitle">Aktywne i zakończone hipotezy</h3><div class="central-table-wrap"><table class="central-table hse2-table"><thead><tr><th>Źródło</th><th>Hipoteza</th><th>Champion / Challenger</th><th>Forward N</th><th>Status</th><th>Freeze / evidence</th></tr></thead><tbody>'+rows+'</tbody></table></div>'+
    (lessonRows?'<h3 class="central-subtitle">Ostatnie LESSONS</h3><div class="central-table-wrap"><table class="central-table"><thead><tr><th>Źródło</th><th>Werdykt</th><th>Lesson</th></tr></thead><tbody>'+lessonRows+'</tbody></table></div>':'<p class="central-note">Brak zakończonych LESSONS — HSE2 zbiera dopiero evidence po granicy T0.</p>')+
    '<p class="central-note">Wynik formalny może być tylko SUPPORTED, REJECTED albo INCONCLUSIVE. HSE2 nie może zmieniać konfiguracji źródłowych silników ani produkcji.</p></article>';
}

function fseInstrumentLabel(value){
  const key=String(value||'').toUpperCase();
  return ({EURUSD:'EUR/USD',BTCUSD:'BTC/USD',SPX:'S&P 500'})[key]||key||'—';
}
function fseSignedPct(value,d=2){
  if(!finite(value))return '—';
  const n=Number(value)*100;
  return (n>0?'+':'')+num(n,d)+'%';
}
function fseMae(value,d=2){
  if(!finite(value))return '—';
  return '−'+num(Math.abs(Number(value))*100,d)+'%';
}
function fseRegime(value){
  const key=String(value||'UNKNOWN').toUpperCase();
  return '<span class="fse-regime '+key.toLowerCase().replaceAll('_','-')+'">'+esc(key)+'</span>';
}
function fseTrackLabel(m){
  const kind=String(m?.details?.kind||'');
  if(kind==='risk_calibration')return 'Structural Riskasync function fse(){
  const root=document.querySelector('#central-fse-lab');
  if(!root)return;
  try{
    const [r,hse,v2]=await Promise.all([
      get('/data/investments/fse_public.json'),
      get('/data/investments/hypothesis_shadow_engine_v2_public.json').catch(()=>({})),
      get('/data/investments/fse_v2_public.json').catch(()=>({}))
    ]);
    const instruments=Array.isArray(r.instruments)?r.instruments:[];
    const v2Instruments=Array.isArray(v2.instruments)?v2.instruments:[];
    const measurements=Array.isArray(r.hse_measurements)?r.hse_measurements:[];
    const phaseMeasurements=Array.isArray(v2.hse_measurements)?v2.hse_measurements:[];
    const allMeasurements=[...measurements,...phaseMeasurements];
    const experiments=Array.isArray(hse.experiments)?hse.experiments.filter(x=>x.source_engine==='FSE'):[];
    const available=Number(r?.source_status?.available||instruments.length);
    const configured=Number(r?.source_status?.configured||instruments.length);
    const overviewRows=instruments.map(x=>{
      const m=x.fractal_memory||{};
      return '<tr><td><strong>'+esc(fseInstrumentLabel(x.instrument))+'</strong><small>'+shadowWhen(x.observed_at)+'</small></td><td>'+fseRegime(x.regime)+'</td><td><b>'+pct(x.risk_score,1)+'</b></td><td><b>'+pct(m.p_up_4h,1)+'</b></td><td>'+num(m.analogues_n)+'</td><td>'+pct(m.top_similarity,1)+'</td><td>'+fseSignedPct(m.median_forward_return,2)+'</td><td>'+fseMae(m.median_adverse_excursion,2)+'</td></tr>';
    }).join('');
    const memoryCards=instruments.map(x=>{
      const m=x.fractal_memory||{},g=x.research_risk_geometry||{};
      return '<div class="fse-memory-card"><div class="fse-card-head"><div><strong>'+esc(fseInstrumentLabel(x.instrument))+'</strong><small>'+esc(m.source||'—')+'</small></div>'+fseRegime(x.regime)+'</div><div class="fse-card-prob"><span>P UP</span><b>'+pct(m.p_up_4h,1)+'</b><em>'+esc(m.forecast||'—')+'</em></div><div class="fse-card-grid"><span><small>Top similarity</small><b>'+pct(m.top_similarity,1)+'</b></span><span><small>Mean similarity</small><b>'+pct(m.mean_similarity,1)+'</b></span><span><small>Analogi</small><b>'+num(m.analogues_n)+'</b></span><span><small>Median +4h</small><b>'+fseSignedPct(m.median_forward_return,2)+'</b></span><span><small>MAE</small><b>'+fseMae(m.median_adverse_excursion,2)+'</b></span><span><small>Risk</small><b>'+pct(x.risk_score,1)+'</b></span></div><div class="fse-risk-geometry"><span>Research sizing ×'+num(g.position_size_multiplier,2)+'</span><span>SL distance ×'+num(g.stop_distance_multiplier,2)+'</span><b>PRODUCTION OFF</b></div></div>';
    }).join('');
    const phaseSummary=v2Instruments.map(x=>{
      const a=x.cross_scale_alignment||{},c=x.p_calibration_challenger||{},p1=x?.phase_map?.['1h']||{},pm=p1.phase_memory||{};
      return '<tr><td><strong>'+esc(fseInstrumentLabel(x.instrument))+'</strong></td><td><b>'+esc(a.cascade_state||'—')+'</b></td><td>'+pct(a.alignment_score,1)+'</td><td>'+esc(a.dominant_scale||'—')+'</td><td>'+pct(c.p_base,1)+'</td><td>'+pct(pm.p_up_remaining,1)+'</td><td><b>'+pct(c.p_challenger,1)+'</b></td><td>'+(finite(c.shift_pp)?num(c.shift_pp,1)+' pp':'—')+'</td><td>'+esc(x.regime||'—')+'</td></tr>';
    }).join('');
    const phaseRows=v2Instruments.flatMap(x=>{
      const formation=x.intrabar_formation||{};
      return ['1m','5m','15m','1h','4h','1d','1w'].map(tf=>{
        const p=x?.phase_map?.[tf]||{},pm=p.phase_memory||{},ib=formation[tf]||{};
        return '<tr><td><strong>'+esc(fseInstrumentLabel(x.instrument))+'</strong></td><td><b>'+esc(tf)+'</b></td><td>'+esc(p.structure_id||'—')+'</td><td>'+esc(p.phase||'—')+'</td><td>'+pct(pm.phase_progress,1)+'</td><td>'+pct(pm.top_similarity,1)+'</td><td>'+pct(pm.p_up_remaining,1)+'</td><td>'+(ib.available?pct(ib.formation_progress,1):'—')+'</td><td>'+num(pm.analogues_n)+'</td></tr>';
      });
    }).join('');
    const validationRows=allMeasurements.map(m=>{
      const instrument=String(m?.details?.instrument||''),kind=String(m?.details?.kind||'');
      const counter=Number(m.counter||0),total=Number(m.total||0),edge=counter>0?total/counter:null;
      const exp=experiments.find(x=>x.metric_name===m.metric_name&&String(x.claim||'').includes(instrument));
      const brierText=kind==='p_calibration_regime_phase'
        ? (edge==null?'—':'Δ '+num(edge,4))
        : (edge==null?'—':num(.25-edge,4));
      return '<tr><td><strong>'+esc(fseInstrumentLabel(instrument))+'</strong></td><td>'+esc(fseTrackLabel(m))+'</td><td>'+num(counter)+' / '+num(m.target_n)+'</td><td>'+brierText+'</td><td>'+(edge==null?'—':fseSignedPct(edge,2))+'</td><td>'+shadowStatus(exp?.status||'RUNNING_SHADOW')+'</td><td>'+shadowWhen(exp?.last_evidence_at||exp?.frozen_at)+'</td></tr>';
    }).join('');
    const forwardN=experiments.reduce((sum,x)=>sum+Number(x.prospective_n||0),0);
    const targetN=experiments.reduce((sum,x)=>sum+Number(x.target_n||0),0);
    const terminal=experiments.filter(x=>['SUPPORTED','REJECTED','INCONCLUSIVE'].includes(String(x.status||'').toUpperCase())).length;
    root.innerHTML=
      '<article class="panel central-head fse-panel"><header><div><h2>FSE — Fractal Structure Engine</h2><p>Wieloskalowa struktura rynku: regime, Fractal Memory oraz FSE-PHASE. Phase Engine śledzi etap formowania wzorca na różnych skalach, bez wpływu na produkcyjny trading.</p></div>'+chip(v2?.methodology_version||r.mode||'SHADOW_ONLY')+'</header>'+
      '<div class="central-stats fse-summary"><div><small>Źródła</small><strong>'+num(available)+' / '+num(configured)+'</strong></div><div><small>Instrumenty</small><strong>'+num(instruments.length)+'</strong></div><div><small>HSE2 forward N</small><strong>'+num(forwardN)+' / '+num(targetN)+'</strong></div><div><small>Promotion gate</small><strong>'+esc(v2?.promotion_gate?.status||'NOT ELIGIBLE')+'</strong></div></div>'+
      '<h3 class="central-subtitle">Aktualna struktura rynku</h3><div class="central-table-wrap"><table class="central-table fse-overview-table"><thead><tr><th>Instrument</th><th>Regime</th><th>Risk</th><th>P UP</th><th>Analogów</th><th>Similarity</th><th>Median +4h</th><th>MAE</th></tr></thead><tbody>'+overviewRows+'</tbody></table></div>'+
      '<h3 class="central-subtitle">Fractal Memory</h3><div class="fse-memory-grid">'+memoryCards+'</div>'+
      '<h3 class="central-subtitle">FSE-PHASE · Cross-Scale Alignment + P Challenger</h3>'+
      (v2?.methodology_version?'<div class="central-table-wrap"><table class="central-table fse-phase-summary"><thead><tr><th>Instrument</th><th>Cascade</th><th>Alignment</th><th>Dominant TF</th><th>P base</th><th>P phase 1H</th><th>P challenger</th><th>Shift</th><th>Regime</th></tr></thead><tbody>'+phaseSummary+'</tbody></table></div>':'<p class="central-note">FSE-PHASE czeka na pierwszy cykl po wdrożeniu.</p>')+
      (phaseRows?'<h3 class="central-subtitle">Fractal Phase Map · Intrabar Formation</h3><div class="central-table-wrap"><table class="central-table fse-phase-table"><thead><tr><th>Instrument</th><th>TF</th><th>Structure</th><th>Phase</th><th>Progress</th><th>Similarity</th><th>P UP remaining</th><th>Intrabar</th><th>Analogi</th></tr></thead><tbody>'+phaseRows+'</tbody></table></div>':'')+
      '<h3 class="central-subtitle">Prospective learning · Brier · HSE2</h3><div class="central-table-wrap"><table class="central-table fse-validation-table"><thead><tr><th>Instrument</th><th>Tor</th><th>Forward N</th><th>Brier / Δ</th><th>Edge</th><th>HSE2 status</th><th>Freeze / evidence</th></tr></thead><tbody>'+validationRows+'</tbody></table></div>'+
      '<p class="central-note">FSE-PHASE nie dostaje credit za historię. Phase Memory i P Calibration Challenger zaczynają formalne N dopiero od snapshotów zamrożonych pod metodologią FSE-PHASE-1.0. Challenger ma twardy limit korekty ±6 pp i nie może pisać do produkcji. Wygenerowano '+shadowWhen(v2.generated_at||r.generated_at)+'.</p></article>';
  }catch(e){
    root.innerHTML='<div class="central-error">FSE jest chwilowo niedostępny.</div>';
  }
}Wygenerowano '+shadowWhen(r.generated_at)+'.</p></article>';
  }catch(e){
    root.innerHTML='<div class="central-error">FSE jest chwilowo niedostępny.</div>';
  }
}

async function shadows(){
  const root=document.querySelector('#central-shadow-engines');
  if(!root)return;
  try{
    const [r,hse]=await Promise.all([get('/data/investments/shadow_engines_public.json'),get('/data/investments/hypothesis_shadow_engine_v2_public.json').catch(()=>({}))]);
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
      '<p class="central-note">Sekcja jest wyłącznie obserwatorium. Nie ma prawa do promocji, writebacku, zmiany parametrów ani wykonywania transakcji. Wygenerowano '+shadowWhen(r.generated_at)+'.</p></article>'+
      hsePanel(hse);
  }catch(e){
    root.innerHTML='<div class="central-error">Shadow Engines Observatory jest chwilowo niedostępny.</div>';
  }
}
function start(){strategy();brace();fse();shadows();registry()}document.readyState==='loading'?document.addEventListener('DOMContentLoaded',start,{once:true}):start();})();