(() => {
  'use strict';

  const isEn = (document.documentElement.lang || 'pl').toLowerCase().startsWith('en');
  const LOOP_MS = 15_000;
  const BACKEND_POLL_MS = 60_000;
  const REQUEST_TIMEOUT_MS = 6_000;
  const CACHE_PREFIX = 'briefrooms:weekly-market-feed:v6:';
  const BACKEND_URL = '/data/investments/live_prices.json';

  const T = isEn ? {
    backend: 'BriefRooms backend',
    result: 'Result',
    points: 'pts',
  } : {
    backend: 'backend BriefRooms',
    result: 'Wynik',
    points: 'pkt',
  };

  const FEEDS = {
    eurusd: {
      pollMs: 60_000,
      maxAgeMs: 10 * 60_000,
      minPrice: 0.8,
      maxPrice: 1.5,
      directPriority: 'first-fresh',
      directAuthoritativeWhenFresh: true,
      sources: [
        { name: 'fxapi.app', fetch: fetchEurUsdFxApi },
        { name: 'Currency Exchange Tool', fetch: fetchEurUsdCurrencyExchangeTool },
      ],
    },
    btcusd: {
      pollMs: 15_000,
      maxAgeMs: 2 * 60_000,
      backendMaxAgeMs: 7 * 60_000,
      minPrice: 1_000,
      maxPrice: 2_000_000,
      sources: [
        { name: 'Coinbase BTC-USD', fetch: fetchCoinbaseBtc },
        { name: 'CoinGecko BTC/USD', fetch: fetchCoinGeckoBtc },
      ],
    },
    sp500_futures: {
      pollMs: 15_000,
      maxAgeMs: 15 * 60_000,
      backendMaxAgeMs: 15 * 60_000,
      backendSnapshotMaxAgeMs: 12 * 60_000,
      minPrice: 500,
      maxPrice: 100_000,
      sources: [
        { name: 'CNBC @SP.1', fetch: fetchCnbcEs },
        { name: 'eSignal ES active', fetch: () => fetchEsignalEs('direct') },
        { name: 'eSignal ES active · proxy 1', fetch: () => fetchEsignalEs('codetabs') },
        { name: 'eSignal ES active · proxy 2', fetch: () => fetchEsignalEs('allorigins') },
        { name: 'Stooq ES.F', fetch: () => fetchStooqEs('direct') },
        { name: 'Stooq ES.F · proxy 1', fetch: () => fetchStooqEs('codetabs') },
        { name: 'Stooq ES.F · proxy 2', fetch: () => fetchStooqEs('allorigins') },
        { name: 'Yahoo ES=F', fetch: () => fetchYahooQuote('ES=F', 'codetabs') },
        { name: 'Yahoo ES=F · backup route', fetch: () => fetchYahooQuote('ES=F', 'allorigins') },
      ],
    },
  };

  let selectedWeek = null;
  let feedRoundInFlight = false;
  let backendInFlight = false;
  let lastBackendAttemptAt = 0;
  const lastAttemptAt = new Map();
  const states = new Map();
  const backendQuotes = new Map();

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
    }).replace(',', ' ·');
  }

  function validTimestamp(value) {
    const numeric = number(value);
    const normalized = numeric !== null && numeric > 0 && numeric < 1_000_000_000_000
      ? numeric * 1000
      : value;
    const date = new Date(normalized);
    return Number.isNaN(date.valueOf()) ? null : date;
  }

  function quoteAgeMs(quote) {
    const stamp = validTimestamp(quote?.updatedAt);
    return stamp ? Date.now() - stamp.valueOf() : Number.POSITIVE_INFINITY;
  }

  function quoteFresh(quote, maxAgeMs) {
    if (!quote || positive(quote.price) === null) return false;
    const age = quoteAgeMs(quote);
    return age >= -60_000 && age <= maxAgeMs;
  }

  function timestampFresh(value, maxAgeMs) {
    const stamp = validTimestamp(value);
    if (!stamp) return false;
    const age = Date.now() - stamp.valueOf();
    return age >= -60_000 && age <= maxAgeMs;
  }

  function validateQuote(instrumentId, quote, sourceName) {
    const cfg = FEEDS[instrumentId];
    const price = positive(quote?.price);
    const stamp = validTimestamp(quote?.updatedAt);
    if (!cfg || price === null || price < cfg.minPrice || price > cfg.maxPrice) {
      throw new Error(`${instrumentId}_invalid_price`);
    }
    if (!stamp) throw new Error(`${instrumentId}_missing_source_timestamp`);
    if (stamp.valueOf() > Date.now() + 60_000) throw new Error(`${instrumentId}_future_timestamp`);
    return {
      price,
      updatedAt: stamp.toISOString(),
      source: quote?.source || sourceName,
      backendFresh: quote?.backendFresh !== false,
    };
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

  async function fetchText(url) {
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
      return response.text();
    } finally {
      window.clearTimeout(timer);
    }
  }

  function timeZoneOffsetMs(date, timeZone) {
    const formatter = new Intl.DateTimeFormat('en-CA', {
      timeZone,
      hour12: false,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
    const parts = Object.fromEntries(
      formatter.formatToParts(date)
        .filter((part) => part.type !== 'literal')
        .map((part) => [part.type, part.value]),
    );
    const wallAsUtc = Date.UTC(
      Number(parts.year),
      Number(parts.month) - 1,
      Number(parts.day),
      Number(parts.hour) % 24,
      Number(parts.minute),
      Number(parts.second),
    );
    return wallAsUtc - date.getTime();
  }

  function warsawLocalTimestamp(dateText, timeText) {
    const normalizedTime = String(timeText || '').length === 5 ? `${timeText}:00` : String(timeText || '');
    const [year, month, day] = String(dateText || '').split('-').map(Number);
    const [hour, minute, second] = normalizedTime.split(':').map(Number);
    if (![year, month, day, hour, minute, second].every(Number.isFinite)) {
      throw new Error('stooq_invalid_timestamp');
    }
    const wallUtcMs = Date.UTC(year, month - 1, day, hour, minute, second);
    const firstGuess = new Date(wallUtcMs);
    const firstOffset = timeZoneOffsetMs(firstGuess, 'Europe/Warsaw');
    let instant = new Date(wallUtcMs - firstOffset);
    const correctedOffset = timeZoneOffsetMs(instant, 'Europe/Warsaw');
    if (correctedOffset !== firstOffset) instant = new Date(wallUtcMs - correctedOffset);
    return instant;
  }

  function warsawYmd(date = new Date()) {
    const parts = Object.fromEntries(
      new Intl.DateTimeFormat('en-CA', {
        timeZone: 'Europe/Warsaw',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
      }).formatToParts(date)
        .filter((part) => part.type !== 'literal')
        .map((part) => [part.type, part.value]),
    );
    return { year: Number(parts.year), month: Number(parts.month), day: Number(parts.day) };
  }

  function activeEsignalSymbol() {
    const current = warsawYmd();
    const quarters = [3, 6, 9, 12];
    let year = current.year;
    let month = quarters.find((candidate) => current.month <= candidate) || 3;
    if (month === 3 && current.month > 12) year += 1;
    if (current.month > 12) month = 3;

    if (current.month > 12 || !quarters.includes(month)) {
      year += 1;
      month = 3;
    }

    if (current.month === 12 && month === 3) year += 1;

    if (current.month === month) {
      const firstWeekday = new Date(Date.UTC(year, month - 1, 1)).getUTCDay();
      const firstFriday = 1 + ((5 - firstWeekday + 7) % 7);
      const thirdFriday = firstFriday + 14;
      const rollStart = thirdFriday - 8;
      if (current.day >= rollStart) {
        const next = { 3: 6, 6: 9, 9: 12, 12: 3 }[month];
        if (month === 12) year += 1;
        month = next;
      }
    }

    const code = { 3: 'H', 6: 'M', 9: 'U', 12: 'Z' }[month];
    return `ES ${code}${String(year).slice(-2)}`;
  }

  function parseEsignalEs(markup, symbol) {
    const doc = new DOMParser().parseFromString(String(markup || ''), 'text/html');
    const text = String(doc.body?.textContent || '').replace(/\s+/g, ' ').trim();
    const priceMatch = text.match(/Last:\s*([0-9,]+(?:\.[0-9]+)?)/i);
    const timeMatch = text.match(/Time of last trade:\s*([A-Za-z]{3})\s+(\d{1,2})\s+(\d{4})\s+(\d{2}):(\d{2}):(\d{2})\s+(EST|EDT)/i);
    if (!priceMatch || !timeMatch) throw new Error('esignal_es_incomplete_quote');

    const price = positive(priceMatch[1].replace(/,/g, ''));
    const months = { Jan: 0, Feb: 1, Mar: 2, Apr: 3, May: 4, Jun: 5, Jul: 6, Aug: 7, Sep: 8, Oct: 9, Nov: 10, Dec: 11 };
    const month = months[timeMatch[1]];
    if (price === null || month === undefined) throw new Error('esignal_es_invalid_quote');

    const easternToUtcHours = timeMatch[7].toUpperCase() === 'EDT' ? 4 : 5;
    const utcMs = Date.UTC(
      Number(timeMatch[3]),
      month,
      Number(timeMatch[2]),
      Number(timeMatch[4]) + easternToUtcHours,
      Number(timeMatch[5]),
      Number(timeMatch[6]),
    );

    return {
      price,
      updatedAt: new Date(utcMs).toISOString(),
      source: `eSignal delayed:${symbol}`,
    };
  }

  async function fetchCnbcEs() {
    const url = 'https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol'
      + '?symbols=%40SP.1&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json&events=1';
    const data = await fetchJson(url);
    const row = data?.FormattedQuoteResult?.FormattedQuote?.[0]
      || data?.QuickQuoteResult?.QuickQuote?.[0]
      || null;
    if (!row) throw new Error('cnbc_es_missing_quote');

    const price = positive(String(row.last || '').replace(/,/g, ''));
    const stamp = validTimestamp(row.last_time);
    if (price === null || !stamp) throw new Error('cnbc_es_incomplete_quote');

    return {
      price,
      updatedAt: stamp.toISOString(),
      source: 'CNBC delayed:@SP.1',
    };
  }

  async function fetchEsignalEs(route) {
    const symbol = activeEsignalSymbol();
    const upstream = `https://quotes.esignal.com/esignalprod/quote.action?symbol=${encodeURIComponent(symbol)}&types=future&_=${Date.now()}`;
    const url = route === 'allorigins'
      ? `https://api.allorigins.win/raw?url=${encodeURIComponent(upstream)}`
      : route === 'codetabs'
        ? `https://api.codetabs.com/v1/proxy?quest=${encodeURIComponent(upstream)}`
        : upstream;
    return parseEsignalEs(await fetchText(url), symbol);
  }

  function parseStooqEsCsv(text) {
    const lines = String(text || '').trim().split(/\r?\n/).filter(Boolean);
    if (lines.length < 2) throw new Error('stooq_es_missing_row');
    const header = lines[0].split(',').map((value) => value.trim().toLowerCase());
    const row = lines[lines.length - 1].split(',').map((value) => value.trim());
    const at = (name) => {
      const index = header.indexOf(name);
      return index >= 0 ? (row[index] || '') : '';
    };
    const price = positive(at('close'));
    const dateText = at('date');
    const timeText = at('time');
    if (price === null) throw new Error('stooq_es_invalid_price');
    if (!/^\d{4}-\d{2}-\d{2}$/.test(dateText) || !/^\d{2}:\d{2}(:\d{2})?$/.test(timeText)) {
      throw new Error('stooq_es_invalid_timestamp');
    }
    const stamp = warsawLocalTimestamp(dateText, timeText);
    return {
      price,
      updatedAt: stamp.toISOString(),
      source: 'Stooq ES.F',
    };
  }

  async function fetchStooqEs(route) {
    const upstream = `https://stooq.com/q/l/?s=es.f&f=sd2t2ohlcv&h&e=csv&_=${Date.now()}`;
    const url = route === 'allorigins'
      ? `https://api.allorigins.win/raw?url=${encodeURIComponent(upstream)}`
      : route === 'codetabs'
        ? `https://api.codetabs.com/v1/proxy?quest=${encodeURIComponent(upstream)}`
        : upstream;
    return parseStooqEsCsv(await fetchText(url));
  }

  async function fetchEurUsdFxApi() {
    const data = await fetchJson('https://fxapi.app/api/EUR/USD.json');
    if (!data?.timestamp) throw new Error('fxapi_eurusd_source_timestamp_missing');
    return { price: data.rate, updatedAt: data.timestamp, source: 'fxapi.app' };
  }

  async function fetchEurUsdCurrencyExchangeTool() {
    const data = await fetchJson('https://www.currencyexchangetool.com/api/v1/convert?amount=1&from=EUR&to=USD');
    if (!data || data.success === false) throw new Error('currencyexchangetool_eurusd_api_error');
    const updatedAt = data.updatedAt || data.updated_at || data.timestamp || data.time;
    if (!updatedAt) throw new Error('currencyexchangetool_eurusd_source_timestamp_missing');
    return { price: data.rate ?? data.result, updatedAt, source: 'Currency Exchange Tool' };
  }

  async function fetchCoinbaseBtc() {
    const data = await fetchJson('https://api.exchange.coinbase.com/products/BTC-USD/ticker');
    if (!data?.time) throw new Error('coinbase_source_timestamp_missing');
    return { price: data.price, updatedAt: data.time, source: 'Coinbase BTC-USD' };
  }

  async function fetchCoinGeckoBtc() {
    const data = await fetchJson('https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_last_updated_at=true');
    const row = data?.bitcoin;
    const stamp = number(row?.last_updated_at);
    if (stamp === null) throw new Error('coingecko_source_timestamp_missing');
    return {
      price: row?.usd,
      updatedAt: new Date(stamp * 1000).toISOString(),
      source: 'CoinGecko BTC/USD',
    };
  }

  async function fetchYahooQuote(symbol, route) {
    // The cache-buster must be part of the Yahoo upstream URL itself. Adding it
    // only to the proxy URL allows a proxy/CDN to keep returning an old Yahoo snapshot.
    const upstream = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?interval=1m&range=1d&_=${Date.now()}`;
    const url = route === 'allorigins'
      ? `https://api.allorigins.win/raw?url=${encodeURIComponent(upstream)}`
      : `https://api.codetabs.com/v1/proxy?quest=${encodeURIComponent(upstream)}`;
    const data = await fetchJson(url);
    const chart = data?.chart?.result?.[0];
    if (!chart) throw new Error(`yahoo_${symbol}_missing_chart`);

    const timestamps = Array.isArray(chart.timestamp) ? chart.timestamp : [];
    const closes = chart?.indicators?.quote?.[0]?.close || [];
    for (let index = Math.min(timestamps.length, closes.length) - 1; index >= 0; index -= 1) {
      const epochSeconds = number(timestamps[index]);
      const price = positive(closes[index]);
      if (epochSeconds === null || price === null) continue;
      return {
        price,
        updatedAt: new Date(epochSeconds * 1000).toISOString(),
        source: symbol === 'ES=F' ? 'Yahoo ES=F' : `Yahoo ${symbol}`,
      };
    }

    const metaPrice = positive(chart?.meta?.regularMarketPrice);
    const metaTime = number(chart?.meta?.regularMarketTime);
    if (metaPrice === null || metaTime === null) throw new Error(`yahoo_${symbol}_missing_quote`);
    return {
      price: metaPrice,
      updatedAt: new Date(metaTime * 1000).toISOString(),
      source: symbol === 'ES=F' ? 'Yahoo ES=F' : `Yahoo ${symbol}`,
    };
  }

  function cacheKey(instrumentId) {
    return `${CACHE_PREFIX}${instrumentId}`;
  }

  function saveCache(instrumentId, quote) {
    if (!quote || positive(quote.price) === null) return;
    try {
      localStorage.setItem(cacheKey(instrumentId), JSON.stringify({
        price: quote.price,
        updatedAt: quote.updatedAt,
        source: quote.source,
        savedAt: new Date().toISOString(),
      }));
    } catch (_) {
      // Same-origin backend remains available when storage is blocked.
    }
  }

  function loadCache(instrumentId) {
    try {
      const raw = localStorage.getItem(cacheKey(instrumentId));
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      return validateQuote(instrumentId, parsed, parsed?.source || 'cache');
    } catch (_) {
      return null;
    }
  }

  function newestQuote(...quotes) {
    return quotes.filter(Boolean).sort((a, b) => {
      const aTime = validTimestamp(a.updatedAt)?.valueOf() || 0;
      const bTime = validTimestamp(b.updatedAt)?.valueOf() || 0;
      return bTime - aTime;
    })[0] || null;
  }

  async function refreshBackend() {
    if (backendInFlight) return;
    backendInFlight = true;
    lastBackendAttemptAt = Date.now();
    try {
      const data = await fetchJson(BACKEND_URL);
      const prices = data?.prices || {};
      Object.keys(FEEDS).forEach((instrumentId) => {
        const row = prices[instrumentId];
        if (!row) return;
        try {
          const quote = validateQuote(instrumentId, {
            price: row.price,
            updatedAt: row.current_price_updated_at || row.timestamp,
            source: `${T.backend} · ${row.source || 'live_prices.json'}`,
            backendFresh: row.fresh !== false,
          }, T.backend);
          if (instrumentId === 'sp500_futures') {
            const observedAt = validTimestamp(row.last_attempt_at || data?.updated_at);
            if (observedAt) quote.backendObservedAt = observedAt.toISOString();
          }
          backendQuotes.set(instrumentId, quote);
          saveCache(instrumentId, quote);
        } catch (error) {
          console.warn(`BriefRooms Weekly ${instrumentId} backend quote rejected:`, error?.message || error);
        }
      });
      reconcileAll();
      applyStates();
    } catch (error) {
      console.warn('BriefRooms Weekly backend refresh failed:', error?.message || error);
    } finally {
      backendInFlight = false;
    }
  }

  async function fetchAllSources(instrumentId) {
    const cfg = FEEDS[instrumentId];
    if (cfg.directPriority === 'first-fresh') {
      const attempts = [];
      for (let index = 0; index < cfg.sources.length; index += 1) {
        const source = cfg.sources[index];
        try {
          const quote = validateQuote(instrumentId, await source.fetch(), source.name);
          attempts.push({ index, quote });
          if (quoteFresh(quote, cfg.maxAgeMs)) return attempts;
        } catch (error) {
          console.warn(`BriefRooms Weekly ${instrumentId} source failed (${source.name}):`, error?.message || error);
        }
      }
      return attempts;
    }

    const attempts = await Promise.allSettled(cfg.sources.map(async (source, index) => ({
      index,
      quote: validateQuote(instrumentId, await source.fetch(), source.name),
    })));
    attempts.forEach((attempt, index) => {
      if (attempt.status === 'rejected') {
        console.warn(`BriefRooms Weekly ${instrumentId} source failed (${cfg.sources[index].name}):`, attempt.reason?.message || attempt.reason);
      }
    });
    return attempts.filter((attempt) => attempt.status === 'fulfilled').map((attempt) => attempt.value);
  }

  async function refreshInstrument(instrumentId) {
    const cfg = FEEDS[instrumentId];
    if (!cfg) return;
    const previous = states.get(instrumentId) || null;
    const direct = await fetchAllSources(instrumentId);
    const freshDirect = direct.filter((row) => quoteFresh(row.quote, cfg.maxAgeMs));
    const preferredFreshDirect = cfg.directPriority === 'first-fresh'
      ? (freshDirect[0] || null)
      : (freshDirect.sort((a, b) =>
          (validTimestamp(b.quote.updatedAt)?.valueOf() || 0) - (validTimestamp(a.quote.updatedAt)?.valueOf() || 0))[0] || null);
    const newestDirect = newestQuote(...direct.map((row) => row.quote));

    if (preferredFreshDirect) {
      const current = cfg.directAuthoritativeWhenFresh
        ? preferredFreshDirect.quote
        : newestQuote(preferredFreshDirect.quote, previous?.quote);
      const sameAsNew = current === preferredFreshDirect.quote;
      states.set(instrumentId, {
        mode: sameAsNew ? (preferredFreshDirect.index === 0 ? 'live' : 'fallback') : (previous?.mode || 'live'),
        quote: current,
      });
    } else {
      const quote = newestQuote(newestDirect, previous?.quote, loadCache(instrumentId));
      if (quote) states.set(instrumentId, { mode: 'delayed', quote });
    }

    reconcileInstrument(instrumentId);
    const finalQuote = states.get(instrumentId)?.quote;
    if (finalQuote) saveCache(instrumentId, finalQuote);
  }

  function reconcileInstrument(instrumentId) {
    const cfg = FEEDS[instrumentId];
    const state = states.get(instrumentId) || null;
    const backend = backendQuotes.get(instrumentId) || null;
    const backendAgeLimit = cfg.backendMaxAgeMs || cfg.maxAgeMs;
    const backendUsable = instrumentId === 'sp500_futures'
      ? backend && backend.backendFresh && timestampFresh(backend.backendObservedAt, cfg.backendSnapshotMaxAgeMs)
      : backend && backend.backendFresh && quoteFresh(backend, backendAgeLimit);
    const stateFresh = state?.quote && quoteFresh(state.quote, cfg.maxAgeMs);

    if (cfg.directAuthoritativeWhenFresh && stateFresh && ['live', 'fallback'].includes(state?.mode)) {
      return;
    }

    if (backendUsable) {
      if (instrumentId === 'sp500_futures' && !stateFresh) {
        // The backend has already validated the CME-delayed quote at fetch time.
        // For S&P, judge backend freshness by the recent backend observation,
        // not by re-applying the exchange-delay window in the browser.
        states.set(instrumentId, { mode: 'backend-live', quote: backend });
        return;
      }
      const freshest = newestQuote(state?.quote, backend);
      if (freshest === backend && (!stateFresh || freshest !== state?.quote)) {
        states.set(instrumentId, { mode: 'backend-live', quote: backend });
        return;
      }
    }

    if (stateFresh) return;

    const quote = newestQuote(state?.quote, backend, loadCache(instrumentId));
    if (quote) states.set(instrumentId, { mode: 'delayed', quote });
  }

  function reconcileAll() {
    Object.keys(FEEDS).forEach(reconcileInstrument);
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

  function setResult(item, card, mark) {
    if (!isOpen(item) || positive(mark) === null) return;
    const result = resultText(item, mark);
    const resultNode = findResultCell(card)?.querySelector('dd');
    if (!result || !resultNode) return;
    resultNode.textContent = result.text;
    resultNode.classList.toggle('positive', result.value > 0);
    resultNode.classList.toggle('negative', result.value < 0);
    resultNode.classList.toggle('neutral', Math.abs(result.value) < 0.000001);
  }

  function patchCard(item, index, state) {
    const cards = document.querySelectorAll('#app .cards > .card');
    const card = cards[index];
    if (!card || card.classList.contains('integrity-withheld')) return;
    const nowBox = card.querySelector('.now');
    const priceNode = nowBox?.querySelector('strong');
    const timeNode = nowBox?.querySelector('small');
    if (!nowBox || !priceNode || !timeNode) return;

    const quote = state?.quote || null;
    if (quote && positive(quote.price) !== null) {
      const cfg = FEEDS[item.instrument_id];
      const allowedAge = state?.mode === 'backend-live'
        ? (cfg.backendMaxAgeMs || cfg.maxAgeMs)
        : cfg.maxAgeMs;
      const quoteUsable = item.instrument_id === 'sp500_futures' && state?.mode === 'backend-live'
        ? quote.backendFresh && timestampFresh(quote.backendObservedAt, cfg.backendSnapshotMaxAgeMs)
        : quoteFresh(quote, allowedAge);
      if (!quoteUsable) {
        if (item.instrument_id === 'sp500_futures') {
          const labelNode = nowBox.querySelector('span');
          if (labelNode) {
            if (!nowBox.dataset.defaultPriceLabel) nowBox.dataset.defaultPriceLabel = labelNode.textContent || '';
            labelNode.textContent = isEn ? 'Last available quote' : 'Ostatni dostępny kurs';
          }
          priceNode.textContent = fmtPrice(quote.price, item.instrument_id);
          timeNode.textContent = `${fmtTime(quote.updatedAt)} · ${isEn ? 'delayed / stale source' : 'opóźnione / źródło nieświeże'}`;
          timeNode.style.color = '#ffb86b';
          nowBox.dataset.liveAt = quote.updatedAt;
          nowBox.dataset.liveSource = quote.source;
          nowBox.dataset.feedStatus = 'stale';
          return;
        }
        priceNode.textContent = '—';
        timeNode.textContent = isEn ? 'No fresh market quote' : 'Brak świeżej ceny rynkowej';
        timeNode.style.color = '#ffb86b';
        nowBox.dataset.feedStatus = 'stale';
        return;
      }
      if (item.instrument_id === 'sp500_futures') {
        const labelNode = nowBox.querySelector('span');
        if (labelNode && nowBox.dataset.defaultPriceLabel) {
          labelNode.textContent = nowBox.dataset.defaultPriceLabel;
        }
      }
      const currentAt = validTimestamp(nowBox.dataset.liveAt)?.valueOf() || 0;
      const quoteAt = validTimestamp(quote.updatedAt)?.valueOf() || 0;
      if (quoteAt >= currentAt) {
        priceNode.textContent = fmtPrice(quote.price, item.instrument_id);
        setResult(item, card, quote.price);
        timeNode.textContent = item.instrument_id === 'sp500_futures'
          ? `${fmtTime(quote.updatedAt)} · ${isEn ? 'delayed ~10 min' : 'opóźniony ~10 min'}`
          : fmtTime(quote.updatedAt);
        nowBox.dataset.liveAt = quote.updatedAt;
        nowBox.dataset.liveSource = quote.source;
        nowBox.dataset.feedStatus = state.mode;
      }
      timeNode.style.color = item.instrument_id === 'sp500_futures'
        ? '#ffb86b'
        : (state.mode === 'fallback' ? '#9fe8ff' : '#72f0c1');
      return;
    }

    // Preserve the server-rendered price and timestamp when all browser sources fail.
    const raw = String(timeNode.textContent || '').trim();
    const match = raw.match(/(\d{2}\.\d{2}\.\d{4})[, ·]+([0-2]\d:[0-5]\d(?::[0-5]\d)?)/);
    if (match) timeNode.textContent = `${match[1]} · ${match[2]}`;
  }

  function applyStates() {
    if (!selectedWeek || !Array.isArray(selectedWeek.instruments)) return;
    selectedWeek.instruments.forEach((item, index) => {
      if (!FEEDS[item.instrument_id]) return;
      const state = states.get(item.instrument_id)
        || { mode: 'delayed', quote: newestQuote(backendQuotes.get(item.instrument_id), loadCache(item.instrument_id)) };
      patchCard(item, index, state);
    });
  }

  function bootstrapCachedStates() {
    Object.keys(FEEDS).forEach((instrumentId) => {
      const cached = loadCache(instrumentId);
      if (cached) states.set(instrumentId, { mode: 'delayed', quote: cached });
    });
  }

  async function refreshFeeds({ forceAll = false } = {}) {
    if (document.hidden || feedRoundInFlight) return;
    const now = Date.now();
    if (forceAll || now - lastBackendAttemptAt >= BACKEND_POLL_MS) {
      void refreshBackend();
    }

    const due = Object.entries(FEEDS).filter(([instrumentId, cfg]) => {
      const last = lastAttemptAt.get(instrumentId) || 0;
      return forceAll || now - last >= cfg.pollMs;
    });
    if (!due.length) return;

    due.forEach(([instrumentId]) => lastAttemptAt.set(instrumentId, now));
    feedRoundInFlight = true;
    try {
      await Promise.allSettled(due.map(([instrumentId]) => refreshInstrument(instrumentId)));
      reconcileAll();
      applyStates();
    } finally {
      feedRoundInFlight = false;
    }
  }

  bootstrapCachedStates();

  document.addEventListener('br:weekly-rendered', (event) => {
    selectedWeek = event?.detail || null;
    applyStates();
    refreshFeeds({ forceAll: true });
  });

  const timer = window.setInterval(() => refreshFeeds(), LOOP_MS);
  window.setTimeout(() => refreshFeeds({ forceAll: true }), 300);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refreshFeeds({ forceAll: true });
  });
  window.addEventListener('online', () => refreshFeeds({ forceAll: true }));
  window.addEventListener('pagehide', () => window.clearInterval(timer), { once: true });
})();
