(() => {
  'use strict';

  const APP_SELECTOR = '.i10k-app';
  const DATA_TAB_SELECTOR = `${APP_SELECTOR} [data-tab]`;
  const PROJECT_HASH_SELECTOR = `${APP_SELECTOR} .i10k-projects a[href^="#"]`;
  const PANEL_SELECTOR = `${APP_SELECTOR} .i10k-panel[data-panel]`;
  const VALID_TABS = new Set(['overview','portfolio','benchmark','agents','analytics','history','rules','lab']);
  const TAB_ALIASES = Object.freeze({ brace: 'analytics' });
  const isEn = document.documentElement.lang.toLowerCase().startsWith('en');
  const currency = isEn ? 'USD' : 'PLN';
  const FRESHNESS_SLA_MS = 100 * 60 * 1000;
  const FRESHNESS_POLL_MS = 5 * 60 * 1000;
  let freshnessMode = 'unknown';

  function normalizeTab(name) {
    const value = String(name || '');
    return TAB_ALIASES[value] || value;
  }

  function isNavigableTab(name) {
    return VALID_TABS.has(normalizeTab(name));
  }

  function cssEscape(value) {
    if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(String(value));
    return String(value).replace(/[^a-zA-Z0-9_-]/g, '\\$&');
  }

  function panelFor(name) {
    name = normalizeTab(name);
    if (!VALID_TABS.has(name)) return null;
    return document.querySelector(`${APP_SELECTOR} .i10k-panel[data-panel="${cssEscape(name)}"]`);
  }

  function nameFromTrigger(trigger) {
    if (!trigger) return '';
    if (trigger.dataset?.tab) return String(trigger.dataset.tab);
    const href = trigger.getAttribute?.('href') || '';
    if (href.startsWith('#')) return href.slice(1);
    return '';
  }

  function allTriggers() {
    return [...document.querySelectorAll(`${DATA_TAB_SELECTOR}, ${PROJECT_HASH_SELECTOR}`)];
  }

  function activate(name, scroll = false) {
    name = normalizeTab(name);
    const panel = panelFor(name);
    if (!panel) return false;

    allTriggers().forEach(trigger => {
      const same = nameFromTrigger(trigger) === name;
      trigger.classList.toggle('active', same);
      if (trigger.tagName === 'BUTTON') trigger.disabled = false;
      trigger.style.pointerEvents = 'auto';
      trigger.style.touchAction = 'manipulation';
      trigger.setAttribute('aria-current', same ? 'page' : 'false');
      if (trigger.matches('.i10k-tabs [data-tab], .i10k-side-nav [data-tab]')) {
        trigger.setAttribute('aria-selected', same ? 'true' : 'false');
      }
    });

    document.querySelectorAll(PANEL_SELECTOR).forEach(item => {
      const active = item === panel;
      item.classList.toggle('active', active);
      item.hidden = !active;
      item.setAttribute('aria-hidden', active ? 'false' : 'true');
    });

    try {
      if (location.hash !== `#${name}`) history.replaceState(null, '', `#${name}`);
    } catch (_) {}

    if (scroll) window.scrollTo({ top: 0, behavior: 'auto' });
    document.body.dataset.investmentActiveTab = name;
    document.body.dataset.investmentNavigationGuard = 'active-v2';

    if (name === 'agents') requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
    try {
      window.dispatchEvent(new CustomEvent('briefrooms:investment-tab-change', { detail: { tab: name } }));
    } catch (_) {}
    return true;
  }

  function triggerFromEvent(event) {
    const target = event.target instanceof Element ? event.target : null;
    return target?.closest(`${DATA_TAB_SELECTOR}, ${PROJECT_HASH_SELECTOR}`) || null;
  }

  function consume(event, scroll) {
    const trigger = triggerFromEvent(event);
    const name = nameFromTrigger(trigger);
    if (!name || !isNavigableTab(name)) return false;
    if (!activate(name, scroll)) return false;
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    return true;
  }

  function handlePointerDown(event) {
    if (event.button !== undefined && event.button !== 0) return;
    consume(event, true);
  }

  function handleClick(event) {
    consume(event, false);
  }

  function handleKeyDown(event) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    consume(event, true);
  }

  function expectedRefreshWindow(now = new Date()) {
    const day = now.getUTCDay();
    const hour = now.getUTCHours();
    return day >= 1 && day <= 5 && hour >= 6 && hour <= 22;
  }

  function setStatusVisual(mode) {
    const status = document.querySelector('#data-status');
    const badge = document.querySelector('.live-badge');
    if (!status) return;

    status.classList.remove('portfolio-status-active', 'portfolio-status-stale', 'portfolio-status-after-hours');
    if (mode === 'stale') {
      status.textContent = `${isEn ? 'STALE DATA' : 'DANE OPÓŹNIONE'} · ${currency}`;
      status.classList.add('portfolio-status-stale');
      status.style.color = '#b42318';
      status.style.fontWeight = '900';
      if (badge) {
        badge.innerHTML = `<i></i> ${isEn ? 'STALE' : 'OPÓŹNIONE'}`;
        badge.dataset.automationStatus = 'stale';
      }
      return;
    }
    if (mode === 'after-hours') {
      status.textContent = `${isEn ? 'AFTER HOURS' : 'PO SESJI'} · ${currency}`;
      status.classList.add('portfolio-status-after-hours');
      status.style.color = '#667085';
      status.style.fontWeight = '800';
      if (badge) {
        badge.innerHTML = `<i></i> ${isEn ? 'AFTER HOURS' : 'PO SESJI'}`;
        badge.dataset.automationStatus = 'after-hours';
      }
      return;
    }
    status.textContent = `${isEn ? 'ACTIVE' : 'AKTYWNY'} · ${currency}`;
    status.classList.add('portfolio-status-active');
    status.style.color = '#15964d';
    status.style.fontWeight = '900';
    if (badge) {
      badge.innerHTML = `<i></i> ${isEn ? 'ACTIVE' : 'AKTYWNY'}`;
      badge.dataset.automationStatus = 'healthy';
    }
  }

  function applyPortfolioFreshness(portfolio) {
    const updated = new Date(portfolio?.last_updated_at || '');
    const now = new Date();
    if (Number.isNaN(updated.valueOf())) {
      freshnessMode = 'stale';
      setStatusVisual(freshnessMode);
      return;
    }
    const age = Math.max(0, now.valueOf() - updated.valueOf());
    if (expectedRefreshWindow(now) && age > FRESHNESS_SLA_MS) freshnessMode = 'stale';
    else if (!expectedRefreshWindow(now) && age > FRESHNESS_SLA_MS) freshnessMode = 'after-hours';
    else freshnessMode = 'active';
    document.body.dataset.portfolioFreshness = freshnessMode;
    document.body.dataset.portfolioFreshnessAgeMinutes = String(Math.round(age / 60000));
    setStatusVisual(freshnessMode);
  }

  function applyOperationalStatus() {
    if (freshnessMode !== 'unknown') {
      setStatusVisual(freshnessMode);
      return;
    }
    const status = document.querySelector('#data-status');
    if (!status) return;
    const text = String(status.textContent || '').trim().toUpperCase();
    const active = text.startsWith('AKTYWNY') || text.startsWith('ACTIVE');
    status.classList.toggle('portfolio-status-active', active);
    if (active) {
      status.style.color = '#15964d';
      status.style.fontWeight = '900';
    } else {
      status.style.removeProperty('color');
      status.style.removeProperty('font-weight');
    }
  }

  async function applyCashYieldLabel() {
    const url = isEn
      ? '/data/investments/portfolio_10k_usd.json'
      : '/data/investments/portfolio_10k.json';
    try {
      const response = await fetch(`${url}?freshness=${Date.now()}`, { cache: 'no-store' });
      if (!response.ok) return;
      const portfolio = await response.json();
      applyPortfolioFreshness(portfolio);
      const cashYield = portfolio?.cash_yield || {};
      const rate = Number(cashYield.rate_percent);
      if (!Number.isFinite(rate)) return;
      const cashValue = document.querySelector('#cash-value');
      const label = cashValue?.parentElement?.querySelector('small');
      if (!label) return;
      if (isEn) {
        label.textContent = `Cash · ${cashYield.label_en || `Fed midpoint ${rate.toFixed(3)}%`}`;
        label.title = 'Model cash earns the Federal Reserve target-range midpoint on an ACT/365 basis.';
      } else {
        label.textContent = `Gotówka · ${cashYield.label_pl || `NBP ${rate.toFixed(2).replace('.', ',')}%`}`;
        label.title = 'Gotówka modelowa jest oprocentowana stopą referencyjną NBP w konwencji ACT/365.';
      }
      document.body.dataset.portfolioCashYield = String(cashYield.benchmark || 'active');
    } catch (_) {
      // A failed supplemental fetch does not overwrite the last verified state.
    }
  }

  function loadExperimentRegistryUI() {
    if (document.querySelector('script[data-experiment-registry-ui]')) return;
    const script = document.createElement('script');
    script.src = '/scripts/portfolio-10k-experiment-registry.js?v=1';
    script.async = false;
    script.dataset.experimentRegistryUi = 'v1';
    document.head.appendChild(script);
  }

  function installInteractionLayer() {
    if (!document.getElementById('investment-navigation-v2-style')) {
      const style = document.createElement('style');
      style.id = 'investment-navigation-v2-style';
      style.textContent = `
        .i10k-tabs,.i10k-side-nav,.i10k-projects{position:relative;z-index:30;pointer-events:auto!important}
        .i10k-tabs [data-tab],.i10k-side-nav [data-tab],.i10k-projects a[href^="#"],.text-button[data-tab]{position:relative;z-index:31;pointer-events:auto!important;touch-action:manipulation}
        .i10k-panel[hidden]{display:none!important}
        .i10k-panel.active:not([hidden]){display:block!important}
        .top-meta #data-status.portfolio-status-active{color:#15964d!important;font-weight:900!important}
        .top-meta #data-status.portfolio-status-stale{color:#b42318!important;font-weight:900!important}
        .top-meta #data-status.portfolio-status-after-hours{color:#667085!important;font-weight:800!important}
      `;
      document.head.appendChild(style);
    }

    allTriggers().forEach(trigger => {
      if (trigger.tagName === 'BUTTON') trigger.disabled = false;
      trigger.style.pointerEvents = 'auto';
      trigger.style.touchAction = 'manipulation';
    });

    const hash = location.hash.slice(1);
    activate(isNavigableTab(hash) ? hash : 'overview', false);
    applyOperationalStatus();
    applyCashYieldLabel();
  }

  window.addEventListener('pointerdown', handlePointerDown, true);
  window.addEventListener('click', handleClick, true);
  window.addEventListener('keydown', handleKeyDown, true);
  window.addEventListener('hashchange', () => {
    const name = location.hash.slice(1);
    if (isNavigableTab(name)) activate(name, false);
  });

  const start = () => {
    loadExperimentRegistryUI();
    installInteractionLayer();
    const app = document.querySelector(APP_SELECTOR);
    if (app && typeof MutationObserver !== 'undefined') {
      let scheduled = false;
      new MutationObserver(() => {
        if (scheduled) return;
        scheduled = true;
        requestAnimationFrame(() => {
          scheduled = false;
          allTriggers().forEach(trigger => {
            if (trigger.tagName === 'BUTTON') trigger.disabled = false;
            trigger.style.pointerEvents = 'auto';
          });
          applyOperationalStatus();
        });
      }).observe(app, { childList: true, subtree: true, characterData: true });
    }
    window.setTimeout(applyCashYieldLabel, 1500);
    window.setInterval(applyCashYieldLabel, FRESHNESS_POLL_MS);
  };

  window.BriefRoomsInvestmentNavigation = { activate, version: 6 };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
