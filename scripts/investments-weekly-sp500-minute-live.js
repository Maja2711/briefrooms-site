(() => {
  'use strict';

  const POLL_MS = 30_000;
  const MAX_AGE_MS = 90_000;
  const REQUEST_TIMEOUT_MS = 7_000;
  const STOOQ_URL = 'https://stooq.com/q/l/?s=es.f&f=sd2t2ohlcv&h&e=csv';
  const isEn = (document.documentElement.lang || 'pl').toLowerCase().startsWith('en');

  let selectedWeek = null;
  let currentQuote = null;
  let inFlight = false;
  let observer = null;
  let patching = false;

  const positive = (value) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  };

  function fmtPrice(value) {
    return Number(value).toLocaleString(isEn ? 'en-US' : 'pl-PL', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
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

  function quoteFresh(quote) {
    if (!quote || positive(quote.price) === null) return false;
    const stamp = new Date(quote.updatedAt);
    if (Number.isNaN(stamp.valueOf())) return false;
    const age = Date.now() - stamp.valueOf();
    return age >= -30_000 && age <= MAX_AGE_MS;
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

  function parseStooqCsv(text) {
    const lines = String(text || '').trim().split(/\r?\n/).filter(Boolean);
    if (lines.length < 2) throw new Error('stooq_missing_row');
    const header = lines[0].split(',').map((value) => value.trim().toLowerCase());
    const row = lines[lines.length - 1].split(',').map((value) => value.trim());
    const at = (name) => row[header.indexOf(name)] || '';
    const price = positive(at('close'));
    const date = at('date');
    const time = at('time');
    if (price === null || price < 500 || price > 100_000) throw new Error('stooq_invalid_price');
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !/^\d{2}:\d{2}(:\d{2})?$/.test(time)) {
      throw new Error('stooq_invalid_timestamp');
    }

    // Stooq quote timestamps use CET (UTC+1) as the feed clock.
    const clock = time.length === 5 ? `${time}:00` : time;
    const stamp = new Date(`${date}T${clock}+01:00`);
    if (Number.isNaN(stamp.valueOf())) throw new Error('stooq_invalid_timestamp');
    return {
      price,
      updatedAt: stamp.toISOString(),
      source: 'Stooq ES.F',
    };
  }

  async function fetchStooq(route) {
    const upstream = `${STOOQ_URL}&_=${Date.now()}`;
    let url = upstream;
    if (route === 'codetabs') {
      url = `https://api.codetabs.com/v1/proxy?quest=${encodeURIComponent(upstream)}`;
    } else if (route === 'allorigins') {
      url = `https://api.allorigins.win/raw?url=${encodeURIComponent(upstream)}`;
    }
    return parseStooqCsv(await fetchText(url));
  }

  function selectedSp500() {
    if (!selectedWeek || !Array.isArray(selectedWeek.instruments)) return null;
    const index = selectedWeek.instruments.findIndex((item) => item?.instrument_id === 'sp500_futures');
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
    const notional = positive(item?.notional_usd) || 10_000;
    const value = move / entry * notional;
    const money = `${value >= 0 ? '+' : ''}${value.toLocaleString(isEn ? 'en-US' : 'pl-PL', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USD`;
    const points = `${move >= 0 ? '+' : ''}${move.toLocaleString(isEn ? 'en-US' : 'pl-PL', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${isEn ? 'pts' : 'pkt'}`;
    const pct = `${percent >= 0 ? '+' : ''}${percent.toLocaleString(isEn ? 'en-US' : 'pl-PL', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`;
    return { text: `${money} · ${points} · ${pct}`, value };
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

  function applyQuote() {
    if (patching || !quoteFresh(currentQuote)) return;
    const target = selectedSp500();
    if (!target) return;
    const cards = document.querySelectorAll('#app .cards > .card');
    const card = cards[target.index];
    const nowBox = card?.querySelector('.now');
    const priceNode = nowBox?.querySelector('strong');
    const timeNode = nowBox?.querySelector('small');
    if (!card || !nowBox || !priceNode || !timeNode) return;

    patching = true;
    try {
      priceNode.textContent = fmtPrice(currentQuote.price);
      timeNode.textContent = `${isEn ? 'As of' : 'Stan na'}: ${fmtTime(currentQuote.updatedAt)} · LIVE · ${currentQuote.source}`;
      timeNode.style.color = '#72f0c1';
      nowBox.dataset.feedStatus = 'live';
      nowBox.dataset.liveSource = currentQuote.source;
      nowBox.dataset.liveAt = currentQuote.updatedAt;
      patchResult(target.item, card, currentQuote.price);
    } finally {
      patching = false;
    }
  }

  function observeCard() {
    if (observer) observer.disconnect();
    const target = selectedSp500();
    if (!target) return;
    const card = document.querySelectorAll('#app .cards > .card')[target.index];
    const nowBox = card?.querySelector('.now');
    if (!nowBox) return;
    observer = new MutationObserver(() => applyQuote());
    observer.observe(nowBox, { childList: true, subtree: true, characterData: true, attributes: true });
  }

  async function refresh() {
    if (document.hidden || inFlight) return;
    inFlight = true;
    try {
      for (const route of ['direct', 'codetabs', 'allorigins']) {
        try {
          const quote = await fetchStooq(route);
          if (quoteFresh(quote)) {
            currentQuote = quote;
            applyQuote();
            return;
          }
        } catch (error) {
          console.warn(`BriefRooms Weekly S&P minute feed failed (${route}):`, error?.message || error);
        }
      }
    } finally {
      inFlight = false;
    }
  }

  document.addEventListener('br:weekly-rendered', (event) => {
    selectedWeek = event?.detail || null;
    observeCard();
    applyQuote();
    refresh();
  });

  const timer = window.setInterval(() => {
    applyQuote();
    refresh();
  }, POLL_MS);

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refresh();
  });
  window.addEventListener('online', () => refresh());
  window.addEventListener('pagehide', () => {
    window.clearInterval(timer);
    if (observer) observer.disconnect();
  }, { once: true });
})();
