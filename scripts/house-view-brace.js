(()=>{
  'use strict';

  const roots=[...document.querySelectorAll('[data-brace-house]')];
  if(!roots.length)return;

  const lang=(document.documentElement.lang||'pl').toLowerCase().startsWith('en')?'en':'pl';
  const nonce=Date.now();

  const copy=lang==='pl'?{
    kicker:'QUANT CROSS-CHECK · WES + BRACE-SPX',
    pill:'model read-through',
    titleNeutral:'Modele nie potwierdzają dziś pełnego risk-off',
    titleLong:'WES daje dodatni taktyczny read-through; BRACE nadal bez opinii kierunkowej',
    titleShort:'WES daje ujemny taktyczny read-through; BRACE nadal bez opinii kierunkowej',
    introNeutral:'Warstwa ilościowa jest dziś użyteczna głównie przez WES. S&P 500 pozostaje w sygnale neutralnym, a reżim trend/vol nie potwierdza załamania trendu. Frozen G6 nie ma jeszcze kwalifikowanego sygnału kierunkowego; Adaptive Research jest aktywny, ale jego nowe challengery dopiero budują własną historię prospective.',
    introLong:'WES daje dodatni sygnał taktyczny dla S&P 500. Frozen G6 nadal nie publikuje kwalifikowanej opinii point-in-time, a Adaptive Research dopiero zbiera własny dowód, więc BRACE nie dostaje jeszcze wagi kierunkowej.',
    introShort:'WES daje ujemny sygnał taktyczny dla S&P 500. Frozen G6 nadal nie publikuje kwalifikowanej opinii point-in-time, a Adaptive Research dopiero zbiera własny dowód, więc BRACE nie dostaje jeszcze wagi kierunkowej.',
    wes:'WES · S&P 500',
    brace:'BRACE-SPX',
    impact:'Wpływ na House View',
    noOpinion:'G6 NO OPINION',
    adaptive:'Adaptive',
    active:'ACTIVE',
    noOverride:'NO OVERRIDE',
    tacticalPositive:'TAKTYCZNY +',
    tacticalNegative:'TAKTYCZNY −',
    score:'score',
    week:'tydzień',
    directionalWeight:'waga kierunkowa 0%',
    eligible:'kwalifikowane rekordy point-in-time',
    challengers:'challengerów',
    conclusionNeutral:'Wniosek: WES nie potwierdza przejścia do pełnego risk-off. Neutralny score przy reżimie trend_up / vol_normal jest zgodny z ostrożnym, ale nie bearish House View. BRACE nie zmienia dziś scenariuszy: Frozen G6 nie ma opinii, a Adaptive Research nie ma jeszcze własnego N pozwalającego na ocenę przewagi.',
    conclusionLong:'Wniosek: WES wzmacnia krótkoterminowo stronę wzrostową, ale nie jest automatycznym powodem do podniesienia 6M. BRACE pozostaje neutralny dla House View do czasu kwalifikowanego dowodu z G6 lub Adaptive Research.',
    conclusionShort:'Wniosek: WES wzmacnia krótkoterminowo ryzyko spadkowe, ale nie jest automatycznym powodem do obniżenia 6M. BRACE pozostaje neutralny dla House View do czasu kwalifikowanego dowodu z G6 lub Adaptive Research.',
    braceMeta:'BRACE jest teraz jedną platformą: Frozen G6 zachowuje czystą walidację, Adaptive Research tworzy nowe challengery od własnej daty startu, a Promotion Gate może dopuścić dopiero zweryfikowaną kolejną generację. Do tego czasu wpływ kierunkowy = 0%.',
    longLabel:'Model cross-check',
    longNetNeutral:'brak bearish override',
    longNetLong:'taktyczne wsparcie wzrostowe',
    longNetShort:'taktyczne wsparcie spadkowe',
    normalTrend:'trend wzrostowy',
    flatTrend:'trend boczny',
    downTrend:'trend spadkowy',
    normalVol:'normalna zmienność',
    highVol:'wysoka zmienność',
    lowVol:'niska zmienność'
  }:{
    kicker:'QUANT CROSS-CHECK · WES + BRACE-SPX',
    pill:'model read-through',
    titleNeutral:'The models do not confirm a full risk-off regime today',
    titleLong:'WES provides a positive tactical read-through; BRACE still has no directional opinion',
    titleShort:'WES provides a negative tactical read-through; BRACE still has no directional opinion',
    introNeutral:'The quantitative layer is currently useful mainly through WES. The S&P 500 signal is neutral and the trend/vol regime does not confirm a trend break. Frozen G6 still has no qualified directional signal; Adaptive Research is active, but its new challengers are only beginning to build their own prospective history.',
    introLong:'WES provides a positive tactical S&P 500 signal. Frozen G6 still has no qualified point-in-time opinion, while Adaptive Research is only beginning to collect its own evidence, so BRACE receives no directional weight yet.',
    introShort:'WES provides a negative tactical S&P 500 signal. Frozen G6 still has no qualified point-in-time opinion, while Adaptive Research is only beginning to collect its own evidence, so BRACE receives no directional weight yet.',
    wes:'WES · S&P 500',
    brace:'BRACE-SPX',
    impact:'House View impact',
    noOpinion:'G6 NO OPINION',
    adaptive:'Adaptive',
    active:'ACTIVE',
    noOverride:'NO OVERRIDE',
    tacticalPositive:'TACTICAL +',
    tacticalNegative:'TACTICAL −',
    score:'score',
    week:'week',
    directionalWeight:'directional weight 0%',
    eligible:'point-in-time eligible records',
    challengers:'challengers',
    conclusionNeutral:'Conclusion: WES does not confirm a move into full risk-off. A neutral score with a trend_up / vol_normal regime is consistent with a cautious, but not bearish, House View. BRACE does not change scenario weights today: Frozen G6 has no opinion and Adaptive Research does not yet have enough own prospective N to assess edge.',
    conclusionLong:'Conclusion: WES strengthens the upside case tactically, but it is not an automatic reason to upgrade the 6M House View. BRACE stays neutral for House View until qualified evidence emerges from G6 or Adaptive Research.',
    conclusionShort:'Conclusion: WES strengthens downside risk tactically, but it is not an automatic reason to downgrade the 6M House View. BRACE stays neutral for House View until qualified evidence emerges from G6 or Adaptive Research.',
    braceMeta:'BRACE is now one platform: Frozen G6 preserves clean validation, Adaptive Research creates new challengers from their own start date, and the Promotion Gate can admit only a verified next generation. Until then, directional influence = 0%.',
    longLabel:'Model cross-check',
    longNetNeutral:'no bearish override',
    longNetLong:'tactical upside support',
    longNetShort:'tactical downside support',
    normalTrend:'uptrend',
    flatTrend:'sideways trend',
    downTrend:'downtrend',
    normalVol:'normal volatility',
    highVol:'high volatility',
    lowVol:'low volatility'
  };

  const esc=(value)=>String(value??'—').replace(/[&<>'"]/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
  const safeJson=async(url)=>{
    try{
      const r=await fetch(url+(url.includes('?')?'&':'?')+'v='+nonce,{cache:'no-store',credentials:'same-origin'});
      return r.ok?await r.json():null;
    }catch(_){return null;}
  };

  const findValue=(obj,key,seen=new WeakSet())=>{
    if(!obj||typeof obj!=='object')return null;
    if(seen.has(obj))return null;
    seen.add(obj);
    if(Object.prototype.hasOwnProperty.call(obj,key)&&obj[key]!=null)return obj[key];
    for(const value of Object.values(obj)){
      if(value&&typeof value==='object'){
        const found=findValue(value,key,seen);
        if(found!=null)return found;
      }
    }
    return null;
  };

  const regimeHuman=(raw)=>{
    const s=String(raw||'');
    const parts=s.split(':');
    const trend=parts[0]||'';
    const vol=parts[1]||'';
    const trendLabel=trend==='trend_up'?copy.normalTrend:trend==='trend_down'?copy.downTrend:trend==='trend_flat'?copy.flatTrend:trend||'—';
    const volLabel=vol==='vol_normal'?copy.normalVol:vol==='vol_high'?copy.highVol:vol==='vol_low'?copy.lowVol:vol||'—';
    return {raw:s||'—',human:`${trendLabel} · ${volLabel}`};
  };

  const dirClass=(direction)=>direction==='long'?'long':direction==='short'?'short':'neutral';
  const dirLabel=(direction)=>direction==='long'?'LONG':direction==='short'?'SHORT':'NEUTRAL';

  const renderHouse=(root,state)=>{
    const mode=dirClass(state.direction);
    const title=mode==='long'?copy.titleLong:mode==='short'?copy.titleShort:copy.titleNeutral;
    const intro=mode==='long'?copy.introLong:mode==='short'?copy.introShort:copy.introNeutral;
    const impact=mode==='long'?copy.tacticalPositive:mode==='short'?copy.tacticalNegative:copy.noOverride;
    const conclusion=mode==='long'?copy.conclusionLong:mode==='short'?copy.conclusionShort:copy.conclusionNeutral;
    root.style.padding='16px 18px';
    root.innerHTML=`
      <div class="hv-kicker"><span>${copy.kicker}</span><span class="hv-pill">${copy.pill}</span></div>
      <h2>${title}</h2>
      <p>${intro}</p>
      <div class="hv-summary-grid">
        <div class="hv-metric"><small>${copy.wes}</small><strong>${dirLabel(state.direction)} · ${copy.score} ${esc(state.score)}</strong><span>${esc(state.regime.human)} · ${copy.week} ${esc(state.weekId)}</span></div>
        <div class="hv-metric"><small>${copy.brace}</small><strong>${copy.noOpinion} · ${copy.adaptive} ${state.adaptiveActive?copy.active:'—'}</strong><span>Frozen ${esc(state.braceShadow)} · Adaptive N=${esc(state.adaptiveN)} · ${esc(state.activeChallengers)} ${copy.challengers}</span></div>
        <div class="hv-metric"><small>${copy.impact}</small><strong>${impact}</strong><span>${copy.directionalWeight} · ${esc(state.eligible)} ${copy.eligible}</span></div>
      </div>
      <p class="hv-brace-note"><strong>${conclusion}</strong></p>
      <p style="margin:9px 0 0;color:#8fa4b9;font-size:.76rem;line-height:1.5">${copy.braceMeta}</p>`;
  };

  const renderLong=(root,state)=>{
    const box=root.querySelector('.lv-brace');
    if(!box)return;
    const mode=dirClass(state.direction);
    const net=mode==='long'?copy.longNetLong:mode==='short'?copy.longNetShort:copy.longNetNeutral;
    box.innerHTML=`
      <div class="lv-brace-head"><strong>${copy.longLabel}</strong><span>${copy.week} ${esc(state.weekId)}</span></div>
      <div class="lv-brace-copy" style="margin-top:7px"><b>WES:</b> ${dirLabel(state.direction)} · ${copy.score} ${esc(state.score)} · ${esc(state.regime.raw)} &nbsp;|&nbsp; <b>BRACE:</b> ${copy.noOpinion}; ${copy.adaptive} ${state.adaptiveActive?copy.active:'—'} N=${esc(state.adaptiveN)} · ${copy.directionalWeight} &nbsp;|&nbsp; <b>net:</b> ${net}.</div>`;
  };

  (async()=>{
    const [brace,wesReport,alpha]=await Promise.all([
      safeJson('/data/public/brace_spx_generation6_public.json'),
      safeJson('/data/investments/wes_report.json'),
      safeJson('/data/investments/wes_spx_brace_alpha_report.json')
    ]);

    const weekId=wesReport?.week_id||'—';
    const weekly=weekId!=='—'?await safeJson('/data/investments/weekly/'+encodeURIComponent(weekId)+'.json'):null;
    const spx=(weekly?.instruments||[]).find(item=>item?.instrument_id==='sp500_futures')||null;
    const direction=String(spx?.direction||spx?.forecast_direction||'neutral').toLowerCase();
    const score=Number.isFinite(Number(spx?.score))?Number(spx.score):Number.isFinite(Number(spx?.forecast_score))?Number(spx.forecast_score):'—';
    const regime=regimeHuman(findValue(spx,'weekly_regime'));
    const shadow=brace?.shadow||{};
    const adaptive=brace?.adaptive_research||{};
    const braceShadow=`${shadow.observations_collected??'—'} / ${shadow.warmup_required??'—'}`;
    const adaptiveN=Number.isFinite(Number(adaptive?.initial_challenger_prospective_n))?Number(adaptive.initial_challenger_prospective_n):0;
    const activeChallengers=Number.isFinite(Number(adaptive?.active_challengers))?Number(adaptive.active_challengers):0;
    const adaptiveActive=String(adaptive?.status||'').startsWith('ACTIVE');
    const eligible=Number.isFinite(Number(alpha?.point_in_time_alpha_eligible_records))?Number(alpha.point_in_time_alpha_eligible_records):0;

    const state={weekId,direction,score,regime,braceShadow,adaptiveN,activeChallengers,adaptiveActive,eligible};
    roots.forEach(root=>{
      if(root.classList.contains('hv-brace'))renderHouse(root,state);
      else renderLong(root,state);
    });
  })();
})();
