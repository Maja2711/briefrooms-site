(function () {
  'use strict';
  const lang = (window.BR_WEEKLY || {}).lang === 'en' ? 'en' : 'pl';
  const copy = lang === 'pl' ? {
    title: 'Zasady bezpieczeństwa modelu v5',
    expand: 'Rozwiń', collapse: 'Zwiń',
    gate: 'Wspólna bramka walidacyjna',
    gateText: 'Każda warstwa modelu podlega temu samemu dopuszczeniu instrumentu do nowych wejść.',
    timing: 'Zakaz wejść wstecznych',
    timingText: 'Cena wejścia musi pochodzić z pierwszej zakończonej świecy 5-minutowej nie wcześniejszej niż zamrożona decyzja.',
    lock: 'Blokada po unieważnieniu tezy',
    lockText: 'Wyjście po zdarzeniu materialnym lub unieważnieniu tezy blokuje ponowne wejście do końca tygodnia.',
    integrity: 'Kontrola spójności publikacji',
    integrityText: 'Rekord z niespójną chronologią, cenami albo wynikiem jest automatycznie wyłączany z karty, historii i podsumowania.',
    version: 'Wersja', passed: 'zasada aktywna'
  } : {
    title: 'Model v5 safety rules',
    expand: 'Expand', collapse: 'Collapse',
    gate: 'Shared validation gate',
    gateText: 'Every model layer is subject to the same approval for new instrument entries.',
    timing: 'No backdated entries',
    timingText: 'Entry must use the first completed five-minute bar at or after the frozen decision timestamp.',
    lock: 'Thesis-invalidation lock',
    lockText: 'A material-event or thesis-invalidation exit blocks re-entry until the end of the week.',
    integrity: 'Publication integrity gate',
    integrityText: 'A record with inconsistent chronology, prices, or P/L is automatically excluded from the card, history, and totals.',
    version: 'Version', passed: 'rule active'
  };

  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
  const row = (title, text) => `<article class="governance-rule"><b>${esc(title)}</b><span>${esc(text)}</span><small>✓ ${esc(copy.passed)}</small></article>`;

  function mount(week) {
    const app = document.getElementById('app');
    if (!app) return;
    document.getElementById('weekly-governance-live')?.remove();
    const layer = week?.multi_instrument_exposure_layer || {};
    const version = layer.version || week?.method_version || '5.0.0-experimental';
    const section = document.createElement('section');
    section.id = 'weekly-governance-live';
    section.className = 'panel governance-panel';
    section.innerHTML = `<details class="governance-details"><summary><span><b>${esc(copy.title)}</b><small>${esc(copy.version)}: ${esc(version)}</small></span><span class="governance-toggle"><span class="when-closed">${esc(copy.expand)}</span><span class="when-open">${esc(copy.collapse)}</span></span></summary><div class="governance-grid">${row(copy.gate, copy.gateText)}${row(copy.timing, copy.timingText)}${row(copy.lock, copy.lockText)}${row(copy.integrity, copy.integrityText)}</div></details>`;
    app.prepend(section);
  }

  document.addEventListener('br:weekly-rendered', (event) => mount(event.detail || {}));
}());
