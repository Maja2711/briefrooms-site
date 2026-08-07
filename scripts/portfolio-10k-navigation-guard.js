(() => {
  'use strict';

  const APP_SELECTOR = '.i10k-app';
  const DATA_TAB_SELECTOR = `${APP_SELECTOR} [data-tab]`;
  const PROJECT_HASH_SELECTOR = `${APP_SELECTOR} .i10k-projects a[href^="#"]`;
  const PANEL_SELECTOR = `${APP_SELECTOR} .i10k-panel[data-panel]`;
  const VALID_TABS = new Set(['overview','portfolio','benchmark','agents','projections','rules','brace','analytics','history']);

  function cssEscape(value) {
    if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(String(value));
    return String(value).replace(/[^a-zA-Z0-9_-]/g, '\\$&');
  }

  function panelFor(name) {
    if (!VALID_TABS.has(String(name || ''))) return null;
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
    name = String(name || '');
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

    if (name === 'agents') {
      requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
    }
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
    if (!name || !VALID_TABS.has(name)) return false;
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

  function installInteractionLayer() {
    if (!document.getElementById('investment-navigation-v2-style')) {
      const style = document.createElement('style');
      style.id = 'investment-navigation-v2-style';
      style.textContent = `
        .i10k-tabs,.i10k-side-nav,.i10k-projects{position:relative;z-index:30;pointer-events:auto!important}
        .i10k-tabs [data-tab],.i10k-side-nav [data-tab],.i10k-projects a[href^="#"],.text-button[data-tab]{position:relative;z-index:31;pointer-events:auto!important;touch-action:manipulation}
        .i10k-panel[hidden]{display:none!important}
        .i10k-panel.active:not([hidden]){display:block!important}
      `;
      document.head.appendChild(style);
    }

    allTriggers().forEach(trigger => {
      if (trigger.tagName === 'BUTTON') trigger.disabled = false;
      trigger.style.pointerEvents = 'auto';
      trigger.style.touchAction = 'manipulation';
    });

    const hash = location.hash.slice(1);
    activate(VALID_TABS.has(hash) ? hash : 'overview', false);
  }

  // Capture on window is deliberately first in the event path. Once a valid
  // Investment Room navigation target is handled, legacy listeners do not get
  // the same event and therefore cannot switch the view back afterwards.
  window.addEventListener('pointerdown', handlePointerDown, true);
  window.addEventListener('click', handleClick, true);
  window.addEventListener('keydown', handleKeyDown, true);
  window.addEventListener('hashchange', () => {
    const name = location.hash.slice(1);
    if (VALID_TABS.has(name)) activate(name, false);
  });

  const start = () => {
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
        });
      }).observe(app, { childList: true, subtree: true });
    }
  };

  window.BriefRoomsInvestmentNavigation = { activate, version: 2 };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
