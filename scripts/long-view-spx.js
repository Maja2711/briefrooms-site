(()=>{
  'use strict';

  const root=document.querySelector('[data-long-view-engines]');
  if(!root)return;

  const lang=(document.documentElement.lang||'pl').toLowerCase().startsWith('en')?'en':'pl';
  const copy=lang==='pl'?{
    title:'Cross-check silników BriefRooms',
    sub:'Niezależne warstwy modelowe nie zastępują danych makro. Pokazujemy zgodność, konflikt horyzontów i siłę dowodu.',
    gse:'GSE v2 · geopolityka → rynek',
    wes:'WES · tygodniowy S&P 500',
    fse:'FSE · struktura fraktalna',
    brace:'BRACE-SPX',
    net:'Wspólny odczyt',
    down:'SPADEK',
    up:'WZROST',
    noTrade:'NO_TRADE',
    noOpinion:'NO OPINION',
    stale:'nieświeży freeze',
    fresh:'świeży freeze',
    confidence:'confidence',
    clusters:'N efektywne',
    candidate:'kandydat',
    confirmations:'potwierdzenia',
    regime:'reżim',
    risk:'risk',
    validation:'walidacja',
    warmup:'warm-up',
    adaptive:'Adaptive',
    challengers:'challengery',
    mixed:'MIESZANY · konflikt horyzontów',
    mixedText:'GSE daje defensywny 30d read-through dla SPX, ale WES/FSE nie potwierdzają pełnego risk-off w krótkim horyzoncie. BRACE nadal nie ma autoryzowanego kierunku. To wspiera neutralno-defensywny 1M, a nie jednoznaczny bearish House View.',
    defensive:'DEFENSYWNY',
    defensiveText:'Modele wewnętrzne przesuwają bilans ryzyka w dół; pozostaje to cross-check research, nie samodzielny sygnał inwestycyjny.',
    positive:'TAKTYCZNIE DODATNI',
    positiveText:'Krótkoterminowe modele są dodatnie, ale bez wystarczającego potwierdzenia do zmiany strategicznego House View.',
    neutral:'NEUTRALNY',
    neutralText:'Brak spójnego sygnału kierunkowego między silnikami.',
    unavailable:'brak danych',
    research:'research-only'
  }:{
    title:'BriefRooms engine cross-check',
    sub:'Independent model layers do not replace macro data. We show agreement, horizon conflict and evidence strength.',
    gse:'GSE v2 · geopolitics → market',
    wes:'WES · weekly S&P 500',
    fse:'FSE · fractal structure',
    brace:'BRACE-SPX',
    net:'Combined read-through',
    down:'DOWN',
    up:'UP',
    noTrade:'NO_TRADE',
    noOpinion:'NO OPINION',
    stale:'stale freeze',
    fresh:'fresh freeze',
    confidence:'confidence',
    clusters:'effective N',
    candidate:'candidate',
    confirmations:'confirmations',
    regime:'regime',
    risk:'risk',
    validation:'validation',
    warmup:'warm-up',
    adaptive:'Adaptive',
    challengers:'challengers',
    mixed:'MIXED · horizon conflict',
    mixedText:'GSE has a defensive 30d SPX read-through, while WES/FSE do not confirm full risk-off at the short horizon. BRACE still has no authorized direction. That supports a neutral-defensive 1M stance rather than an outright bearish House View.',
    defensive:'DEFENSIVE',
    defensiveText:'Internal models shift the risk balance lower; this remains a research cross-check, not a standalone investment signal.',
    positive:'TACTICALLY POSITIVE',
    positiveText:'Short-horizon models lean positive, but not with enough confirmation to change the strategic House View.',
    neutral:'NEUTRAL',
    neutralText:'No coherent directional signal across internal engines.',
    unavailable:'unavailable',
    research:'research-only'
  };

  const esc=(v)=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=(v)=>Number.isFinite(Number(v))?Number(v):null;
  const pct=(v,d=1)=>num(v)===null?'—':`${(Number(v)*100).toFixed(d)}%`;
  const safeJson=async(url)=>{
    try{
      const r=await fetch(`${url}${url.includes('?')?'&':'?'}v=${Date.now()}`,{cache:'no-store'});
      if(!r.ok)return null;
      return await r.json();
    }catch(_){return null;}
  };
  const directionLabel=(d)=>String(d||'').toUpperCase()==='DOWN'?copy.down:String(d||'').toUpperCase()==='UP'?copy.up:'—';
  const freshnessLabel=(x)=>String(x||'').toLowerCase()==='fresh'?copy.fresh:copy.stale;

  const card=(cls,title,value,meta,detail)=>`
    <article class="lv-engine-card ${cls}">
      <div class="lv-engine-kicker">${esc(title)}</div>
      <strong>${esc(value)}</strong>
      <div class="lv-engine-meta">${esc(meta)}</div>
      <p>${esc(detail)}</p>
    </article>`;

  (async()=>{
    const [gse,fse,brace,wesReport]=await Promise.all([
      safeJson('/data/gse/gse_v2_lab_public.json'),
      safeJson('/data/investments/fse_public.json'),
      safeJson('/data/public/brace_spx_platform_public.json'),
      safeJson('/data/investments/wes_report.json')
    ]);

    const weekId=wesReport?.week_id||null;
    const weekly=weekId?await safeJson('/data/investments/weekly/'+encodeURIComponent(weekId)+'.json'):null;
    const spx=(weekly?.instruments||[]).find(x=>x?.instrument_id==='sp500_futures')||null;

    const gseSp=gse?.spx_long_view?.primary_horizon||null;
    const gseProb=num(gseSp?.probability);
    const gseDown=gseSp?.direction==='DOWN'&&gseProb!==null&&gseProb>=0.60;
    const gseText=gseSp
      ?`${directionLabel(gseSp.direction)} ${pct(gseSp.probability)} · 30d`
      :copy.unavailable;
    const gseMeta=gseSp
      ?`${copy.confidence} ${pct(gseSp.epistemic_confidence)} · ${copy.clusters} ${gseSp.effective_cluster_n??'—'} · ${freshnessLabel(gseSp.freshness)}`
      :'—';
    const gseDetail=gseSp
      ?`${copy.research}; ${(gseSp.scenario_types||[]).slice(0,3).map(x=>String(x).replaceAll('_',' ')).join(' · ')||'—'}`
      :copy.unavailable;

    const noTrade=String(spx?.trade_status||'').toLowerCase()==='no_trade';
    const blocked=spx?.no_trade_decision?.blocked_candidate||null;
    const admission=spx?.no_trade_decision?.directional_admission||null;
    const wesDirection=String(blocked?.direction||spx?.direction||'neutral').toUpperCase();
    const wesLong=(wesDirection==='LONG'&&num(blocked?.raw_score)!==null&&Number(blocked.raw_score)>0);
    const wesText=noTrade?copy.noTrade:`${wesDirection} · score ${spx?.score??'—'}`;
    const wesMeta=noTrade&&blocked
      ?`${copy.candidate} ${wesDirection} ${blocked.raw_score??'—'} · ${copy.confirmations} ${admission?.confirmations??0}/2`
      :`score ${spx?.score??'—'} · ${copy.confirmations} ${admission?.confirmations??0}/2`;
    const weeklyRegime=blocked?.contextual_learning_components?.weekly_regime||spx?.signals?.weekly_regime||'trend_up:vol_normal';
    const wesDetail=`${copy.regime}: ${String(weeklyRegime).replaceAll('_',' ')} · WES ${spx?.wes_methodology||wesReport?.version||'—'}`;

    const fseSp=(fse?.instruments||[]).find(x=>x?.instrument==='SPX')||null;
    const fseMem=fseSp?.fractal_memory||null;
    const fseProb=num(fseMem?.p_up_4h);
    const fseUp=fseMem?.forecast==='UP'&&fseProb!==null&&fseProb>=0.55;
    const fseTest=(fse?.hse_measurements||[]).find(x=>x?.details?.instrument==='SPX'&&x?.details?.kind==='directional_memory')||null;
    const fseText=fseMem?`${directionLabel(fseMem.forecast)} · P↑ ${pct(fseMem.p_up_4h)} · 4h`:copy.unavailable;
    const fseMeta=fseSp?`${copy.regime} ${fseSp.regime||'—'} · ${copy.risk} ${pct(fseSp.risk_score)}`:'—';
    const fseDetail=fseMem
      ?`${copy.validation} N=${fseTest?.counter??0}/${fseTest?.target_n??40} · ${String(fseMem.source||'').replaceAll('_',' ')} · ${copy.research}`
      :copy.unavailable;

    const frozen=brace?.frozen_track||null;
    const adaptive=brace?.adaptive_research||null;
    const braceText=frozen?copy.noOpinion:copy.unavailable;
    const braceMeta=frozen
      ?`${copy.warmup} ${frozen.observations_collected??'—'}/${frozen.warmup_required??'—'} · ${copy.adaptive} N=${adaptive?.initial_challenger_prospective_n??0}`
      :'—';
    const braceDetail=frozen
      ?`${adaptive?.active_challengers??0} ${copy.challengers} · G7 ${brace?.promotion_gate?.status||'NOT_READY'} · ${copy.research}`
      :copy.unavailable;

    let netLabel=copy.neutral,netText=copy.neutralText,netClass='neutral';
    if(gseDown&&(wesLong||fseUp)){netLabel=copy.mixed;netText=copy.mixedText;netClass='mixed';}
    else if(gseDown){netLabel=copy.defensive;netText=copy.defensiveText;netClass='defensive';}
    else if(wesLong&&fseUp){netLabel=copy.positive;netText=copy.positiveText;netClass='positive';}

    root.innerHTML=`
      <div class="lv-engine-head">
        <div><span class="engine-chip">LIVE MODEL CROSS-CHECK</span><h2>${esc(copy.title)}</h2><p>${esc(copy.sub)}</p></div>
        <span class="lv-engine-net ${netClass}">${esc(netLabel)}</span>
      </div>
      <div class="lv-engine-grid">
        ${card(gseDown?'defensive':'',copy.gse,gseText,gseMeta,gseDetail)}
        ${card(wesLong?'positive':'',copy.wes,wesText,wesMeta,wesDetail)}
        ${card(fseUp?'positive':'',copy.fse,fseText,fseMeta,fseDetail)}
        ${card('',copy.brace,braceText,braceMeta,braceDetail)}
      </div>
      <div class="lv-engine-conclusion"><strong>${esc(copy.net)}:</strong> ${esc(netText)}</div>`;
  })();
})();