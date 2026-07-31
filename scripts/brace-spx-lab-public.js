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
    noSnapshots:'Dzienny stan kandydatów nie jest publikowany'
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
    noSnapshots:'Daily candidate snapshots are not published'
  };

  function byId(id){return document.getElementById(id);}
  function text(id,value){var node=byId(id);if(node) node.textContent=value==null?'—':String(value);}
  function number(value,digits){var n=Number(value);return Number.isFinite(n)?n.toLocaleString(locale,{minimumFractionDigits:digits,maximumFractionDigits:digits}):'—';}
  function pct(value,digits){var n=Number(value);return Number.isFinite(n)?(n*100).toLocaleString(locale,{minimumFractionDigits:digits,maximumFractionDigits:digits})+'%':'—';}
  function dateTime(value){var d=new Date(value);if(Number.isNaN(d.getTime())) return '—';return new Intl.DateTimeFormat(language==='pl'?'pl-PL':'en-GB',{dateStyle:'medium',timeStyle:'short',timeZone:'Europe/Warsaw'}).format(d);}
  function dateOnly(value){var d=new Date(value+'T12:00:00Z');if(Number.isNaN(d.getTime())) return '—';return new Intl.DateTimeFormat(language==='pl'?'pl-PL':'en-GB',{dateStyle:'medium',timeZone:'Europe/Warsaw'}).format(d);}
  function status(id,label,kind){var node=byId(id);if(!node) return;node.textContent=label;node.className='brace-status '+(kind||'');}
  function metricRow(name,metrics){
    var tr=document.createElement('tr');
    var values=[name,pct(metrics.cagr,1),pct(metrics.annualized_volatility,1),number(metrics.sharpe_excess,2),pct(metrics.max_drawdown,1),number(metrics.calmar,2),number(metrics.annualized_turnover,2)];
    values.forEach(function(value,index){var cell=document.createElement(index===0?'th':'td');if(index===0) cell.setAttribute('scope','row');cell.textContent=value;tr.appendChild(cell);});
    return tr;
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
      if(tbody){tbody.textContent='';tbody.appendChild(metricRow(labels.model,metrics));var benchmarks=report.benchmarks||{};tbody.appendChild(metricRow(labels.buyHold,benchmarks.buy_and_hold||{}));tbody.appendChild(metricRow(labels.trend,benchmarks.trend_200d||{}));}

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
