(() => {
  'use strict';

  const POLL_MS = 30_000;
  const MAX_AGE_MS = 5 * 60_000;
  const REQUEST_TIMEOUT_MS = 7_000;
  const WARSAW_TZ = 'Europe/Warsaw';
  const isEn = (document.documentElement.lang || 'pl').toLowerCase().startsWith('en');

  const FEEDS = {
    eurusd: {
      symbol: 'eurusd',
      source: 'Stooq EUR/USD',
      minPrice: 0.8,
      maxPrice: 1.5,
      digits: 5,
    },
    sp500_futures: {
      symbol: 'es.f',
      source: 'Stooq ES.F',
      minPrice: 500,
      maxPrice: 100_000,
      digits: 2,
    },
  };

  let selectedWeek = null;
  let inFlight = false;
  const quotes = new Map();
  const observers = new Map();
  const patching = new Set();

  const positive = (value) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  };

  function fmtPrice(value, instrumentId) {
    const digits = FEEDS[instrumentId]?.digits ?? 2;
    return Number(value).toLocaleString(isEn ? 'en-US' : 'pl-PL', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  function fmtTime(value) {
    const date = new Date(value);
    if (Number.isNaN(date.valueOf())) return '';
    return date.toLocaleString(isEn ? 'en-GB' : 'pl-PL', {
      timeZone: WARSAW_TZ,
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
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

  function warsawLocalToUtc(dateText, timeText) {
    const [year, month, day] = dateText.split('-').map(Number);
    const [hour, minute, second = 0] = timeText.split(':').map(Number);
    if (![year, month, day, hour, minute, second].every(Number.isFinite)) {
      throw new Error('stooq_invalid_timestamp');
    }
    const wallUtcMs = Date.UTC(year, month - 1, day, hour, minute, second);
    const firstGuess = new Date(wallUtcMs);
    const firstOffset = timeZoneOffsetMs(firstGuess, WARSAW_TZ);
    let instant = new Date(wallUtcMs - firstOffset);
    const correctedOffset = timeZoneOffsetMs(instant, WARSAW_TZ);
    if (correctedOffset !== firstOffset) instant = new Date(wallUtcMs - correctedOffset);
    return instant;
  }

  function quoteFresh(quote) {
    if (!quote || positive(quote.price) === null) return false;
    const stamp = new Date(quote.updatedAt);
    if (Number.isNaN(stamp.valueOf())) return false;
    const age = Date.now() - stamp.valueOf();
    return age >= -60_000 && age <= MAX_AGE_MS;
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

  function parseStooqCsv(text, instrumentId) {
    const cfg = FEEDS[instrumentId];
    if (!cfg) throw new Error('stooq_unknown_instrument');
    const lines = String(text || '').trim().split(/\r?\n/).filter(Boolean);
    if (lines.length < 2) throw new Error('stooq_missing_row');
    const header = lines[0].split(',').map((value) => value.trim().toLowerCase());
    const row = lines[lines.length - 1].split(',').map((value) => value.trim());
    const at = (name) => row[header.indexOf(name)] || '';
    const price = positive(at('close'));
    const date = at('date');
    const time = at('time');
    if (price === null || price < cfg.minPrice || price > cfg.maxPrice) throw new Error('stooq_invalid_price');
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !/^\d{2}:\d{2}(:\d{2})?$/.test(time)) {
      throw new Error('stooq_invalid_timestamp');
    }
    const clock = time.length === 5 ? `${time}:00` : time;
    const stamp = warsawLocalToUtc(date, clock);
    if (Number.isNaN(stamp.valueOf())) throw new Error('stooq_invalid_timestamp');
    return {
      price,
      updatedAt: stamp.toISOString(),
      source: cfg.source,
    };
  }

  async function fetchStooq(instrumentId, route) {
    const cfg = FEEDS[instrumentId];
    const upstream = `https://stooq.com/q/l/?s=${encodeURIComponent(cfg.symbol)}&f=sd2t2ohlcv&h&e=csv&_=${Date.now()}`;
    let url = upstream;
    if (route === 'codetabs') {
      url = `https://api.codetabs.com/v1/proxy?quest=${encodeURIComponent(upstream)}`;
    } else if (route === 'allorigins') {
      url = `https://api.allorigins.win/raw?url=${encodeURIComponent(upstream)}`;
    }
    return parseStooqCsv(await fetchText(url), instrumentId);
  }

  function selectedTarget(instrumentId) {
    if (!selectedWeek || !Array.isArray(selectedWeek.instruments)) return null;
    const index = selectedWeek.instruments.findIndex((item) => item?.instrument_id === instrumentId);
    if (index < 0) return null;
    return { item: selectedWeek.instruments[index], index };
  }

  function resultText(item, mark) {
    const entry = positive(item?.entry_price);
    if (entry === null || positive(mark) === null) return null;
    const direction = item?.direction === 'short' ? 'short' : item?.direction === 'long' ? 'long' : 'neutral';
    if (direction === 'neutral') return null;
    const move = direction === 'short' ? entry - mark : mark - entry;
    const percent = move / entry * 100;
    const notional = positive(item?.instrument_id === 'eurusd' ? item?.notional_eur : item?.notional_usd) || 10_000;
    const value = item?.instrument_id === 'eurusd' ? move * notional : move / entry * notional;
    const parts = [
      `${value >= 0 ? '+' : ''}${value.toLocaleString(isEn ? 'en-US' : 'pl-PL', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USD`,
    ];
    if (item?.instrument_id === 'eurusd') {
      parts.push(`${move / 0.0001 >= 0 ? '+' : ''}${(move / 0.0001).toLocaleString(isEn ? 'en-US' : 'pl-PL', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} pips`);
    } else if (item?.instrument_id === 'sp500_futures') {
      parts.push(`${move >= 0 ? '+' : ''}${move.toLocaleString(isEn ? 'en-US' : 'pl-PL', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${isEn ? 'pts' : 'pkt'}`);
    }
    parts.push(`${percent >= 0 ? '+' : ''}${percent.toLocaleString(isEn ? 'en-US' : 'pl-PL', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`);
    return { text: parts.join(' · '), value };
  }

  function patchResult(item, card, mark) {
    if (positive(item?.exit_price) !== null) return;
    const result = resultText(item, mark);
    if (!result) return;
    const label = isEn ? 'Result' : 'Wynik';
    const cell = Array.from(card.querySelectorAll('.cell')).find((node) =>
      String(node.querySelector('dt')?.textContent || '').trim().toLowerCase() === label.toLowerCase());
    const node = cell?.querySelector('dd');
    if (!node) return;
    node.textContent = result.text;
    node.classList.toggle('positive', result.value > 0);
    node.classList.toggle('negative', result.value < 0);
    node.classList.toggle('neutral', Math.abs(result.value) < 0.000001);
  }

  function applyQuote(instrumentId) {
    if (patching.has(instrumentId)) return;
    const quote = quotes.get(instrumentId);
    if (!quoteFresh(quote)) return;
    const target = selectedTarget(instrumentId);
    if (!target) return;
    const cards = document.querySelectorAll('#app .cards > .card');
    const card = cards[target.index];
    const nowBox = card?.querySelector('.now');
    const priceNode = nowBox?.querySelector('strong');
    const timeNode = nowBox?.querySelector('small');
    if (!card || !nowBox || !priceNode || !timeNode) return;

    if (nowBox.dataset.feedStatus === 'live' || nowBox.dataset.feedStatus === 'fallback') return;

    patching.add(instrumentId);
    try {
      priceNode.textContent = fmtPrice(quote.price, instrumentId);
      timeNode.textContent = `${isEn ? 'As of' : 'Stan na'}: ${fmtTime(quote.updatedAt)} · LIVE · ${quote.source}`;
      timeNode.style.color = '#72f0c1';
      nowBox.dataset.feedStatus = 'live';
      nowBox.dataset.liveSource = quote.source;
      nowBox.dataset.liveAt = quote.updatedAt;
      patchResult(target.item, card, quote.price);
    } finally {
      patching.delete(instrumentId);
    }
  }

  function observeCards() {
    observers.forEach((observer) => observer.disconnect());
    observers.clear();
    Object.keys(FEEDS).forEach((instrumentId) => {
      const target = selectedTarget(instrumentId);
      if (!target) return;
      const card = document.querySelectorAll('#app .cards > .card')[target.index];
      const nowBox = card?.querySelector('.now');
      if (!nowBox) return;
      const observer = new MutationObserver(() => applyQuote(instrumentId));
      observer.observe(nowBox, { childList: true, subtree: true, characterData: true, attributes: true });
      observers.set(instrumentId, observer);
    });
  }

  async function refreshInstrument(instrumentId) {
    for (const route of ['direct', 'codetabs', 'allorigins']) {
      try {
        const quote = await fetchStooq(instrumentId, route);
        if (quoteFresh(quote)) {
          quotes.set(instrumentId, quote);
          applyQuote(instrumentId);
          return true;
        }
      } catch (error) {
        console.warn(`BriefRooms Weekly ${instrumentId} minute feed failed (${route}):`, error?.message || error);
      }
    }
    return false;
  }

  async function refresh() {
    if (document.hidden || inFlight) return;
    inFlight = true;
    try {
      await Promise.allSettled(Object.keys(FEEDS).map((instrumentId) => refreshInstrument(instrumentId)));
    } finally {
      inFlight = false;
    }
  }

  document.addEventListener('br:weekly-rendered', (event) => {
    selectedWeek = event?.detail || null;
    observeCards();
    Object.keys(FEEDS).forEach((instrumentId) => applyQuote(instrumentId));
    refresh();
  });

  const timer = window.setInterval(() => {
    Object.keys(FEEDS).forEach((instrumentId) => applyQuote(instrumentId));
    refresh();
  }, POLL_MS);

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refresh();
  });
  window.addEventListener('online', () => refresh());
  window.addEventListener('pagehide', () => {
    window.clearInterval(timer);
    observers.forEach((observer) => observer.disconnect());
    observers.clear();
  }, { once: true });
})();
