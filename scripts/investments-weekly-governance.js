(function () {
  'use strict';
  const lang = (window.BR_WEEKLY || {}).lang === 'en' ? 'en' : 'pl';
  const copy = lang === 'pl' ? {
    modelName: 'WES — Weekly Engine Strategy',
    modelKicker: 'Tygodniowy model decyzyjny BriefRooms',
    about: 'O modelu WES', aboutClose: 'Zwiń opis',
    methodology: 'Metodologia',
    aboutLead: 'WES (Weekly Engine Strategy) to adaptacyjny model tygodniowy, który buduje i prowadzi pozycje paper-trading według zamrożonej decyzji, zasad ryzyka oraz regularnej weryfikacji tezy.',
    learning: 'Model jest stale ulepszany i uczy się z zapisanych, weryfikowalnych wyników. Porównuje skuteczność metod i kontekstów, ale zmiany pozostają ograniczone przez bramki bezpieczeństwa — bez samowolnego obchodzenia zasad ryzyka.',
    futureTitle: 'Docelowa architektura warstwowa',
    futureText: 'Dane i wyspecjalizowane adaptery będą zasilać AI-BRACE Adapter oraz Belief Core. Belief Core oceni evidence, sprzeczności, prawdopodobieństwo i kalibrację, a WES pozostanie warstwą decyzji, zarządzania ryzykiem i wykonania.',
    layerAdapters: 'Market / News / Macro adapters',
    layerBrace: 'AI-BRACE Adapter',
    layerBelief: 'Belief Core',
    layerWes: 'WES · decyzja + ryzyko',
    safetyTitle: 'Zasady bezpieczeństwa i wykonania WES',
    expand: 'Rozwiń', collapse: 'Zwiń',
    gate: 'Wspólna bramka walidacyjna',
    gateText: 'Każda warstwa modelu podlega temu samemu dopuszczeniu instrumentu do nowych wejść.',
    timing: 'Zakaz wejść wstecznych',
    timingText: 'Cena wejścia musi pochodzić z pierwszej zakończonej świecy 5-minutowej nie wcześniejszej niż zamrożona decyzja.',
    lock: 'Blokada po unieważnieniu tezy',
    lockText: 'Wyjście po zdarzeniu materialnym lub unieważnieniu tezy blokuje ponowne wejście do końca tygodnia.',
    integrity: 'Kontrola spójności publikacji',
    integrityText: 'Rekord z niespójną chronologią, cenami albo wynikiem jest automatycznie wyłączany z karty, historii i podsumowania.',
    passed: 'zasada aktywna'
  } : {
    modelName: 'WES — Weekly Engine Strategy',
    modelKicker: 'BriefRooms weekly decision model',
    about: 'About WES', aboutClose: 'Hide description',
    methodology: 'Methodology',
    aboutLead: 'WES (Weekly Engine Strategy) is an adaptive weekly model that builds and manages paper-trading positions using a frozen decision, governed risk rules, and regular thesis verification.',
    learning: 'The model is continuously improved and learns from recorded, verifiable outcomes. It compares methods and market contexts, while all changes remain bounded by safety gates — it cannot silently bypass risk rules.',
    futureTitle: 'Target layered architecture',
    futureText: 'Market data and specialised adapters will feed an AI-BRACE Adapter and Belief Core. Belief Core will evaluate evidence, contradictions, probability and calibration, while WES remains the decision, risk-management and execution layer.',
    layerAdapters: 'Market / News / Macro adapters',
    layerBrace: 'AI-BRACE Adapter',
    layerBelief: 'Belief Core',
    layerWes: 'WES · decision + risk',
    safetyTitle: 'WES safety & execution rules',
    expand: 'Expand', collapse: 'Collapse',
    gate: 'Shared validation gate',
    gateText: 'Every model layer is subject to the same approval for new instrument entries.',
    timing: 'No backdated entries',
    timingText: 'Entry must use the first completed five-minute bar at or after the frozen decision timestamp.',
    lock: 'Thesis-invalidation lock',
    lockText: 'A material-event or thesis-invalidation exit blocks re-entry until the end of the week.',
    integrity: 'Publication integrity gate',
    integrityText: 'A record with inconsistent chronology, prices, or P/L is automatically excluded from the card, history, and totals.',
    passed: 'rule active'
  };

  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
  const row = (title, text) => `<article class="governance-rule"><b>${esc(title)}</b><span>${esc(text)}</span><small>✓ ${esc(copy.passed)}</small></article>`;
  const layer = (text, cls) => `<span class="wes-layer ${cls || ''}">${esc(text)}</span>`;

  function mount(week) {
    const app = document.getElementById('app');
    if (!app) return;
    document.getElementById('weekly-governance-live')?.remove();
    const exposureLayer = week?.multi_instrument_exposure_layer || {};
    const version = exposureLayer.version || week?.method_version || '5.0.0-experimental';
    const section = document.createElement('section');
    section.id = 'weekly-governance-live';
    section.className = 'panel governance-panel';
    section.innerHTML = `
      <details class="wes-about-details">
        <summary>
          <span class="wes-identity">
            <span class="wes-badge">WES</span>
            <span class="wes-identity-copy"><b>${esc(copy.modelName)}</b><small>${esc(copy.modelKicker)} · ${esc(copy.methodology)}: ${esc(version)}</small></span>
          </span>
          <span class="wes-about-toggle"><span class="when-closed">${esc(copy.about)}</span><span class="when-open">${esc(copy.aboutClose)}</span></span>
        </summary>
        <div class="wes-about-body">
          <p>${esc(copy.aboutLead)}</p>
          <p>${esc(copy.learning)}</p>
          <div class="wes-future"><b>${esc(copy.futureTitle)}</b><span>${esc(copy.futureText)}</span></div>
          <div class="wes-layers" aria-label="${esc(copy.futureTitle)}">
            ${layer(copy.layerAdapters, 'source')}
            <span class="wes-arrow">→</span>
            ${layer(copy.layerBrace, 'ai')}
            <span class="wes-arrow">→</span>
            ${layer(copy.layerBelief, 'belief')}
            <span class="wes-arrow">→</span>
            ${layer(copy.layerWes, 'wes')}
          </div>
        </div>
      </details>
      <details class="governance-details">
        <summary><span><b>${esc(copy.safetyTitle)}</b><small>${esc(copy.methodology)}: ${esc(version)}</small></span><span class="governance-toggle"><span class="when-closed">${esc(copy.expand)}</span><span class="when-open">${esc(copy.collapse)}</span></span></summary>
        <div class="governance-grid">${row(copy.gate, copy.gateText)}${row(copy.timing, copy.timingText)}${row(copy.lock, copy.lockText)}${row(copy.integrity, copy.integrityText)}</div>
      </details>`;
    app.prepend(section);
  }

  document.addEventListener('br:weekly-rendered', (event) => mount(event.detail || {}));
}());
