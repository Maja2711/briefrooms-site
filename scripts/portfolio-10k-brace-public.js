(() => {
  'use strict';

  const SOURCE = '/data/portfolio10k/public/brace_engine_public.json';
  const lang = window.BR_PORTFOLIO_10K?.lang === 'en' ? 'en' : 'pl';
  const locale = lang === 'en' ? 'en-US' : 'pl-PL';
  const T = lang === 'pl' ? {
    loading:'Ładowanie bieżącego stanu BRACE…', unavailable:'Publiczny stan BRACE jest chwilowo niedostępny.',
    score:'Ocena portfela', confidence:'Pewność', status:'Stan kontrolera', positions:'Pozycje przeanalizowane',
    learning:'Pętla uczenia', samples:'Dojrzałe próbki', next:'Następny przegląd', decisions:'Ocena pozycji',
    currentWeight:'Bieżąca waga', proposedWeight:'Proponowana waga', promotion:'Bramka champion–challenger',
    baseline:'Immutable baseline', shadow:'Wynik BRACE / shadow', updated:'Aktualizacja', canonical:'KANONICZNE ŹRÓDŁO',
    noPositions:'Brak bieżących ocen pozycji.', noLearning:'Stan uczenia nie został jeszcze opublikowany.',
    actions:{HOLD:'TRZYMAJ',WATCH:'OBSERWUJ',REDUCE:'REDUKUJ',EXIT:'WYJDŹ',ADD:'DOKUP',REPLACE:'ZAMIEŃ',NO_ACTION:'BEZ ZMIAN'}
  } : {
    loading:'Loading current BRACE state…', unavailable:'The public BRACE state is temporarily unavailable.',
    score:'Portfolio score', confidence:'Confidence', status:'Controller state', positions:'Positions reviewed',
    learning:'Learning loop', samples:'Mature samples', next:'Next review', decisions:'Position assessment',
    currentWeight:'Current weight', proposedWeight:'Proposed weight', promotion:'Champion–challenger gate',
    baseline:'Immutable baseline', shadow:'BRACE / shadow return', updated:'Updated', canonical:'CANONICAL SOURCE',
    noPositions:'No current position assessments are available.', noLearning:'Learning state has not been published yet.',
    actions:{HOLD:'HOLD',WATCH:'WATCH',REDUCE:'REDUCE',EXIT:'EXIT',ADD:'ADD',REPLACE:'REPLACE',NO_ACTION:'NO CHANGE'}
  };

  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const num = value => Number.isFinite(Number(value)) ? Number(value) : null;
  const pct = (value, digits=1) => {
    const n=num(value); return n===null?'—':(n*100).toLocaleString(locale,{minimumFractionDigits:digits,maximumFractionDigits:digits})+'%';
  };
  const score = value => {
    const n=num(value); return n===null?'—':n.toLocaleString(locale,{minimumFractionDigits:1,maximumFractionDigits:1})+'/100';
  };
  const dateTime = value => {
    if(!value)return '—'; const d=new Date(value);
    return Number.isNaN(d.valueOf())?String(value):d.toLocaleString(locale,{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
  };
  const actionLabel = value => T.actions[String(value||'')] || String(value||'—').replaceAll('_',' ');
  const card = (label,value,sub='') => `<article class="brace-kpi"><small>${esc(label)}</small><strong>${esc(value)}</strong><span>${esc(sub)}</span></article>`;

  function renderSummary(data){
    const box=document.getElementById('brace-summary'); if(!box)return;
    const summary=data.analysis_summary||{};
    const confidenceRaw=num(summary.confidence);
    const confidence=confidenceRaw===null?null:(confidenceRaw<=1?confidenceRaw:confidenceRaw/100);
    const counts=Object.entries(summary.decision_counts||{}).map(([k,v])=>`${actionLabel(k)}: ${v}`).join(' · ');
    box.innerHTML=[
      card(T.score,score(summary.portfolio_score),counts||T.canonical),
      card(T.confidence,pct(confidence),`${summary.positions_reviewed||0} · ${T.positions}`),
      card(T.status,String(data.display_status||data.controller_status||'—').replaceAll('_',' '),String(summary.analysis_liveness_status||data.data_freshness||'—')),
      card(T.updated,dateTime(summary.generated_at||data.generated_at),T.canonical)
    ].join('');
    const meta=document.getElementById('brace-meta');
    if(meta)meta.textContent=`${T.canonical} · ${dateTime(data.generated_at)}`;
  }

  function renderLearning(data){
    const box=document.getElementById('brace-learning'); if(!box)return;
    const loop=data.learning_loop||{};
    if(!Object.keys(loop).length){
      box.innerHTML=`<div class="status-note"><b>${esc(T.learning)}</b><br>${esc(T.noLearning)}<br><small>${esc(dateTime(data.last_incremental_learning))}</small></div>`;
      return;
    }
    box.innerHTML=`<div class="brace-summary">
      ${card(T.learning,String(loop.status||'—').replaceAll('_',' '),loop.active_parameters?'ACTIVE':'WARMUP')}
      ${card(T.samples,`${num(loop.effective_samples)??0}/${num(loop.minimum_effective_samples)??'—'}`,`${loop.outcome_events||0} events`)}
      ${card(T.next,dateTime(loop.next_scheduled_review_at),loop.overdue?'OVERDUE':'CURRENT')}
    </div><div class="status-note">${esc(lang==='pl'?(loop.explanation_pl||''):(loop.explanation_en||''))}</div>`;
  }

  function renderPositions(data){
    const box=document.getElementById('brace-positions'); if(!box)return;
    const activeIds=new Set((data.active_portfolio_ids||[]).map(x=>String(x||'').toLowerCase()));
    const rows=(data.position_recommendations||[]).filter(item=>!activeIds.size||activeIds.has(String(item.instrument||item.instrument_id||'').toLowerCase()));
    if(!rows.length){box.innerHTML=`<div class="brace-empty">${esc(T.noPositions)}</div>`;return;}
    box.hidden=false;
    const heading=box.previousElementSibling; if(heading?.classList?.contains('brace-section-title'))heading.hidden=false;
    box.innerHTML=rows.map(item=>{
      const confidence=num(item.confidence);
      const rationale=lang==='pl'?item.rationale_pl:item.rationale_en;
      const reports=Number(item.material_event_context?.report_count||0);
      return `<article class="brace-position">
        <div class="brace-position-head"><div><div class="symbol">${esc(item.broker_symbol||item.instrument||'—')}</div></div><span class="brace-decision">${esc(actionLabel(item.action))}</span></div>
        <div class="brace-scoreline"><div><small>${esc(T.score)}</small><strong>${esc(score(item.final_score))}</strong></div><div><small>${esc(T.confidence)}</small><strong>${esc(pct(confidence))}</strong></div></div>
        <div class="brace-facts"><div><small>${esc(T.currentWeight)}</small><b>${esc(pct(item.current_weight))}</b></div><div><small>${esc(T.proposedWeight)}</small><b>${esc(pct(item.proposed_weight))}</b></div></div>
        <p>${esc(rationale||'')}</p><small>${reports} material reports</small>
      </article>`;
    }).join('');
    const note=document.getElementById('brace-note');
    if(note)note.textContent=lang==='pl'
      ? 'Bieżący panel BRACE korzysta wyłącznie z publicznego, kanonicznego brace_engine_public.json.'
      : 'The live BRACE panel uses only the canonical public brace_engine_public.json.';
  }

  function renderBacktest(data){
    const box=document.getElementById('brace-backtest'); if(!box)return;
    const progress=data.promotion_progress||{}, shadow=data.shadow||{}, baseline=data.baseline||{};
    box.innerHTML=`<div class="brace-summary">
      ${card(T.promotion,`${progress.passed||0}/${progress.total||0}`,`${num(progress.percentage)??0}%`)}
      ${card(T.baseline,baseline.immutable?'IMMUTABLE':'—',baseline.source||'—')}
      ${card(T.shadow,pct(shadow.shadow_return),`baseline ${pct(shadow.baseline_return)}`)}
    </div>`;
  }

  function render(data){
    renderSummary(data); renderLearning(data); renderPositions(data); renderBacktest(data);
  }

  async function load(){
    const positions=document.getElementById('brace-positions');
    if(positions)positions.innerHTML=`<div class="loading">${esc(T.loading)}</div>`;
    try{
      const response=await fetch(`${SOURCE}?v=${Date.now()}`,{cache:'no-store'});
      if(!response.ok)throw new Error('HTTP '+response.status);
      const data=await response.json();
      if(data?.frontend_contract?.canonical_source!==SOURCE)throw new Error('canonical source contract mismatch');
      render(data);
    }catch(error){
      if(positions)positions.innerHTML=`<div class="error">${esc(T.unavailable)}</div>`;
      const summary=document.getElementById('brace-summary');
      if(summary)summary.innerHTML=`<div class="error">${esc(T.unavailable)}</div>`;
    }
  }

  load();
})();
