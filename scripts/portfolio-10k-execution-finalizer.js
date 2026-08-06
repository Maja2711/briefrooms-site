(() => {
  'use strict';

  // Compatibility entry point kept intentionally small. The portfolio JSON and
  // the bilingual dashboard controller remain the only sources of live values.
  // This script only removes static Polish loading placeholders that otherwise
  // remain after the data-driven parts of the room are ready.

  const isPolish = (window.BR_PORTFOLIO_10K?.lang || document.documentElement.lang)
    .toLowerCase()
    .startsWith('pl');

  if (!isPolish) return;

  function replacePolishPlaceholders() {
    const projectionOverview = document.getElementById('projection-overview');
    if (projectionOverview && /ładowanie|sprawdzanie/i.test(projectionOverview.textContent || '')) {
      projectionOverview.innerHTML = `
        <div><b>Scenariusz bazowy</b><span>warunki, katalizatory i ryzyka</span></div>
        <div><b>Wariant wzrostowy / spadkowy</b><span>jawne założenia zamiast jednej ceny docelowej</span></div>
        <div><b>Ocena trafności</b><span>kalibracja i późniejszy pomiar wyników</span></div>`;
    }

    const projectionsPanel = document.querySelector('.i10k-panel[data-panel="projections"] .page-card');
    if (projectionsPanel && /ładowanie|sprawdzanie/i.test(projectionsPanel.textContent || '')) {
      projectionsPanel.innerHTML = `
        <div class="card-head">
          <div>
            <h2>PROJEKCJE</h2>
            <p>Ta sekcja nie prezentuje arbitralnej prognozy. Scenariusze będą publikowane wraz z założeniami, poziomem pewności i późniejszą oceną trafności.</p>
          </div>
        </div>
        <div class="projection-policy">
          <div><b>Scenariusz bazowy</b><span>warunki, katalizatory i ryzyka</span></div>
          <div><b>Wariant wzrostowy / spadkowy</b><span>jawne założenia, nie jedna cena docelowa</span></div>
          <div><b>Pewność</b><span>kalibracja na wynikach historycznych</span></div>
          <div><b>Trafność</b><span>Brier score i trafność przedziałów</span></div>
        </div>`;
    }

    const braceImpact = document.getElementById('brace-impact');
    if (braceImpact && /ładowanie|sprawdzanie/i.test(braceImpact.textContent || '')) {
      braceImpact.textContent = 'Ocena BRACE jest aktualizowana niezależnie od bieżących danych portfela.';
    }

    document.body.dataset.investmentPlaceholders = 'finalized';
  }

  const schedule = () => window.setTimeout(replacePolishPlaceholders, 500);
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', schedule, { once: true });
  } else {
    schedule();
  }
})();
