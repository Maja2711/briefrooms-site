(()=>{'use strict';
const esc=v=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const finite=v=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v));
const num=(v,d=0)=>finite(v)?new Intl.NumberFormat('en-US',{maximumFractionDigits:d}).format(Number(v)):'—';
const pct=(v,d=1)=>finite(v)?num(Number(v)*100,d)+'%':'—';
const get=async u=>{const r=await fetch(u+'?lab='+Date.now(),{cache:'no-store'});if(!r.ok)throw Error(r.status);return r.json()};
const chip=s=>'<span class="central-chip">'+esc(String(s||'—').replaceAll('_',' '))+'</span>';
function researchGate(status){
  const s=String(status||'').toLowerCase();
  if(s==='promoted'||s==='promotion_candidate')return {stage:5,next:'manual production review',reason:'full research pipeline passed'};
  if(s.includes('prospective'))return {stage:4,next:'prospective shadow',reason:'collecting the required prospective sample'};
  if(s.includes('regime'))return {stage:3,next:'regime',reason:'stability across regimes required'};
  if(s.includes('holdout'))return {stage:2,next:'frozen holdout',reason:'independent frozen holdout required'};
  if(s.includes('walk'))return {stage:1,next:'walk-forward',reason:'wymagany stabilny walk-forward'};
  if(s==='rejected_discovery')return {stage:0,next:'walk-forward',reason:'rejected at discovery — quality criteria not met'};
  return {stage:0,next:'walk-forward',reason:'insufficient data to pass discovery'};
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
root.innerHTML='<article class="panel central-head"><header><div><h2>Autonomous Strategy Research</h2><p>An isolated engine for generating and validating strategy candidates. Central LAB only reads its canonical report.</p></div>'+chip(r.execution_loop_closed?'LOOP CLOSED':'CHECK LOOP')+'</header><div class="central-stats"><div><small>Research cycle</small><strong>'+num(r.cycle)+'</strong></div><div><small>Evidence cycle</small><strong>'+num(r.evidence_cycle)+'</strong></div><div><small>Candidates</small><strong>'+num(r.candidate_count)+'</strong></div><div><small>Promotion registry</small><strong>'+num(counts.promotion)+'</strong></div></div>'+
'<h3 class="central-subtitle">Validation funnel</h3><div class="research-funnel">'+funnel.map((x,i)=>'<div><small>'+esc(x[0])+'</small><strong>'+num(x[1])+'</strong>'+(i<funnel.length-1?'<span>→</span>':'')+'</div>').join('')+'</div>'+
'<p class="central-note">The funnel shows only gates confirmed by status in the canonical report. Missing pass status is not interpreted as a pass.</p>'+
'<h3 class="central-subtitle">Top candidates closest to the next gate</h3><div class="central-table-wrap"><table class="central-table research-candidates"><thead><tr><th>Candidate</th><th>Setup</th><th>Status</th><th>Next gate</th><th>Holdout n</th><th>Hit rate</th><th>PF</th><th>Mean net</th><th>Max DD</th><th>Why it did not pass</th></tr></thead><tbody>'+topRows.map(x=>{const h=x.holdout_metrics||{},s=x.spec||{},g=researchGate(x.status);return '<tr><td><strong>'+esc(x.candidate_id)+'</strong></td><td>'+esc((s.instrument_id||'—').toUpperCase()+' · '+(s.side||'—')+' · '+(s.timeframe||'—'))+'<small>'+esc(s.rule||'—')+'</small></td><td>'+chip(x.status)+'</td><td><b>'+esc(g.next)+'</b></td><td>'+num(h.count)+'</td><td>'+pct(h.hit_rate)+'</td><td>'+num(h.profit_factor,2)+'</td><td>'+num(h.mean_net_percent,3)+'%</td><td>'+num(h.max_drawdown_percent,2)+'%</td><td>'+esc(candidateReason(x))+'</td></tr>'}).join('')+'</tbody></table></div>'+
'<p class="central-note">The pipeline remains independent: candidate → walk-forward → frozen holdout → regime → prospective shadow. There is no automatic promotion to production.</p></article>';}catch(e){root.innerHTML='<div class="central-error">Strategy Research is temporarily unavailable.</div>'}}
async function brace(){const root=document.querySelector('#central-brace-lab');if(!root)return;try{const r=await get('/data/public/brace_spx_platform_public.json');const f=r.frozen_track||{},a=r.adaptive_research||{},p=r.promotion_gate||{};root.innerHTML='<article class="panel central-head"><header><div><h2>BRACE-SPX</h2><p>Frozen G6 i Adaptive Research pozostają jednym odseparowanym pipeline’em S&P 500. Tutaj widzisz jego as of bez przenoszenia backendu.</p></div>'+chip(p.status)+'</header><div class="central-stats"><div><small>Frozen G6</small><strong>'+num(f.observations_collected)+' / '+num(f.warmup_required)+'</strong></div><div><small>Research cycles</small><strong>'+num(a.research_cycles)+'</strong></div><div><small>Challengery</small><strong>'+num(a.active_challengers)+'</strong></div><div><small>Prospective N</small><strong>'+num(a.initial_challenger_prospective_n)+'</strong></div></div><div class="central-actions"><a href="/en/investing/brace-spx-lab.html">Full BRACE-SPX view</a></div></article>';}catch(e){root.innerHTML='<div class="central-error">BRACE-SPX is temporarily unavailable.</div>'}}
function metric(m){if(!m||m.value==null)return'—';return m.unit==='fraction'?pct(m.value,1):m.unit==='percent'?num(m.value,2)+'%':num(m.value,3)}
function domainLink(x){if(x.id==='gse-v2-learning-lab')return '<a href="/en/geopolitics.html">Open in Geopolitics</a>';if(x.id==='eurusd-abc-live-shadow'||x.id==='eurusd-x-adaptive-shadow')return '<a href="/en/investing/daily-trading.html">Open in Daily Trading</a>';return''}
async function registry(){const root=document.querySelector('#central-registry-lab');if(!root)return;try{const [r,e,abc,x]=await Promise.all([get('/data/investments/experiment_registry.json'),get('/data/investments/experience_store_public.json'),get('/data/investments/eurusd_abc_public_pl.json').catch(()=>({})),get('/data/investments/eurusd_x_public_pl.json').catch(()=>({}))]);const experiments=[...(r.experiments||[])];if(x?.engine&&!experiments.some(v=>v.id==='eurusd-x-adaptive-shadow')){const xp=x?.performance?.champion||{},xn=Number(xp.n||0),xc=x?.adaptive_layer?.champion||{};experiments.push({id:'eurusd-x-adaptive-shadow',name:'EURUSD X Adaptive Shadow',family:'EURUSD',category:'trading',sample_count:xn,minimum_sample:30,primary_metric:{value:xp.profit_factor,unit:'ratio'},status:xn>=30?'RUNNING':'INSUFFICIENT_DATA',version:xc.version||'X-001'});}const rows=experiments.map(x=>'<tr'+(x.id==='eurusd-x-adaptive-shadow'?' class="central-x-row"':'')+'><td><strong>'+esc(x.name)+'</strong><small>'+esc(x.family)+'</small></td><td>'+esc(x.category)+'</td><td>'+num(x.sample_count)+' / '+num(x.minimum_sample)+'</td><td>'+metric(x.primary_metric)+'</td><td>'+chip(x.status)+'</td><td>'+domainLink(x)+'</td></tr>').join('');const s=e.summary||{},overall=e.overall_evidence||{},tp=overall.trading_performance||{};
const abcMap={'eurusd-abc-a':'A','eurusd-abc-b':'B','eurusd-abc-c':'C'};
const baseEngineRows=(e.engines||[]).map(x=>{const ev=x.evidence||{},perf=ev.trading_performance||{},arm=abcMap[x.engine],derived=arm?abc?.trade_comparison?.arms?.[arm]?.derived_net_2pip:null,useDerived=derived&&Number(derived.sample_size||0)>0;const netSample=useDerived?Number(derived.sample_size):Number(perf.settled_with_return||0),tradeN=Number(perf.n_trades||0),hit=useDerived?derived.hit_rate:perf.hit_rate,pf=useDerived?derived.profit_factor:perf.profit_factor,exp=useDerived?derived.mean_net_return_fraction:perf.expectancy_return_fraction,measure=useDerived?'DERIVED · 2 PIPS':perf.status;return '<tr><td><strong>'+esc(x.label||x.engine)+'</strong><small>'+esc(x.engine)+'</small></td><td>'+num(x.experience_count)+'</td><td>'+num(x.settled_count)+'</td><td>'+num(x.pending_count)+'</td><td>'+num(tradeN)+'</td><td>'+num(netSample)+' / '+num(ev.minimum_sample)+'</td><td>'+pct(hit,1)+'</td><td>'+num(pf,2)+'</td><td>'+pct(exp,2)+'</td><td>'+num(perf.average_r_multiple,3)+'</td><td>'+chip(measure)+'</td><td>'+chip(ev.assessment)+'</td></tr>'}).join('');
const xs=x?.sample||{},xa=x?.adaptive_layer||{},xc=xa.champion||{},xch=xa.challenger||{},xp=x?.performance?.champion||{},xn=Number(xp.n||0),xResolved=Number(xs.resolved||0),xCaptures=Number(xs.captures||0),xAssessment=xn>=30?'RUNNING':'INSUFFICIENT_DATA';
const xRow=x?.engine?'<tr class="central-x-row"><td><strong>EURUSD X</strong><small>'+esc(xc.version||'X-001')+(xch.version?' · challenger '+esc(xch.version):'')+'</small></td><td>'+num(xCaptures)+'</td><td>'+num(xResolved)+'</td><td>'+num(Math.max(0,xCaptures-xResolved))+'</td><td>'+num(xn)+'</td><td>'+num(xn)+' / 30</td><td>'+pct(xp.hit_rate,1)+'</td><td>'+num(xp.profit_factor,2)+'</td><td>'+(finite(xp.expectancy_pips)?num(xp.expectancy_pips,2)+' pips':'—')+'</td><td>—</td><td>'+chip('SHADOW · 2 PIPS')+'</td><td>'+chip(xAssessment)+'</td></tr>':'';
const engineRows=baseEngineRows+xRow;
const recent=(e.recent_experiences||[]).slice(0,12).map(x=>'<tr><td>'+esc(x.engine_label||x.engine)+'</td><td><strong>'+esc(x.instrument||'—')+'</strong></td><td>'+esc(x.action)+'</td><td>'+pct(x.confidence_fraction,1)+'</td><td>'+chip(x.status)+'</td><td>'+pct(x.return_fraction,2)+(x.return_basis?' <small>'+esc(x.return_basis)+'</small>':'')+'</td><td>'+esc(x.exit_reason||'—')+'</td></tr>').join('');
root.innerHTML='<article class="panel central-head"><header><div><h2>Experiment Registry</h2><p>One oversight view. GSE v2 and EUR/USD A/B/C and X remain in their own domains; the Registry only shows their canonical status.</p></div>'+chip(experiments.length+' EXPERIMENTS')+'</header><div class="central-table-wrap"><table class="central-table"><thead><tr><th>Experiment</th><th>Type</th><th>Sample</th><th>Result</th><th>Status</th><th>Domain</th></tr></thead><tbody>'+rows+'</tbody></table></div></article>'+
'<article class="panel central-head"><header><div><h2>Experience Store</h2><p>Shared evidence memory from prospective decisions and later outcomes. Read-only — no writeback, tuning or production impact.</p></div>'+chip(s.assessment)+'</header><div class="central-stats"><div><small>Experiences</small><strong>'+num(s.experience_count)+'</strong></div><div><small>Resolved</small><strong>'+num(s.settled_count)+'</strong></div><div><small>Pending</small><strong>'+num(s.pending_count)+'</strong></div><div><small>Engines</small><strong>'+num(s.engine_count)+'</strong></div><div><small>Sources</small><strong>'+num(s.source_count)+'</strong></div><div><small>Formal alpha</small><strong>'+esc(s.formal_alpha_status)+'</strong></div></div>'+
'<h3 class="central-subtitle">Overall evidence</h3><div class="central-stats"><div><small>Sample evidence</small><strong>'+num(overall.sample_size)+' / '+num(overall.minimum_sample)+'</strong></div><div><small>Hit rate</small><strong>'+pct(tp.hit_rate,1)+'</strong></div><div><small>Profit factor</small><strong>'+num(tp.profit_factor,2)+'</strong></div><div><small>Expectancy / trade</small><strong>'+pct(tp.expectancy_return_fraction,2)+'</strong></div><div><small>Max drawdown</small><strong>'+pct(tp.max_drawdown_fraction,1)+'</strong></div><div><small>Sharpe / trade</small><strong>'+num(tp.sharpe_per_trade,2)+'</strong></div></div>'+
'<h3 class="central-subtitle">Evidence by engine</h3><div class="central-table-wrap"><table class="central-table"><thead><tr><th>Engine</th><th>Exp.</th><th>Resolved</th><th>Pending</th><th>Trade n</th><th>Net n / threshold</th><th>Hit rate net</th><th>PF net</th><th>Expectancy net</th><th>Avg. R</th><th>Net measure</th><th>Assessment</th></tr></thead><tbody>'+engineRows+'</tbody></table></div>'+
'<h3 class="central-subtitle">Recent experiences</h3><div class="central-table-wrap"><table class="central-table"><thead><tr><th>Engine</th><th>Instrument</th><th>Action</th><th>Confidence</th><th>Status</th><th>Return</th><th>Exit</th></tr></thead><tbody>'+recent+'</tbody></table></div>'+
'<p class="central-note">For EUR/USD A/B/C, central LAB uses full prospective virtual trades and a 2-pip round-trip cost. EURUSD X is read directly from its own shadow projection and shows metrics for the current Champion using the same 2-pip cost. None of these reads has writeback or authority to change production. Sources: '+esc((e.sources||[]).map(x=>x.label).join(' · '))+' · EURUSD X · generated '+esc(e.generated_at||'—')+'.</p></article>';}catch(e){root.innerHTML='<div class="central-error">Registry / Experience Store are temporarily unavailable.</div>'}}

function shadowStatus(s){
  const key=String(s||'NO DATA').toUpperCase();
  return '<span class="shadow-engine-status '+key.toLowerCase().replaceAll(' ','-')+'">'+esc(key)+'</span>';
}
function shadowWhen(value){
  if(!value)return '—';
  const d=new Date(value);
  if(Number.isNaN(d.valueOf()))return esc(value);
  return d.toLocaleString('en-US',{dateStyle:'short',timeStyle:'short'});
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
  return '<article class="panel central-head hse2-panel"><header><div><h2>Hypothesis Shadow Engine 2.0</h2><p>Seven sources → frozen hypothesis → forward evidence only → one formal fixed-N evaluation → LESSON. Zero production authority.</p></div>'+chip((s.running_shadow||0)+' ACTIVE')+'</header>'+
    '<div class="central-stats"><div><small>Sources</small><strong>'+num(s.sources_available)+' / '+num(s.sources_configured)+'</strong></div><div><small>Experimenty</small><strong>'+num(s.experiments_total)+'</strong></div><div><small>Forward evidence N</small><strong>'+num(s.prospective_evidence_n)+'</strong></div><div><small>Lessons</small><strong>'+num(s.lessons_total)+'</strong></div></div>'+
    '<h3 class="central-subtitle">Active and completed hypotheses</h3><div class="central-table-wrap"><table class="central-table hse2-table"><thead><tr><th>Source</th><th>Hypothesis</th><th>Champion / Challenger</th><th>Forward N</th><th>Status</th><th>Freeze / evidence</th></tr></thead><tbody>'+rows+'</tbody></table></div>'+
    (lessonRows?'<h3 class="central-subtitle">Recent LESSONS</h3><div class="central-table-wrap"><table class="central-table"><thead><tr><th>Source</th><th>Verdict</th><th>Lesson</th></tr></thead><tbody>'+lessonRows+'</tbody></table></div>':'<p class="central-note">No completed LESSONS — HSE2 is still collecting evidence after the T0 boundary.</p>')+
    '<p class="central-note">Result formalny może być tylko SUPPORTED, REJECTED albo INCONCLUSIVE. HSE2 nie może zmieniać konfiguracji źródłowych silników ani produkcji.</p></article>';
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
  return String(m?.details?.kind||'')==='risk_calibration'?'Structural Risk':'Fractal Memory';
}
async function fse(){
  const root=document.querySelector('#central-fse-lab');
  if(!root)return;
  try{
    const [r,hse]=await Promise.all([
      get('/data/investments/fse_public.json'),
      get('/data/investments/hypothesis_shadow_engine_v2_public.json').catch(()=>({}))
    ]);
    const instruments=Array.isArray(r.instruments)?r.instruments:[];
    const measurements=Array.isArray(r.hse_measurements)?r.hse_measurements:[];
    const experiments=Array.isArray(hse.experiments)?hse.experiments.filter(x=>x.source_engine==='FSE'):[];
    const available=Number(r?.source_status?.available||instruments.length);
    const configured=Number(r?.source_status?.configured||instruments.length);
    const overviewRows=instruments.map(x=>{
      const m=x.fractal_memory||{};
      return '<tr>'+
        '<td><strong>'+esc(fseInstrumentLabel(x.instrument))+'</strong><small>'+shadowWhen(x.observed_at)+'</small></td>'+
        '<td>'+fseRegime(x.regime)+'</td>'+
        '<td><b>'+pct(x.risk_score,1)+'</b></td>'+
        '<td><b>'+pct(m.p_up_4h,1)+'</b></td>'+
        '<td>'+num(m.analogues_n)+'</td>'+
        '<td>'+pct(m.top_similarity,1)+'</td>'+
        '<td>'+fseSignedPct(m.median_forward_return,2)+'</td>'+
        '<td>'+fseMae(m.median_adverse_excursion,2)+'</td>'+
      '</tr>';
    }).join('');
    const memoryCards=instruments.map(x=>{
      const m=x.fractal_memory||{},g=x.research_risk_geometry||{};
      return '<div class="fse-memory-card">'+
        '<div class="fse-card-head"><div><strong>'+esc(fseInstrumentLabel(x.instrument))+'</strong><small>'+esc(m.source||'—')+'</small></div>'+fseRegime(x.regime)+'</div>'+
        '<div class="fse-card-prob"><span>P UP</span><b>'+pct(m.p_up_4h,1)+'</b><em>'+esc(m.forecast||'—')+'</em></div>'+
        '<div class="fse-card-grid">'+
          '<span><small>Top similarity</small><b>'+pct(m.top_similarity,1)+'</b></span>'+
          '<span><small>Mean similarity</small><b>'+pct(m.mean_similarity,1)+'</b></span>'+
          '<span><small>Analogues</small><b>'+num(m.analogues_n)+'</b></span>'+
          '<span><small>Median +4h</small><b>'+fseSignedPct(m.median_forward_return,2)+'</b></span>'+
          '<span><small>MAE</small><b>'+fseMae(m.median_adverse_excursion,2)+'</b></span>'+
          '<span><small>Risk</small><b>'+pct(x.risk_score,1)+'</b></span>'+
        '</div>'+
        '<div class="fse-risk-geometry"><span>Research sizing ×'+num(g.position_size_multiplier,2)+'</span><span>SL distance ×'+num(g.stop_distance_multiplier,2)+'</span><b>PRODUCTION OFF</b></div>'+
      '</div>';
    }).join('');
    const validationRows=measurements.map(m=>{
      const instrument=String(m?.details?.instrument||'');
      const counter=Number(m.counter||0),total=Number(m.total||0);
      const edge=counter>0?total/counter:null;
      const brier=edge==null?null:.25-edge;
      const exp=experiments.find(x=>x.metric_name===m.metric_name&&String(x.claim||'').includes(instrument));
      return '<tr>'+
        '<td><strong>'+esc(fseInstrumentLabel(instrument))+'</strong></td>'+
        '<td>'+esc(fseTrackLabel(m))+'</td>'+
        '<td>'+num(counter)+' / '+num(m.target_n)+'</td>'+
        '<td>'+(brier==null?'—':num(brier,4))+'</td>'+
        '<td>'+(edge==null?'—':fseSignedPct(edge,2))+'</td>'+
        '<td>'+shadowStatus(exp?.status||'RUNNING_SHADOW')+'</td>'+
        '<td>'+shadowWhen(exp?.last_evidence_at||exp?.frozen_at)+'</td>'+
      '</tr>';
    }).join('');
    const forwardN=experiments.reduce((sum,x)=>sum+Number(x.prospective_n||0),0);
    const targetN=experiments.reduce((sum,x)=>sum+Number(x.target_n||0),0);
    const terminal=experiments.filter(x=>['SUPPORTED','REJECTED','INCONCLUSIVE'].includes(String(x.status||'').toUpperCase())).length;
    root.innerHTML=
      '<article class="panel central-head fse-panel"><header><div><h2>FSE — Fractal Structure Engine</h2><p>Multiscale market structure: regime detection, structural risk and Fractal Memory. This view is read-only; FSE does not change positions, sizing or stops.</p></div>'+chip(r.mode||'SHADOW_ONLY')+'</header>'+
      '<div class="central-stats fse-summary"><div><small>Sources</small><strong>'+num(available)+' / '+num(configured)+'</strong></div><div><small>Instruments</small><strong>'+num(instruments.length)+'</strong></div><div><small>HSE2 forward N</small><strong>'+num(forwardN)+' / '+num(targetN)+'</strong></div><div><small>Formal results</small><strong>'+num(terminal)+'</strong></div></div>'+
      '<h3 class="central-subtitle">Current market structure</h3>'+
      '<div class="central-table-wrap"><table class="central-table fse-overview-table"><thead><tr><th>Instrument</th><th>Regime</th><th>Risk</th><th>P UP</th><th>Analogues</th><th>Similarity</th><th>Median +4h</th><th>MAE</th></tr></thead><tbody>'+overviewRows+'</tbody></table></div>'+
      '<h3 class="central-subtitle">Fractal Memory</h3><div class="fse-memory-grid">'+memoryCards+'</div>'+
      '<h3 class="central-subtitle">Prospective learning · Brier · HSE2</h3>'+
      '<div class="central-table-wrap"><table class="central-table fse-validation-table"><thead><tr><th>Instrument</th><th>Track</th><th>Forward N</th><th>Brier</th><th>Edge vs 50/50</th><th>HSE2 status</th><th>Freeze / evidence</th></tr></thead><tbody>'+validationRows+'</tbody></table></div>'+
      '<p class="central-note">Brier and edge appear only after prospective snapshots resolve. Historical bootstrap initializes Fractal Memory but does not count as formal HSE2 evidence. Generated '+shadowWhen(r.generated_at)+'.</p></article>';
  }catch(e){
    root.innerHTML='<div class="central-error">FSE is temporarily unavailable.</div>';
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
      '<td><a href="'+esc(x.domain||'#')+'">'+esc(x.domain_label||'Open')+'</a></td>'+
    '</tr>').join('');
    const coverageChip=coverage.complete?'<span class="shadow-coverage ok">COVERAGE OK</span>':'<span class="shadow-coverage error">UNMAPPED SHADOW</span>';
    const unmapped=Array.isArray(coverage.unmapped_shadow_workflows)&&coverage.unmapped_shadow_workflows.length
      ? '<p class="shadow-coverage-warning">Unmapped shadow workflows: '+esc(coverage.unmapped_shadow_workflows.join(' · '))+'</p>' : '';
    root.innerHTML='<article class="panel central-head shadow-observatory"><header><div><h2>Shadow Engines</h2><p>Automatyczny nadzór nad logicznymi silnikami działającymi w trybie shadow. Status pochodzi z ostatniego GitHub Actions run, a liczba observations oraz Champion/Challenger z kanonicznych stanów danego silnika.</p></div>'+coverageChip+'</header>'+
      '<div class="central-stats shadow-summary">'+
        '<div><small>Engines</small><strong>'+num(s.total)+'</strong></div>'+
        '<div><small>RUNNING</small><strong>'+num(s.RUNNING)+'</strong></div>'+
        '<div><small>IDLE</small><strong>'+num(s.IDLE)+'</strong></div>'+
        '<div><small>ERROR</small><strong>'+num(s.ERROR)+'</strong></div>'+
        '<div><small>NO DATA</small><strong>'+num(s["NO DATA"])+'</strong></div>'+
      '</div>'+unmapped+
      '<div class="central-table-wrap"><table class="central-table shadow-engine-table"><thead><tr><th>Shadow engine</th><th>Status</th><th>Latest run</th><th>Observations</th><th>Champion</th><th>Challenger</th><th>Domain</th></tr></thead><tbody>'+rows+'</tbody></table></div>'+
      '<p class="central-note">This section is an observatory only. It has no authority to promote, write back, change parameters or execute trades. Wygenerowano '+shadowWhen(r.generated_at)+'.</p></article>'+
      hsePanel(hse);
  }catch(e){
    root.innerHTML='<div class="central-error">Shadow Engines Observatory is temporarily unavailable.</div>';
  }
}
function start(){strategy();brace();fse();shadows();registry()}document.readyState==='loading'?document.addEventListener('DOMContentLoaded',start,{once:true}):start();})();