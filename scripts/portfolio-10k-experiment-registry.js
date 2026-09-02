(() => {
  'use strict';

  const VERSION = 2;
  const TAB = 'lab';
  const DATA_URL = '/data/investments/experiment_registry.json';
  const isEn = document.documentElement.lang.toLowerCase().startsWith('en');
  const copy = isEn ? {
    tab: 'Lab',
    kicker: 'BRIEFROOMS RESEARCH CONTROL',
    title: 'Experiment Registry',
    intro: 'One read-only view of active BriefRooms experiments, challengers and learning systems. This panel cannot change production decisions, tune models or promote challengers.',
    active: 'Active',
    awaiting: 'Awaiting evidence',
    promotion: 'Promotion candidates',
    parked: 'Parked / killed',
    all: 'All', trading: 'Trading', forecasting: 'Forecasting', belief: 'Belief', learning: 'Learning',
    experiment: 'Experiment', type: 'Type', version: 'Version', sample: 'Sample', result: 'Primary result', baseline: 'Reference / baseline', status: 'Status',
    details: 'Experiment details', purpose: 'Purpose', family: 'Family', source: 'Canonical source', gate: 'Evidence gate', updated: 'Last update', influence: 'Production influence', autopromotion: 'Automatic promotion', notes: 'Notes', technical: 'Source details',
    none: 'No experiments match this filter.',
    loading: 'Loading Experiment Registry…',
    error: 'Experiment Registry is temporarily unavailable.',
    no: 'NO', yes: 'YES',
    statusMap: {
      RUNNING: 'RUNNING', INSUFFICIENT_DATA: 'INSUFFICIENT DATA', CONTINUE: 'CONTINUE', PROMOTE: 'CANDIDATE', PARK: 'PARKED', KILL: 'ENDED', ERROR: 'ERROR'
    }
  } : {
    tab: 'Laboratorium',
    kicker: 'BRIEFROOMS · NADZÓR BADAŃ',
    title: 'Experiment Registry',
    intro: 'Jedno, tylko do odczytu, miejsce dla aktywnych eksperymentów, challengerów i systemów uczących BriefRooms. Ten panel nie może zmieniać decyzji produkcyjnych, stroić modeli ani promować challengerów.',
    active: 'Aktywne',
    awaiting: 'Czekają na dane',
    promotion: 'Kandydaci do promocji',
    parked: 'Wstrzymane / zakończone',
    all: 'Wszystkie', trading: 'Trading', forecasting: 'Prognozy', belief: 'Belief', learning: 'Uczenie',
    experiment: 'Eksperyment', type: 'Typ', version: 'Wersja', sample: 'Próba', result: 'Główny wynik', baseline: 'Odniesienie / baseline', status: 'Status',
    details: 'Szczegóły eksperymentu', purpose: 'Cel', family: 'Rodzina', source: 'Źródło kanoniczne', gate: 'Próg dowodowy', updated: 'Ostatnia aktualizacja', influence: 'Wpływ na produkcję', autopromotion: 'Automatyczna promocja', notes: 'Uwagi', technical: 'Dane źródłowe',
    none: 'Brak eksperymentów dla tego filtra.',
    loading: 'Ładowanie Experiment Registry…',
    error: 'Experiment Registry jest chwilowo niedostępny.',
    no: 'NIE', yes: 'TAK',
    statusMap: {
      RUNNING: 'TRWA', INSUFFICIENT_DATA: 'ZA MAŁO DANYCH', CONTINUE: 'KONTYNUUJ', PROMOTE: 'KANDYDAT', PARK: 'WSTRZYMANE', KILL: 'ZAKOŃCZONE', ERROR: 'BŁĄD'
    }
  };

  let registry = null;
  let filter = 'all';
  let selectedId = null;

  function esc(value) {
    return String(value ?? '').replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
  }

  function ensureCss() {
    if (document.querySelector('link[data-experiment-registry-css]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = `/assets/experiment-registry.css?v=${VERSION}`;
    link.dataset.experimentRegistryCss = String(VERSION);
    document.head.appendChild(link);
  }

  function injectTab(nav, side = false) {
    if (!nav || nav.querySelector('[data-tab="lab"]')) return;
    const rules = nav.querySelector('[data-tab="rules"]');
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.tab = TAB;
    button.setAttribute('aria-label', copy.tab);
    if (side) button.innerHTML = `⌁ <span>${esc(copy.tab)}</span>`;
    else button.textContent = copy.tab;
    if (rules?.nextSibling) nav.insertBefore(button, rules.nextSibling);
    else if (rules) rules.insertAdjacentElement('afterend', button);
    else nav.appendChild(button);
  }

  function ensurePanel() {
    if (document.querySelector('.i10k-panel[data-panel="lab"]')) return;
    const main = document.querySelector('.i10k-main');
    if (!main) return;
    const footer = main.querySelector('.i10k-footer');
    const panel = document.createElement('section');
    panel.className = 'i10k-panel experiment-registry-panel';
    panel.dataset.panel = TAB;
    panel.hidden = true;
    panel.setAttribute('aria-hidden', 'true');
    panel.innerHTML = `
      <article class="dash-card page-card experiment-registry-shell" aria-labelledby="experiment-registry-title">
        <div class="experiment-registry-hero">
          <div>
            <span class="experiment-registry-kicker">${esc(copy.kicker)}</span>
            <h2 id="experiment-registry-title">${esc(copy.title)}</h2>
            <p>${esc(copy.intro)}</p>
          </div>
          <span class="experiment-registry-readonly">READ ONLY · ZERO AUTHORITY</span>
        </div>
        <div id="experiment-registry-summary" class="experiment-registry-summary" aria-live="polite"></div>
        <div id="experiment-registry-filters" class="experiment-registry-filters" aria-label="Experiment filters"></div>
        <div id="experiment-registry-content" aria-live="polite"><div class="experiment-registry-loading">${esc(copy.loading)}</div></div>
        <div id="experiment-registry-detail"></div>
      </article>`;
    if (footer) main.insertBefore(panel, footer);
    else main.appendChild(panel);
  }

  function ensureUi() {
    ensureCss();
    injectTab(document.querySelector('.i10k-side-nav'), true);
    injectTab(document.querySelector('.i10k-tabs'), false);
    ensurePanel();
  }

  function fmtNumber(value, digits = 2) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return new Intl.NumberFormat(isEn ? 'en-US' : 'pl-PL', { maximumFractionDigits: digits, minimumFractionDigits: 0 }).format(number);
  }

  function fmtMetric(metric) {
    if (!metric || metric.value === null || metric.value === undefined) return '—';
    const value = Number(metric.value);
    if (!Number.isFinite(value)) return esc(metric.value);
    if (metric.unit === 'fraction') return `${fmtNumber(value * 100, 2)}%`;
    if (metric.unit === 'percent' || metric.unit === 'incremental_percent') return `${fmtNumber(value, 2)}%`;
    if (metric.unit === 'score') return fmtNumber(value, 4);
    if (metric.unit === 'candidates') return fmtNumber(value, 0);
    return fmtNumber(value, 3);
  }

  function fmtDate(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.valueOf())) return esc(value);
    return new Intl.DateTimeFormat(isEn ? 'en-GB' : 'pl-PL', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Europe/Warsaw' }).format(date);
  }

  function sampleLabel(row) {
    const count = row.sample_count;
    const min = row.minimum_sample;
    if (count === null || count === undefined) return min ? `— / ${fmtNumber(min, 0)}` : '—';
    return min ? `${fmtNumber(count, 0)} / ${fmtNumber(min, 0)}` : fmtNumber(count, 0);
  }

  function renderSummary() {
    const root = document.querySelector('#experiment-registry-summary');
    if (!root || !registry) return;
    const s = registry.summary || {};
    const cards = [
      [copy.active, s.active, 'active'],
      [copy.awaiting, s.awaiting_evidence, 'awaiting'],
      [copy.promotion, s.promotion_candidates, 'promotion'],
      [copy.parked, s.parked_or_killed, 'parked']
    ];
    root.innerHTML = cards.map(([label, value, cls]) => `<div class="experiment-registry-stat ${cls}"><small>${esc(label)}</small><strong>${fmtNumber(value, 0)}</strong></div>`).join('');
  }

  function renderFilters() {
    const root = document.querySelector('#experiment-registry-filters');
    if (!root || !registry) return;
    const present = new Set((registry.experiments || []).map(row => row.category));
    const filters = ['all','trading','forecasting','belief','learning'].filter(key => key === 'all' || present.has(key));
    if (!filters.includes(filter)) filter = 'all';
    root.innerHTML = filters.map(key => `<button type="button" data-experiment-filter="${key}" class="${filter === key ? 'active' : ''}">${esc(copy[key])}</button>`).join('');
    root.querySelectorAll('[data-experiment-filter]').forEach(button => button.addEventListener('click', () => {
      filter = button.dataset.experimentFilter || 'all';
      renderFilters();
      renderTable();
    }));
  }

  function statusChip(status) {
    const value = String(status || 'ERROR');
    return `<span class="experiment-status experiment-status-${esc(value.toLowerCase())}">${esc(copy.statusMap[value] || value)}</span>`;
  }

  function renderTable() {
    const root = document.querySelector('#experiment-registry-content');
    if (!root || !registry) return;
    const rows = (registry.experiments || []).filter(row => filter === 'all' || row.category === filter);
    if (!rows.length) {
      root.innerHTML = `<div class="experiment-registry-empty">${esc(copy.none)}</div>`;
      renderDetail(null);
      return;
    }
    root.innerHTML = `
      <div class="experiment-registry-table-wrap">
        <table class="experiment-registry-table">
          <thead><tr><th>${esc(copy.experiment)}</th><th>${esc(copy.type)}</th><th>${esc(copy.version)}</th><th>${esc(copy.sample)}</th><th>${esc(copy.result)}</th><th>${esc(copy.baseline)}</th><th>${esc(copy.status)}</th></tr></thead>
          <tbody>${rows.map(row => `
            <tr data-experiment-id="${esc(row.id)}" tabindex="0" role="button" aria-label="${esc(row.name)}">
              <td><strong>${esc(row.name)}</strong><small>${esc(row.family || '')}</small></td>
              <td><span class="experiment-category">${esc(copy[row.category] || row.category)}</span></td>
              <td>${esc(row.version || '—')}</td>
              <td><strong>${sampleLabel(row)}</strong><small>${esc(row.sample_unit || '')}</small></td>
              <td><strong>${fmtMetric(row.primary_metric)}</strong><small>${esc(row.primary_metric?.label || '')}</small></td>
              <td>${fmtMetric(row.benchmark)}</td>
              <td>${statusChip(row.status)}</td>
            </tr>`).join('')}</tbody>
        </table>
      </div>`;
    root.querySelectorAll('[data-experiment-id]').forEach(row => {
      const open = () => {
        selectedId = row.dataset.experimentId;
        renderDetail((registry.experiments || []).find(item => item.id === selectedId));
        root.querySelectorAll('[data-experiment-id]').forEach(item => item.classList.toggle('selected', item === row));
      };
      row.addEventListener('click', open);
      row.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); }
      });
    });
    const selected = rows.find(item => item.id === selectedId);
    renderDetail(selected || null);
  }

  function renderDetail(row) {
    const root = document.querySelector('#experiment-registry-detail');
    if (!root) return;
    if (!row) { root.innerHTML = ''; return; }
    const details = row.details && typeof row.details === 'object' ? row.details : {};
    const notes = Array.isArray(row.notes) ? row.notes : [];
    const detailPairs = Object.entries(details).filter(([, value]) => value !== null && value !== undefined && typeof value !== 'object').slice(0, 10);
    root.innerHTML = `
      <article class="experiment-registry-detail-card">
        <div class="experiment-registry-detail-head"><div><span class="experiment-registry-kicker">${esc(copy.details)}</span><h3>${esc(row.name)}</h3></div>${statusChip(row.status)}</div>
        <p class="experiment-registry-purpose">${esc(row.purpose || '—')}</p>
        <dl class="experiment-registry-meta">
          <div><dt>${esc(copy.family)}</dt><dd>${esc(row.family || '—')}</dd></div>
          <div><dt>${esc(copy.source)}</dt><dd><code>${esc(row.source || '—')}</code></dd></div>
          <div><dt>${esc(copy.gate)}</dt><dd>${sampleLabel(row)}</dd></div>
          <div><dt>${esc(copy.updated)}</dt><dd>${fmtDate(row.last_updated)}</dd></div>
          <div><dt>${esc(copy.influence)}</dt><dd class="experiment-no-authority">${row.production_impact ? esc(copy.yes) : esc(copy.no)}</dd></div>
          <div><dt>${esc(copy.autopromotion)}</dt><dd class="experiment-no-authority">${row.automatic_promotion ? esc(copy.yes) : esc(copy.no)}</dd></div>
        </dl>
        ${detailPairs.length ? `<h4>${esc(copy.technical)}</h4><div class="experiment-registry-technical">${detailPairs.map(([key, value]) => `<div><span>${esc(key)}</span><b>${esc(value)}</b></div>`).join('')}</div>` : ''}
        ${notes.length ? `<h4>${esc(copy.notes)}</h4><ul class="experiment-registry-notes">${notes.map(note => `<li>${esc(note)}</li>`).join('')}</ul>` : ''}
      </article>`;
  }

  function renderError() {
    const root = document.querySelector('#experiment-registry-content');
    if (root) root.innerHTML = `<div class="experiment-registry-error"><strong>${esc(copy.error)}</strong><span>${esc(DATA_URL)}</span></div>`;
    const summary = document.querySelector('#experiment-registry-summary');
    if (summary) summary.innerHTML = '';
  }

  async function refresh() {
    ensureUi();
    try {
      const response = await fetch(`${DATA_URL}?registry=${Date.now()}`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (!payload || !Array.isArray(payload.experiments) || !payload.summary) throw new Error('Invalid registry payload');
      registry = payload;
      document.body.dataset.experimentRegistry = payload.schema_version || 'loaded';
      renderSummary();
      renderFilters();
      renderTable();
    } catch (error) {
      console.warn('Experiment Registry unavailable:', error);
      renderError();
    }
  }

  function start() {
    ensureUi();
    refresh();
    if (location.hash === '#lab') {
      requestAnimationFrame(() => window.BriefRoomsInvestmentNavigation?.activate?.('lab', false));
    }
  }

  window.BriefRoomsExperimentRegistry = { refresh, version: VERSION };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
})();
