(() => {
  'use strict';

  const isEn = (document.documentElement.lang || 'pl').toLowerCase().startsWith('en');
  const EURUSD_URL = 'https://www.currencyexchangetool.com/api/v1/convert?amount=1&from=EUR&to=USD';
  const BACKEND_URL = '/data/investments/live_prices.json';
  const EUR_REFRESH_MS = 60_000;
  const ES_REFRESH_MS = 15_000;
  const EUR_LIVE_MAX_AGE_MS = 5 * 60_000;
  const ES_USABLE_MAX_AGE_MS = 45 * 60_000;
  const ES_DISPLAY_DELAY_MS = 5 * 60_000;
  const REQUEST_TIMEOUT_MS = 8_000;
  let lastEsQuote = null;

  const number = (value) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };

  const fmtPrice = (value, digits) => Number(value).toLocaleString(isEn ? 'en-US' : 'pl-PL', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
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
    if (sp) cleanMeta(sp, ES_DISPLAY_DELAY_MS);
    if (btc) cleanMeta(btc, 2 * 60_000);
  }

  async function fetchResponse(url) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(url, {
        cache: 'no-store',
        mode: 'cors',
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`http_${response.status}`);
      return response;
    } finally {
      window.clearTimeout(timer);
    }
  }

  async function fetchJson(url) {
    return (await fetchResponse(url)).json();
  }

  async function fetchText(url) {
    return (await fetchResponse(url)).text();
  }

  async function fetchEurUsdLikeDaily() {
    const data = await fetchJson(`${EURUSD_URL}&_=${Date.now()}`);
    if (!data || data.success === false) throw new Error('eurusd_api_error');

    const price = number(data.rate ?? data.result);
    if (price === null || price < 0.8 || price > 1.5) throw new Error('eurusd_invalid_price');

    const sourceTime = data.updatedAt ? new Date(data.updatedAt) : new Date();
    if (Number.isNaN(sourceTime.valueOf())) throw new Error('eurusd_invalid_timestamp');
    const age = Date.now() - sourceTime.valueOf();
    if (age < -60_000 || age > EUR_LIVE_MAX_AGE_MS) throw new Error('eurusd_stale');

    return { price, updatedAt: sourceTime.toISOString(), source: 'daily-eurusd-direct' };
  }

  function warsawYmd() {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Europe/Warsaw',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).formatToParts(new Date());
    const values = Object.fromEntries(parts.filter((part) => part.type !== 'literal').map((part) => [part.type, part.value]));
    return { year: Number(values.year), month: Number(values.month), day: Number(values.day) };
  }

  function thirdFridayUtc(year, month) {
    const first = new Date(Date.UTC(year, month - 1, 1));
    const firstFriday = 1 + ((5 - first.getUTCDay() + 7) % 7);
    return new Date(Date.UTC(year, month - 1, firstFriday + 14));
  }

  function nextQuarter(year, month) {
    if (month === 3) return { year, month: 6 };
    if (month === 6) return { year, month: 9 };
    if (month === 9) return { year, month: 12 };
    return { year: year + 1, month: 3 };
  }

  function activeEsContract() {
    const local = warsawYmd();
    const quarters = [3, 6, 9, 12];
    let year = local.year;
    let month = quarters.find((candidate) => local.month <= candidate) || 3;
    if (!quarters.some((candidate) => local.month <= candidate)) year += 1;

    if (local.month === month) {
      const today = new Date(Date.UTC(local.year, local.month - 1, local.day));
      const expiry = thirdFridayUtc(year, month);
      const rollStart = new Date(expiry.valueOf() - 8 * 24 * 60 * 60 * 1000);
      if (today >= rollStart) ({ year, month } = nextQuarter(year, month));
    }

    const code = { 3: 'H', 6: 'M', 9: 'U', 12: 'Z' }[month];
    const yy = String(year).slice(-2);
    return {
      yahoo: `ES${code}${yy}.CME`,
      esignal: `ES ${code}${yy}`,
    };
  }

  function quoteTime(quote) {
    const value = new Date(quote?.updatedAt || 0).valueOf();
    return Number.isFinite(value) ? value : 0;
  }

  function newestQuote(...quotes) {
    return quotes.filter(Boolean).sort((a, b) => quoteTime(b) - quoteTime(a))[0] || null;
  }

  function validateEsQuote(quote) {
    const price = number(quote?.price);
    const stamp = new Date(quote?.updatedAt || 0);
    if (price === null || price < 500 || price > 100_000 || Number.isNaN(stamp.valueOf())) {
      throw new Error('es_invalid_quote');
    }
    const age = Date.now() - stamp.valueOf();
    if (age < -60_000 || age > ES_USABLE_MAX_AGE_MS) throw new Error('es_quote_too_old');
    return { ...quote, price, updatedAt: stamp.toISOString() };
  }

  function proxyUrl(upstream, route) {
    return route === 'allorigins'
      ? `https://api.allorigins.win/raw?url=${encodeURIComponent(upstream)}&_=${Date.now()}`
      : `https://api.codetabs.com/v1/proxy?quest=${encodeURIComponent(upstream)}&_=${Date.now()}`;
  }

  async function withProxyFallback(upstream, parser) {
    let lastError = null;
    for (const route of ['codetabs', 'allorigins']) {
      try {
        return await parser(proxyUrl(upstream, route));
      } catch (error) {
        lastError = error;
      }
    }
    throw lastError || new Error('proxy_unavailable');
  }

  async function fetchYahooEsSymbol(symbol) {
    const upstream = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?interval=1m&range=1d`;
    return withProxyFallback(upstream, async (url) => {
      const data = await fetchJson(url);
      const chart = data?.chart?.result?.[0];
      if (!chart) throw new Error(`es_chart_missing_${symbol}`);
      const timestamps = Array.isArray(chart.timestamp) ? chart.timestamp : [];
      const closes = chart?.indicators?.quote?.[0]?.close || [];
      for (let index = Math.min(timestamps.length, closes.length) - 1; index >= 0; index -= 1) {
        const price = number(closes[index]);
        const epoch = number(timestamps[index]);
        if (price === null || epoch === null || price < 500 || price > 100_000) continue;
        return validateEsQuote({
          price,
          updatedAt: new Date(epoch * 1000).toISOString(),
          source: `Yahoo ${symbol}`,
        });
      }
      throw new Error(`es_quote_missing_${symbol}`);
    });
  }

  function zonedLocalToUtc(year, month, day, hour, minute, second, timeZone) {
    const target = Date.UTC(year, month - 1, day, hour, minute, second);
    let guess = target;
    const formatter = new Intl.DateTimeFormat('en-CA', {
      timeZone,
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      hourCycle: 'h23',
    });
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const parts = formatter.formatToParts(new Date(guess));
      const values = Object.fromEntries(parts.filter((part) => part.type !== 'literal').map((part) => [part.type, part.value]));
      const rendered = Date.UTC(
        Number(values.year), Number(values.month) - 1, Number(values.day),
        Number(values.hour), Number(values.minute), Number(values.second),
      );
      const delta = target - rendered;
      guess += delta;
      if (Math.abs(delta) < 1000) break;
    }
    return new Date(guess);
  }

  async function fetchEsignalEs() {
    const contract = activeEsContract();
    const upstream = `https://quotes.esignal.com/esignalprod/quote.action?symbol=${encodeURIComponent(contract.esignal)}&types=future`;
    return withProxyFallback(upstream, async (url) => {
      const markup = await fetchText(url);
      const parsed = new DOMParser().parseFromString(markup, 'text/html');
      const text = String(parsed.body?.textContent || '').replace(/\s+/g, ' ').trim();
      const priceMatch = text.match(/Last:\s*([0-9,]+(?:\.[0-9]+)?)/i);
      const timeMatch = text.match(/Time of last trade:\s*([A-Za-z]{3})\s+(\d{1,2})\s+(\d{4})\s+(\d{2}):(\d{2}):(\d{2})/i);
      if (!priceMatch || !timeMatch) throw new Error('esignal_es_parse_failed');

      const months = { Jan: 1, Feb: 2, Mar: 3, Apr: 4, May: 5, Jun: 6, Jul: 7, Aug: 8, Sep: 9, Oct: 10, Nov: 11, Dec: 12 };
      const month = months[timeMatch[1].slice(0, 1).toUpperCase() + timeMatch[1].slice(1, 3).toLowerCase()];
      if (!month) throw new Error('esignal_es_month_invalid');
      const stamp = zonedLocalToUtc(
        Number(timeMatch[3]), month, Number(timeMatch[2]),
        Number(timeMatch[4]), Number(timeMatch[5]), Number(timeMatch[6]),
        'America/New_York',
      );
      return validateEsQuote({
        price: Number(priceMatch[1].replace(/,/g, '')),
        updatedAt: stamp.toISOString(),
        source: `eSignal ${contract.esignal}`,
      });
    });
  }

  async function fetchBackendEs() {
    const data = await fetchJson(`${BACKEND_URL}?_=${Date.now()}`);
    const row = data?.prices?.sp500_futures;
    if (!row) throw new Error('backend_es_missing');
    return validateEsQuote({
      price: row.price,
      updatedAt: row.current_price_updated_at || row.timestamp,
      source: 'BriefRooms backend',
    });
  }

  async function fetchBestEs() {
    const contract = activeEsContract();
    const attempts = await Promise.allSettled([
      fetchEsignalEs(),
      fetchYahooEsSymbol(contract.yahoo),
      fetchYahooEsSymbol('ES=F'),
      fetchBackendEs(),
    ]);
    const quotes = attempts.filter((item) => item.status === 'fulfilled').map((item) => item.value);
    const best = newestQuote(...quotes);
    if (best) return best;
    const errors = attempts.filter((item) => item.status === 'rejected').map((item) => item.reason?.message || String(item.reason));
    throw new Error(errors.join('; ') || 'es_unavailable');
  }

  function setQuote(label, quote, digits, delayThresholdMs, source) {
    const card = cardByLabel(label);
    const nowBox = card?.querySelector('.now');
    const priceNode = nowBox?.querySelector('strong');
    const timeNode = nowBox?.querySelector('small');
    if (!nowBox || !priceNode || !timeNode) return;

    const existingAt = new Date(nowBox.dataset.liveAt || 0).valueOf() || 0;
    const quoteAt = new Date(quote.updatedAt).valueOf() || 0;
    if (existingAt > quoteAt) return;

    const ageMs = Math.max(0, Date.now() - quoteAt);
    const minutes = Math.round(ageMs / 60_000);
    const delayed = ageMs > delayThresholdMs;
    const nextPrice = fmtPrice(quote.price, digits);
    const nextTime = delayed
      ? `${fmtStamp(quote.updatedAt)} · ${isEn ? 'delayed' : 'opóźniony'} ${minutes} min`
      : fmtStamp(quote.updatedAt);
    const nextStatus = delayed ? 'delayed' : 'live';

    if (priceNode.textContent !== nextPrice) priceNode.textContent = nextPrice;
    if (timeNode.textContent !== nextTime) timeNode.textContent = nextTime;
    timeNode.style.color = delayed ? '#ffb86b' : '#72f0c1';
    if (nowBox.dataset.feedStatus !== nextStatus) nowBox.dataset.feedStatus = nextStatus;
    if (nowBox.dataset.liveAt !== quote.updatedAt) nowBox.dataset.liveAt = quote.updatedAt;
    if (nowBox.dataset.liveSource !== source) nowBox.dataset.liveSource = source;
  }

  async function refreshEurUsd() {
    try {
      const quote = await fetchEurUsdLikeDaily();
      setQuote('EUR/USD', quote, 5, EUR_LIVE_MAX_AGE_MS, quote.source);
    } catch (error) {
      console.warn('BriefRooms Weekly EUR/USD direct feed fallback:', error?.message || error);
      compactAll();
    }
  }

  async function refreshEs() {
    try {
      const quote = await fetchBestEs();
      lastEsQuote = newestQuote(lastEsQuote, quote);
      setQuote('S&P 500 FUTURES', lastEsQuote, 2, ES_DISPLAY_DELAY_MS, lastEsQuote.source);
    } catch (error) {
      console.warn('BriefRooms Weekly ES futures feed fallback:', error?.message || error);
      compactAll();
    }
  }

  let compacting = false;
  const observer = new MutationObserver(() => {
    if (compacting) return;
    compacting = true;
    try {
      compactAll();
      if (lastEsQuote) setQuote('S&P 500 FUTURES', lastEsQuote, 2, ES_DISPLAY_DELAY_MS, lastEsQuote.source);
    } finally {
      compacting = false;
    }
  });

  const app = document.getElementById('app');
  if (app) observer.observe(app, { childList: true, subtree: true, characterData: true, attributes: true });

  function refreshAll() {
    compactAll();
    refreshEurUsd();
    refreshEs();
  }

  document.addEventListener('br:weekly-rendered', refreshAll);
  window.setTimeout(refreshAll, 100);

  const eurTimer = window.setInterval(refreshEurUsd, EUR_REFRESH_MS);
  const esTimer = window.setInterval(refreshEs, ES_REFRESH_MS);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refreshAll();
  });
  window.addEventListener('pagehide', () => {
    window.clearInterval(eurTimer);
    window.clearInterval(esTimer);
    observer.disconnect();
  }, { once: true });
})();
