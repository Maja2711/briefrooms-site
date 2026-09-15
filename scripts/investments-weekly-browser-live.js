(() => {
  'use strict';

  const isEn = (document.documentElement.lang || 'pl').toLowerCase().startsWith('en');
  const BTC_POLL_MS = 15_000;
  const EUR_POLL_MS = 60_000;
  const REQUEST_TIMEOUT_MS = 8_000;
  const BTC_MAX_AGE_MS = 2 * 60_000;
  const EUR_MAX_AGE_MS = 5 * 60_000;

  const EUR_URL = 'https://www.currencyexchangetool.com/api/v1/convert?amount=1&from=EUR&to=USD';
  const BTC_TICKER_URL = 'https://api.exchange.coinbase.com/products/BTC-USD/ticker';
  const BTC_SPOT_URL = 'https://api.coinbase.com/v2/prices/BTC-USD/spot';

  const T = isEn ? {
    asOf: 'As of',
    live: 'LIVE',
    received: 'received',
    result: 'Result',
    points: 'pts',
  } : {
    asOf: 'Stan na',
    live: 'LIVE',
    received: 'pobrano',
    result: 'Wynik',
    points: 'pkt',
  };

  let selectedWeek = null;
  let inFlight = false;
  let lastEurFetchAt = 0;
  const quotes = new Map();

  const number = (value) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };

  const positive = (value) => {
    const parsed = number(value);
    return parsed !== null && parsed > 0 ? parsed : null;
  };

  function fmtPrice(value, instrumentId) {
    const digits = instrumentId === 'eurusd' ? 5 : 2;
    return Number(value).toLocaleString(isEn ? 'en-US' : 'pl-PL', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  function fmtMoney(value) {
    return `${value >= 0 ? '+' : ''}${value.toLocaleString(isEn ? 'en-US' : 'pl-PL', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })} USD`;
  }

  function fmtSigned(value, digits = 2) {
    return `${value >= 0 ? '+' : ''}${value.toLocaleString(isEn ? 'en-US' : 'pl-PL', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    })}`;
  }

  function fmtTime(value) {
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
    });
  }

  function quoteFresh(quote, maxAge) {
    if (!quote || positive(quote.price) === null) return false;
    const stamp = new Date(quote.updatedAt);
    if (Number.isNaN(stamp.valueOf())) return false;
    const age = Date.now() - stamp.valueOf();
    return age >= -60_000 && age <= maxAge;
  }

  async function fetchJson(url) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const separator = url.includes('?') ? '&' : '?';
      const response = await fetch(`${url}${separator}_=${Date.now()}`, {
        cache: 'no-store',
        mode: 'cors',
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`http_${response.status}`);
      return response.json();
    } finally {
      window.clearTimeout(timer);
    }
  }

  async function fetchEurUsd() {
    const data = await fetchJson(EUR_URL);
    if (!data || data.success === false) throw new Error('eurusd_api_error');
    const price = positive(data.rate ?? data.result);
    if (price === null || price < 0.8 || price > 1.5) throw new Error('eurusd_invalid_price');
    const sourceTime = data.updatedAt ? new Date(data.updatedAt) : new Date();
    if (Number.isNaN(sourceTime.valueOf())) throw new Error('eurusd_invalid_time');
    const quote = {
      price,
      updatedAt: sourceTime.toISOString(),
      source: 'FX mid-market',
      maxAge: EUR_MAX_AGE_MS,
    };
    if (!quoteFresh(quote, EUR_MAX_AGE_MS)) throw new Error('eurusd_stale');
    return quote;
  }

  async function fetchBtcUsd() {
    try {
      const data = await fetchJson(BTC_TICKER_URL);
      const price = positive(data?.price);
      const sourceTime = data?.time ? new Date(data.time) : new Date();
      if (price === null || price < 1_000 || price > 2_000_000) throw new Error('btc_invalid_price');
      if (Number.isNaN(sourceTime.valueOf())) throw new Error('btc_invalid_time');
      const quote = {
        price,
        updatedAt: sourceTime.toISOString(),
        source: 'Coinbase BTC-USD',
        maxAge: BTC_MAX_AGE_MS,
      };
      if (!quoteFresh(quote, BTC_MAX_AGE_MS)) throw new Error('btc_stale');
      return quote;
    } catch (primaryError) {
      const data = await fetchJson(BTC_SPOT_URL);
      const price = positive(data?.data?.amount);
      if (price === null || price < 1_000 || price > 2_000_000) throw primaryError;
      return {
        price,
        updatedAt: new Date().toISOString(),
        source: 'Coinbase BTC-USD spot',
        maxAge: BTC_MAX_AGE_MS,
      };
    }
  }

  function direction(item) {
    return item?.direction === 'short' ? 'short' : item?.direction === 'long' ? 'long' : 'neutral';
  }

  function isOpen(item) {
    return direction(item) !== 'neutral'
      && positive(item?.entry_price) !== null
      && positive(item?.exit_price) === null;
  }

  function notional(item) {
    return positive(item?.instrument_id === 'eurusd' ? item?.notional_eur : item?.notional_usd) || 10_000;
  }

  function resultText(item, mark) {
    const entry = positive(item?.entry_price);
    if (entry === null || positive(mark) === null || direction(item) === 'neutral') return null;
    const move = direction(item) === 'short' ? entry - mark : mark - entry;
    const percent = move / entry * 100;
    const value = item.instrument_id === 'eurusd'
      ? move * notional(item)
      : move / entry * notional(item);
    const parts = [fmtMoney(value)];
    if (item.instrument_id === 'eurusd') parts.push(`${fmtSigned(move / 0.0001, 1)} pips`);
    if (item.instrument_id === 'sp500_futures') parts.push(`${fmtSigned(move, 2)} ${T.points}`);
    parts.push(`${fmtSigned(percent, 2)}%`);
    return { text: parts.join(' · '), value };
  }

  function findResultCell(card) {
    return Array.from(card.querySelectorAll('.cell')).find((cell) =>
      String(cell.querySelector('dt')?.textContent || '').trim().toLowerCase() === T.result.toLowerCase());
  }

  function patchCard(item, index, quote) {
    if (!quoteFresh(quote, quote.maxAge)) return;
    const cards = document.querySelectorAll('#app .cards > .card');
    const card = cards[index];
    if (!card || card.classList.contains('integrity-withheld')) return;

    const nowBox = card.querySelector('.now');
    const priceNode = nowBox?.querySelector('strong');
    const timeNode = nowBox?.querySelector('small');
    if (!nowBox || !priceNode || !timeNode) return;

    priceNode.textContent = fmtPrice(quote.price, item.instrument_id);
    timeNode.textContent = `${T.asOf}: ${fmtTime(quote.updatedAt)} · ${T.live} · ${quote.source}`;
    timeNode.style.color = '#72f0c1';
    nowBox.dataset.liveSource = quote.source;
    nowBox.dataset.liveAt = quote.updatedAt;

    if (!isOpen(item)) return;
    const result = resultText(item, quote.price);
    const resultCell = findResultCell(card);
    const resultNode = resultCell?.querySelector('dd');
    if (!result || !resultNode) return;
    resultNode.textContent = result.text;
    resultNode.classList.toggle('positive', result.value > 0);
    resultNode.classList.toggle('negative', result.value < 0);
    resultNode.classList.toggle('neutral', Math.abs(result.value) < 0.000001);
  }

  function applyCachedQuotes() {
    if (!selectedWeek || !Array.isArray(selectedWeek.instruments)) return;
    selectedWeek.instruments.forEach((item, index) => {
      const quote = quotes.get(item.instrument_id);
      if (quote) patchCard(item, index, quote);
    });
  }

  async function refreshLive({ forceEur = false } = {}) {
    if (document.hidden || inFlight) return;
    inFlight = true;
    try {
      const tasks = [];
      const now = Date.now();
      if (forceEur || now - lastEurFetchAt >= EUR_POLL_MS) {
        lastEurFetchAt = now;
        tasks.push(fetchEurUsd()
          .then((quote) => quotes.set('eurusd', quote))
          .catch((error) => console.warn('BriefRooms Weekly EUR/USD live fallback:', error?.message || error)));
      }
      tasks.push(fetchBtcUsd()
        .then((quote) => quotes.set('btcusd', quote))
        .catch((error) => console.warn('BriefRooms Weekly BTC/USD live fallback:', error?.message || error)));
      await Promise.allSettled(tasks);
      applyCachedQuotes();
    } finally {
      inFlight = false;
    }
  }

  document.addEventListener('br:weekly-rendered', (event) => {
    selectedWeek = event?.detail || null;
    applyCachedQuotes();
    refreshLive();
  });

  const timer = window.setInterval(() => refreshLive(), BTC_POLL_MS);
  window.setTimeout(() => refreshLive({ forceEur: true }), 900);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refreshLive({ forceEur: true });
  });
  window.addEventListener('pagehide', () => window.clearInterval(timer), { once: true });
})();
