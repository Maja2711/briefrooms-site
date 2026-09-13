(() => {
  'use strict';

  const VERSION = 1;
  const isEn = document.documentElement.lang.toLowerCase().startsWith('en');
  const copy = isEn ? {
    loading: 'DATA · LOADING',
    ok: 'DATA · OK',
    error: 'DATA · ERROR',
    registryOk: 'Experiment Registry data loaded successfully.',
    registryError: 'Experiment Registry data could not be loaded.',
    experienceOk: 'Experience Store data loaded successfully.',
    experienceError: 'Experience Store data could not be loaded.',
    loadingTitle: 'Data is being loaded.',
    readonly: 'READ ONLY · ZERO AUTHORITY',
    filters: 'Experiment filters',
    settled: 'SETTLED',
    pending: 'PENDING'
  } : {
    loading: 'DANE · POBIERANIE',
    ok: 'DANE · OK',
    error: 'DANE · BŁĄD',
    registryOk: 'Dane Experiment Registry zostały pobrane poprawnie.',
    registryError: 'Nie udało się pobrać danych Experiment Registry.',
    experienceOk: 'Dane Experience Store zostały pobrane poprawnie.',
    experienceError: 'Nie udało się pobrać danych Experience Store.',
    loadingTitle: 'Trwa pobieranie danych.',
    readonly: 'TYLKO ODCZYT · BRAK WPŁYWU NA DECYZJE',
    filters: 'Filtry eksperymentów',
    settled: 'ROZLICZONE',
    pending: 'OCZEKUJE'
  };

  let scheduled = false;
  let lastSignature = '';

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

  function stateFor(source) {
    if (source === 'experience') {
      if (document.querySelector('#experience-store-view .experience-store-error')) return 'error';
      if (document.body.dataset.experienceStore) return 'ok';
      return 'loading';
    }
    if (document.querySelector('#experiment-registry-content .experiment-registry-error')) return 'error';
    if (document.body.dataset.experimentRegistry) return 'ok';
    return 'loading';
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
      indicator.className = 'research-lab-data-health research-lab-data-health-loading';
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

  function sync() {
    scheduled = false;
    ensureCss();
    localizeStaticUi();

    const indicator = ensureHealthIndicator();
    if (!indicator) return;

    const source = activeSource();
    const state = stateFor(source);
    const label = state === 'ok' ? copy.ok : state === 'error' ? copy.error : copy.loading;
    const title = state === 'loading'
      ? copy.loadingTitle
      : source === 'experience'
        ? (state === 'ok' ? copy.experienceOk : copy.experienceError)
        : (state === 'ok' ? copy.registryOk : copy.registryError);
    const signature = `${source}:${state}:${label}:${title}`;

    if (signature !== lastSignature) {
      indicator.className = `research-lab-data-health research-lab-data-health-${state}`;
      const text = indicator.querySelector('span');
      if (text) text.textContent = label;
      indicator.title = title;
      indicator.dataset.source = source;
      indicator.dataset.state = state;
      document.body.dataset.researchLabDataHealth = state;
      document.body.dataset.researchLabDataSource = source;
      lastSignature = signature;
    }
  }

  function scheduleSync() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(sync);
  }

  function start() {
    ensureCss();
    scheduleSync();

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

  window.BriefRoomsLabHealth = { sync: scheduleSync, version: VERSION };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
})();
