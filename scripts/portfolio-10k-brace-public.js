(() => {
  'use strict';

  const lang = window.BR_PORTFOLIO_10K?.lang === 'en' ? 'en' : 'pl';
  const locale = lang === 'en' ? 'en-US' : 'pl-PL';
  const T = lang === 'pl' ? {
    loading: 'BRACE buduje pierwszy przegląd…', unavailable: 'Dane BRACE są chwilowo niedostępne.',
    score: 'Conviction score', confidence: 'Pewność danych', regime: 'Reżim rynku', status: 'Status modelu',
    challenger: 'challenger / tryb równoległy', positions: 'Analiza pozycji', backtest: 'Test historyczny BRACE-Lite',
    decision: 'Decyzja', strongest: 'Najmocniejszy argument', weakest: 'Największe ryzyko', catalyst: 'Następny katalizator',
    thesisClock: 'Zegar tezy', evidence: 'Rejestr dowodów', contradictions: 'Sprzeczności do zbadania', noContradictions: 'Brak istotnych sprzeczności',
    noEvidence: 'Brak wystarczających dowodów do publikacji.', noBacktest: 'Backtest oczekuje na pierwszy pełny przebieg.',
    model: 'Model', cagr: 'CAGR', drawdown: 'Maks. obsunięcie', sharpe: 'Sharpe', turnover: 'Obrót roczny',
    generated: 'Aktualizacja', quality: 'jakość', decay: 'waga po wygaszeniu',
    promotionTitle: 'Bramka awansu champion–challenger', promoted: 'KANDYDAT DO POTWIERDZENIA LIVE', notPromoted: 'NIE AWANSOWAŁ', championLabel: 'Obecny champion',
    decisions: {
      ADD_REVIEW: 'PRZEGLĄD DOKUPIENIA', HOLD: 'TRZYMAJ', HOLD_BUILD_EVIDENCE: 'BUDUJ DOWODY',
      WAIT_FOR_EVENT: 'CZEKAJ NA WYDARZENIE', WAIT_INVESTIGATE: 'ZBADAJ SPRZECZNOŚCI',
      TRIM_REVIEW: 'PRZEGLĄD REDUKCJI', THESIS_REVIEW: 'PILNY PRZEGLĄD TEZY', EXIT_REVIEW: 'PRZEGLĄD WYJŚCIA'
    },
    pillars: {
      business_quality: 'Jakość biznesu', results_revisions: 'Wyniki i rewizje', attractiveness: 'Wycena',
      confirmation: 'Potwierdzenie rynku', risk: 'Odporność na ryzyko', context: 'Kontekst', events_information: 'Informacje'
    },
    models: { buy_hold: 'Buy & Hold', baseline: 'Model bazowy', brace_standard: 'BRACE-Lite', benchmark: 'Benchmark' }
  } : {
    loading: 'BRACE is building its first review…', unavailable: 'BRACE data is temporarily unavailable.',
    score: 'Conviction score', confidence: 'Data confidence', regime: 'Market regime', status: 'Model status',
    challenger: 'challenger / shadow mode', positions: 'Position analysis', backtest: 'BRACE-Lite historical test',
    decision: 'Decision', strongest: 'Strongest argument', weakest: 'Largest risk', catalyst: 'Next catalyst',
    thesisClock: 'Thesis clock', evidence: 'Evidence ledger', contradictions: 'Contradictions to investigate', noContradictions: 'No material contradictions',
    noEvidence: 'Insufficient evidence to publish.', noBacktest: 'The backtest is waiting for its first complete run.',
    model: 'Model', cagr: 'CAGR', drawdown: 'Max drawdown', sharpe: 'Sharpe', turnover: 'Annual turnover',
    generated: 'Updated', quality: 'quality', decay: 'decayed weight',
    promotionTitle: 'Champion–challenger promotion gate', promoted: 'ELIGIBLE FOR LIVE CONFIRMATION', notPromoted: 'NOT PROMOTED', championLabel: 'Current champion',
    decisions: {
      ADD_REVIEW: 'REVIEW ADDING', HOLD: 'HOLD', HOLD_BUILD_EVIDENCE: 'BUILD EVIDENCE',
      WAIT_FOR_EVENT: 'WAIT FOR EVENT', WAIT_INVESTIGATE: 'INVESTIGATE CONFLICT',
      TRIM_REVIEW: 'REVIEW TRIMMING', THESIS_REVIEW: 'URGENT THESIS REVIEW', EXIT_REVIEW: 'REVIEW EXIT'
    },
    pillars: {
      business_quality: 'Business quality', results_revisions: 'Results & revisions', attractiveness: 'Valuation',
      confirmation: 'Market confirmation', risk: 'Risk resilience', context: 'Context', events_information: 'Information'
    },
    models: { buy_hold: 'Buy & Hold', baseline: 'Baseline model', brace_standard: 'BRACE-Lite', benchmark: 'Benchmark' }
  };

  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const num = value => Number.isFinite(Number(value)) ? Number(value) : null;
  const pct = (value, digits = 1) => num(value) === null ? '—' : (num(value) * 100).toLocaleString(locale, {minimumFractionDigits: digits, maximumFractionDigits: digits}) + '%';
  const score = value => num(value) === null ? '—' : num(value).toLocaleString(locale, {minimumFractionDigits: 1, maximumFractionDigits: 1}) + '/100';
  const tone = value => num(value) === null ? 'neutral' : num(value) >= 70 ? 'positive' : num(value) < 45 ? 'negative' : 'neutral';
  const dateFmt = value => {
    if (!value) return '—';
    const d = new Date(String(value).length === 10 ? value + 'T12:00:00Z' : value);
    return Number.isNaN(d.valueOf()) ? esc(value) : d.toLocaleDateString(locale, {year:'numeric', month:'short', day:'2-digit'});
  };

  function summaryCard(label, value, sub, cls = '') {
    return `<article class="brace-kpi"><small>${esc(label)}</small><strong class="${esc(cls)}">${esc(value)}</strong><span>${esc(sub || '')}</span></article>`;
  }

  function renderSummary(data) {
    const box = document.getElementById('brace-summary');
    if (!box) return;
    const portfolio = data.portfolio || {};
    const context = data.market_context || {};
    const counts = Object.entries(portfolio.decision_counts || {}).map(([key, value]) => `${T.decisions[key] || key}: ${value}`).join(' · ');
    box.innerHTML = [
      summaryCard(T.score, score(portfolio.score), counts || T.challenger, tone(portfolio.score)),
      summaryCard(T.confidence, score(portfolio.confidence), `${Math.round((num(portfolio.positions_reviewed) || 0))} ${T.positions.toLowerCase()}`, tone(portfolio.confidence)),
      summaryCard(T.regime, String(context.regime || 'pending').replaceAll('_', ' '), context.market_date ? dateFmt(context.market_date) : '—'),
      summaryCard(T.status, data.status || 'initialising', T.challenger)
    ].join('');
    const meta = document.getElementById('brace-meta');
    if (meta) meta.textContent = `${T.generated}: ${dateFmt(data.generated_at)}`;
  }

  function pillarRows(pillars) {
    return Object.entries(T.pillars).map(([key, label]) => {
      const value = num(pillars?.[key]);
      const width = value === null ? 0 : Math.max(2, Math.min(100, value));
      return `<div class="brace-pillar"><span>${esc(label)}</span><div class="brace-track"><i style="width:${width}%"></i></div><b>${value === null ? '—' : value.toFixed(0)}</b></div>`;
    }).join('');
  }

  function evidenceRows(items) {
    if (!items?.length) return `<p class="brace-empty">${esc(T.noEvidence)}</p>`;
    return items.slice(0, 7).map(item => {
      const direction = num(item.direction) < 0 ? 'negative' : num(item.direction) > 0 ? 'positive' : 'neutral';
      const description = lang === 'pl' ? item.description_pl : item.description_en;
      return `<article class="brace-evidence ${direction}"><b>${esc(description || item.code)}</b><span>${esc(item.source || '')} · ${dateFmt(item.observed_at)} · ${esc(T.quality)} ${(num(item.quality) || 0).toFixed(2)} · ${esc(T.decay)} ${(num(item.decayed_weight) || 0).toFixed(2)}</span></article>`;
    }).join('');
  }

  function positionCard(position) {
    const decision = position.decision?.code || 'HOLD';
    const contradictions = position.contradictions || [];
    const clock = position.thesis_clock || {};
    const clockText = `${(num(clock.quarters_elapsed) || 0).toFixed(1)} / ${num(clock.target_quarters) || '—'} Q · ${String(clock.status || 'pending').replaceAll('_', ' ')}`;
    return `<article class="brace-position ${decision.includes('EXIT') || decision.includes('THESIS') ? 'alert' : decision.includes('TRIM') || decision.includes('WAIT') ? 'review' : ''}">
      <div class="brace-position-head"><div><div class="symbol">${esc(position.broker_symbol)}</div><h3>${esc(position.label)}</h3></div><span class="brace-decision ${esc(decision)}">${esc(T.decisions[decision] || decision)}</span></div>
      <div class="brace-scoreline"><div><small>${esc(T.score)}</small><strong class="${tone(position.score)}">${esc(score(position.score))}</strong></div><div><small>${esc(T.confidence)}</small><strong>${esc(score(position.confidence))}</strong></div></div>
      <div class="brace-pillars">${pillarRows(position.pillar_scores)}</div>
      <div class="brace-facts"><div><small>${esc(T.strongest)}</small><b>${esc(position.strongest_argument || '—')}</b></div><div><small>${esc(T.weakest)}</small><b>${esc(position.largest_risk || '—')}</b></div><div><small>${esc(T.catalyst)}</small><b>${esc(lang === 'pl' ? position.next_catalyst_pl : position.next_catalyst_en)}</b></div><div><small>${esc(T.thesisClock)}</small><b>${esc(clockText)}</b></div></div>
      <div class="brace-contradictions"><b>${esc(T.contradictions)}:</b> ${contradictions.length ? contradictions.map(x => `<span>${esc(String(x).replaceAll('_', ' '))}</span>`).join('') : `<em>${esc(T.noContradictions)}</em>`}</div>
      <details><summary>${esc(T.evidence)}</summary><div class="brace-evidence-list">${evidenceRows(position.evidence_ledger)}</div></details>
    </article>`;
  }

  function renderPositions(data) {
    const box = document.getElementById('brace-positions');
    if (!box) return;
    box.innerHTML = data.positions?.length ? data.positions.map(positionCard).join('') : `<div class="loading">${esc(T.loading)}</div>`;
    const note = document.getElementById('brace-note');
    const limitations = lang === 'pl' ? data.limitations_pl : data.limitations_en;
    if (note) note.textContent = (limitations || []).join(' ');
  }

  function metricCell(value, kind) {
    if (num(value) === null) return '—';
    return kind === 'ratio' ? num(value).toFixed(2) : pct(value, 1);
  }

  function promotionGate(gate) {
    if (!gate?.status) return '';
    const promoted = gate.status === 'eligible_for_live_confirmation';
    const reason = lang === 'pl' ? gate.reason_pl : gate.reason_en;
    const champion = T.models[gate.champion] || gate.champion || '—';
    return `<div class="status-note ${promoted ? 'ok' : ''}" style="margin:0 0 16px"><b>${esc(T.promotionTitle)} — ${esc(promoted ? T.promoted : T.notPromoted)}</b><br>${esc(reason || '')}<br><small>${esc(T.championLabel)}: ${esc(champion)}</small></div>`;
  }

  function renderBacktest(data) {
    const box = document.getElementById('brace-backtest');
    if (!box) return;
    const metrics = data.metrics || {};
    if (!Object.keys(metrics).length) {
      box.innerHTML = `<div class="loading">${esc(T.noBacktest)}</div>`;
      return;
    }
    const order = ['buy_hold', 'baseline', 'brace_standard', 'benchmark'];
    const rows = order.filter(key => metrics[key]).map(key => `<tr><td><b>${esc(T.models[key] || key)}</b></td><td>${metricCell(metrics[key].cagr)}</td><td>${metricCell(metrics[key].max_drawdown)}</td><td>${metricCell(metrics[key].sharpe_zero_rf, 'ratio')}</td><td>${metricCell(metrics[key].annualized_turnover)}</td></tr>`).join('');
    const method = lang === 'pl' ? data.methodology_pl : data.methodology_en;
    box.innerHTML = `${promotionGate(data.promotion_gate)}<div class="table-scroll"><table class="audit brace-table"><thead><tr><th>${esc(T.model)}</th><th>${esc(T.cagr)}</th><th>${esc(T.drawdown)}</th><th>${esc(T.sharpe)}</th><th>${esc(T.turnover)}</th></tr></thead><tbody>${rows}</tbody></table></div><p class="brace-method">${esc(method || '')}</p>`;
  }

  async function json(url) {
    const response = await fetch(url + '?v=' + Date.now(), {cache: 'no-store'});
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return response.json();
  }

  async function load() {
    const positions = document.getElementById('brace-positions');
    if (positions) positions.innerHTML = `<div class="loading">${esc(T.loading)}</div>`;
    const [live, backtest] = await Promise.allSettled([
      json('/data/investments/portfolio_10k_brace.json'),
      json('/data/investments/portfolio_10k_brace_backtest.json')
    ]);
    if (live.status === 'fulfilled') {
      renderSummary(live.value);
      renderPositions(live.value);
    } else if (positions) {
      positions.innerHTML = `<div class="error">${esc(T.unavailable)}</div>`;
    }
    renderBacktest(backtest.status === 'fulfilled' ? backtest.value : {});
  }

  load();
})();
