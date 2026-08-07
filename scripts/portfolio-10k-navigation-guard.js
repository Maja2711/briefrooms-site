(() => {
  'use strict';

  const APP_SELECTOR = '.i10k-app';
  const TAB_SELECTOR = `${APP_SELECTOR} [data-tab]`;

  function panelFor(name) {
    if (!name) return null;
    return document.querySelector(`.i10k-panel[data-panel="${CSS.escape(String(name))}"]`);
  }

  function activate(name, scroll = false) {
    const panel = panelFor(name);
    if (!panel) return false;

    document.querySelectorAll(TAB_SELECTOR).forEach(trigger => {
      trigger.classList.toggle('active', trigger.dataset.tab === name);
      if (trigger.tagName === 'BUTTON') trigger.disabled = false;
      trigger.style.pointerEvents = 'auto';
    });
    document.querySelectorAll('.i10k-panel[data-panel]').forEach(item => {
      item.classList.toggle('active', item === panel);
    });

    try {
      if (location.hash !== `#${name}`) history.replaceState(null, '', `#${name}`);
    } catch (_) {}

    if (scroll) window.scrollTo({ top: 0, behavior: 'auto' });
    document.body.dataset.investmentActiveTab = String(name);
    document.body.dataset.investmentNavigationGuard = 'active';
    return true;
  }

  function triggerFromEvent(event) {
    const target = event.target instanceof Element ? event.target : null;
    return target?.closest(TAB_SELECTOR) || null;
  }

  function handlePointerDown(event) {
    if (event.button !== undefined && event.button !== 0) return;
    const trigger = triggerFromEvent(event);
    const name = trigger?.dataset?.tab;
    if (!name) return;
    if (activate(name, true)) event.preventDefault();
  }

  function handleClick(event) {
    const trigger = triggerFromEvent(event);
    const name = trigger?.dataset?.tab;
    if (!name) return;
    if (activate(name, false)) event.preventDefault();
  }

  function handleKeyDown(event) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const trigger = triggerFromEvent(event);
    const name = trigger?.dataset?.tab;
    if (!name) return;
    if (activate(name, true)) event.preventDefault();
  }

  function enableTriggers() {
    document.querySelectorAll(TAB_SELECTOR).forEach(trigger => {
      if (trigger.tagName === 'BUTTON') trigger.disabled = false;
      trigger.style.pointerEvents = 'auto';
    });
    const hash = location.hash.slice(1);
    if (hash) activate(hash, false);
  }

  // Window capture runs before document-level handlers, so navigation remains
  // usable even if a legacy enrichment module later interferes with click.
  window.addEventListener('pointerdown', handlePointerDown, true);
  window.addEventListener('click', handleClick, true);
  window.addEventListener('keydown', handleKeyDown, true);
  window.addEventListener('hashchange', () => activate(location.hash.slice(1), false));

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', enableTriggers, { once: true });
  } else {
    enableTriggers();
  }
})();
