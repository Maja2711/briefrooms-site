(() => {
  'use strict';

  const isEn = (document.documentElement.lang || 'pl').toLowerCase().startsWith('en');
  const EURUSD_URL = 'https://www.currencyexchangetool.com/api/v1/convert?amount=1&from=EUR&to=USD';
  const REFRESH_MS = 60_000;
  const LIVE_MAX_AGE_MS = 5 * 60_000;

  const number = (value) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };

  const fmtPrice = (value) => Number(value).toLocaleString(isEn ? 'en-US' : 'pl-PL', {
    minimumFractionDigits: 5,
    maximumFractionDigits: 5,
  });

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

  function cardByLabel(label) {
    return Array.from(document.querySelectorAll('#app .cards > .card')).find((card) =>
      String(card.querySelector('.head p')?.textContent || '').trim().toUpperCase() === label.toUpperCase());
  }

  function cleanMeta(nowBox, maxAgeMs) {
    const timeNode = nowBox?.querySelector('small');
    if (!timeNode) return;

    const liveAt = nowBox.dataset.liveAt;
    if (liveAt) {
      const stamp = new Date(liveAt);
      if (!Number.isNaN(stamp.valueOf())) {
        const ageMs = Math.max(0, Date.now() - stamp.valueOf());
        const minutes = Math.round(ageMs / 60_000);
        const delayed = ageMs > maxAgeMs;
        const next = delayed
          ? `${fmtStamp(liveAt)} · ${isEn ? 'delayed' : 'opóźniony'} ${minutes} min`
          : fmtStamp(liveAt);
        if (timeNode.textContent !== next) timeNode.textContent = next;
        timeNode.style.color = delayed ? '#ffb86b' : '#72f0c1';
        return;
      }
    }

    const raw = String(timeNode.textContent || '').trim();
    if (!raw) return;
    const match = raw.match(/(\d{2}\.\d{2}\.\d{4})[, ]+([0-2]\d:[0-5]\d(?::[0-5]\d)?)/);
    if (match) {
      const next = `${match[1]} · ${match[2]}`;
      if (timeNode.textContent !== next) timeNode.textContent = next;
      timeNode.style.color = '';
    }
  }

  function compactAll() {
    const eur = cardByLabel('EUR/USD')?.querySelector('.now');
    const sp = cardByLabel('S&P 500 FUTURES')?.querySelector('.now');
    const btc = cardByLabel('BTC/USD')?.querySelector('.now');
    if (eur) cleanMeta(eur, 5 * 60_000);
    if (sp) cleanMeta(sp, 5 * 60_000);
    if (btc) cleanMeta(btc, 2 * 60_000);
  }

  async function fetchEurUsdLikeDaily() {
    const response = await fetch(`${EURUSD_URL}&_=${Date.now()}`, {
      cache: 'no-store',
      mode: 'cors',
    });
    if (!response.ok) throw new Error(`eurusd_http_${response.status}`);
    const data = await response.json();
    if (!data || data.success === false) throw new Error('eurusd_api_error');

    const price = number(data.rate ?? data.result);
    if (price === null || price < 0.8 || price > 1.5) throw new Error('eurusd_invalid_price');

    const sourceTime = data.updatedAt ? new Date(data.updatedAt) : new Date();
    if (Number.isNaN(sourceTime.valueOf())) throw new Error('eurusd_invalid_timestamp');
    const age = Date.now() - sourceTime.valueOf();
    if (age < -60_000 || age > LIVE_MAX_AGE_MS) throw new Error('eurusd_stale');

    return { price, updatedAt: sourceTime.toISOString() };
  }

  async function refreshEurUsd() {
    try {
      const quote = await fetchEurUsdLikeDaily();
      const card = cardByLabel('EUR/USD');
      const nowBox = card?.querySelector('.now');
      const priceNode = nowBox?.querySelector('strong');
      const timeNode = nowBox?.querySelector('small');
      if (!nowBox || !priceNode || !timeNode) return;

      priceNode.textContent = fmtPrice(quote.price);
      timeNode.textContent = fmtStamp(quote.updatedAt);
      timeNode.style.color = '#72f0c1';
      nowBox.dataset.feedStatus = 'live';
      nowBox.dataset.liveAt = quote.updatedAt;
      nowBox.dataset.liveSource = 'daily-eurusd-direct';
    } catch (error) {
      console.warn('BriefRooms Weekly EUR/USD direct feed fallback:', error?.message || error);
      compactAll();
    }
  }

  let compacting = false;
  const observer = new MutationObserver(() => {
    if (compacting) return;
    compacting = true;
    try { compactAll(); } finally { compacting = false; }
  });

  const app = document.getElementById('app');
  if (app) observer.observe(app, { childList: true, subtree: true, characterData: true, attributes: true });

  document.addEventListener('br:weekly-rendered', () => {
    compactAll();
    refreshEurUsd();
  });

  window.setTimeout(() => {
    compactAll();
    refreshEurUsd();
  }, 100);

  const timer = window.setInterval(refreshEurUsd, REFRESH_MS);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refreshEurUsd();
  });
  window.addEventListener('pagehide', () => {
    window.clearInterval(timer);
    observer.disconnect();
  }, { once: true });
})();
