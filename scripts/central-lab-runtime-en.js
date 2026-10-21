(()=>{'use strict';
const esc=v=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const finite=v=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v));
const num=(v,d=0)=>finite(v)?new Intl.NumberFormat('en-US',{maximumFractionDigits:d}).format(Number(v)):'—';
const pct=(v,d=1)=>finite(v)?num(Number(v)*100,d)+'%':'—';
const get=async u=>{const r=await fetch(u+'?lab='+Date.now(),{cache:'no-store',signal:AbortSignal.timeout(10000)});if(!r.ok)throw Error(r.status);return r.json()};
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
  const kind=String(m?.details?.kind||'');
  if(kind==='risk_calibration')return 'Structural Risk';
  if(kind==='phase_memory')return 'Phase Fractal Memory';
  if(kind==='adaptive_ensemble')return 'Adaptive Ensemble';
  if(kind==='adaptive_risk')return 'Calibrated large-move probability';
  if(kind==='p_calibration_regime_phase')return 'P Calibration Challenger';
  if(kind==='feature_ablation'||m?.metric_name==='full_brier_improvement_vs_simple')return 'Full features vs shape-only · paired Brier';
  return 'Fractal Memory';
}

function fseSignal(p){
  if(!finite(p)||Number(p)<0||Number(p)>1)return '<span class="fse-signal unavailable">—</span>';
  const n=Number(p),direction=n>=.55?'long':n<=.45?'short':'neutral';
  return '<span class="fse-signal '+direction+'">'+direction.toUpperCase()+'</span>';
}
function fseSearchProbability(memory,p){
  if(memory?.available===false||memory?.status==='ABSTAIN')return null;
  return finite(p)&&Number(p)>=0&&Number(p)<=1?Number(p):null;
}
function fseSearchQuality(memory,p){
  const m=memory||{},value=fseSearchProbability(m,p),explicit=m.available!==undefined||m.status!==undefined;
  const status=value===null?'No reliable analogue':(explicit?'Analogue quality gate passed':'Analogue quality not published');
  const raw=m.raw_windows_n??m.history_candidates??m.raw_analogues_n??m.analogues_n,independent=m.independent_episodes_n??m.independent_analogues_n;
  const reason=typeof m.reason==='string'&&m.reason.trim()?m.reason.replaceAll('_',' '):'';
  return '<div class="fse-search-quality"><strong>'+esc(status)+'</strong>'+(reason?'<small>'+esc(reason)+'</small>':'')+
    '<small>Raw windows: '+num(raw)+' · Independent episodes: '+num(independent)+'</small>'+
    (finite(m.query_distance)||finite(m.quality_threshold)?'<small>Distance / threshold: '+num(m.query_distance,4)+' / '+num(m.quality_threshold,4)+'</small>':'')+'</div>';
}
function fseSearchComparison(rows){
  const cards=rows.filter(x=>x?.feature_ablation).map(x=>{
    const a=x.feature_ablation,full=fseSearchProbability(x.memory,a.full_p_up),simple=fseSearchProbability(x.simple_memory,a.simple_p_up);
    const paired=a.paired_available===true&&full!==null&&simple!==null;
    return '<article class="fse-memory-card"><h4>'+esc(fseInstrumentLabel(x.instrument))+'</h4>'+
      '<p>Full feature set: <b>'+pct(full,1)+'</b></p>'+fseSearchQuality(x.memory,full)+
      '<p>Shape-only distance profile (AB-join): <b>'+pct(simple,1)+'</b></p>'+fseSearchQuality(x.simple_memory,simple)+
      (a.frozen_at?'<small>Forecast frozen: '+esc(shadowWhen(a.frozen_at))+'</small>':'')+'<p class="central-note">'+(paired?'Forecast pair frozen before the outcome':'No complete forecast pair; no comparison outcome')+'</p></article>';
  }).join('');
  return cards?'<section class="fse-search-comparison"><h3 class="central-subtitle">Do extra features improve the forecast?</h3><div class="fse-memory-grid">'+cards+'</div><p class="central-note">The comparison uses the same next four bars only when both forecasts existed before the outcome. This is an AB-join distance profile, not an all-pairs Matrix Profile.</p></section>':'';
}

function fseModelCell(p,score,fallback,kind,memory){
  p=fseSearchProbability(memory,p);
  const n=Number(score?.resolved_n??fallback?.n??0);
  const brier=finite(score?.mean_brier)?Number(score.mean_brier):fallback?.brier;
  const signals=Number(score?.signal_n||0),hits=Number(score?.correct_n||0);
  const hit=signals>0&&finite(score?.accuracy)?pct(score.accuracy,1)+' ('+num(hits)+'/'+num(signals)+')':'—';
  const delta=finite(score?.delta_brier_vs_base)?Number(score.delta_brier_vs_base):fallback?.delta;
  const diff=kind==='challenger'&&finite(delta)?'<small class="fse-comparison-delta '+(delta>=0?'better':'worse')+'">ΔBrier vs Deep v2: '+(delta>0?'+':'')+num(delta,4)+'</small>':'';
  return '<div class="fse-model-cell"><div class="fse-model-forecast">'+fseSignal(p)+'<b>P UP '+pct(p,1)+'</b></div>'+
    fseSearchQuality(memory,p)+'<small>Directional hit rate: '+hit+'</small><small>Brier: '+(finite(brier)?num(brier,4):'—')+' · N='+num(n)+'</small>'+diff+'</div>';
}

function fseBrierText(measurement,edge){
  if(edge===null||!finite(edge))return '—';
  const paired=['p_calibration_regime_phase','feature_ablation','adaptive_ensemble','adaptive_risk'].includes(measurement?.details?.kind);
  return paired?'Δ '+num(edge,4):num(.25-Number(edge),4);
}

function fseHseMatches(experiment,measurement){
  const method=measurement?.details?.methodology_version,instrument=String(measurement?.details?.instrument||'');
  const explicitInstrument=experiment?.instrument!==undefined&&experiment?.instrument!==null;
  const sameInstrument=explicitInstrument?String(experiment.instrument)===instrument:String(experiment?.claim||'').includes(instrument);
  return experiment?.source_engine==='FSE'&&typeof method==='string'&&method.length>0
    &&experiment.methodology_version===method&&experiment.metric_name===measurement.metric_name
    &&instrument.length>0&&sameInstrument;
}
function fseCurrentExperiments(experiments,measurements){
  return (Array.isArray(experiments)?experiments:[]).filter(experiment=>measurements.some(measurement=>fseHseMatches(experiment,measurement)));
}

// Fast presentation selection is independent of immutable forecast evidence.
const FSE_FAST_VERSION='FSE-FAST-2.0';
const FSE_FAST_TFS=['1m','5m','15m','1h'];
const fseLastProjection=new Map();
function fseProjectionValid(payload,now=Date.now()){
  const at=Date.parse(String(payload?.generated_at||''));
  return ['briefrooms-fse-intraday-fast-edge-v1','briefrooms-fse-intraday-fast-v1'].includes(payload?.schema_version)
    &&payload.transport_version===FSE_FAST_VERSION&&payload.mode==='SHADOW_ONLY'&&payload.production_impact===false
    &&Array.isArray(payload.instruments)&&Number.isFinite(at)&&at<=now+60000;
}
function fseFastRowFresh(payload,row,now=Date.now()){
  if(!fseProjectionValid(payload,now)||!row?.phase_map)return false;
  const generated=Date.parse(payload.generated_at),source=Date.parse(String(row.phase_map['1m']?.source_observed_at||row.latest_fast_observed_at||''));
  if(now-generated>12*60000||!Number.isFinite(source)||source>now+60000)return false;
  const status=row.source_status||{},reference=Date.parse(String(status.source_reference_at||''));
  if(!['OPEN','CLOSED'].includes(status.market_state)||!finite(status.source_lag_minutes)||Number(status.source_lag_minutes)>12||Number(status.source_lag_minutes)<0)return false;
  if(!Number.isFinite(reference)||reference>now+60000||now-reference>5*86400000)return false;
  if(status.market_state==='OPEN'&&now-reference>12*60000)return false;
  return Math.max(0,(reference-source-60000)/60000)<=12;
}
async function fseLoadFast(instruments=['EURUSD','BTCUSD','SPX']){
  const edge=await get('https://briefrooms-fse-fast.szczerbinski0577.workers.dev/fse-fast').catch(()=>null);
  const candidates=edge?[edge]:[];
  const complete=instruments.every(id=>{
    const row=edge?.instruments?.find(x=>x?.instrument===id);
    return fseFastRowFresh(edge,row)&&FSE_FAST_TFS.every(tf=>row.phase_map[tf]?.available===true);
  });
  if(!complete){
    const fallback=await get('/data/investments/fse_intraday_public.json').catch(()=>null);
    if(fallback)candidates.push(fallback);
  }
  return candidates;
}
function fseScaleTime(row){
  const ms=Date.parse(String(row?.source_observed_at||row?.observed_at||''));
  return Number.isFinite(ms)&&ms<=Date.now()+60000?ms:-Infinity;
}
function fseMergeProjection(baseRows,candidates,now=Date.now()){
  const bases=new Map(baseRows.map(row=>[String(row.instrument),row]));
  for(const payload of candidates){
    if(!fseProjectionValid(payload,now))continue;
    for(const row of payload.instruments){
      if(['EURUSD','BTCUSD','SPX'].includes(row?.instrument)&&!bases.has(row.instrument))bases.set(row.instrument,{instrument:row.instrument,phase_map:{}});
    }
  }
  return [...bases.values()].map(base=>{
    const map={...(base.phase_map||{})};
    const previous=fseLastProjection.get(base.instrument);
    if(previous)for(const [tf,row] of Object.entries(previous.phase_map||{}))if(fseScaleTime(row)>fseScaleTime(map[tf]))map[tf]=row;
    let selected=null;
    for(const payload of candidates){
      const row=payload?.instruments?.find(x=>x?.instrument===base.instrument);
      if(!fseFastRowFresh(payload,row,now))continue;
      if(!selected||Date.parse(payload.generated_at)>Date.parse(selected.payload.generated_at))selected={payload,row};
      for(const [tf,value] of Object.entries(row.phase_map)){
        if(!['1m','5m','15m','1h','4h','1d','1w'].includes(tf)||value?.available!==true)continue;
        if(fseScaleTime(value)<fseScaleTime(map[tf]))continue;
        map[tf]=FSE_FAST_TFS.includes(tf)?{...value,phase_memory:null,phase_progress:null}:value;
      }
    }
    const meta=selected?{available:true,fresh:true,generated_at:selected.payload.generated_at,latest_fast_observed_at:selected.row.latest_fast_observed_at,
      source_age_minutes:selected.row.source_status.source_age_minutes,cadence_minutes:selected.payload.cadence_minutes||5,market_state:selected.row.source_status.market_state}
      :{available:!!previous?._fast_meta?.available,...(previous?._fast_meta||{}),fresh:false};
    const result={...base,_frozen_phase_map:base.phase_map||{},phase_map:map,cross_scale_alignment:fseAlignment(map),_fast_meta:meta};
    fseLastProjection.set(base.instrument,result);
    return result;
  });
}
function fseAlignment(phaseMap) {
  const SCALE_ORDER=["1m","5m","15m","1h","4h","1d","1w"],mean=xs=>xs.length?xs.reduce((a,b)=>a+b,0)/xs.length:0,clamp=x=>Math.max(0,Math.min(1,x));
  const pairs=[];
  for (let i=0;i<SCALE_ORDER.length-1;i+=1) {
    const fast=SCALE_ORDER[i],slow=SCALE_ORDER[i+1];
    const a=phaseMap?.[fast]||{},b=phaseMap?.[slow]||{};
    if (!a.available||!b.available) continue;
    const da=a.direction,db=b.direction;
    const directionScore=(da==="FLAT"||db==="FLAT")?0.5:(da===db?1:0);
    const shapeDist=Math.sqrt(mean([
      (Number(a.efficiency||0)-Number(b.efficiency||0))**2,
      (Math.tanh(Math.log(Math.max(Number(a.volatility_ratio||1),1e-9)))-Math.tanh(Math.log(Math.max(Number(b.volatility_ratio||1),1e-9))))**2,
      (Number(a.curvature||0)-Number(b.curvature||0))**2,
    ]));
    const shapeScore=clamp(1-shapeDist/1.25);
    const conf=Math.sqrt(clamp(Number(a.confidence||0))*clamp(Number(b.confidence||0)));
    const pa=a.phase_progress,pb=b.phase_progress;
    const lead=(pa==null||pb==null)?null:clamp(0.5+(Number(pa)-Number(pb))/0.8);
    const score=0.45*directionScore+0.40*shapeScore+0.15*conf;
    pairs.push({fast,slow,alignment:score,fast_lead:lead});
  }
  const alignment=pairs.length?mean(pairs.map((x)=>x.alignment)):0;
  const leads=pairs.filter((x)=>x.fast_lead!=null).map((x)=>Number(x.fast_lead));
  const ranked=Object.entries(phaseMap||{}).filter(([,row])=>row?.available===true).map(([tf,row])=>{
    const mem=row?.phase_memory||{};
    return [Number(row?.confidence||0)*Number(mem?.top_similarity||0),tf];
  });
  let dominantScale=null,best=-Infinity;
  for (const [quality,tf] of ranked) {
    if (quality>best||(quality===best&&String(tf)>String(dominantScale||""))) {best=quality;dominantScale=tf;}
  }
  return {
    alignment_score:alignment,
    cascade_state:alignment>=0.70?"COHERENT":(alignment>=0.50?"MIXED":"FRACTURED"),
    fast_scale_lead_score:leads.length?mean(leads):null,
    dominant_scale:dominantScale,
    pairs,
  };
}


const FSE_TREND_TFS=['5m','15m','1h','4h','1d','1w'];
const FSE_TREND_WEIGHTS={ '1m':1,'5m':1.25,'15m':1.5,'1h':2,'4h':2.5,'1d':3,'1w':3.5 };
function fseDirectionValue(value){
  const key=String(value||'').toUpperCase();
  return key==='UP'?1:(key==='DOWN'?-1:0);
}
function fseTrendScore(phaseMap,tfs=FSE_TREND_TFS){
  let total=0,weight=0;
  tfs.forEach(tf=>{
    const row=phaseMap?.[tf];
    if(row?.available!==true||!['UP','DOWN','FLAT'].includes(row.direction))return;
    const w=Number(FSE_TREND_WEIGHTS[tf]||1);
    total+=w*fseDirectionValue(row.direction);
    weight+=w;
  });
  return weight?total/weight:0;
}
function fseTrendLabel(score){
  if(score>=.45)return {key:'bullish',label:'BULLISH'};
  if(score>=.20)return {key:'up',label:'LEAN UP'};
  if(score>0)return {key:'mixed-up',label:'MIXED · slight UP'};
  if(score<=-.45)return {key:'bearish',label:'BEARISH'};
  if(score<=-.20)return {key:'down',label:'LEAN DOWN'};
  if(score<0)return {key:'mixed-down',label:'MIXED · slight DOWN'};
  return {key:'mixed',label:'MIXED'};
}
function fseBiasLabel(score){
  if(score>=.20)return 'UP';
  if(score<=-.20)return 'DOWN';
  return 'MIXED';
}
function fseBarClock(value){
  if(!value)return '—';
  const d=new Date(value);
  if(Number.isNaN(d.valueOf()))return '—';
  const age=Math.max(0,Date.now()-d.valueOf());
  if(age>24*60*60*1000)return d.toLocaleString('en-US',{dateStyle:'short',timeStyle:'short'});
  return d.toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit'});
}
function fseDirectionChip(tf,row,dominant){
  const dir=row?.available===true?String(row.direction||'FLAT').toUpperCase():'—';
  const key=['UP','DOWN','FLAT'].includes(dir)?dir.toLowerCase():'flat';
  const arrow=dir==='UP'?'↑':(dir==='DOWN'?'↓':'→');
  return '<span class="fse-tf-direction '+key+(String(dominant||'').toLowerCase()===tf?' dominant':'')+'"><small>'+esc(tf)+'</small><b>'+arrow+' '+esc(dir)+'</b><em>'+esc(fseBarClock(row?.observed_at))+'</em></span>';
}
function fseFractalTrendCard(x){
  const map=x.phase_map||{},a=x.cross_scale_alignment||{},fm=x._fast_meta||{};
  const score=fseTrendScore(map);
  const trend=fseTrendLabel(score);
  const lower=fseBiasLabel(fseTrendScore(map,['5m','15m']));
  const higher=fseBiasLabel(fseTrendScore(map,['1h','4h','1d','1w']));
  const ribbon=FSE_TREND_TFS.map(tf=>fseDirectionChip(tf,map[tf],a.dominant_scale)).join('');
  const freshness=fm.available
    ? '<span class="fse-fast-state '+(fm.fresh?'fresh':'stale')+'">'+(fm.fresh?('FAST '+esc(String(fm.cadence_minutes||5))+'M'):'STALE')+'</span>'
    : '<span class="fse-fast-state stale">NO FAST</span>';
  const sourceAge=finite(fm.source_age_minutes)?(num(fm.source_age_minutes,0)+' min'):'—';
  const session=fm.market_state==='CLOSED'?' · session closed':'';
  return '<section class="fse-trend-card '+trend.key+'"><div class="fse-trend-head"><div><strong>'+esc(fseInstrumentLabel(x.instrument))+'</strong><small>Fractal Trend · FSE-PHASE</small></div><div class="fse-trend-badges">'+freshness+'<span class="fse-trend-badge '+trend.key+'">'+esc(trend.label)+'</span></div></div>'+
    '<div class="fse-trend-meta"><span>Lower TF <b>'+esc(lower)+'</b></span><span>Higher TF <b>'+esc(higher)+'</b></span><span>Alignment <b>'+pct(a.alignment_score,1)+'</b></span><span>Cascade <b>'+esc(a.cascade_state||'—')+'</b></span></div>'+
    '<div class="fse-fast-meta"><span>Fast run <b>'+esc(fm.generated_at?shadowWhen(fm.generated_at):'—')+'</b></span><span>Latest bar <b>'+esc(fm.latest_fast_observed_at?shadowWhen(fm.latest_fast_observed_at):'—')+'</b></span><span>Source age <b>'+esc(sourceAge+session)+'</b></span></div>'+
    '<div class="fse-tf-ribbon">'+ribbon+'</div></section>';
}
function fseDiscoveryEnglishCopy(f){
  // Backward-compatible with older ASD public artifacts that contain Polish
  // fields only. Keep the immutable source record unchanged.
  const known={ 
    'Wielopulsowy':'Multi-pulse', 'Falowy':'Wave-like', 'Ciągły':'Continuous',
    'wznoszący':'rising', 'opadający':'falling', 'boczny':'sideways',
    'zwarty':'compact', 'poszarpany':'jagged', 'zmienny':'variable'
  };
  const original=String(f?.name||'');
  const pieces=original.match(/^(Wielopulsowy|Falowy|Ciągły) ślad (wznoszący|opadający|boczny) · (zwarty|poszarpany|zmienny)$/);
  const fallbackName=pieces
    ? known[pieces[1]]+' '+known[pieces[2]]+' price trace · '+known[pieces[3]]
    : 'Autonomously discovered price structure '+String(f?.family_id||'');
  const n=Number(f?.historical_sample_n||0);
  const p=Number(f?.historical_p_up);
  const hasP=f?.historical_p_up!==null&&f?.historical_p_up!==undefined&&f?.historical_p_up!==''&&Number.isFinite(p)&&p>=0&&p<=1;
  const pText=hasP?(p*100).toFixed(1)+'%':'not available';
  const fallbackDescription='In '+n+' historical analogues, the exploratory, smoothed frequency of an upward move over the next four hourly market bars was '+pText+'. Historical similarity is not proof of prospective predictive performance.';
  const fallbackApplications=[];
  return {
    name:typeof f?.name_en==='string'&&f.name_en.trim()?f.name_en:fallbackName,
    description:typeof f?.description_en==='string'&&f.description_en.trim()?f.description_en:fallbackDescription,
    applications:Array.isArray(f?.applications_en)?f.applications_en:fallbackApplications
  };
}

function fseDiscoveryShape(f){
  const pts=Array.isArray(f?.shape_points)?f.shape_points.map(Number):[];
  if(pts.length<3||pts.length>64||pts.some(v=>!Number.isFinite(v)))return '';
  const low=Math.min(...pts), high=Math.max(...pts), span=Math.max(high-low,0.001);
  const d=pts.map((v,i)=>(i===0?'M':'L')+(8+248*i/(pts.length-1)).toFixed(1)+' '+(58-47*(v-low)/span).toFixed(1)).join(' ');
  return '<div class="fse-discovery-shape"><svg viewBox="0 0 264 66" role="img" aria-label="Normalized discovered price-shape prototype" preserveAspectRatio="none"><path d="'+d+'" fill="none" stroke="#8cd7ff" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg><small>Discovered medoid · normalized path, not a price forecast</small></div>';
}

function fseDiscoveryPanel(d){
  if(!d||!['FSE-ASD-1.0','FSE-ASD-2.0'].includes(d.schema_version))return '<section class="fse-discovery"><h3 class="central-subtitle">Autonomous Structure Discovery</h3><p class="central-note">No autonomous structure registry yet. Waiting for the first validated market-data cycle.</p></section>';
  const sum=d.summary||{},current=Array.isArray(d.current_matches)?d.current_matches:[];
  const allFamilies=[...new Map((Array.isArray(d.families)?d.families:[]).filter(f=>f&&typeof f.family_id==='string').map(f=>[f.family_id,f])).values()];
  const method=String(d.methodology_version||(d.schema_version==='FSE-ASD-2.0'?'FSE-ASD-2.0':''));
  const legacyFamilies=[...new Map([...(Array.isArray(d.legacy_families)?d.legacy_families:[]),...allFamilies.filter(f=>method&&f.methodology_version!==method)].filter(f=>f&&typeof f.family_id==='string').map(f=>[f.family_id,f])).values()];
  const legacyIds=new Set(legacyFamilies.map(f=>f.family_id));
  const families=allFamilies.filter(f=>!legacyIds.has(f.family_id)),legacy=d.legacy_evidence||{};
  const freshAt=Date.parse(String(d.generated_at||'')),stale=!Number.isFinite(freshAt)||Date.now()-freshAt>180*60000;
  const stats='<div class="fse-discovery-stats"><span>Structure families <b>'+num(families.length)+'</b></span><span>Frozen observations <b>'+num(sum.frozen_snapshots||0)+'</b></span><span>Forward resolutions <b>'+num(sum.prospective_resolutions||0)+'</b></span><span>Production <b>OFF</b></span></div>';
  // Noisy unknown/novel outcomes remain in the immutable Shadow evidence only.
  // The LAB highlights verified associations with still-active discovered families.
  const activeIds=new Set(families.map(f=>f.family_id).filter(Boolean));
  const matches=current
    .filter(x=>!stale&&x&&x.status==='FROZEN_MATCH'&&x.p_up!==null&&typeof x.family_id==='string'&&activeIds.has(x.family_id))
    .map(x=>'<span class="fse-discovery-match"><b>'+esc(fseInstrumentLabel(x.instrument))+'</b> '+esc(x.family_id)+'</span>')
    .join('');
  const abstentions=current.filter(x=>!stale&&x&&['FROZEN_NOVEL_UNMATCHED','FROZEN_AMBIGUOUS','FROZEN_LOW_QUALITY'].includes(x.status)).map(x=>
    '<p class="central-note fse-search-abstention"><b>'+esc(fseInstrumentLabel(x.instrument))+': No reliable analogue.</b> '+esc(String(x.match_quality?.reason||x.status).replaceAll('_',' '))+'</p>').join('');
  const renderCard=(f,archived=false)=>{
    const english=fseDiscoveryEnglishCopy(f);
    const apps=f.application_status==='RESEARCH_CANDIDATE'&&!archived?english.applications:[];
    const applications=apps.map(s=>'<li>'+esc(s)+'</li>').join('');
    const use=apps.length?'<details><summary>Potential uses (subject to validation)</summary><ul>'+applications+'</ul></details>':
      '<p class="fse-discovery-no-use"><strong>Application undetermined.</strong> FSE is collecting independent outcomes; historical analogues do not prove utility.</p>';
    return '<article class="fse-discovery-card"><div class="fse-discovery-cardtop"><span>'+esc(f.family_id)+'</span><small>'+esc(archived?'ARCHIVE · excluded from current results':f.evidence_status)+'</small></div>'+
      '<h4>'+esc(english.name)+'</h4><p class="fse-discovery-meta">'+esc(fseInstrumentLabel(f.instrument))+' · '+num(f.window)+' × 1h · Raw historical analogues: '+num(f.historical_sample_n)+' · Independent historical episodes: '+num(f.historical_independent_n)+'</p>'+
      '<p>'+esc(english.description)+'</p>'+fseDiscoveryShape(f)+'<div class="fse-discovery-metrics"><span>Prospective forward <b>N='+num(f.prospective_n)+'</b></span><span>Independent prospective outcomes <b>N='+num(f.independent_n)+' / '+num(f.required_independent_n??40)+'</b></span><span>Holdout <b>'+num(f.holdout_n)+'</b></span>'+
      '<span>Prospective Brier <b>'+(finite(f.mean_brier)?num(f.mean_brier,4):'—')+'</b></span>'+
      '<span>Brier improvement <b>'+(finite(f.incremental_brier)?num(f.incremental_brier,4):'—')+'</b></span></div>'+
      use+'</article>';
  };
  const cards=families.map(f=>renderCard(f)).join('');
  const archive=legacyFamilies.map(f=>renderCard(f,true)).join('');
  const archivePanel=(archive||Number(legacy.families||0)>0)?'<details class="fse-discovery-archive"><summary>Earlier methodology archive ('+num(legacyFamilies.length||legacy.families||0)+')</summary><p class="central-note">History retained; excluded from current metrics and current family matches · Snapshots: '+num(legacy.snapshots)+' · Outcomes: '+num(legacy.resolutions)+'</p><div class="fse-discovery-grid">'+archive+'</div></details>':'';
  return '<section class="fse-discovery"><div class="fse-discovery-head"><h3 class="central-subtitle">Autonomous Structure Discovery</h3><p>Unsupervised families discovered from market prices; names and research hypotheses are generated automatically. These are not trading orders.</p></div>'+
    (stale?'<p class="central-note"><strong>STALE:</strong> '+esc(String(d.generated_at||'missing'))+'</p>':'')+stats+
    abstentions+(matches?'<div class="fse-discovery-matches"><strong>Current match:</strong>'+matches+'</div>':'')+
    (cards?'<details class="fse-discovery-catalogue"><summary>Show structure catalogue ('+num(families.length)+')</summary><div class="fse-discovery-grid">'+cards+'</div></details>':'<p class="central-note">No autonomous structure registry yet. Waiting for the first validated market-data cycle.</p>')+
    archivePanel+'<p class="central-note">Method: data-discovered clusters, not predefined chart formations. No template applications. After at least 40 independent prospective episodes and a separate holdout: research review candidate or removal from the active catalogue (audit history retained). Shadow only; no production authority.</p></section>';
}

async function fse(){
  const root=document.querySelector('#central-fse-lab');
  if(!root)return;
  try{
    const [r,hse,v2,discovery,fastCandidates]=await Promise.all([
      get('/data/investments/fse_public.json'),
      get('/data/investments/hypothesis_shadow_engine_v2_public.json').catch(()=>({})),
      get('/data/investments/fse_v2_public.json').catch(()=>({})),
      get('/data/investments/fse_discovery_public.json').catch(()=>({})),
      fseLoadFast()
    ]);
    const instruments=Array.isArray(r.instruments)?r.instruments:[];
    const v2Instruments=fseMergeProjection(Array.isArray(v2.instruments)?v2.instruments:[],fastCandidates);
    const measurements=Array.isArray(r.hse_measurements)?r.hse_measurements:[];
    const phaseMeasurements=Array.isArray(v2.hse_measurements)?v2.hse_measurements:[];
    const allMeasurements=[...measurements,...phaseMeasurements];
    const experiments=fseCurrentExperiments(hse.experiments,allMeasurements);
    const available=Number(r?.source_status?.available||instruments.length);
    const configured=Number(r?.source_status?.configured||instruments.length);
    const fractalTrendCards=v2Instruments.map(fseFractalTrendCard).join('');
    const overviewRows=instruments.map(x=>{const m=x.fractal_memory||{};return '<tr><td><strong>'+esc(fseInstrumentLabel(x.instrument))+'</strong><small>'+shadowWhen(x.observed_at)+'</small></td><td>'+fseRegime(x.regime)+'</td><td><b>'+pct(x.risk_score,1)+'</b></td><td><b>'+pct(fseSearchProbability(m,m.p_up_4h),1)+'</b></td><td>'+num(m.analogues_n)+'</td><td>'+pct(m.top_similarity,1)+'</td><td>'+fseSignedPct(m.median_forward_return,2)+'</td><td>'+fseMae(m.median_adverse_excursion,2)+'</td></tr>';}).join('');
    const memoryCards=instruments.map(x=>{const m=x.fractal_memory||{},g=x.research_risk_geometry||{};return '<div class="fse-memory-card"><div class="fse-card-head"><div><strong>'+esc(fseInstrumentLabel(x.instrument))+'</strong><small>'+esc(m.source||'—')+'</small></div>'+fseRegime(x.regime)+'</div><div class="fse-card-prob"><span>P UP</span><b>'+pct(fseSearchProbability(m,m.p_up_4h),1)+'</b><em>'+esc(m.forecast||'—')+'</em></div>'+fseSearchQuality(m,m.p_up_4h)+'<div class="fse-card-grid"><span><small>Top similarity</small><b>'+pct(m.top_similarity,1)+'</b></span><span><small>Mean similarity</small><b>'+pct(m.mean_similarity,1)+'</b></span><span><small>Analogues</small><b>'+num(m.analogues_n)+'</b></span><span><small>Median +4h</small><b>'+fseSignedPct(m.median_forward_return,2)+'</b></span><span><small>MAE</small><b>'+fseMae(m.median_adverse_excursion,2)+'</b></span><span><small>Risk</small><b>'+pct(x.risk_score,1)+'</b></span></div><div class="fse-risk-geometry"><span>Research sizing ×'+num(g.position_size_multiplier,2)+'</span><span>SL distance ×'+num(g.stop_distance_multiplier,2)+'</span><b>PRODUCTION OFF</b></div></div>';}).join('');
    const v1Performance=new Map((Array.isArray(r.directional_performance)?r.directional_performance:[]).map(x=>[String(x.instrument||''),x]));
    const v2Performance=new Map((Array.isArray(v2.directional_performance)?v2.directional_performance:[]).map(x=>[String(x.instrument||''),x]));
    const v2Models=new Map(v2Instruments.map(x=>[String(x.instrument||''),x]));
    const v1Fallback=new Map(measurements.filter(x=>x?.details?.kind==='directional_memory').map(m=>{
      const n=Number(m.counter||0);return [String(m.details.instrument||''),{n,brier:n>0?.25-Number(m.total||0)/n:null}];
    }));
    const deepFallback=new Map((Array.isArray(v2.measurements)?v2.measurements:[]).map(m=>[String(m.instrument||''),{n:Number(m.counter||0),brier:m.mean_brier}]));
    const challengerFallback=new Map(phaseMeasurements.filter(x=>x?.details?.kind==='p_calibration_regime_phase').map(m=>{
      const n=Number(m.counter||0);return [String(m.details.instrument||''),{n,delta:n>0?Number(m.total||0)/n:null}];
    }));
    const comparisonRows=instruments.map(x=>{
      const id=String(x.instrument||''),deep=v2Models.get(id),cal=deep?.p_calibration_challenger||{},score=v2Performance.get(id)||{};
      const calP=cal.active===true?cal.p_challenger:null;
      return '<tr><td data-label="Instrument"><strong>'+esc(fseInstrumentLabel(id))+'</strong></td>'+
        '<td data-label="Fractal Memory v1">'+fseModelCell(x.fractal_memory?.p_up_4h,v1Performance.get(id),v1Fallback.get(id),'legacy',x.fractal_memory)+'</td>'+
        '<td data-label="Deep Fractal Memory v2">'+fseModelCell(deep?.memory?.p_up,score.deep,deepFallback.get(id),'deep',deep?.memory)+'</td>'+
        '<td data-label="v2 Calibration Challenger">'+fseModelCell(calP,score.challenger,challengerFallback.get(id),'challenger',cal)+'</td></tr>';
    }).join('');
    const phaseSummary=v2Instruments.map(x=>{const a=x.cross_scale_alignment||{},c=x.p_calibration_challenger||{},p1=x?._frozen_phase_map?.['1h']||{},pm=p1.phase_memory||{};return '<tr><td><strong>'+esc(fseInstrumentLabel(x.instrument))+'</strong></td><td><b>'+esc(a.cascade_state||'—')+'</b></td><td>'+pct(a.alignment_score,1)+'</td><td>'+esc(a.dominant_scale||'—')+'</td><td>'+pct(c.p_base,1)+'</td><td>'+pct(fseSearchProbability(pm,pm.p_up_4h),1)+'</td><td><b>'+pct(c.p_challenger,1)+'</b></td><td>'+(finite(c.shift_pp)?num(c.shift_pp,1)+' pp':'—')+'</td><td>'+esc(x.regime||'—')+'</td></tr>';}).join('');
    const phaseRows=v2Instruments.flatMap(x=>{const formation=x.intrabar_formation||{};return ['5m','15m','1h','4h','1d','1w'].map(tf=>{const p=x?.phase_map?.[tf]||{},pm=p.phase_memory||{},ib=formation[tf]||{};return '<tr><td><strong>'+esc(fseInstrumentLabel(x.instrument))+'</strong></td><td><b>'+esc(tf)+'</b></td><td>'+esc(p.structure_id||'—')+'</td><td>'+esc(p.phase||'—')+'</td><td><b>'+esc((p.available===true?String(p.direction||'FLAT').toUpperCase():'—'))+'</b></td><td>'+pct(pm.phase_progress,1)+'</td><td>'+pct(pm.top_similarity,1)+'</td><td>'+pct(fseSearchProbability(pm,pm.p_up_remaining),1)+'</td><td>'+(ib.available?pct(ib.formation_progress,1):'—')+'</td><td>'+num(pm.analogues_n)+'</td></tr>';});}).join('');
    const validationRows=allMeasurements.map(m=>{const instrument=String(m?.details?.instrument||''),kind=String(m?.details?.kind||'');const counter=Number(m.counter||0),total=Number(m.total||0),edge=counter>0?total/counter:null;const exp=experiments.find(x=>fseHseMatches(x,m));const brierText=fseBrierText(m,edge);return '<tr><td><strong>'+esc(fseInstrumentLabel(instrument))+'</strong></td><td>'+esc(fseTrackLabel(m))+'</td><td>'+num(counter)+' / '+num(m.target_n)+'</td><td>'+brierText+'</td><td>'+(edge==null?'—':fseSignedPct(edge,2))+'</td><td>'+shadowStatus(exp?.status||'RUNNING_SHADOW')+'</td><td>'+shadowWhen(exp?.last_evidence_at||exp?.frozen_at)+'</td></tr>';}).join('');
    const forwardN=experiments.reduce((sum,x)=>sum+Number(x.prospective_n||0),0);
    const targetN=experiments.reduce((sum,x)=>sum+Number(x.target_n||0),0);
    root.innerHTML='<article class="panel central-head fse-panel"><header><div><h2>FSE — Fractal Structure Engine</h2><p>Multiscale market structure: regime, Fractal Memory and FSE-PHASE. The Phase Engine tracks how a structure forms across time scales without production trading authority.</p></div>'+chip(v2?.methodology_version||r.mode||'SHADOW_ONLY')+'</header>'+
      '<div class="central-stats fse-summary"><div><small>Sources</small><strong>'+num(available)+' / '+num(configured)+'</strong></div><div><small>Instruments</small><strong>'+num(instruments.length)+'</strong></div><div><small>HSE2 forward N</small><strong>'+num(forwardN)+' / '+num(targetN)+'</strong></div><div><small>Promotion gate</small><strong>'+esc(v2?.promotion_gate?.status||'NOT ELIGIBLE')+'</strong></div></div>'+
      fseDiscoveryPanel(discovery)+fseSearchComparison(v2Instruments)+'<h3 class="central-subtitle fse-trend-title">Fractal Trend — direction from fractal structures</h3>'+(fractalTrendCards?'<div class="fse-trend-grid">'+fractalTrendCards+'</div>':'<p class="central-note">Fractal Trend is waiting for FSE-PHASE data.</p>')+
      '<h3 class="central-subtitle">Current market structure</h3><div class="central-table-wrap"><table class="central-table fse-overview-table"><thead><tr><th>Instrument</th><th>Regime</th><th>Risk</th><th>P UP</th><th>Analogues</th><th>Similarity</th><th>Median +4h</th><th>MAE</th></tr></thead><tbody>'+overviewRows+'</tbody></table></div>'+
      '<h3 class="central-subtitle">Fractal Memory</h3><div class="fse-memory-grid">'+memoryCards+'</div>'+
      '<h3 class="central-subtitle fse-comparison-title">Fractal Memory — direction and performance</h3>'+
      '<div class="central-table-wrap"><table class="central-table fse-comparison-table"><thead><tr><th>Instrument</th><th>Fractal Memory v1</th><th>Deep Fractal Memory v2</th><th>v2 Calibration Challenger</th></tr></thead><tbody>'+comparisonRows+'</tbody></table></div>'+
      '<p class="central-note fse-comparison-note">Horizon: next four 1h market bars. Hit rate covers only settled LONG/SHORT calls (not NEUTRAL). Brier includes neutral forecasts. N counts settlements. SHADOW results do not imply trade success.</p>'+
      '<h3 class="central-subtitle">FSE-PHASE · Cross-Scale Alignment + P Challenger</h3>'+(v2?.methodology_version?'<div class="central-table-wrap"><table class="central-table fse-phase-summary"><thead><tr><th>Instrument</th><th>Cascade</th><th>Alignment</th><th>Dominant TF</th><th>P base</th><th>P phase +4 × 1h (frozen)</th><th>P challenger</th><th>Shift</th><th>Regime</th></tr></thead><tbody>'+phaseSummary+'</tbody></table></div>':'<p class="central-note">FSE-PHASE is waiting for its first post-deployment cycle.</p>')+
      (phaseRows?'<h3 class="central-subtitle">Fractal Phase Map · Intrabar Formation</h3><div class="central-table-wrap"><table class="central-table fse-phase-table"><thead><tr><th>Instrument</th><th>TF</th><th>Structure</th><th>Phase</th><th>Direction</th><th>Progress</th><th>Similarity</th><th>P UP remaining</th><th>Intrabar</th><th>Analogues</th></tr></thead><tbody>'+phaseRows+'</tbody></table></div>':'')+
      '<h3 class="central-subtitle">Prospective learning · Brier · HSE2</h3><div class="central-table-wrap"><table class="central-table fse-validation-table"><thead><tr><th>Instrument</th><th>Track</th><th>Forward N</th><th>Brier / Δ</th><th>Edge</th><th>HSE2 status</th><th>Freeze / evidence</th></tr></thead><tbody>'+validationRows+'</tbody></table></div>'+
      '<p class="central-note">FSE-PHASE receives no historical credit. Phase Memory and the P Calibration Challenger start formal N only from snapshots frozen under FSE-PHASE-1.0. The challenger is capped at ±6 pp and cannot write to production. Generated '+shadowWhen(v2.generated_at||r.generated_at)+'.</p></article>';
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
function start(){
  strategy();brace();fse();shadows();registry();
  window.setInterval(()=>{if(document.querySelector('#tab-fse.active'))fse()},60000);
}
document.readyState==='loading'?document.addEventListener('DOMContentLoaded',start,{once:true}):start();})();