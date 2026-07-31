(function(){
  'use strict';

  var roots = Array.prototype.slice.call(document.querySelectorAll('[data-brace-spx-generation3],[data-brace-spx-generation4]'));
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
    var nodes = root.querySelectorAll('[data-v3-field="' + field + '"],[data-v4-field="' + field + '"]');
    Array.prototype.forEach.call(nodes, function(node){ node.textContent = value == null ? '—' : String(value); });
  }

  function updateStaticCopy(root, report) {
    root.setAttribute('data-brace-spx-generation4', 'true');
    var heading = root.querySelector('h2');
    if (heading) heading.textContent = locale === 'pl' ? 'BRACE-SPX LAB — Generacja 4' : 'BRACE-SPX LAB — Generation 4';
    var note = root.querySelector('.brace-note') || root.querySelector('p');
    if (note) {
      note.textContent = locale === 'pl'
        ? 'Zamknięta przestrzeń 16 strukturalnie różnych konstrukcji dla SPY: trend, breadth, kredyt, zmienność i stopy, recovery oraz z góry określone ensemble. Generacja 3 nie była dalej strojona, a holdout pozostał zamknięty.'
        : 'A closed space of 16 structurally different SPY constructions: trend, breadth, credit, volatility and rates, recovery, and predeclared ensembles. Generation 3 was not tuned further and the holdout remained sealed.';
    }
    var diagnostics = root.querySelector('[data-v4-diagnostics]');
    if (!diagnostics) {
      diagnostics = document.createElement('p');
      diagnostics.setAttribute('data-v4-diagnostics', 'true');
      diagnostics.className = 'brace-note';
      root.appendChild(diagnostics);
    }
    var values = report.overfitting_and_diversity || {};
    diagnostics.textContent = locale === 'pl'
      ? 'PBO: ' + pct(values.pbo_probability, 1) + ' · mediana |korelacji|: ' + pct(values.median_absolute_pairwise_correlation, 1) + ' · efektywni niezależni kandydaci: ' + number(values.effective_independent_candidates, 2)
      : 'PBO: ' + pct(values.pbo_probability, 1) + ' · median |correlation|: ' + pct(values.median_absolute_pairwise_correlation, 1) + ' · effective independent candidates: ' + number(values.effective_independent_candidates, 2);
  }

  function render(root, report) {
    var progress = report.progress || {};
    var leader = report.selected_development_result || report.development_leader || {};
    var holdout = report.holdout || {};
    var gate = report.strict_gate || {};
    var total = Number(progress.total || 16);
    var completed = Number(progress.completed || 0);
    var ratio = Number(progress.ratio);
    if (!Number.isFinite(ratio)) ratio = total > 0 ? completed / total : 0;
    ratio = Math.max(0, Math.min(1, ratio));

    updateStaticCopy(root, report);
    set(root, 'generation', report.generation_id || 'spx-diversified-v4');
    set(root, 'status', labelStatus(report.status));
    set(root, 'completed', completed.toLocaleString(locale === 'pl' ? 'pl-PL' : 'en-US'));
    set(root, 'total', total.toLocaleString(locale === 'pl' ? 'pl-PL' : 'en-US'));
    set(root, 'remaining', Number(progress.remaining || 0).toLocaleString(locale === 'pl' ? 'pl-PL' : 'en-US'));
    set(root, 'progress', pct(ratio, 1));
    set(root, 'signature', String(report.candidate_signature || 'pending-first-run').slice(0, 16) + '…');
    set(root, 'holdout', holdout.accessed ? (locale === 'pl' ? 'otwarty' : 'opened') : (locale === 'pl' ? 'zapieczętowany' : 'sealed'));
    set(root, 'cagr', pct(leader.cagr, 1));
    set(root, 'sharpe', number(leader.sharpe_excess, 2));
    set(root, 'drawdown', pct(leader.max_drawdown, 1));
    set(root, 'gate', gate.passed ? (locale === 'pl' ? 'zaliczona' : 'passed') : (locale === 'pl' ? 'niezaliczona' : 'not passed'));

    var bars = root.querySelectorAll('[data-v3-progress-bar],[data-v4-progress-bar]');
    Array.prototype.forEach.call(bars, function(bar){
      bar.style.width = (ratio * 100).toFixed(2) + '%';
      bar.setAttribute('aria-valuenow', String(Math.round(ratio * 100)));
    });
    root.classList.remove('is-loading');
  }

  function load(url) {
    return fetch(url + '?ts=' + Date.now(), {cache: 'no-store', credentials: 'same-origin'})
      .then(function(response){ if (!response.ok) throw new Error('HTTP ' + response.status); return response.json(); });
  }

  load('/data/public/brace_spx_generation4_public.json')
    .catch(function(){ return load('/data/public/brace_spx_generation3_public.json'); })
    .then(function(report){ roots.forEach(function(root){ render(root, report); }); })
    .catch(function(){
      roots.forEach(function(root){
        set(root, 'status', locale === 'pl' ? 'Brak zweryfikowanego raportu' : 'No verified report');
        root.classList.remove('is-loading');
      });
    });
})();
