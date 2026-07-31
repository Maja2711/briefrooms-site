(() => {
  'use strict';

  const root = document.getElementById('brace-control-root');
  if (!root) return;
  const lang = window.BR_PORTFOLIO_10K?.lang === 'en' ? 'en' : 'pl';
  const locale = lang === 'pl' ? 'pl-PL' : 'en-US';
  const T = lang === 'pl' ? {
    status: 'Stan kontroli',
    champion: 'Champion',
    challenger: 'Challenger',
    progress: 'Postęp awansu',
    shadow: 'Tryb shadow',
    days: 'dni',
    decisions: 'decyzji',
    trades: 'zakończonych transakcji',
    risk: 'Ryzyko',
    target: 'Cel 10% rocznie',
    remaining: 'Pozostałe bramki',
    candidates: 'Najwyżej ocenieni kandydaci',
    pending: 'Decyzje shadow',
    history: 'Historia kontroli',
    noCandidates: 'Lista kandydatów pojawi się po pełnym cyklu analizy.',
    noDecisions: 'Brak rotacji spełniającej wszystkie warunki.',
    noHistory: 'BRACE nie przejął ani nie utracił kontroli.',
    loadError: 'Nie udało się pobrać publicznego statusu BRACE.',
    fallback: 'Powód trybu bezpiecznego',
    safe: 'Tryb bezpieczny',
    monitored: 'Limity monitorowane',
    baselineReturn: 'baseline',
    shadowReturn: 'shadow',
    action: 'Akcja',
    confidence: 'Pewność',
    paperOnly: 'Wyłącznie portfel modelowy. Brak połączenia z rachunkiem brokerskim.'
  } : {
    status: 'Control state',
    champion: 'Champion',
    challenger: 'Challenger',
    progress: 'Promotion progress',
    shadow: 'Shadow mode',
    days: 'days',
    decisions: 'decisions',
    trades: 'completed trades',
    risk: 'Risk',
    target: '10% annual target',
    remaining: 'Remaining gates',
    candidates: 'Top-ranked candidates',
    pending: 'Shadow decisions',
    history: 'Control history',
    noCandidates: 'Candidates will appear after the full analysis cycle.',
    noDecisions: 'No rotation currently passes every gate.',
    noHistory: 'BRACE has not gained or lost control.',
    loadError: 'The public BRACE status could not be loaded.',
    fallback: 'Safe-mode reason',
    safe: 'Safe mode',
    monitored: 'Limits monitored',
    baselineReturn: 'baseline',
    shadowReturn: 'shadow',
    action: 'Action',
    confidence: 'Confidence',
    paperOnly: 'Model portfolio only. No brokerage-account connection.'
  };

  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);
  const num = value => Number.isFinite(Number(value)) ? Number(value) : null;
  const pct = value => {
    const number = num(value);
    return number === null ? '—' : `${(number * 100).toLocaleString(locale, {maximumFractionDigits: 2})}%`;
  };
  const dateTime = value => {
    if (!value) return '—';
    const parsed = new Date(value);
    return Number.isNaN(parsed.valueOf()) ? esc(value) : parsed.toLocaleString(locale, {
      year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
    });
  };
  const human = value => String(value || '—').replaceAll('_', ' ').toLowerCase();
  const targetLabel = value => {
    const labels = lang === 'pl' ? {
      TARGET_CURRENTLY_JUSTIFIED_WITHIN_MODEL: 'cel obecnie uzasadniony w modelu',
      TARGET_NOT_CURRENTLY_JUSTIFIED: 'cel obecnie nieuzasadniony',
      TARGET_REQUIRES_EXCESSIVE_RISK: 'cel wymaga nadmiernego ryzyka'
    } : {
      TARGET_CURRENTLY_JUSTIFIED_WITHIN_MODEL: 'currently justified in the model',
      TARGET_NOT_CURRENTLY_JUSTIFIED: 'not currently justified',
      TARGET_REQUIRES_EXCESSIVE_RISK: 'requires excessive risk'
    };
    return labels[value] || human(value);
  };
  const gateLabel = value => {
    if (lang !== 'pl') return human(value);
    const labels = {
      out_of_sample_beats_baseline: 'wynik poza próbą lepszy od baseline',
      parameter_neighborhood_stable: 'stabilne sąsiedztwo parametrów',
      expected_shortfall_within_limit: 'expected shortfall w limicie',
      minimum_calendar_days: 'minimalny okres kalendarzowy shadow',
      minimum_decisions: 'minimalna liczba decyzji shadow',
      minimum_completed_trades: 'minimalna liczba zakończonych transakcji paper',
      risk_adjusted_advantage: 'przewaga po uwzględnieniu ryzyka',
      confidence_interval_positive: 'dodatni przedział ufności przewagi'
    };
    return labels[value] || human(value);
  };
  const tone = status => {
    if (/FALLBACK|SAFE|DEGRADED|SUSPENDED/.test(status)) return 'danger';
    if (/ACTIVE_PAPER|PROBATIONARY/.test(status)) return 'active';
    return 'shadow';
  };
  const metric = (label, value, sub = '') =>
    `<article class="control-metric"><small>${esc(label)}</small><strong>${esc(value)}</strong><span>${esc(sub)}</span></article>`;

  function candidateRows(items) {
    if (!items?.length) return `<p class="brace-empty">${esc(T.noCandidates)}</p>`;
    const ranked = [...items].sort(
      (left, right) => (num(right.final_score) ?? -Infinity) - (num(left.final_score) ?? -Infinity)
    );
    return `<div class="control-list">${ranked.slice(0, 5).map(item => `
      <article>
        <div><b>${esc(item.broker_symbol || item.instrument_id)}</b><span>${esc(item.label || '')}</span></div>
        <strong>${num(item.final_score) === null ? '—' : `${num(item.final_score).toFixed(1)}/100`}</strong>
      </article>`).join('')}</div>`;
  }

  function decisionRows(items) {
    if (!items?.length) return `<p class="brace-empty">${esc(T.noDecisions)}</p>`;
    return `<div class="control-list">${items.slice(0, 5).map(item => `
      <article>
        <div><b>${esc(item.action)}</b><span>${esc(
          lang === 'pl' ? item.rationale_pl : item.rationale_en
        )}</span></div>
        <strong>${esc(T.confidence)} ${pct(item.confidence)}</strong>
      </article>`).join('')}</div>`;
  }

  function historyRows(items) {
    if (!items?.length) return `<p class="brace-empty">${esc(T.noHistory)}</p>`;
    return `<div class="control-history">${[...items].reverse().slice(0, 5).map(item => `
      <article><time>${dateTime(item.evaluated_at)}</time><b>${esc(item.previous_status)} → ${esc(item.new_status)}</b><span>${esc(item.reason || '')}</span></article>
    `).join('')}</div>`;
  }

  function render(data) {
    const progress = data.promotion_progress || {};
    const shadow = data.shadow || {};
    const risk = data.risk || {};
    const target = data.target || {};
    const remaining = (progress.remaining || []).slice(0, 10);
    document.getElementById('brace-control-updated').textContent = dateTime(data.generated_at);
    root.innerHTML = `
      <div class="control-status ${tone(data.controller_status)}">
        <span>${esc(T.status)}</span>
        <strong>${esc(data.display_status || data.controller_status)}</strong>
        <small>${esc(T.paperOnly)}</small>
      </div>
      <div class="control-metrics">
        ${metric(T.champion, `${data.champion?.methodology_id || '—'} ${data.champion?.version || ''}`, data.champion?.status || '')}
        ${metric(T.challenger, `${data.challenger?.methodology_id || '—'} ${data.challenger?.version || ''}`, data.challenger?.status || '')}
        ${metric(T.shadow, `${shadow.calendar_days || 0} ${T.days}`, `${shadow.decisions || 0} ${T.decisions} · ${shadow.completed_trades || 0} ${T.trades}`)}
        ${metric(T.risk, risk.safe_mode ? T.safe : T.monitored, risk.status || '')}
        ${metric(T.target, targetLabel(target.status), `P: ${pct(target.probability_of_reaching_target)}`)}
      </div>
      <section class="control-progress">
        <div><b>${esc(T.progress)}</b><span>${esc(`${progress.passed || 0}/${progress.total || 0} · ${progress.percentage || 0}%`)}</span></div>
        <div class="control-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Number(progress.percentage) || 0}"><i style="width:${Math.max(0, Math.min(100, Number(progress.percentage) || 0))}%"></i></div>
        <p><strong>${esc(T.shadowReturn)}:</strong> ${pct(shadow.shadow_return)} · <strong>${esc(T.baselineReturn)}:</strong> ${pct(shadow.baseline_return)}</p>
      </section>
      ${data.fallback_reason ? `<div class="control-alert"><b>${esc(T.fallback)}</b><span>${esc(data.fallback_reason)}</span></div>` : ''}
      <div class="control-columns">
        <section><h3>${esc(T.remaining)}</h3>${remaining.length ? `<ul>${remaining.map(item => `<li>${esc(gateLabel(item))}</li>`).join('')}</ul>` : '<p>—</p>'}</section>
        <section><h3>${esc(T.candidates)}</h3>${candidateRows(data.candidates)}</section>
        <section><h3>${esc(T.pending)}</h3>${decisionRows(data.pending_decisions)}</section>
      </div>
      <section class="control-history-wrap"><h3>${esc(T.history)}</h3>${historyRows(data.promotion_history)}</section>
      <p class="brace-method">${esc(lang === 'pl' ? data.disclaimer_pl : data.disclaimer_en)}</p>`;
  }

  async function load() {
    try {
      const response = await fetch(`/data/portfolio10k/public/brace_engine_public.json?v=${Date.now()}`, {cache: 'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      render(await response.json());
    } catch (error) {
      root.innerHTML = `<div class="error">${esc(T.loadError)}</div>`;
    }
  }

  load();
})();
