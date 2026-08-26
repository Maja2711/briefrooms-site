(() => {
  'use strict';
  const root = document.getElementById('brace-control-root');
  if (!root) return;
  const lang = window.BR_PORTFOLIO_10K?.lang === 'en' ? 'en' : 'pl';
  const locale = lang === 'pl' ? 'pl-PL' : 'en-US';
  const T = lang === 'pl' ? {
    status:'Stan kontroli', champion:'Metoda sterująca', challenger:'Silnik BRACE',
    risk:'Ryzyko', target:'Cel 10% rocznie', remaining:'Bramki nadal monitorowane', candidates:'Najwyżej ocenieni kandydaci',
    pending:'Decyzje BRACE (paper)', recommendations:'Ocena każdej pozycji', history:'Historia kontroli',
    noCandidates:'Lista kandydatów pojawi się po pełnym cyklu analizy.', noDecisions:'Brak zmiany spełniającej wszystkie limity wykonania.',
    noRecommendations:'Brak ocen pozycji.', noHistory:'Brak wcześniejszych zmian kontrolera.', loadError:'Nie udało się pobrać publicznego statusu BRACE.',
    fallback:'Powód trybu bezpiecznego', safe:'Tryb bezpieczny', monitored:'Limity monitorowane',
    confidence:'Pewność', paperOnly:'Wyłącznie oddzielny portfel modelowy. Brak połączenia z rachunkiem brokerskim.',
    learning:'Pętla uczenia', lastLearning:'Ostatni przegląd uczenia', nextAnalysis:'Następny planowy przegląd', material:'Raporty istotne', score:'Ocena', reports:'raportów'
  } : {
    status:'Control state', champion:'Controlling methodology', challenger:'BRACE engine',
    risk:'Risk', target:'10% annual target', remaining:'Gates still monitored', candidates:'Top-ranked candidates',
    pending:'BRACE decisions (paper)', recommendations:'Position-by-position assessment',
    history:'Control history', noCandidates:'Candidates will appear after the full analysis cycle.', noDecisions:'No change currently passes all execution limits.',
    noRecommendations:'No position assessments are available.', noHistory:'No previous controller changes.', loadError:'The public BRACE status could not be loaded.',
    fallback:'Safe-mode reason', safe:'Safe mode', monitored:'Limits monitored', confidence:'Confidence',
    paperOnly:'Separate model portfolio only. No brokerage-account connection.', learning:'Learning loop', lastLearning:'Last learning review', nextAnalysis:'Next scheduled review',
    material:'Material reports', score:'Score', reports:'reports'
  };
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const num = value => Number.isFinite(Number(value)) ? Number(value) : null;
  const pct = value => { const n=num(value); return n===null?'—':`${(n*100).toLocaleString(locale,{maximumFractionDigits:2})}%`; };
  const dateTime = value => { if(!value)return '—'; const d=new Date(value); return Number.isNaN(d.valueOf())?esc(value):d.toLocaleString(locale,{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}); };
  const human = value => String(value || '—').replaceAll('_',' ').toLowerCase();
  const actionLabel = value => (lang==='pl'?{HOLD:'TRZYMAJ',WATCH:'OBSERWUJ',REDUCE:'REDUKUJ',EXIT:'WYJDŹ',ADD:'DOKUP',REPLACE:'ZAMIEŃ',NO_ACTION:'BEZ ZMIAN'}[value]:value)||'—';
  const targetLabel = value => ({
    TARGET_CURRENTLY_JUSTIFIED_WITHIN_MODEL:lang==='pl'?'cel obecnie uzasadniony w modelu':'currently justified in the model',
    TARGET_NOT_CURRENTLY_JUSTIFIED:lang==='pl'?'cel obecnie nieuzasadniony':'not currently justified',
    TARGET_REQUIRES_EXCESSIVE_RISK:lang==='pl'?'cel wymaga nadmiernego ryzyka':'requires excessive risk'
  }[value]||human(value));
  const gateLabel = value => lang!=='pl'?human(value):({out_of_sample_beats_baseline:'wynik poza próbą lepszy od baseline',parameter_neighborhood_stable:'stabilne sąsiedztwo parametrów',expected_shortfall_within_limit:'expected shortfall w limicie',minimum_calendar_days:'minimalny okres kalendarzowy',minimum_decisions:'minimalna liczba decyzji',minimum_completed_trades:'minimalna liczba zakończonych transakcji paper',risk_adjusted_advantage:'przewaga po uwzględnieniu ryzyka',confidence_interval_positive:'dodatni przedział ufności przewagi'}[value]||human(value));
  const tone = status => /FALLBACK|SAFE|DEGRADED|SUSPENDED/.test(status)?'danger':/ACTIVE_PAPER|PROBATIONARY|BRACE_PROBATIONARY/.test(status)?'active':'shadow';
  const metric = (label,value,sub='') => `<article class="control-metric"><small>${esc(label)}</small><strong>${esc(value)}</strong><span>${esc(sub)}</span></article>`;

  function weeklyLearningSchedule(){
    const now=new Date();
    const previous=new Date(Date.UTC(now.getUTCFullYear(),now.getUTCMonth(),now.getUTCDate(),8,40,0));
    const daysSinceSunday=(now.getUTCDay()+7)%7;
    previous.setUTCDate(previous.getUTCDate()-daysSinceSunday);
    if(previous>now) previous.setUTCDate(previous.getUTCDate()-7);
    return new Date(previous.getTime()+7*24*60*60*1000);
  }

  function candidates(items){
    if(!items?.length)return `<p class="brace-empty">${esc(T.noCandidates)}</p>`;
    return `<div class="control-list">${[...items].sort((a,b)=>(num(b.final_score)??-Infinity)-(num(a.final_score)??-Infinity)).slice(0,5).map(item=>`<article><div><b>${esc(item.broker_symbol||item.instrument_id)}</b><span>${esc(item.label||'')}</span></div><strong>${num(item.final_score)===null?'—':num(item.final_score).toFixed(1)+'/100'}</strong></article>`).join('')}</div>`;
  }
  function decisions(items){
    const visible=(items||[]).filter(item=>['PENDING','EXECUTED','ALREADY_APPLIED'].includes(String(item.execution_status||'')));
    if(!visible.length)return `<p class="brace-empty">${esc(T.noDecisions)}</p>`;
    return `<div class="control-list">${visible.slice(0,6).map(item=>{
      const source=item.instrument||item.instrument_id||'';
      const replacement=item.replacement_instrument||item.replacement_instrument_id||'';
      const route=String(item.action||'')==='REPLACE'&&replacement?`${source} → ${replacement}`:source;
      const done=['EXECUTED','ALREADY_APPLIED'].includes(String(item.execution_status||''));
      const state=done?(lang==='pl'?'WYKONANE':'EXECUTED'):(lang==='pl'?'OCZEKUJE':'PENDING');
      const note=String(item.execution_status||'')==='ALREADY_APPLIED'?(lang==='pl'?'Stan portfela już odzwierciedla tę zmianę.':'The current portfolio state already reflects this change.'):'';
      return `<article data-execution-status="${esc(item.execution_status||'')}"><div><b>${esc(actionLabel(item.action))}${route?' · '+esc(route):''}</b><span>${esc(lang==='pl'?item.rationale_pl:item.rationale_en)}${note?' '+esc(note):''}</span></div><strong>${esc(state)} · ${esc(T.confidence)} ${pct(item.confidence)}</strong></article>`;
    }).join('')}</div>`;
  }
  function recommendations(items){
    if(!items?.length)return `<p class="brace-empty">${esc(T.noRecommendations)}</p>`;
    return `<div class="control-recommendations">${items.map(item=>{const ctx=item.material_event_context||{};return `<article class="recommendation action-${esc(String(item.action||'').toLowerCase())}"><div><b>${esc(item.broker_symbol||item.instrument||'—')}</b><span>${esc(actionLabel(item.action))}</span></div><strong>${esc(T.score)}: ${num(item.final_score)===null?'—':num(item.final_score).toFixed(1)}/100</strong><p>${esc(lang==='pl'?item.rationale_pl:item.rationale_en)}</p><small>${esc(T.material)}: ${Number(ctx.report_count||0)} ${esc(T.reports)} · ${esc(T.confidence)} ${pct(item.confidence)}</small></article>`;}).join('')}</div>`;
  }
  function history(items){
    if(!items?.length)return `<p class="brace-empty">${esc(T.noHistory)}</p>`;
    return `<div class="control-history">${[...items].reverse().slice(0,6).map(item=>`<article><time>${dateTime(item.evaluated_at)}</time><b>${esc(item.previous_status)} → ${esc(item.new_status)}</b><span>${esc(item.reason||'')}</span></article>`).join('')}</div>`;
  }
  function render(data){
    const progress=data.promotion_progress||{}, risk=data.risk||{}, target=data.target||{}, remaining=(progress.remaining||[]).slice(0,10);
    const summary=lang==='pl'?data.control_summary_pl:data.control_summary_en;
    const loop=data.learning_loop||{};
    const activeIds=new Set((data.active_portfolio_ids||[]).map(value=>String(value||'').toLowerCase()));
    const positionRecommendations=(data.position_recommendations||[]).filter(item=>!activeIds.size||activeIds.has(String(item.instrument||item.instrument_id||'').toLowerCase()));
    const lastLearning=loop.last_review_at||data.last_research_run||data.last_incremental_learning;
    const nextLearning=loop.next_scheduled_review_at||weeklyLearningSchedule().toISOString();
    document.getElementById('brace-control-updated').textContent=dateTime(data.generated_at);
    root.innerHTML=`<div class="control-status ${tone(data.controller_status)}"><span>${esc(T.status)}</span><strong>${esc(data.display_status||data.controller_status)}</strong><small>${esc(T.paperOnly)}</small></div>
      ${summary?`<p class="control-summary">${esc(summary)}</p>`:''}
      <div class="control-metrics">${metric(T.champion,`${data.champion?.methodology_id||'—'} ${data.champion?.version||''}`,data.champion?.status||'')}${metric(T.challenger,`${data.challenger?.methodology_id||'—'} ${data.challenger?.version||''}`,data.challenger?.status||'')}${metric(T.risk,risk.safe_mode?T.safe:T.monitored,risk.status||'')}${metric(T.target,targetLabel(target.status),`P: ${pct(target.probability_of_reaching_target)}`)}</div>
      <section class="control-learning"><h3>${esc(T.learning)}</h3><div>${metric(T.lastLearning,dateTime(lastLearning),'')}${metric(T.nextAnalysis,dateTime(nextLearning),lang==='pl'?'niedziela · 08:40 UTC':'Sunday · 08:40 UTC')}</div></section>
      ${data.fallback_reason?`<div class="control-alert"><b>${esc(T.fallback)}</b><span>${esc(data.fallback_reason)}</span></div>`:''}
      <section class="control-recommendations-wrap"><h3>${esc(T.recommendations)}</h3>${recommendations(positionRecommendations)}</section>
      <div class="control-columns"><section><h3>${esc(T.remaining)}</h3>${remaining.length?`<ul>${remaining.map(item=>`<li>${esc(gateLabel(item))}</li>`).join('')}</ul>`:'<p>—</p>'}</section><section><h3>${esc(T.candidates)}</h3>${candidates(data.candidates)}</section><section><h3>${esc(T.pending)}</h3>${decisions(data.pending_decisions)}</section></div>
      <section class="control-history-wrap"><h3>${esc(T.history)}</h3>${history(data.promotion_history)}</section><p class="brace-method">${esc(lang==='pl'?data.disclaimer_pl:data.disclaimer_en)}</p>`;
  }
  async function load(){try{const response=await fetch(`/data/portfolio10k/public/brace_engine_public.json?v=${Date.now()}`,{cache:'no-store'});if(!response.ok)throw Error(`HTTP ${response.status}`);render(await response.json())}catch(_){root.innerHTML=`<div class="error">${esc(T.loadError)}</div>`}}
  load();
})();
