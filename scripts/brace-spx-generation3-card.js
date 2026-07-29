(function(){
  'use strict';

  var roots = Array.prototype.slice.call(document.querySelectorAll('[data-brace-spx-generation3]'));
  if (!roots.length) return;
  var locale = (document.documentElement.lang || 'pl').toLowerCase().indexOf('en') === 0 ? 'en' : 'pl';

  function pct(value, digits) {
    var numeric = Number(value);
    return Number.isFinite(numeric)
      ? (numeric * 100).toLocaleString(locale === 'pl' ? 'pl-PL' : 'en-US', {minimumFractionDigits: digits, maximumFractionDigits: digits}) + '%'
      : '—';
  }

  function number(value, digits) {
    var numeric = Number(value);
    return Number.isFinite(numeric)
      ? numeric.toLocaleString(locale === 'pl' ? 'pl-PL' : 'en-US', {minimumFractionDigits: digits, maximumFractionDigits: digits})
      : '—';
  }

  function labelStatus(status) {
    var labels = locale === 'pl' ? {
      research_in_progress: 'Badania w toku',
      development_search: 'Badania w toku',
      generation_exhausted_holdout_still_sealed: 'Przestrzeń testów zakończona',
      passed_development_gate_holdout_still_sealed: 'Bramka rozwojowa zaliczona',
      failed_development_gate_holdout_still_sealed: 'Bramka rozwojowa niezaliczona'
    } : {
      research_in_progress: 'Research in progress',
      development_search: 'Research in progress',
      generation_exhausted_holdout_still_sealed: 'Candidate space completed',
      passed_development_gate_holdout_still_sealed: 'Development gate passed',
      failed_development_gate_holdout_still_sealed: 'Development gate not passed'
    };
    return labels[status] || status || '—';
  }

  function set(root, field, value) {
    var nodes = root.querySelectorAll('[data-v3-field="' + field + '"]');
    Array.prototype.forEach.call(nodes, function(node){ node.textContent = value == null ? '—' : String(value); });
  }

  function render(root, report) {
    var progress = report.progress || {};
    var leader = report.development_leader || {};
    var holdout = report.holdout || {};
    var gate = report.strict_gate || {};
    var ratio = Math.max(0, Math.min(1, Number(progress.ratio) || 0));

    set(root, 'generation', report.generation_id || 'spx-focused-v3');
    set(root, 'status', labelStatus(report.status));
    set(root, 'completed', Number(progress.completed || 0).toLocaleString(locale === 'pl' ? 'pl-PL' : 'en-US'));
    set(root, 'total', Number(progress.total || 48).toLocaleString(locale === 'pl' ? 'pl-PL' : 'en-US'));
    set(root, 'remaining', Number(progress.remaining || 0).toLocaleString(locale === 'pl' ? 'pl-PL' : 'en-US'));
    set(root, 'progress', pct(ratio, 1));
    set(root, 'signature', String(report.candidate_signature || 'pending-first-run').slice(0, 16) + '…');
    set(root, 'holdout', holdout.accessed ? (locale === 'pl' ? 'otwarty' : 'opened') : (locale === 'pl' ? 'zapieczętowany' : 'sealed'));
    set(root, 'cagr', pct(leader.cagr, 1));
    set(root, 'sharpe', number(leader.sharpe_excess, 2));
    set(root, 'drawdown', pct(leader.max_drawdown, 1));
    set(root, 'gate', gate.passed ? (locale === 'pl' ? 'zaliczona' : 'passed') : (locale === 'pl' ? 'niezaliczona / w toku' : 'not passed / pending'));

    var bars = root.querySelectorAll('[data-v3-progress-bar]');
    Array.prototype.forEach.call(bars, function(bar){
      bar.style.width = (ratio * 100).toFixed(2) + '%';
      bar.setAttribute('aria-valuenow', String(Math.round(ratio * 100)));
    });
    root.classList.remove('is-loading');
  }

  fetch('/data/public/brace_spx_generation3_public.json?ts=' + Date.now(), {cache: 'no-store', credentials: 'same-origin'})
    .then(function(response){ if (!response.ok) throw new Error('HTTP ' + response.status); return response.json(); })
    .then(function(report){ roots.forEach(function(root){ render(root, report); }); })
    .catch(function(){
      roots.forEach(function(root){
        set(root, 'status', locale === 'pl' ? 'Oczekiwanie na pierwszy zweryfikowany przebieg' : 'Waiting for the first verified run');
        root.classList.remove('is-loading');
      });
    });
})();
