(() => {
  'use strict';

  const VERSION = 3;
  const HEALTH_URL = '/data/investments/lab_health.json';
  const REFRESH_MS = 5 * 60 * 1000;
  const isEn = document.documentElement.lang.toLowerCase().startsWith('en');
  const copy = isEn ? {
    healthy: 'LAB · OK',
    degraded: 'LAB · DEGRADED',
    error: 'LAB · ERROR',
    loading: 'LAB · CHECKING',
    localError: 'The active Lab view could not load its data.',
    contractMissing: 'Canonical Lab health contract is unavailable.',
    readonly: 'READ ONLY · ZERO AUTHORITY',
    filters: 'Experiment filters',
    settled: 'SETTLED',
    pending: 'PENDING'
  } : {
    healthy: 'LAB · OK',
    degraded: 'LAB · OSTRZEŻENIE',
    error: 'LAB · BŁĄD',
    loading: 'LAB · SPRAWDZANIE',
    localError: 'Aktywny widok Laboratorium nie może załadować swoich danych.',
    contractMissing: 'Kanoniczny health contract Laboratorium jest niedostępny.',
    readonly: 'TYLKO ODCZYT · BRAK WPŁYWU NA DECYZJE',
    filters: 'Filtry eksperymentów',
    settled: 'ROZLICZONE',
    pending: 'OCZEKUJE'
  };

  let scheduled = false;
  let lastSignature = '';
  let contract = null;
  let contractError = null;
  let loading = false;

  function ensureCss() {
    if (document.querySelector('link[data-lab-health-css]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = `/assets/portfolio-10k-lab-health.css?v=${VERSION}`;
    link.dataset.labHealthCss = String(VERSION);
    document.head.appendChild(link);
  }

  function activeSource() {
    return document.body.dataset.researchLabView === 'experience' ? 'experience' : 'registry';
  }

  function localViewState(source) {
    if (source === 'experience') {
      if (document.querySelector('#experience-store-view .experience-store-error')) return 'error';
      if (document.body.dataset.experienceStore) return 'ok';
      return 'pending';
    }
    if (document.querySelector('#experiment-registry-content .experiment-registry-error')) return 'error';
    if (document.body.dataset.experimentRegistry) return 'ok';
    return 'pending';
  }

  function ensureHealthIndicator() {
    const hero = document.querySelector('.experiment-registry-hero');
    if (!hero) return null;
    let actions = hero.querySelector('.experiment-registry-hero-actions');
    const readonly = hero.querySelector('.experiment-registry-readonly');
    if (!actions) {
      actions = document.createElement('div');
      actions.className = 'experiment-registry-hero-actions';
      if (readonly) {
        readonly.before(actions);
        actions.appendChild(readonly);
      } else {
        hero.appendChild(actions);
      }
    }
    let indicator = actions.querySelector('#research-lab-data-health');
    if (!indicator) {
      indicator = document.createElement('span');
      indicator.id = 'research-lab-data-health';
      indicator.className = 'research-lab-data-health';
      indicator.setAttribute('role', 'status');
      indicator.setAttribute('aria-live', 'polite');
      indicator.innerHTML = '<i aria-hidden="true"></i><span></span>';
      actions.appendChild(indicator);
    }
    return indicator;
  }

  function localizeStaticUi() {
    const readonly = document.querySelector('.experiment-registry-readonly');
    if (readonly && readonly.textContent !== copy.readonly) readonly.textContent = copy.readonly;
    const filters = document.querySelector('#experiment-registry-filters');
    if (filters && filters.getAttribute('aria-label') !== copy.filters) filters.setAttribute('aria-label', copy.filters);
    if (!isEn) {
      document.querySelectorAll('.experience-outcome-settled').forEach(node => {
        if (String(node.textContent || '').trim().toUpperCase() === 'SETTLED') node.textContent = copy.settled;
      });
      document.querySelectorAll('.experience-outcome-pending').forEach(node => {
        if (String(node.textContent || '').trim().toUpperCase() === 'PENDING') node.textContent = copy.pending;
      });
    }
    document.querySelectorAll('#experience-store-view td strong').forEach(node => {
      if (String(node.textContent || '').trim() === 'GPW Daily') node.textContent = 'GPW Trading';
    });
  }

  function contractTitle(payload) {
    if (!payload) return copy.contractMissing;
    const reasons = Array.isArray(payload.reasons) ? payload.reasons.filter(Boolean) : [];
    const generated = payload.generated_at ? ` · ${payload.generated_at}` : '';
    if (!reasons.length) return `Canonical Lab health: ${payload.status || 'UNKNOWN'}${generated}`;
    return `Canonical Lab health: ${payload.status || 'UNKNOWN'}${generated}\n${reasons.join('\n')}`;
  }

  function effectiveState() {
    const source = activeSource();
    const local = localViewState(source);
    if (local === 'error') return { state: 'error', label: copy.error, title: copy.localError, source };
    if (!contract) {
      if (contractError) return { state: 'error', label: copy.error, title: copy.contractMissing, source };
      return { state: 'pending', label: copy.loading, title: copy.loading, source };
    }
    const status = String(contract.status || '').toUpperCase();
    if (status === 'HEALTHY') return { state: local === 'pending' ? 'pending' : 'ok', label: local === 'pending' ? copy.loading : copy.healthy, title: contractTitle(contract), source };
    if (status === 'DEGRADED') return { state: 'degraded', label: copy.degraded, title: contractTitle(contract), source };
    return { state: 'error', label: copy.error, title: contractTitle(contract), source };
  }

  function sync() {
    scheduled = false;
    ensureCss();
    localizeStaticUi();
    const indicator = ensureHealthIndicator();
    if (!indicator) return;

    const view = effectiveState();
    const signature = `${view.source}:${view.state}:${view.label}:${view.title}`;
    indicator.hidden = false;
    if (signature !== lastSignature) {
      indicator.className = `research-lab-data-health research-lab-data-health-${view.state}`;
      const text = indicator.querySelector('span');
      if (text) text.textContent = view.label;
      indicator.title = view.title;
      indicator.dataset.source = view.source;
      indicator.dataset.state = view.state;
      document.body.dataset.researchLabDataHealth = view.state;
      document.body.dataset.researchLabDataSource = view.source;
      document.body.dataset.researchLabContractStatus = contract?.status || (contractError ? 'ERROR' : 'PENDING');
      lastSignature = signature;
    }
  }

  function scheduleSync() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(sync);
  }

  async function refreshContract() {
    if (loading) return;
    loading = true;
    try {
      const response = await fetch(`${HEALTH_URL}?health=${Date.now()}`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (!payload || payload.schema_version !== 'briefrooms-lab-health-v1' || !payload.status || !payload.sources) {
        throw new Error('invalid Lab health contract');
      }
      contract = payload;
      contractError = null;
    } catch (error) {
      contract = null;
      contractError = error;
      console.warn('Research Lab health contract unavailable:', error);
    } finally {
      loading = false;
      scheduleSync();
    }
  }

  function start() {
    ensureCss();
    scheduleSync();
    refreshContract();
    window.setInterval(refreshContract, REFRESH_MS);

    if (typeof MutationObserver !== 'undefined') {
      new MutationObserver(scheduleSync).observe(document.body, {
        childList: true,
        subtree: true,
        characterData: true,
        attributes: true,
        attributeFilter: ['data-experiment-registry', 'data-experience-store', 'data-research-lab-view']
      });
    }

    window.addEventListener('briefrooms:investment-tab-change', scheduleSync);
    window.addEventListener('hashchange', scheduleSync);
    document.addEventListener('click', event => {
      if (event.target instanceof Element && event.target.closest('[data-research-view]')) {
        window.setTimeout(scheduleSync, 0);
      }
    }, true);
  }

  window.BriefRoomsLabHealth = { sync: scheduleSync, refresh: refreshContract, version: VERSION };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
})();
