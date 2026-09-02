(() => {
  'use strict';

  const VERSION = 1;
  const DATA_URL = '/data/investments/experience_store_public.json';
  const isEn = document.documentElement.lang.toLowerCase().startsWith('en');
  const copy = isEn ? {
    registry: 'Experiment Registry', experience: 'Experience Store',
    kicker: 'BRIEFROOMS · LEARNING MEMORY', title: 'Experience Store',
    intro: 'Read-only evidence memory built from prospective decisions and later outcomes. It shows what BriefRooms has actually observed without exposing raw research payloads or giving the panel any production authority.',
    experiences: 'Experiences', settled: 'Settled', pending: 'Pending', sources: 'Sources',
    evidence: 'Evidence status', sample: 'Settled sample', formalAlpha: 'Formal alpha', updated: 'Last update',
    engines: 'Evidence by engine', engine: 'Engine', actions: 'Actions', meanReturn: 'Mean return', winRate: 'Win rate', maxDd: 'Max DD', maeMfe: 'Avg MAE / MFE', status: 'Evidence',
    recent: 'Recent experiences', time: 'Decision', instrument: 'Instrument', action: 'Action', outcome: 'Outcome', result: 'Result', rMultiple: 'R', exit: 'Exit',
    notMeasurable: 'NOT MEASURABLE', insufficient: 'INSUFFICIENT DATA', positive: 'POSITIVE EVIDENCE', noPositive: 'NO POSITIVE EVIDENCE',
    noData: 'No Experience Store observations are available yet.', loading: 'Loading Experience Store…', error: 'Experience Store is temporarily unavailable.',
    privacy: 'Public projection only · raw payloads, signal snapshots and ledger hashes are not exposed.',
    gross: 'gross', net: 'net', other: 'other',
    sourceNote: 'The dashboard is descriptive. Promotion or production changes require separate governance gates.'
  } : {
    registry: 'Experiment Registry', experience: 'Experience Store',
    kicker: 'BRIEFROOMS · PAMIĘĆ UCZENIA', title: 'Experience Store',
    intro: 'Tylko do odczytu: pamięć dowodowa z prospektywnych decyzji i ich późniejszych wyników. Pokazuje, czego BriefRooms faktycznie doświadczył, bez ujawniania surowych danych badawczych i bez jakiegokolwiek wpływu na produkcję.',
    experiences: 'Doświadczenia', settled: 'Rozliczone', pending: 'Oczekujące', sources: 'Źródła',
    evidence: 'Status dowodów', sample: 'Próba rozliczona', formalAlpha: 'Formalna alpha', updated: 'Ostatnia aktualizacja',
    engines: 'Dowody według silnika', engine: 'Silnik', actions: 'Decyzje', meanReturn: 'Śr. wynik', winRate: 'Win rate', maxDd: 'Max DD', maeMfe: 'Śr. MAE / MFE', status: 'Dowody',
    recent: 'Ostatnie doświadczenia', time: 'Decyzja', instrument: 'Instrument', action: 'Akcja', outcome: 'Wynik', result: 'Zwrot', rMultiple: 'R', exit: 'Wyjście',
    notMeasurable: 'NIEMIERZALNA', insufficient: 'ZA MAŁO DANYCH', positive: 'DODATNIE DOWODY', noPositive: 'BRAK DODATNICH DOWODÓW',
    noData: 'Experience Store nie ma jeszcze obserwacji.', loading: 'Ładowanie Experience Store…', error: 'Experience Store jest chwilowo niedostępny.',
    privacy: 'Tylko publiczna projekcja · surowe payloady, snapshoty sygnałów i hashe ledgerów nie są ujawniane.',
    gross: 'brutto', net: 'netto', other: 'inne',
    sourceNote: 'Dashboard ma charakter opisowy. Promocja lub zmiana produkcyjna wymaga osobnych bramek governance.'
  };

  let payload = null;
  let installed = false;
  let originalHero = null;

  function esc(value) {
    return String(value ?? '').replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
  }

  function fmtNumber(value, digits = 2) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return new Intl.NumberFormat(isEn ? 'en-US' : 'pl-PL', { maximumFractionDigits: digits, minimumFractionDigits: 0 }).format(number);
  }

  function fmtPercent(value, digits = 2) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    const sign = number > 0 ? '+' : '';
    return `${sign}${fmtNumber(number * 100, digits)}%`;
  }

  function fmtDate(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.valueOf())) return esc(value);
    return new Intl.DateTimeFormat(isEn ? 'en-GB' : 'pl-PL', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Europe/Warsaw' }).format(date);
  }

  function ensureCss() {
    if (document.querySelector('link[data-experience-store-css]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = `/assets/experience-store.css?v=${VERSION}`;
    link.dataset.experienceStoreCss = String(VERSION);
    document.head.appendChild(link);
  }

  function statusLabel(value) {
    const status = String(value || 'INSUFFICIENT_DATA').toUpperCase();
    if (status === 'INSUFFICIENT_DATA') return copy.insufficient;
    if (status === 'POSITIVE_EVIDENCE') return copy.positive;
    if (status === 'NO_POSITIVE_EVIDENCE') return copy.noPositive;
    if (status === 'NOT_MEASURABLE') return copy.notMeasurable;
    return status.replaceAll('_', ' ');
  }

  function statusChip(value) {
    const status = String(value || 'INSUFFICIENT_DATA').toUpperCase();
    return `<span class="experience-status experience-status-${esc(status.toLowerCase())}">${esc(statusLabel(status))}</span>`;
  }

  function actionChip(value) {
    const action = String(value || 'OTHER').toUpperCase();
    return `<span class="experience-action experience-action-${esc(action.toLowerCase())}">${esc(action === 'OTHER' ? copy.other : action)}</span>`;
  }

  function install() {
    if (installed) return true;
    const shell = document.querySelector('.experiment-registry-shell');
    const hero = shell?.querySelector('.experiment-registry-hero');
    const summary = document.querySelector('#experiment-registry-summary');
    if (!shell || !hero || !summary) return false;
    ensureCss();

    const title = hero.querySelector('h2');
    const intro = hero.querySelector('p');
    const kicker = hero.querySelector('.experiment-registry-kicker');
    originalHero = {
      title: title?.textContent || copy.registry,
      intro: intro?.textContent || '',
      kicker: kicker?.textContent || ''
    };

    const switcher = document.createElement('div');
    switcher.className = 'research-lab-switcher';
    switcher.setAttribute('role', 'tablist');
    switcher.innerHTML = `
      <button type="button" role="tab" aria-selected="true" data-research-view="registry">${esc(copy.registry)}</button>
      <button type="button" role="tab" aria-selected="false" data-research-view="experience">${esc(copy.experience)}</button>`;
    shell.insertBefore(switcher, summary);

    const section = document.createElement('section');
    section.id = 'experience-store-view';
    section.className = 'experience-store-view';
    section.hidden = true;
    section.setAttribute('aria-hidden', 'true');
    section.innerHTML = `<div class="experience-store-loading">${esc(copy.loading)}</div>`;
    shell.insertBefore(section, summary);

    switcher.querySelectorAll('[data-research-view]').forEach(button => button.addEventListener('click', () => setView(button.dataset.researchView || 'registry')));
    installed = true;
    document.body.dataset.experienceStoreUi = 'installed-v1';
    return true;
  }

  function setHero(mode) {
    const hero = document.querySelector('.experiment-registry-hero');
    if (!hero) return;
    const title = hero.querySelector('h2');
    const intro = hero.querySelector('p');
    const kicker = hero.querySelector('.experiment-registry-kicker');
    if (mode === 'experience') {
      if (title) title.textContent = copy.title;
      if (intro) intro.textContent = copy.intro;
      if (kicker) kicker.textContent = copy.kicker;
    } else if (originalHero) {
      if (title) title.textContent = originalHero.title;
      if (intro) intro.textContent = originalHero.intro;
      if (kicker) kicker.textContent = originalHero.kicker;
    }
  }

  function setView(mode) {
    if (!install()) return;
    const experience = mode === 'experience';
    const switcher = document.querySelector('.research-lab-switcher');
    switcher?.querySelectorAll('[data-research-view]').forEach(button => {
      const active = button.dataset.researchView === (experience ? 'experience' : 'registry');
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    ['#experiment-registry-summary','#experiment-registry-filters','#experiment-registry-content','#experiment-registry-detail'].forEach(selector => {
      const node = document.querySelector(selector);
      if (!node) return;
      node.hidden = experience;
      node.setAttribute('aria-hidden', experience ? 'true' : 'false');
    });
    const view = document.querySelector('#experience-store-view');
    if (view) {
      view.hidden = !experience;
      view.setAttribute('aria-hidden', experience ? 'false' : 'true');
    }
    setHero(experience ? 'experience' : 'registry');
    document.body.dataset.researchLabView = experience ? 'experience' : 'registry';
    if (experience && !payload) refresh();
  }

  function renderSummary() {
    const s = payload?.summary || {};
    const cards = [
      [copy.experiences, s.experience_count],
      [copy.settled, s.settled_count],
      [copy.pending, s.pending_count],
      [copy.sources, s.source_count],
    ];
    return `<div class="experience-store-summary">${cards.map(([label, value]) => `<div class="experience-store-stat"><small>${esc(label)}</small><strong>${fmtNumber(value, 0)}</strong></div>`).join('')}</div>`;
  }

  function renderEvidence() {
    const s = payload?.summary || {};
    const evidence = payload?.overall_evidence || {};
    return `<article class="experience-store-evidence">
      <div><small>${esc(copy.evidence)}</small>${statusChip(s.assessment)}</div>
      <div><small>${esc(copy.sample)}</small><strong>${fmtNumber(evidence.sample_size, 0)} / ${fmtNumber(s.minimum_sample, 0)}</strong></div>
      <div><small>${esc(copy.formalAlpha)}</small><strong>${esc(statusLabel(s.formal_alpha_status))}</strong></div>
      <div><small>${esc(copy.updated)}</small><strong>${fmtDate(payload?.generated_at)}</strong></div>
    </article>`;
  }

  function renderEngines() {
    const engines = Array.isArray(payload?.engines) ? payload.engines : [];
    if (!engines.length) return `<div class="experience-store-empty">${esc(copy.noData)}</div>`;
    return `<section class="experience-store-block"><div class="experience-store-block-head"><h3>${esc(copy.engines)}</h3></div>
      <div class="experience-store-table-wrap"><table class="experience-store-table">
        <thead><tr><th>${esc(copy.engine)}</th><th>${esc(copy.experiences)}</th><th>${esc(copy.settled)}</th><th>${esc(copy.actions)}</th><th>${esc(copy.meanReturn)}</th><th>${esc(copy.winRate)}</th><th>${esc(copy.maxDd)}</th><th>${esc(copy.maeMfe)}</th><th>${esc(copy.status)}</th></tr></thead>
        <tbody>${engines.map(row => {
          const e = row.evidence || {};
          const a = row.actions || {};
          return `<tr>
            <td><strong>${esc(row.label || row.engine)}</strong><small>${esc(row.engine || '')}</small></td>
            <td>${fmtNumber(row.experience_count, 0)}</td>
            <td>${fmtNumber(row.settled_count, 0)}<small>${fmtNumber(row.pending_count, 0)} ${esc(copy.pending.toLowerCase())}</small></td>
            <td><span class="experience-actions-mini">L ${fmtNumber(a.LONG,0)} · S ${fmtNumber(a.SHORT,0)} · F ${fmtNumber(a.FLAT,0)}</span></td>
            <td>${fmtPercent(e.mean_return_fraction)}</td>
            <td>${e.win_rate === null || e.win_rate === undefined ? '—' : fmtPercent(e.win_rate, 1)}</td>
            <td>${fmtPercent(e.max_drawdown_fraction)}</td>
            <td>${fmtPercent(e.avg_mae_fraction)} / ${fmtPercent(e.avg_mfe_fraction)}</td>
            <td>${statusChip(e.assessment)}</td>
          </tr>`;
        }).join('')}</tbody>
      </table></div></section>`;
  }

  function renderRecent() {
    const rows = Array.isArray(payload?.recent_experiences) ? payload.recent_experiences : [];
    if (!rows.length) return '';
    return `<section class="experience-store-block"><div class="experience-store-block-head"><h3>${esc(copy.recent)}</h3></div>
      <div class="experience-store-table-wrap"><table class="experience-store-table experience-store-recent">
        <thead><tr><th>${esc(copy.time)}</th><th>${esc(copy.engine)}</th><th>${esc(copy.instrument)}</th><th>${esc(copy.action)}</th><th>${esc(copy.outcome)}</th><th>${esc(copy.result)}</th><th>${esc(copy.rMultiple)}</th><th>${esc(copy.exit)}</th></tr></thead>
        <tbody>${rows.map(row => {
          const basis = row.return_basis === 'NET' ? copy.net : row.return_basis === 'GROSS' ? copy.gross : '';
          return `<tr>
            <td>${fmtDate(row.decision_at)}</td>
            <td><strong>${esc(row.engine_label || row.engine || '—')}</strong><small>${esc(row.engine_version || '')}</small></td>
            <td>${esc(row.instrument || '—')}</td>
            <td>${actionChip(row.action)}</td>
            <td><span class="experience-outcome experience-outcome-${esc(String(row.status || '').toLowerCase())}">${esc(row.status || '—')}</span></td>
            <td>${row.return_fraction === null || row.return_fraction === undefined ? '—' : `${fmtPercent(row.return_fraction)}${basis ? ` <small>${esc(basis)}</small>` : ''}`}</td>
            <td>${fmtNumber(row.r_multiple, 2)}</td>
            <td>${esc(row.exit_reason || '—')}</td>
          </tr>`;
        }).join('')}</tbody>
      </table></div></section>`;
  }

  function render() {
    const root = document.querySelector('#experience-store-view');
    if (!root || !payload) return;
    root.innerHTML = `${renderSummary()}${renderEvidence()}${renderEngines()}${renderRecent()}
      <div class="experience-store-footnote"><span>${esc(copy.privacy)}</span><span>${esc(copy.sourceNote)}</span></div>`;
  }

  function renderError() {
    const root = document.querySelector('#experience-store-view');
    if (root) root.innerHTML = `<div class="experience-store-error"><strong>${esc(copy.error)}</strong><span>${esc(DATA_URL)}</span></div>`;
  }

  async function refresh() {
    if (!install()) return;
    try {
      const response = await fetch(`${DATA_URL}?experience=${Date.now()}`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (!data || data.schema_version !== 'briefrooms-experience-store-public-v1' || !data.summary || !Array.isArray(data.engines)) throw new Error('Invalid Experience Store projection');
      if (data.authority?.read_only !== true || data.authority?.production_decision_influence !== false) throw new Error('Invalid authority boundary');
      payload = data;
      document.body.dataset.experienceStore = data.schema_version;
      render();
    } catch (error) {
      console.warn('Experience Store unavailable:', error);
      renderError();
    }
  }

  function start(attempt = 0) {
    if (install()) {
      const button = document.querySelector('[data-research-view="registry"]');
      button?.classList.add('active');
      refresh();
      return;
    }
    if (attempt < 50) window.setTimeout(() => start(attempt + 1), 100);
  }

  window.BriefRoomsExperienceStore = { refresh, activate: () => setView('experience'), version: VERSION };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => start(), { once: true });
  else start();
})();
