(() => {
  'use strict';

  const WEEK_ID = /\b\d{4}-W\d{2}\b/;

  function weekIdFrom(button) {
    const explicit = button?.dataset?.weekId;
    if (explicit) return explicit;
    const match = String(button?.textContent || '').match(WEEK_ID);
    return match ? match[0] : '';
  }

  function markActive(button) {
    document.querySelectorAll('#app .week-tab').forEach((tab) => {
      const active = tab === button;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
  }

  function selectWeek(button) {
    const weekId = weekIdFrom(button);
    if (!weekId || typeof window.BR_WEEKLY_SELECT !== 'function') return;
    markActive(button);
    window.BR_WEEKLY_SELECT(weekId);
  }

  document.addEventListener('click', (event) => {
    const target = event.target instanceof Element
      ? event.target.closest('#app .week-tab')
      : null;
    if (!target) return;

    // Capture the interaction before the legacy inline onclick handler. This
    // keeps the tabs working even when inline event handlers are restricted.
    event.preventDefault();
    event.stopPropagation();
    selectWeek(target);
  }, true);
})();
