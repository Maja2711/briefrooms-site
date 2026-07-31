(function(){
  'use strict';

  var root=document.querySelector('[data-brace-lab-root]');
  if(!root) return;

  var language=(document.documentElement.lang||'pl').toLowerCase().indexOf('en')===0?'en':'pl';
  var locale=language==='pl'?'pl-PL':'en-US';
  var labels=language==='pl'?{
    loading:'Ładowanie bezpiecznego raportu…',
    error:'Nie udało się pobrać aktualnego raportu BRACE-SPX Lab.',
    snapshot:'Snapshot',
    completed:'przebadanych konstrukcji',
    remaining:'pozostało',
    noChampion:'Brak championa',
    championBlocked:'ranking nadal niestabilny',
    externalPass:'Zgodna',
    externalFail:'Niezgodna',
    holdout:'Zapieczętowany',
    gateFail:'Niezaliczona',
    gatePass:'Zaliczona',
    warming:'Rozgrzewanie',
    active:'Shadow aktywny',
    notStarted:'Jeszcze nie rozpoczęty',
    noOrders:'Brak zleceń',
    sourceNames:{rates:'Stopy',liquidity:'Płynność',options:'Opcje / VIX'},
    model:'Najlepszy wariant diagnostyczny',
    buyHold:'Buy & Hold',
    trend:'Trend 200D',
    noCode:'Kod modeli nie jest wysyłany do przeglądarki',
    noParams:'Parametry i progi pozostają prywatne',
    noPredictions:'Surowe prognozy pozostają prywatne',
    noLedger:'Pełny dziennik eksperymentów pozostaje prywatny',
    noSnapshots:'Dzienny stan kandydatów nie jest publikowany',
    economicAssessment:'Ocena ekonomiczna względem Trend 200D',
    edgeConfirmed:'Przewaga nad Trend 200D potwierdzona',
    edgeNotConfirmed:'Brak potwierdzonej przewagi nad Trend 200D',
    cagrDelta:'CAGR vs Trend 200D',
    sharpeDelta:'Sharpe vs Trend 200D',
    calmarDelta:'Calmar vs Trend 200D',
    turnoverMultiple:'Obrót vs Trend 200D',
    mandateAndCosts:'Co faktycznie robi obecny wariant?',
    mandate:'Aktualny mandat',
    longFlat:'Long / Flat',
    longShortFlat:'Long / Short / Flat',
    averageExposure:'Średnia ekspozycja',
    timeLong:'Czas z ekspozycją long',
    timeFlat:'Czas bez ekspozycji',
    timeShort:'Czas z ekspozycją short',
    costModel:'Koszt transakcyjny',
    grossToNet:'CAGR brutto → netto',
    annualCostDrag:'Roczny wpływ kosztów',
    shortDisabled:'short jest niedozwolony',
    metricsNet:'wyniki w tabeli są po kosztach',
    gradedExposure:'ekspozycja stopniowana od 0,25 do 1,00',
    noMarketExposure:'ekspozycja rynkowa równa zero'
  }:{
    loading:'Loading the sanitized research report…',
    error:'The current BRACE-SPX Lab report could not be loaded.',
    snapshot:'Snapshot',
    completed:'tested constructions',
    remaining:'remaining',
    noChampion:'No champion',
    championBlocked:'ranking remains unstable',
    externalPass:'Matched',
    externalFail:'Mismatch',
    holdout:'Sealed',
    gateFail:'Failed',
    gatePass:'Passed',
    warming:'Warming up',
    active:'Shadow active',
    notStarted:'Not started yet',
    noOrders:'No orders',
    sourceNames:{rates:'Rates',liquidity:'Liquidity',options:'Options / VIX'},
    model:'Best diagnostic variant',
    buyHold:'Buy & Hold',
    trend:'200D trend',
    noCode:'Model code is never sent to the browser',
    noParams:'Parameters and thresholds remain private',
    noPredictions:'Raw predictions remain private',
    noLedger:'The full experiment ledger remains private',
    noSnapshots:'Daily candidate snapshots are not published',
    economicAssessment:'Economic assessment versus the 200D trend',
    edgeConfirmed:'Edge over the 200D trend confirmed',
    edgeNotConfirmed:'No confirmed edge over the 200D trend',
    cagrDelta:'CAGR vs 200D trend',
    sharpeDelta:'Sharpe vs 200D trend',
    calmarDelta:'Calmar vs 200D trend',
    turnoverMultiple:'Turnover vs 200D trend',
    mandateAndCosts:'What does the current variant actually do?',
    mandate:'Current mandate',
    longFlat:'Long / Flat',
    longShortFlat:'Long / Short / Flat',
    averageExposure:'Average exposure',
    timeLong:'Time with long exposure',
    timeFlat:'Time with no exposure',
    timeShort:'Time with short exposure',
    costModel:'Transaction cost',
    grossToNet:'Gross → net CAGR',
    annualCostDrag:'Annual cost impact',
    shortDisabled:'short exposure is disabled',
    metricsNet:'table metrics are net of costs',
    gradedExposure:'graded exposure from 0.25 to 1.00',
    noMarketExposure:'market exposure equals zero'
  };

  function byId(id){return document.getElementById(id);}
  function text(id,value){var node=byId(id);if(node) node.textContent=value==null?'—':String(value);}
  function number(value,digits){var n=Number(value);return Number.isFinite(n)?n.toLocaleString(locale,{minimumFractionDigits:digits,maximumFractionDigits:digits}):'—';}
  function pct(value,digits){var n=Number(value);return Number.isFinite(n)?(n*100).toLocaleString(locale,{minimumFractionDigits:digits,maximumFractionDigits:digits})+'%':'—';}
  function signedPct(value,digits){var n=Number(value);if(!Number.isFinite(n)) return '—';var sign=n>0?'+':'';return sign+(n*100).toLocaleString(locale,{minimumFractionDigits:digits,maximumFractionDigits:digits})+' p.p.';}
  function signedNumber(value,digits){var n=Number(value);if(!Number.isFinite(n)) return '—';var sign=n>0?'+':'';return sign+n.toLocaleString(locale,{minimumFractionDigits:digits,maximumFractionDigits:digits});}
  function multiple(value,digits){var n=Number(value);return Number.isFinite(n)?n.toLocaleString(locale,{minimumFractionDigits:digits,maximumFractionDigits:digits})+'×':'—';}
  function bps(value,digits){var n=Number(value);return Number.isFinite(n)?n.toLocaleString(locale,{minimumFractionDigits:digits,maximumFractionDigits:digits})+' bp':'—';}
  function dateTime(value){var d=new Date(value);if(Number.isNaN(d.getTime())) return '—';return new Intl.DateTimeFormat(language==='pl'?'pl-PL':'en-GB',{dateStyle:'medium',timeStyle:'short',timeZone:'Europe/Warsaw'}).format(d);}
  function dateOnly(value){var d=new Date(value+'T12:00:00Z');if(Number.isNaN(d.getTime())) return '—';return new Intl.DateTimeFormat(language==='pl'?'pl-PL':'en-GB',{dateStyle:'medium',timeZone:'Europe/Warsaw'}).format(d);}
  function status(id,label,kind){var node=byId(id);if(!node) return;node.textContent=label;node.className='brace-status '+(kind||'');}
  function metricRow(name,metrics){
    var tr=document.createElement('tr');
    var values=[name,pct(metrics.cagr,1),pct(metrics.annualized_volatility,1),number(metrics.sharpe_excess,2),pct(metrics.max_drawdown,1),number(metrics.calmar,2),number(metrics.annualized_turnover,2)];
    values.forEach(function(value,index){var cell=document.createElement(index===0?'th':'td');if(index===0) cell.setAttribute('scope','row');cell.textContent=value;tr.appendChild(cell);});
    return tr;
  }
  function metricCard(label,value,description){
    var card=document.createElement('div');
    card.className='brace-metric';
    var small=document.createElement('small');small.textContent=label;
    var strong=document.createElement('strong');strong.textContent=value==null?'—':String(value);
    var span=document.createElement('span');span.textContent=description||'';
    card.appendChild(small);card.appendChild(strong);card.appendChild(span);
    return card;
  }
  function renderEconomicAssessment(report,metrics,benchmarks){
    var heading=byId('comparison-heading');
    var panel=heading&&heading.closest?heading.closest('.brace-panel'):null;
    if(!panel||byId('brace-economic-assessment')) return;

    var assessment=report.comparison_assessment||{};
    var trend=benchmarks.trend_200d||{};
    var economics=((report.development||{}).diagnostic_leader||{}).economics||{};
    var mandate=report.mandate||{};
    var costs=report.cost_model||{};
    var cagrDelta=assessment.cagr_delta;
    var sharpeDelta=assessment.sharpe_delta;
    var calmarDelta=assessment.calmar_delta;
    var turnoverMultiple=assessment.turnover_multiple;
    if(!Number.isFinite(Number(cagrDelta))) cagrDelta=Number(metrics.cagr)-Number(trend.cagr);
    if(!Number.isFinite(Number(sharpeDelta))) sharpeDelta=Number(metrics.sharpe_excess)-Number(trend.sharpe_excess);
    if(!Number.isFinite(Number(calmarDelta))) calmarDelta=Number(metrics.calmar)-Number(trend.calmar);
    if(!Number.isFinite(Number(turnoverMultiple))&&Number(trend.annualized_turnover)>0) turnoverMultiple=Number(metrics.annualized_turnover)/Number(trend.annualized_turnover);
    var edgeConfirmed=assessment.edge_confirmed===true;

    var wrapper=document.createElement('div');
    wrapper.id='brace-economic-assessment';
    wrapper.style.marginTop='20px';

    var row=document.createElement('div');row.className='brace-status-row';
    var title=document.createElement('h3');title.textContent=labels.economicAssessment;
    var badge=document.createElement('span');badge.className='brace-status '+(edgeConfirmed?'is-pass':'is-fail');badge.textContent=edgeConfirmed?labels.edgeConfirmed:labels.edgeNotConfirmed;
    row.appendChild(title);row.appendChild(badge);wrapper.appendChild(row);

    var callout=document.createElement('p');callout.className='brace-callout';
    if(language==='pl'){
      callout.textContent=edgeConfirmed
        ?'Wariant przewyższa Trend 200D jednocześnie pod względem CAGR, Sharpe’a i Calmaru. Nadal wymaga pełnej walidacji governance.'
        :'Wariant ogranicza zmienność i obsunięcie, ale CAGR jest niższy o '+signedPct(cagrDelta,2).replace('+','')+', a obrót wynosi '+multiple(turnoverMultiple,2)+' poziomu Trend 200D. To nie uzasadnia wyboru championa.';
    }else{
      callout.textContent=edgeConfirmed
        ?'The variant exceeds the 200D trend simultaneously on CAGR, Sharpe and Calmar. Full governance validation is still required.'
        :'The variant reduces volatility and drawdown, but CAGR is lower by '+signedPct(cagrDelta,2).replace('+','')+' while turnover is '+multiple(turnoverMultiple,2)+' the 200D trend level. This does not justify selecting a champion.';
    }
    wrapper.appendChild(callout);

    var comparisonGrid=document.createElement('div');comparisonGrid.className='brace-grid';comparisonGrid.style.marginTop='12px';
    comparisonGrid.appendChild(metricCard(labels.cagrDelta,signedPct(cagrDelta,2),language==='pl'?'ujemna różnica blokuje tezę o pełnej przewadze':'a negative difference blocks a full-edge claim'));
    comparisonGrid.appendChild(metricCard(labels.sharpeDelta,signedNumber(sharpeDelta,2),language==='pl'?'lepszy zwrot skorygowany o ryzyko':'better risk-adjusted return'));
    comparisonGrid.appendChild(metricCard(labels.calmarDelta,signedNumber(calmarDelta,2),language==='pl'?'niewielka poprawa relacji zwrot/obsunięcie':'small improvement in return-to-drawdown'));
    comparisonGrid.appendChild(metricCard(labels.turnoverMultiple,multiple(turnoverMultiple,2),language==='pl'?'większa aktywność niż prosty benchmark':'more activity than the simple benchmark'));
    wrapper.appendChild(comparisonGrid);

    var mandateTitle=document.createElement('h3');mandateTitle.textContent=labels.mandateAndCosts;wrapper.appendChild(mandateTitle);
    var details=document.createElement('div');details.className='brace-grid';
    var mandateLabel=mandate.short_allowed?labels.longShortFlat:labels.longFlat;
    details.appendChild(metricCard(labels.mandate,mandateLabel,mandate.short_allowed?'':labels.shortDisabled));
    details.appendChild(metricCard(labels.averageExposure,pct(economics.average_exposure!=null?economics.average_exposure:metrics.average_exposure,1),language==='pl'?'średnio zaangażowana część kapitału':'average invested share of capital'));
    details.appendChild(metricCard(labels.timeLong,pct(economics.time_long,1),labels.gradedExposure));
    details.appendChild(metricCard(labels.timeFlat,pct(economics.time_flat,1),labels.noMarketExposure));
    details.appendChild(metricCard(labels.timeShort,pct(economics.time_short,1),labels.shortDisabled));
    details.appendChild(metricCard(labels.costModel,bps(costs.basis_points_per_unit_turnover,1),labels.metricsNet));
    details.appendChild(metricCard(labels.grossToNet,pct(economics.gross_cagr_before_costs,1)+' → '+pct(economics.net_cagr_after_costs!=null?economics.net_cagr_after_costs:metrics.cagr,1),language==='pl'?'różnica wynika z kosztów obrotu':'difference reflects turnover costs'));
    details.appendChild(metricCard(labels.annualCostDrag,signedPct(-Math.abs(Number(economics.annualized_linear_cost_drag)),2),language==='pl'?'liniowe przybliżenie na podstawie obrotu':'linear estimate based on turnover'));
    wrapper.appendChild(details);
    panel.appendChild(wrapper);
  }

  text('brace-loading',labels.loading);

  fetch('/data/public/brace_spx_public.json?ts='+Date.now(),{cache:'no-store',credentials:'same-origin'})
    .then(function(response){if(!response.ok) throw new Error('HTTP '+response.status);return response.json();})
    .then(function(report){
      if(String(report.schema_version||'').indexOf('2.')!==0) throw new Error('Unsupported BRACE-SPX public schema');
      var architecture=report.architecture||{};
      var progress=report.progress||{};
      var development=report.development||{};
      var diagnostic=development.diagnostic_leader||{};
      var metrics=diagnostic.metrics||{};
      var gate=development.strict_gate||{};
      var diversity=report.diversity||{};
      var signals=diversity.signals||{};
      var returns=diversity.returns||{};
      var exposures=diversity.exposures||{};
      var external=report.external_validation||{};
      var audit=report.orthogonality_audit||{};
      var shadow=report.shadow||{};
      var holdout=report.sealed_holdout||{};
      var benchmarks=report.benchmarks||{};
      var ratio=Math.max(0,Math.min(1,Number(progress.completion_ratio)||0));

      status('brace-status',(report.status_labels||{})[language]||report.status,'is-fail');
      text('brace-updated',labels.snapshot+': '+dateTime(report.source_snapshot_at));
      text('brace-generation',(architecture.labels||{})[language]||architecture.id);
      text('brace-generation-version',architecture.version||'A2');
      text('brace-signature',architecture.candidate_signature||'—');
      text('brace-frequency',development.frequency||'—');
      text('brace-development-range',(development.start||'—')+' → '+(development.end||'—'));
      text('brace-completed',Number(progress.experiments_completed||0).toLocaleString(locale)+' '+labels.completed);
      text('brace-remaining',Number(progress.experiments_remaining||0).toLocaleString(locale)+' '+labels.remaining);
      text('brace-progress-percent',pct(ratio,1));
      var bar=byId('brace-progress-bar');if(bar){bar.style.width=(ratio*100).toFixed(2)+'%';bar.setAttribute('aria-valuenow',String(Math.round(ratio*100)));}

      text('metric-pbo',pct(gate.pbo,1));
      text('metric-global-dsr',pct(gate.global_dsr,1));
      text('metric-global-trials',Number(gate.global_trials||0).toLocaleString(locale));
      text('metric-rank-correlation',number(gate.rank_correlation,3));
      text('metric-fold-winners',gate.unique_fold_winners==null?'—':gate.unique_fold_winners);
      status('metric-external',external.passed?labels.externalPass:labels.externalFail,external.passed?'is-pass':'is-fail');
      status('metric-champion',labels.noChampion,'is-neutral');
      status('metric-holdout',labels.holdout,'is-pass');
      status('brace-gate',gate.passed?labels.gatePass:labels.gateFail,gate.passed?'is-pass':'is-fail');

      text('metric-cagr',pct(metrics.cagr,1));
      text('metric-volatility',pct(metrics.annualized_volatility,1));
      text('metric-sharpe',number(metrics.sharpe_excess,2));
      text('metric-drawdown',pct(metrics.max_drawdown,1));
      text('metric-calmar',number(metrics.calmar,2));
      text('metric-turnover',number(metrics.annualized_turnover,2));
      text('metric-positive-folds',(diagnostic.positive_folds==null?'—':diagnostic.positive_folds)+' / '+(development.folds||6));
      text('metric-bootstrap-cagr',pct(gate.bootstrap_cagr_advantage_probability,1));
      text('metric-bootstrap-sharpe',pct(gate.bootstrap_sharpe_advantage_probability,1));

      var tbody=byId('brace-comparison-body');
      if(tbody){tbody.textContent='';tbody.appendChild(metricRow(labels.model,metrics));tbody.appendChild(metricRow(labels.buyHold,benchmarks.buy_and_hold||{}));tbody.appendChild(metricRow(labels.trend,benchmarks.trend_200d||{}));}
      renderEconomicAssessment(report,metrics,benchmarks);

      text('metric-signal-correlation',pct(signals.median_absolute_pairwise_correlation,1));
      text('metric-signal-rank',number(signals.effective_independent_series,2));
      text('metric-exposure-rank',number(exposures.effective_independent_series,2));
      text('metric-return-correlation',pct(returns.median_absolute_pairwise_correlation,1));

      text('metric-audit-rank',number(audit.effective_rank,2));
      text('metric-audit-correlation',pct(audit.median_absolute_pairwise_correlation,1));
      text('metric-audit-pc',audit.principal_components_for_85pct_variance==null?'—':audit.principal_components_for_85pct_variance);
      var sourceList=byId('brace-source-list');
      if(sourceList){sourceList.textContent='';(audit.selected_sources||[]).forEach(function(source){var chip=document.createElement('span');chip.className='brace-source-chip';chip.textContent=labels.sourceNames[source]||source;sourceList.appendChild(chip);});}

      var shadowTotal=Math.max(1,Number(shadow.warmup_required)||252);
      var shadowDone=Math.max(0,Number(shadow.observations_collected)||0);
      var shadowRatio=Math.max(0,Math.min(1,shadowDone/shadowTotal));
      var shadowLabel=shadow.status==='shadow_active_no_orders'?labels.active:(shadow.status==='warming_up'?labels.warming:labels.notStarted);
      status('shadow-status',shadowLabel,shadow.status==='shadow_active_no_orders'?'is-pass':'is-neutral');
      text('shadow-start',dateOnly(shadow.start));
      text('shadow-updated',dateTime(shadow.updated_at));
      text('shadow-observations',shadowDone+' / '+shadowTotal);
      text('shadow-progress-percent',pct(shadowRatio,1));
      text('shadow-remaining',Number(shadow.observations_remaining||0).toLocaleString(locale));
      status('shadow-orders',labels.noOrders,'is-pass');
      status('shadow-champion',labels.noChampion,'is-neutral');
      var shadowBar=byId('shadow-progress-bar');if(shadowBar){shadowBar.style.width=(shadowRatio*100).toFixed(2)+'%';shadowBar.setAttribute('aria-valuenow',String(Math.round(shadowRatio*100)));}

      text('brace-holdout',(holdout.labels||{})[language]||holdout.status);
      text('brace-holdout-range',(holdout.start||'—')+' → '+(holdout.end||'—'));
      text('brace-note',(report.notes||{})[language]||'—');

      text('boundary-code',labels.noCode);
      text('boundary-params',labels.noParams);
      text('boundary-predictions',labels.noPredictions);
      text('boundary-ledger',labels.noLedger);
      text('boundary-snapshots',labels.noSnapshots);

      root.classList.remove('brace-skeleton');
      var loading=byId('brace-loading');if(loading) loading.hidden=true;
    })
    .catch(function(){var error=byId('brace-error');if(error){error.textContent=labels.error;error.classList.add('is-visible');}var loading=byId('brace-loading');if(loading) loading.hidden=true;});
})();
