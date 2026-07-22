(function () {
  'use strict';
  const lang = (window.BR_WEEKLY || {}).lang === 'en' ? 'en' : 'pl';
  const copy = lang === 'pl' ? {
    title: 'Zasady bezpieczeństwa modelu v5',
    gate: 'Wspólna bramka walidacyjna', gateText: 'Każda warstwa modelu podlega temu samemu dopuszczeniu instrumentu do nowych wejść.',
    timing: 'Zakaz wejść wstecznych', timingText: 'Cena wejścia musi pochodzić z pierwszej zakończonej świecy 5-minutowej nie wcześniejszej niż zamrożona decyzja.',
    lock: 'Blokada po unieważnieniu tezy', lockText: 'Wyjście po zdarzeniu materialnym lub unieważnieniu tezy blokuje ponowne wejście do końca tygodnia.',
    abstain: 'NO_TRADE', abstainText: 'Brak pozycji jest pełnoprawną decyzją przy słabym, sprzecznym lub niekompletnym sygnale.',
    version: 'Wersja', status: 'Status', passed: 'zasada aktywna', experimental: 'eksperymentalny paper trading'
  } : {
    title: 'Model v5 safety rules',
    gate: 'Shared validation gate', gateText: 'Every model layer is subject to the same approval for new instrument entries.',
    timing: 'No backdated entries', timingText: 'Entry must use the first completed five-minute bar at or after the frozen decision timestamp.',
    lock: 'Thesis-invalidation lock', lockText: 'A material-event or thesis-invalidation exit blocks re-entry until the end of the week.',
    abstain: 'NO_TRADE', abstainText: 'No position is a first-class decision when evidence is weak, conflicting or incomplete.',
    version: 'Version', status: 'Status', passed: 'rule active', experimental: 'experimental paper trading'
  };
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const row = (title, text) => `<article class="governance-rule"><b>${esc(title)}</b><span>${esc(text)}</span><small>✓ ${esc(copy.passed)}</small></article>`;
  function mount(week) {
    const app = document.getElementById('app');
    if (!app || document.getElementById('weekly-governance-live')) return;
    const layer = week?.multi_instrument_exposure_layer || {};
    const version = layer.version || week?.method_version || '5.0.0-experimental';
    const section = document.createElement('section');
    section.id = 'weekly-governance-live';
    section.className = 'panel governance-panel';
    section.innerHTML = `<div class="governance-head"><div><h2>${esc(copy.title)}</h2><p>${esc(copy.version)}: ${esc(version)} · ${esc(copy.status)}: ${esc(copy.experimental)}</p></div><span class="pill">NO_TRADE</span></div><div class="governance-grid">${row(copy.gate, copy.gateText)}${row(copy.timing, copy.timingText)}${row(copy.lock, copy.lockText)}${row(copy.abstain, copy.abstainText)}</div>`;
    app.prepend(section);
  }
  function isoWeek(date) {
    const x = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
    const day = x.getUTCDay() || 7; x.setUTCDate(x.getUTCDate() + 4 - day);
    const year = new Date(Date.UTC(x.getUTCFullYear(), 0, 1));
    return `${x.getUTCFullYear()}-W${String(Math.ceil((((x - year) / 86400000) + 1) / 7)).padStart(2, '0')}`;
  }
  async function latest() {
    for (let i = 0; i < 8; i += 1) {
      const date = new Date(); date.setUTCDate(date.getUTCDate() - 7 * i);
      try {
        const response = await fetch(`/data/investments/weekly/${isoWeek(date)}.json?v=${Date.now()}`, {cache: 'no-store'});
        if (response.ok) return response.json();
      } catch (_) {}
    }
    return {};
  }
  latest().then(week => setTimeout(() => mount(week), 50)).catch(() => setTimeout(() => mount({}), 50));
})();
