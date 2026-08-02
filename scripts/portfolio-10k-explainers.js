(() => {
  'use strict';
  const lang = window.BR_PORTFOLIO_10K?.lang === 'en' ? 'en' : 'pl';
  const copy = lang === 'pl' ? {
    signals: 'Wskaźniki są przeliczane w cotygodniowym przeglądzie. Wpływają na ocenę modelu i flagę TRZYMAJ / PRZEGLĄD / REDUKUJ, ale pojedynczy wskaźnik nie jest automatycznym zleceniem.',
    reports: 'Istotne raporty to zweryfikowane zdarzenia używane jako wejście do decyzji modelu. „Najnowsze informacje” są szerszą listą nagłówków źródłowych — nie każdy nagłówek zmienia decyzję.'
  } : {
    signals: 'Indicators are recalculated in the weekly review. They affect the model score and HOLD / REVIEW / REDUCE flag, but no single indicator is an automatic order.',
    reports: 'Material reports are verified events used as model-decision inputs. “Recent information” is a broader source-headline list — not every headline changes a decision.'
  };

  function apply(root = document) {
    root.querySelectorAll('.position').forEach(card => {
      const signals = card.querySelector('.signals');
      if (signals && !card.querySelector('.signals-note')) {
        const note = document.createElement('p'); note.className = 'signals-note'; note.textContent = copy.signals;
        signals.insertAdjacentElement('beforebegin', note);
      }
      card.querySelectorAll('.signal').forEach(signal => {
        signal.title = copy.signals; signal.setAttribute('aria-label', signal.textContent.trim());
      });
      const header = card.querySelector('.material-reports__header');
      if (header && !card.querySelector('.material-reports__explanation')) {
        const note = document.createElement('p'); note.className = 'material-reports__explanation'; note.textContent = copy.reports;
        header.insertAdjacentElement('afterend', note);
      }
    });
  }
  apply();
  const positions = document.getElementById('positions');
  if (positions && typeof MutationObserver !== 'undefined') new MutationObserver(() => apply()).observe(positions, {childList:true, subtree:true});
})();
