(() => {
  'use strict';

  const isEn = (document.documentElement.lang || 'pl').toLowerCase().startsWith('en');

  const fmtStamp = (value) => {
    const date = new Date(value);
    if (Number.isNaN(date.valueOf())) return '';
    return date.toLocaleString(isEn ? 'en-GB' : 'pl-PL', {
      timeZone: 'Europe/Warsaw',
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    }).replace(',', ' ·');
  };

  function compactTime(nowBox) {
    const timeNode = nowBox?.querySelector('small');
    if (!timeNode) return;

    const liveAt = nowBox.dataset.liveAt;
    if (liveAt) {
      const next = fmtStamp(liveAt);
      if (next && timeNode.textContent !== next) timeNode.textContent = next;
      return;
    }

    const raw = String(timeNode.textContent || '').trim();
    if (!raw) return;
    const match = raw.match(/(\d{2}\.\d{2}\.\d{4})[, ·]+([0-2]\d:[0-5]\d(?::[0-5]\d)?)/);
    if (!match) return;
    const next = `${match[1]} · ${match[2]}`;
    if (timeNode.textContent !== next) timeNode.textContent = next;
  }

  function compactAll() {
    document.querySelectorAll('#app .cards > .card .now').forEach(compactTime);
  }

  let scheduled = false;
  const observer = new MutationObserver(() => {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(() => {
      scheduled = false;
      compactAll();
    });
  });

  const app = document.getElementById('app');
  if (app) observer.observe(app, { childList: true, subtree: true, characterData: true, attributes: true });

  document.addEventListener('br:weekly-rendered', compactAll);
  window.setTimeout(compactAll, 100);
  const timer = window.setInterval(compactAll, 15_000);
  window.addEventListener('pagehide', () => {
    window.clearInterval(timer);
    observer.disconnect();
  }, { once: true });
})();
