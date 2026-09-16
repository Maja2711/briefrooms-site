(() => {
  'use strict';

  const isEn = (document.documentElement.lang || 'pl').toLowerCase().startsWith('en');
  const LOOP_MS = 15_000;
  const BACKEND_POLL_MS = 60_000;
  const REQUEST_TIMEOUT_MS = 6_000;
  const CACHE_PREFIX = 'briefrooms:weekly-market-feed:v4:';
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
      maxAgeMs: 5 * 60_000,
      minPrice: 0.8,
      maxPrice: 1.5,
      sources: [
        { name: 'FX mid-market', fetch: fetchEurUsdMidMarket },
        { name: 'Yahoo EURUSD=X', fetch: () => fetchYahooQuote('EURUSD=X', 'codetabs') },
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
      maxAgeMs: 5 * 60_000,
      backendMaxAgeMs: 45 * 60_000,
      minPrice: 500,
      maxPrice: 100_000,
      sources: [
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

  async function fetchEurUsdMidMarket() {
    const data = await fetchJson('https://www.currencyexchangetool.com/api/v1/convert?amount=1&from=EUR&to=USD');
    if (!data || data.success === false) throw new Error('eurusd_api_error');
    const updatedAt = data.updatedAt || data.updated_at || data.timestamp || data.time;
    if (!updatedAt) throw new Error('eurusd_source_timestamp_missing');
    return { price: data.rate ?? data.result, updatedAt, source: 'FX mid-market' };
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
    const upstream = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?interval=1m&range=1d`;
    const url = route === 'allorigins'
      ? `https://api.allorigins.win/raw?url=${encodeURIComponent(upstream)}`
      : `https://api.codetabs.com/v1/proxy?quest=${encodeURIComponent(upstream)}`;
    const data = await fetchJson(url);
    const chart = data?.chart?.result?.[0];
    if (!chart) throw new Error(`yahoo_${symbol}_missing_chart`);
    const timestamps = Array.isArray(chart.timestamp) ? chart.timestamp.filter(Number.isFinite) : [];
    const latestChartTime = timestamps.length ? timestamps[timestamps.length - 1] : null;
    const metaTime = number(chart?.meta?.regularMarketTime);
    const epochSeconds = latestChartTime !== null ? latestChartTime : metaTime;
    if (epochSeconds === null) throw new Error(`yahoo_${symbol}_missing_timestamp`);
    return {
      price: chart?.meta?.regularMarketPrice,
      updatedAt: new Date(epochSeconds * 1000).toISOString(),
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
    const newestFreshDirect = freshDirect.sort((a, b) =>
      (validTimestamp(b.quote.updatedAt)?.valueOf() || 0) - (validTimestamp(a.quote.updatedAt)?.valueOf() || 0))[0] || null;
    const newestDirect = newestQuote(...direct.map((row) => row.quote));

    if (newestFreshDirect) {
      const current = newestQuote(newestFreshDirect.quote, previous?.quote);
      const sameAsNew = current === newestFreshDirect.quote;
      states.set(instrumentId, {
        mode: sameAsNew ? (newestFreshDirect.index === 0 ? 'live' : 'fallback') : (previous?.mode || 'live'),
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
    const backendUsable = backend && backend.backendFresh && quoteFresh(backend, backendAgeLimit);
    const stateFresh = state?.quote && quoteFresh(state.quote, cfg.maxAgeMs);

    if (backendUsable) {
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
      const currentAt = validTimestamp(nowBox.dataset.liveAt)?.valueOf() || 0;
      const quoteAt = validTimestamp(quote.updatedAt)?.valueOf() || 0;
      if (quoteAt >= currentAt) {
        priceNode.textContent = fmtPrice(quote.price, item.instrument_id);
        setResult(item, card, quote.price);
        timeNode.textContent = fmtTime(quote.updatedAt);
        nowBox.dataset.liveAt = quote.updatedAt;
        nowBox.dataset.liveSource = quote.source;
        nowBox.dataset.feedStatus = state.mode;
      }
      timeNode.style.color = state.mode === 'delayed' ? '#ffb86b' : state.mode === 'fallback' ? '#9fe8ff' : '#72f0c1';
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
