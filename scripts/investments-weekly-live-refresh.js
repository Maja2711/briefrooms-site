(() => {
  'use strict';

  const LIVE_URL = '/data/investments/live_prices.json';
  const OPEN_REFRESH_MS = 5_000;
  const IDLE_REFRESH_MS = 15_000;
  const HIDDEN_REFRESH_MS = 60_000;
  const FEED_STALE_MS = 12 * 60_000;
  const isPl = (document.documentElement.lang || 'pl').toLowerCase().startsWith('pl');
  const locale = isPl ? 'pl-PL' : 'en-GB';
  const timeZone = 'Europe/Warsaw';

  let timer = null;
  let inFlight = false;
  let lastFeed = null;

  function feedTimestamp(feed) {
    return feed?.updated_at || feed?.generated_at || null;
  }

  function keyFromCard(card) {
    const label = (card.querySelector('.head p')?.textContent || '')
      .toUpperCase()
      .replace(/\s+/g, ' ')
      .trim();
    if (label.includes('EUR/USD') || label.includes('EURUSD')) return 'eurusd';
    if (label.includes('BTC/USD') || label.includes('BTCUSD')) return 'btcusd';
    if (label.includes('S&P 500') || label.includes('SP 500')) return 'sp500_futures';
    return null;
  }

  function formatPrice(key, value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    const digits = key === 'eurusd' ? 5 : 2;
    return new Intl.NumberFormat(locale, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(number);
  }

  function formatTime(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '—';
    return new Intl.DateTimeFormat(locale, {
      timeZone,
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    }).format(date);
  }

  function updateCards(feed) {
    const prices = feed?.prices;
    if (!prices || typeof prices !== 'object') return;

    document.querySelectorAll('#app .card').forEach((card) => {
      const key = keyFromCard(card);
      if (!key) return;
      const quote = prices[key];
      const price = Number(quote?.price);
      if (!Number.isFinite(price)) return;

      const now = card.querySelector('.now');
      const value = now?.querySelector('strong');
      const asOf = now?.querySelector('small');
      if (value) value.textContent = formatPrice(key, price);
      if (asOf) {
        const stamp = quote.current_price_updated_at || quote.timestamp || feedTimestamp(feed);
        asOf.textContent = `${isPl ? 'Stan na' : 'As of'}: ${formatTime(stamp)}`;
      }
    });
  }

  function updateFeedStatus(feed) {
    const updated = document.getElementById('updated');
    if (!updated) return;

    const stamp = feedTimestamp(feed);
    const stampMs = stamp ? new Date(stamp).getTime() : NaN;
    const stale = !Number.isFinite(stampMs) || Date.now() - stampMs > FEED_STALE_MS;

    updated.classList.toggle('stale', stale);
    updated.setAttribute('role', 'status');
    updated.setAttribute('aria-live', 'polite');

    if (stale) {
      updated.textContent = stamp
        ? `${isPl ? '⚠ Dane cenowe opóźnione · ostatni feed' : '⚠ Price feed delayed · last feed'}: ${formatTime(stamp)}`
        : (isPl ? '⚠ Brak aktualnego feedu cenowego' : '⚠ Current price feed unavailable');
      return;
    }

    updated.textContent = `${isPl ? 'Aktualizacja cen' : 'Price update'}: ${formatTime(stamp)}`;
  }

  function apply(feed) {
    if (!feed) return;
    updateCards(feed);
    updateFeedStatus(feed);
  }

  function hasOpenPosition() {
    if (document.querySelector('#app .br-weekly-position-state.is-open')) return true;
    return Array.from(document.querySelectorAll('#app .card')).some((card) => {
      for (const cell of card.querySelectorAll('.cell')) {
        const key = (cell.querySelector('dt')?.textContent || '').trim().toLowerCase();
        if (key !== 'status') continue;
        const value = (cell.querySelector('dd')?.textContent || '').trim().toLowerCase();
        if (value === 'w trakcie' || value === 'in progress' || value === 'open') return true;
      }
      return false;
    });
  }

  function nextDelay() {
    if (document.hidden) return HIDDEN_REFRESH_MS;
    return hasOpenPosition() ? OPEN_REFRESH_MS : IDLE_REFRESH_MS;
  }

  function schedule(delay = nextDelay()) {
    window.clearTimeout(timer);
    timer = window.setTimeout(poll, delay);
  }

  async function poll() {
    if (inFlight) {
      schedule();
      return;
    }

    inFlight = true;
    try {
      const response = await fetch(`${LIVE_URL}?v=${Date.now()}`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      lastFeed = await response.json();
      apply(lastFeed);
    } catch (error) {
      console.warn('Weekly live-price refresh failed', error);
    } finally {
      inFlight = false;
      schedule();
    }
  }

  function refreshNow() {
    window.clearTimeout(timer);
    if (document.hidden) {
      schedule();
      return;
    }
    poll();
  }

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') refreshNow();
    else schedule();
  });
  window.addEventListener('focus', refreshNow);
  document.addEventListener('br:weekly-rendered', () => {
    if (lastFeed) apply(lastFeed);
    schedule(0);
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', refreshNow, { once: true });
  } else {
    refreshNow();
  }
})();
