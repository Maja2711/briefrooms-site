(() => {
  'use strict';
  const lang = window.BR_PORTFOLIO_10K?.lang === 'en' ? 'en' : 'pl';
  const portfolioPath = lang === 'en'
    ? '/data/investments/portfolio_10k_usd.json'
    : '/data/investments/portfolio_10k.json';
  const copy = lang === 'pl' ? {
    signals: 'Wskaźniki są przeliczane w cotygodniowym przeglądzie. Wpływają na ocenę modelu i flagę TRZYMAJ / PRZEGLĄD / REDUKUJ, ale pojedynczy wskaźnik nie jest automatycznym zleceniem.',
    reports: 'Istotne raporty to zweryfikowane zdarzenia używane jako wejście do decyzji modelu. „Najnowsze informacje” są szerszą listą nagłówków źródłowych — nie każdy nagłówek zmienia decyzję.',
    paperControl: 'KONTROLA PAPER',
    paperTitle: 'BRACE steruje oddzielnym portfelem modelowym. Brak połączenia z rachunkiem brokerskim.',
    learningTitle: 'Stan pętli uczenia',
    chartLabel: 'Rzeczywista ścieżka wartości portfela od kapitału startowego',
    methodology: [
      'Aktualizacja cen instrumentów co godzinę podczas ich sesji oraz kursów walut wymaganych do raportowania.',
      'Sprawdzenie kalendarza wyników, zweryfikowanych raportów istotnych i nagłówków dotyczących prognoz, regulacji, marż, produktów i badań klinicznych. Raporty istotne trafiają bezpośrednio do oceny BRACE.',
      'Cotygodniowe przeliczenie trendu 50/200 sesji, momentum 6M, zmienności, obsunięcia i odchylenia udziału od celu. Wskaźniki zmieniają ocenę oraz rekomendację, lecz nie są pojedynczym automatycznym zleceniem.',
      'BRACE podejmuje decyzję paper dopiero po połączeniu tezy, raportów istotnych, wskaźników, kosztów i limitów ryzyka. Możliwe działania: TRZYMAJ, OBSERWUJ, REDUKUJ, WYJDŹ, DOKUP lub ZAMIEŃ.'
    ]
  } : {
    signals: 'Indicators are recalculated in the weekly review. They affect the model score and HOLD / REVIEW / REDUCE flag, but no single indicator is an automatic order.',
    reports: 'Material reports are verified events used as model-decision inputs. “Recent information” is a broader source-headline list — not every headline changes a decision.',
    paperControl: 'PAPER CONTROL',
    paperTitle: 'BRACE controls a separate model portfolio. No brokerage-account connection.',
    learningTitle: 'Learning-loop status',
    chartLabel: 'Actual portfolio-value path from starting capital',
    methodology: [
      'Refresh instrument prices hourly during their trading sessions and update the FX rates required for reporting.',
      'Check earnings dates, verified material reports and source headlines concerning guidance, regulation, margins, products and clinical trials. Material reports feed directly into the BRACE assessment.',
      'Recalculate the 50/200-session trend, six-month momentum, volatility, drawdown and target-weight deviation every week. Indicators change the score and recommendation, but no single indicator is an automatic order.',
      'BRACE makes a paper decision only after combining the thesis, material reports, indicators, costs and risk limits. Available actions are HOLD, WATCH, REDUCE, EXIT, ADD and REPLACE.'
    ]
  };

  function apply(root = document) {
    root.querySelectorAll('.position').forEach(card => {
      const signals = card.querySelector('.signals');
      if (signals && !card.querySelector('.signals-note')) {
        const note = document.createElement('p');
        note.className = 'signals-note';
        note.textContent = copy.signals;
        signals.insertAdjacentElement('beforebegin', note);
      }
      card.querySelectorAll('.signal').forEach(signal => {
        signal.title = copy.signals;
        signal.setAttribute('aria-label', `${signal.textContent.trim()}. ${copy.signals}`);
      });
      const header = card.querySelector('.material-reports__header');
      if (header && !card.querySelector('.material-reports__explanation')) {
        const note = document.createElement('p');
        note.className = 'material-reports__explanation';
        note.textContent = copy.reports;
        header.insertAdjacentElement('afterend', note);
      }
    });
  }

  function applyMethodology() {
    document.querySelectorAll('#method .method-card').forEach((card, index) => {
      const paragraph = card.querySelector('p');
      const expected = copy.methodology[index];
      if (paragraph && expected && paragraph.textContent !== expected) {
        paragraph.textContent = expected;
      }
    });
  }

  function renderLearning(data) {
    const learning = data?.learning_loop;
    const section = document.querySelector('#brace-control-root .control-learning');
    if (!learning || !section || section.querySelector('.learning-state-explanation')) return;
    const note = document.createElement('p');
    note.className = 'learning-state-explanation';
    note.innerHTML = `<b>${copy.learningTitle}:</b> ${lang === 'pl' ? learning.explanation_pl : learning.explanation_en}`;
    section.appendChild(note);
  }

  function numericValue(snapshot) {
    const value = snapshot?.total_value_pln ?? snapshot?.total_value_usd;
    const number = Number(value);
    return Number.isFinite(number) && number > 0 ? number : null;
  }

  function sample(values, maximum = 56) {
    if (values.length <= maximum) return values;
    const result = [];
    for (let index = 0; index < maximum; index += 1) {
      const sourceIndex = Math.round(index * (values.length - 1) / (maximum - 1));
      result.push(values[sourceIndex]);
    }
    return result;
  }

  function renderMiniChart(portfolio) {
    const root = document.getElementById('mini-chart');
    if (!root) return;
    const start = Number(portfolio?.starting_capital_pln ?? portfolio?.starting_capital_usd);
    if (!Number.isFinite(start) || start <= 0) return;

    const raw = [start];
    for (const snapshot of portfolio?.snapshots || []) {
      const value = numericValue(snapshot);
      if (value !== null && value !== raw[raw.length - 1]) raw.push(value);
    }
    const current = Number(portfolio?.total_value_pln ?? portfolio?.total_value_usd);
    if (Number.isFinite(current) && current > 0 && current !== raw[raw.length - 1]) raw.push(current);
    const values = sample(raw);
    if (values.length < 2) values.push(start);

    const width = 600;
    const height = 120;
    const paddingX = 5;
    const paddingY = 12;
    const minimum = Math.min(...values, start);
    const maximum = Math.max(...values, start);
    const spread = Math.max(maximum - minimum, start * 0.004);
    const low = minimum - spread * 0.12;
    const high = maximum + spread * 0.12;
    const x = index => paddingX + (width - paddingX * 2) * index / Math.max(values.length - 1, 1);
    const y = value => paddingY + (height - paddingY * 2) * (high - value) / Math.max(high - low, 1e-9);
    const line = values.map((value, index) => `${index ? 'L' : 'M'}${x(index).toFixed(1)} ${y(value).toFixed(1)}`).join(' ');
    const area = `${line} L${x(values.length - 1).toFixed(1)} ${height} L${x(0).toFixed(1)} ${height} Z`;
    const finalValue = values[values.length - 1];
    const positive = finalValue >= start;
    const color = positive ? '#15964d' : '#d33f57';
    const baselineY = y(start).toFixed(1);

    root.innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="${copy.chartLabel}">
      <defs><linearGradient id="portfolioMiniGradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".23"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
      <line x1="0" y1="${baselineY}" x2="${width}" y2="${baselineY}" stroke="#9aa8b8" stroke-width="1" stroke-dasharray="5 5" opacity=".65"/>
      <path fill="url(#portfolioMiniGradient)" d="${area}"/>
      <path fill="none" stroke="${color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" d="${line}"/>
    </svg>`;
    root.dataset.chartSource = 'portfolio-snapshots';
    root.dataset.chartDirection = positive ? 'positive' : 'negative';
  }

  async function loadMiniChart() {
    try {
      const response = await fetch(`${portfolioPath}?v=${Date.now()}`, {cache: 'no-store'});
      if (!response.ok) return;
      renderMiniChart(await response.json());
    } catch (_) {
      // Keep the base chart when portfolio data is temporarily unavailable.
    }
  }

  async function applyControlStatus() {
    try {
      const response = await fetch(`/data/portfolio10k/public/brace_engine_public.json?v=${Date.now()}`, {cache:'no-store'});
      if (!response.ok) return;
      const data = await response.json();
      if (/PROBATIONARY_CONTROL|ACTIVE_PAPER_CONTROL/.test(String(data.controller_status || ''))) {
        document.querySelectorAll('.active-chip, .side-score-head span').forEach(node => {
          node.textContent = copy.paperControl;
          node.title = copy.paperTitle;
          node.dataset.controlStatus = data.controller_status;
        });
      }
      const controlRoot = document.getElementById('brace-control-root');
      renderLearning(data);
      if (controlRoot && typeof MutationObserver !== 'undefined') {
        const observer = new MutationObserver(() => renderLearning(data));
        observer.observe(controlRoot, {childList:true, subtree:true});
      }
    } catch (_) {
      // The base page remains usable when the control-status endpoint is unavailable.
    }
  }

  function start() {
    apply();
    applyMethodology();
    applyControlStatus();
    loadMiniChart();

    const positions = document.getElementById('positions');
    if (positions && typeof MutationObserver !== 'undefined') {
      let scheduled = false;
      new MutationObserver(() => {
        if (scheduled) return;
        scheduled = true;
        requestAnimationFrame(() => {
          scheduled = false;
          apply();
        });
      }).observe(positions, {childList:true, subtree:true});
    }

    const method = document.getElementById('method');
    if (method && typeof MutationObserver !== 'undefined') {
      let scheduled = false;
      new MutationObserver(() => {
        if (scheduled) return;
        scheduled = true;
        requestAnimationFrame(() => {
          scheduled = false;
          applyMethodology();
        });
      }).observe(method, {childList:true, subtree:true});
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once:true});
  else start();
})();
