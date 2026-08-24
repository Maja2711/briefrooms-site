(()=>{
  'use strict';
  const root=document.getElementById('gse-lab-root');
  if(!root)return;
  const lang=(document.documentElement.lang||'pl').toLowerCase().startsWith('en')?'en':'pl';
  const t={
    pl:{
      intro:'GSE bada, jak zweryfikowane zdarzenia geopolityczne przekładają się na rynki. Łączy podobieństwo wydarzeń z reżimem rynku, a wyniki sprawdza historycznie i live.',
      clusters:'zweryfikowane klastry',responses:'reakcje historyczne',walk:'walk-forward',live:'pary live',
      best:'Najlepszy horyzont historyczny',bestBody:'Najniższy Brier w modelu regime-aware',
      horizons:'Wyniki według horyzontu',horizonsSub:'Porównanie modelu regime-aware z prostą bazą analogii historycznych.',
      horizon:'Horyzont',sample:'N',regime:'Brier GSE',base:'Brier baza',improvement:'Poprawa',hit:'Hit-rate',
      scenarios:'Rodziny scenariuszy',scenariosSub:'Które typy zdarzeń były badane i jak zachowuje się na nich model.',
      history:'Badane epizody historyczne',historySub:'Zweryfikowane kotwice z pierwotnych źródeł. Powiązane komunikaty jednego kryzysu mogą dzielić jeden klaster.',
      source:'Źródło',more:'Pokaż więcej historii',less:'Pokaż mniej',
      challenger:'Challenger i kalibracja',prospective:'Walidacja live',active:'Aktywna polityka pozostaje bez zmian',
      status:'Status',paired:'Pary',v1:'Brier v1',v2:'Brier v2',delta:'Δ Brier',bias:'Bias kalibracji',
      readiness:'Gotowość do promocji',learning:'Historia uczenia',learningSub:'Kolejne cykle zapisywane w Learning Ledger.',
      candidates:'nowi kandydaci',verifications:'nowe weryfikacje',method:'Jak GSE się uczy',
      methodSteps:['Discovery zdarzeń','Clustering kryzysów','Regime similarity','Walk-forward / holdout','Prospective v1 vs v2'],
      unavailable:'Brak aktualnych danych GSE Lab.',updated:'Aktualizacja',automatic:'Brak automatycznej promocji · brak wpływu na decyzje'
    },
    en:{
      intro:'GSE studies how verified geopolitical events transmit into markets. It combines event similarity with market regime similarity, then validates results historically and prospectively.',
      clusters:'verified clusters',responses:'historical responses',walk:'walk-forward',live:'live pairs',
      best:'Best historical horizon',bestBody:'Lowest Brier in the regime-aware model',
      horizons:'Results by horizon',horizonsSub:'Regime-aware model versus the simple historical-analogue baseline.',
      horizon:'Horizon',sample:'N',regime:'GSE Brier',base:'Baseline Brier',improvement:'Improvement',hit:'Hit rate',
      scenarios:'Scenario families',scenariosSub:'Which event families were tested and how the model performs on them.',
      history:'Historical episodes tested',historySub:'Verified anchors from primary sources. Related releases from one crisis may share a single cluster.',
      source:'Source',more:'Show more history',less:'Show less',
      challenger:'Challenger and calibration',prospective:'Prospective validation',active:'The active policy remains unchanged',
      status:'Status',paired:'Pairs',v1:'Brier v1',v2:'Brier v2',delta:'Δ Brier',bias:'Calibration bias',
      readiness:'Promotion readiness',learning:'Learning history',learningSub:'Successive cycles recorded in the Learning Ledger.',
      candidates:'new candidates',verifications:'new verifications',method:'How GSE learns',
      methodSteps:['Event discovery','Crisis clustering','Regime similarity','Walk-forward / holdout','Prospective v1 vs v2'],
      unavailable:'Current GSE Lab data unavailable.',updated:'Updated',automatic:'No automatic promotion · no decision influence'
    }
  }[lang];
  const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
  const valid=v=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v));
  const num=(v,d=4)=>valid(v)?Number(v).toFixed(d):'—';
  const pct=(v,d=1)=>valid(v)?`${Number(v).toFixed(d)}%`:'—';
  const ratio=v=>valid(v)?`${Math.round(Number(v)*100)}%`:'—';
  const signed=(v,d=6)=>valid(v)?`${Number(v)>=0?'+':''}${Number(v).toFixed(d)}`:'—';
  const date=v=>{if(!v)return '—';const d=new Date(v);if(Number.isNaN(d.getTime()))return String(v);return new Intl.DateTimeFormat(lang==='pl'?'pl-PL':'en-GB',{year:'numeric',month:'short',day:'2-digit'}).format(d)};
  const dateTime=v=>{if(!v)return '—';const d=new Date(v);if(Number.isNaN(d.getTime()))return String(v);return new Intl.DateTimeFormat(lang==='pl'?'pl-PL':'en-GB',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}).format(d)};
  const scenarioLabel=s=>String(s||'').replaceAll('_',' ');
  function metric(label,value){const box=el('div','gse-kpi');box.append(el('strong','',String(value??'—')),el('span','',label));return box}
  function row(label,value,kind=''){const r=el('div','gse-metric-row');r.append(el('span','',label),el('strong',kind,value));return r}

  function render(data){
    const frag=document.createDocumentFragment();
    const hero=el('section','gse-hero');
    hero.append(el('span','gse-kicker','GSE v2 · Research Lab'),el('h1','gse-title','GSE v2'),el('p','gse-full-name',data.engine?.full_name||'Geopolitical Scenario Engine'),el('p','gse-intro',t.intro));
    const badges=el('div','gse-badges');badges.append(el('span','gse-badge good',String(data.engine?.mode||'shadow').toUpperCase()),el('span','gse-badge warn',t.automatic));hero.append(badges);
    const grid=el('div','gse-grid');const s=data.summary||{};grid.append(metric(t.clusters,`${s.verified_clusters??'—'} / ${s.target_verified_clusters??100}+`),metric(t.responses,s.historical_response_rows??'—'),metric(t.walk,s.walk_forward_n??'—'),metric(t.live,s.prospective_paired_n??'—'));hero.append(grid);frag.append(hero);

    const best=data.best_horizon||{};
    const highlight=el('section','gse-section');const hi=el('div','gse-highlight');hi.append(el('strong','',`${t.best}: ${best.label||'—'}`),el('p','',`${t.bestBody}. Brier ${num(best.regime_brier)} vs ${num(best.baseline_brier)} · ${t.improvement}: ${pct(best.brier_improvement_pct)} · N=${best.n??'—'}`));highlight.append(hi);frag.append(highlight);

    const hs=el('section','gse-section');const hh=el('div','gse-section-head');const hcopy=el('div');hcopy.append(el('h2','',t.horizons),el('p','',t.horizonsSub));hh.append(hcopy);hs.append(hh);const cards=el('div','gse-horizons');(data.horizons||[]).forEach(h=>{const c=el('article','gse-horizon'+(h.best?' best':''));c.append(el('h3','',h.label||''),row(t.sample,String(h.n??'—')),row(t.regime,num(h.regime_brier)),row(t.base,num(h.baseline_brier)),row(t.improvement,pct(h.brier_improvement_pct),'gse-positive'),row(t.hit,ratio(h.hit_rate)));cards.append(c)});hs.append(cards);frag.append(hs);

    const sc=el('section','gse-section');const sh=el('div','gse-section-head');const scopy=el('div');scopy.append(el('h2','',t.scenarios),el('p','',t.scenariosSub));sh.append(scopy);sc.append(sh);const wrap=el('div','gse-table-wrap');const table=el('table','gse-table');const thead=document.createElement('thead');const tr=document.createElement('tr');[lang==='pl'?'Scenariusz':'Scenario',t.sample,t.regime,t.base,t.improvement,t.hit].forEach(x=>tr.append(el('th','',x)));thead.append(tr);table.append(thead);const tbody=document.createElement('tbody');const scenarios=(data.scenarios||[]).length?data.scenarios:Object.entries(data.scenario_catalog_counts||{}).map(([scenario_type,n])=>({scenario_type,n}));scenarios.slice(0,12).forEach(x=>{const r=document.createElement('tr');r.append(el('td','gse-scenario-name',scenarioLabel(x.scenario_type)),el('td','',String(x.n??'—')),el('td','',num(x.regime_brier)),el('td','',num(x.baseline_brier)),el('td','gse-positive',pct(x.brier_improvement_pct)),el('td','',ratio(x.hit_rate)));tbody.append(r)});table.append(tbody);wrap.append(table);sc.append(wrap);frag.append(sc);

    const two=el('section','gse-section');const th=el('div','gse-section-head');const thcopy=el('div');thcopy.append(el('h2','',t.challenger));th.append(thcopy);two.append(th);const twogrid=el('div','gse-two');
    const ch=data.challenger||{};const cbox=el('div','gse-status-box');cbox.append(el('h3','',t.challenger),row(t.status,String(ch.status||'—')),row('temperature',String(ch.candidate?.similarity_temperature??'—')),row('prior strength',String(ch.candidate?.prior_strength??'—')),row('holdout ΔBrier',signed(ch.holdout_delta_brier_candidate_minus_active),valid(ch.holdout_delta_brier_candidate_minus_active)&&Number(ch.holdout_delta_brier_candidate_minus_active)<0?'gse-positive':''),el('p','',t.active));
    const p=data.prospective||{};const pbox=el('div','gse-status-box');pbox.append(el('h3','',t.prospective),row(t.paired,String(p.paired_n??'—')),row(t.v1,num(p.mean_brier_v1,6)),row(t.v2,num(p.mean_brier_v2,6)),row(t.delta,signed(p.delta_brier_v2_minus_v1,6),valid(p.delta_brier_v2_minus_v1)&&Number(p.delta_brier_v2_minus_v1)<0?'gse-positive':'gse-negative'),row(t.bias,num(p.calibration_bias_v2,4),valid(p.calibration_bias_v2)&&Math.abs(Number(p.calibration_bias_v2))<=.1?'gse-positive':'gse-negative'));
    twogrid.append(cbox,pbox);two.append(twogrid);const ready=el('div','gse-highlight');ready.style.marginTop='14px';ready.append(el('strong','',`${t.readiness}: ${data.readiness?.status||'—'}`));const reasons=el('ul','gse-reasons');(data.readiness?.reasons||[]).forEach(x=>reasons.append(el('li','',scenarioLabel(x))));ready.append(reasons);two.append(ready);frag.append(two);

    const ep=el('section','gse-section');const eh=el('div','gse-section-head');const ecopy=el('div');ecopy.append(el('h2','',t.history),el('p','',t.historySub));eh.append(ecopy);ep.append(eh);const epgrid=el('div','gse-episodes');const episodes=data.episodes||[];episodes.forEach((x,i)=>{const a=el('article','gse-episode');if(i>=8)a.hidden=true;a.dataset.extra=i>=8?'1':'0';a.append(el('time','',date(x.event_at)),el('h3','',x.label||x.event_id||'Event'),el('p','',`${scenarioLabel((x.scenario_types||[]).join(' · '))} · ${t.source}: ${x.source||'—'}`));if(x.source_ref){const link=el('a','',lang==='pl'?'Źródło pierwotne →':'Primary source →');link.href=x.source_ref;link.target='_blank';link.rel='noopener noreferrer external';a.append(link)}epgrid.append(a)});ep.append(epgrid);if(episodes.length>8){const more=el('div','gse-more');const b=el('button','',t.more);b.type='button';b.setAttribute('aria-expanded','false');b.addEventListener('click',()=>{const open=b.getAttribute('aria-expanded')==='true';epgrid.querySelectorAll('[data-extra="1"]').forEach(n=>n.hidden=open);b.setAttribute('aria-expanded',String(!open));b.textContent=open?t.more:t.less});more.append(b);ep.append(more)}frag.append(ep);

    const learn=el('section','gse-section');const lh=el('div','gse-section-head');const lcopy=el('div');lcopy.append(el('h2','',t.learning),el('p','',t.learningSub));lh.append(lcopy);learn.append(lh);const timeline=el('div','gse-timeline');(data.learning_timeline||[]).slice().reverse().forEach(x=>{const r=el('div','gse-timeline-row');r.append(el('time','',dateTime(x.recorded_at)),el('span','',`${x.candidates_added??0} ${t.candidates}`),el('span','',`${x.verifications_added??0} ${t.verifications}`));timeline.append(r)});learn.append(timeline);frag.append(learn);

    const method=el('section','gse-section');const mh=el('div','gse-section-head');const mhcopy=el('div');mhcopy.append(el('h2','',t.method));mh.append(mhcopy);method.append(mh);const flow=el('div','gse-method');t.methodSteps.forEach((x,i)=>flow.append(el('div','',`${i+1}. ${x}`)));method.append(flow,el('p','gse-footnote',`${t.updated}: ${dateTime(data.generated_at)} · ${t.automatic}`));frag.append(method);
    root.replaceChildren(frag);
  }

  fetch('/data/gse/gse_v2_lab_public.json?v='+Date.now(),{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error(String(r.status));return r.json()}).then(render).catch(()=>{root.innerHTML='';root.append(el('div','gse-error',t.unavailable))});
})();
