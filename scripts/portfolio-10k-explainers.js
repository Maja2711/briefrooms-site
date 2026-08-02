(() => {
  'use strict';
  const lang = window.BR_PORTFOLIO_10K?.lang === 'en' ? 'en' : 'pl';
  const copy = lang === 'pl' ? {
    signals: 'Wskaźniki są przeliczane w cotygodniowym przeglądzie. Wpływają na ocenę modelu i flagę TRZYMAJ / PRZEGLĄD / REDUKUJ, ale pojedynczy wskaźnik nie jest automatycznym zleceniem.',
    reports: 'Istotne raporty to zweryfikowane zdarzenia używane jako wejście do decyzji modelu. „Najnowsze informacje” są szerszą listą nagłówków źródłowych — nie każdy nagłówek zmienia decyzję.',
    paperControl: 'KONTROLA PAPER',
    paperTitle: 'BRACE steruje oddzielnym portfelem modelowym. Brak połączenia z rachunkiem brokerskim.',
    learningTitle: 'Stan pętli uczenia',
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
        const note = document.createElement('p'); note.className = 'signals-note'; note.textContent = copy.signals;
        signals.insertAdjacentElement('beforebegin', note);
      }
      card.querySelectorAll('.signal').forEach(signal => {
        signal.title = copy.signals;
        signal.setAttribute('aria-label', `${signal.textContent.trim()}. ${copy.signals}`);
      });
      const header = card.querySelector('.material-reports__header');
      if (header && !card.querySelector('.material-reports__explanation')) {
        const note = document.createElement('p'); note.className = 'material-reports__explanation'; note.textContent = copy.reports;
        header.insertAdjacentElement('afterend', note);
      }
    });
  }

  function applyMethodology() {
    document.querySelectorAll('#method .method-card').forEach((card, index) => {
      const paragraph = card.querySelector('p');
      if (paragraph && copy.methodology[index]) paragraph.textContent = copy.methodology[index];
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

  apply();
  applyMethodology();
  applyControlStatus();
  const positions = document.getElementById('positions');
  if (positions && typeof MutationObserver !== 'undefined') {
    new MutationObserver(() => apply()).observe(positions, {childList:true, subtree:true});
  }
  const method = document.getElementById('method');
  if (method && typeof MutationObserver !== 'undefined') {
    new MutationObserver(applyMethodology).observe(method, {childList:true, subtree:true});
  }
})();
