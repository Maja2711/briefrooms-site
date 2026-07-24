(function(){
  'use strict';

  var root = document.querySelector('[data-brace-lab-root]');
  if (!root) return;

  var language = (document.documentElement.lang || 'pl').toLowerCase().indexOf('en') === 0 ? 'en' : 'pl';
  var labels = language === 'pl' ? {
    loading:'Ładowanie bezpiecznego raportu…',
    error:'Nie udało się pobrać aktualnego raportu BRACE-SPX Lab.',
    completed:'wykonanych eksperymentów',
    remaining:'pozostało',
    updated:'Snapshot',
    gate:'Bramka odporności',
    model:'BRACE-SPX',
    buyHold:'Buy & Hold',
    trend:'Trend 200D',
    noCode:'Kod modeli nie jest wysyłany do przeglądarki',
    noParams:'Parametry i progi pozostają prywatne',
    noPredictions:'Surowe prognozy pozostają prywatne',
    noLedger:'Pełny dziennik eksperymentów pozostaje prywatny',
    researchOnly:'Badania, nie sygnał transakcyjny'
  } : {
    loading:'Loading the sanitized research report…',
    error:'The current BRACE-SPX Lab report could not be loaded.',
    completed:'experiments completed',
    remaining:'remaining',
    updated:'Snapshot',
    gate:'Robustness gate',
    model:'BRACE-SPX',
    buyHold:'Buy & Hold',
    trend:'200D trend',
    noCode:'Model code is never sent to the browser',
    noParams:'Parameters and thresholds remain private',
    noPredictions:'Raw predictions remain private',
    noLedger:'The full experiment ledger remains private',
    researchOnly:'Research, not a trading signal'
  };

  function byId(id){ return document.getElementById(id); }
  function text(id, value){ var node=byId(id); if(node) node.textContent=value == null ? '—' : String(value); }
  function pct(value, digits){
    var numeric=Number(value);
    return Number.isFinite(numeric) ? (numeric*100).toLocaleString(language==='pl'?'pl-PL':'en-US',{minimumFractionDigits:digits,maximumFractionDigits:digits})+'%' : '—';
  }
  function number(value, digits){
    var numeric=Number(value);
    return Number.isFinite(numeric) ? numeric.toLocaleString(language==='pl'?'pl-PL':'en-US',{minimumFractionDigits:digits,maximumFractionDigits:digits}) : '—';
  }
  function dateTime(value){
    var date=new Date(value);
    if(Number.isNaN(date.getTime())) return '—';
    return new Intl.DateTimeFormat(language==='pl'?'pl-PL':'en-GB',{dateStyle:'medium',timeStyle:'short',timeZone:'Europe/Warsaw'}).format(date);
  }
  function metricRow(name, metrics){
    var tr=document.createElement('tr');
    var values=[name,pct(metrics.cagr,1),pct(metrics.annualized_volatility,1),number(metrics.sharpe_zero_rf,2),pct(metrics.max_drawdown,1),number(metrics.calmar,2)];
    values.forEach(function(value,index){
      var cell=document.createElement(index===0?'th':'td');
      if(index===0) cell.setAttribute('scope','row');
      cell.textContent=value;
      tr.appendChild(cell);
    });
    return tr;
  }

  text('brace-loading',labels.loading);

  fetch('/data/public/brace_spx_public.json?ts='+Date.now(),{cache:'no-store',credentials:'same-origin'})
    .then(function(response){if(!response.ok) throw new Error('HTTP '+response.status);return response.json();})
    .then(function(report){
      var progress=report.progress||{};
      var champion=report.development_champion||{};
      var metrics=champion.metrics||{};
      var gate=champion.robustness_gate||{};
      var holdout=report.sealed_holdout||{};
      var generation=report.generation||{};
      var statusLabels=report.status_labels||{};
      var generationLabels=generation.labels||{};
      var holdoutLabels=holdout.labels||{};
      var notes=report.notes||{};
      var ratio=Math.max(0,Math.min(1,Number(progress.completion_ratio)||0));

      text('brace-status',statusLabels[language]||report.status||'—');
      text('brace-updated',labels.updated+': '+dateTime(report.source_snapshot_at));
      text('brace-generation',generationLabels[language]||generation.version||'—');
      text('brace-generation-version','v'+(generation.version||'—'));
      text('brace-completed',Number(progress.experiments_completed||0).toLocaleString(language==='pl'?'pl-PL':'en-US')+' '+labels.completed);
      text('brace-remaining',Number(progress.experiments_remaining||0).toLocaleString(language==='pl'?'pl-PL':'en-US')+' '+labels.remaining);
      text('brace-progress-percent',pct(ratio,1));
      var bar=byId('brace-progress-bar');
      if(bar){bar.style.width=(ratio*100).toFixed(2)+'%';bar.setAttribute('aria-valuenow',String(Math.round(ratio*100)));}

      text('metric-cagr',pct(metrics.cagr,1));
      text('metric-volatility',pct(metrics.annualized_volatility,1));
      text('metric-sharpe',number(metrics.sharpe_zero_rf,2));
      text('metric-drawdown',pct(metrics.max_drawdown,1));
      text('metric-calmar',number(metrics.calmar,2));
      text('metric-exposure',pct(metrics.average_exposure,1));
      text('metric-turnover',pct(metrics.annualized_turnover,1));
      text('metric-positive-years',pct(metrics.positive_year_ratio,0));

      var gateNode=byId('brace-gate');
      if(gateNode){
        gateNode.textContent=(gate.labels&&gate.labels[language])||labels.gate;
        gateNode.className=gate.passed?'brace-gate-pass':'brace-gate-fail';
      }
      text('brace-holdout',holdoutLabels[language]||holdout.status||'—');
      text('brace-holdout-months',String(holdout.months||'—')+'M');
      text('brace-note',notes[language]||labels.researchOnly);

      var tbody=byId('brace-comparison-body');
      if(tbody){
        tbody.textContent='';
        tbody.appendChild(metricRow(labels.model,metrics));
        var benchmarks=report.benchmarks||{};
        tbody.appendChild(metricRow(labels.buyHold,benchmarks.buy_and_hold||{}));
        tbody.appendChild(metricRow(labels.trend,benchmarks.trend_200d||{}));
      }

      text('boundary-code',labels.noCode);
      text('boundary-params',labels.noParams);
      text('boundary-predictions',labels.noPredictions);
      text('boundary-ledger',labels.noLedger);
      root.classList.remove('brace-skeleton');
      var loading=byId('brace-loading');if(loading) loading.hidden=true;
    })
    .catch(function(){
      var error=byId('brace-error');
      if(error){error.textContent=labels.error;error.classList.add('is-visible');}
      var loading=byId('brace-loading');if(loading) loading.hidden=true;
    });
})();
