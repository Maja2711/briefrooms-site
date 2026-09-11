(() => {
  'use strict';

  const root = document.getElementById('stock-trading-portfolio-root');
  if (!root) return;

  const isPl = (document.documentElement.lang || '').toLowerCase().startsWith('pl');
  const locale = isPl ? 'pl-PL' : 'en-US';
  const POLL_MS = 60_000;
  const LIVE_MAX_AGE_MS = 150_000;
  // A persisted quote_session is useful only while it is fresh. Scheduled
  // publishers can be delayed, so an overnight CLOSED snapshot must never
  // overwrite the session state calculated in the browser for the current
  // Warsaw/New York clock.
  const SESSION_STATE_MAX_AGE_MS = 10 * 60_000;
  let inFlight = false;
  let latestData = null;

  const text = isPl ? {
    pre: 'pre-market',
    regular: 'sesja regularna',
    post: 'after-hours',
    closed: 'zamknięcie',
    marketOpen: 'rynek otwarty',
    marketClosed: 'rynek zamknięty',
    live: 'live',
    realtimeSource: 'źródło realtime',
    realtimeUnverified: 'realtime niezweryfikowany',
    delayed: 'opóźnienie',
    minutes: 'min',
    reference: 'kurs referencyjny',
    sourceUnavailable: 'źródło kursu niezweryfikowane'
  } : {
    pre: 'pre-market',
    regular: 'regular session',
    post: 'after-hours',
    closed: 'close',
    marketOpen: 'market open',
    marketClosed: 'market closed',
    live: 'live',
    realtimeSource: 'realtime source',
    realtimeUnverified: 'realtime unverified',
    delayed: 'delayed',
    minutes: 'min',
    reference: 'reference price',
    sourceUnavailable: 'quote source unverified'
  };

  function finiteNumber(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function normalizeState(value) {
    const raw = String(value || '').trim().toUpperCase().replace(/[ -]+/g, '_');
    if (['PRE', 'PREPRE', 'PRE_MARKET', 'PREMARKET'].includes(raw)) return 'PRE';
    if (['REGULAR', 'OPEN', 'REGULAR_MARKET'].includes(raw)) return 'REGULAR';
    if (['POST', 'POSTPOST', 'POST_MARKET', 'POSTMARKET', 'AFTER_HOURS'].includes(raw)) return 'POST';
    if (['CLOSED', 'CLOSE'].includes(raw)) return 'CLOSED';
    return null;
  }

  function marketRow(data, market) {
    return data && data.markets && data.markets[market] && typeof data.markets[market] === 'object'
      ? data.markets[market]
      : {};
  }

  function openPositions(data, market) {
    const rows = marketRow(data, market).open_positions;
    return Array.isArray(rows)
      ? rows.filter(p => p && typeof p === 'object' && String(p.status || 'OPEN').toUpperCase() === 'OPEN')
      : [];
  }

  function money(value, market) {
    const n = finiteNumber(value);
    if (n === null) return '—';
    const currency = market === 'US' ? 'USD' : 'PLN';
    try {
      return new Intl.NumberFormat(locale, {
        style: 'currency',
        currency,
        minimumFractionDigits: 2,
        maximumFractionDigits: n >= 1000 ? 2 : 3
      }).format(n);
    } catch (_) {
      return `${n.toFixed(2)} ${currency}`;
    }
  }

  function percent(value) {
    const n = finiteNumber(value);
    if (n === null) return '—';
    return `${n > 0 ? '+' : ''}${n.toLocaleString(locale, {minimumFractionDigits: 2, maximumFractionDigits: 2})}%`;
  }

  function localTime(value, market) {
    if (!value) return '';
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return '';
    try {
      return new Intl.DateTimeFormat(locale, {
        timeZone: market === 'US' ? 'America/New_York' : 'Europe/Warsaw',
        hour: '2-digit',
        minute: '2-digit',
        hourCycle: 'h23'
      }).format(date);
    } catch (_) {
      return '';
    }
  }

  function stateLabel(state) {
    if (state === 'PRE') return text.pre;
    if (state === 'REGULAR') return text.regular;
    if (state === 'POST') return text.post;
    return text.closed;
  }

  function quoteFor(position) {
    const mark = position && position.current_mark && typeof position.current_mark === 'object'
      ? position.current_mark
      : null;
    const price = mark ? finiteNumber(mark.price) : null;
    if (price !== null) {
      return {
        price,
        state: normalizeState(mark.market_state) || 'CLOSED',
        observedAt: mark.observed_at || null,
        receivedAt: mark.received_at || null,
        provider: mark.provider || '',
        delayStatus: String(mark.delay_status || 'unverified').toLowerCase(),
        delayMinutes: finiteNumber(mark.delay_minutes),
        isRealtime: mark.is_realtime === true,
        isReference: false
      };
    }
    return {
      price: finiteNumber(position && (position.last_mark ?? position.mark ?? position.current_price ?? position.close_price)),
      state: null,
      observedAt: position && (position.last_reviewed_at || position.updated_at) || null,
      receivedAt: null,
      provider: '',
      delayStatus: 'unverified',
      delayMinutes: null,
      isRealtime: false,
      isReference: true
    };
  }

  function quoteNote(quote, market) {
    const time = localTime(quote.observedAt || quote.receivedAt, market);
    const suffix = time ? ` · ${time}` : '';
    if (quote.isReference || !quote.state) return `${text.reference}${suffix}`;

    const age = quote.observedAt ? Date.now() - new Date(quote.observedAt).getTime() : Infinity;
    const freshLive = quote.state === 'REGULAR' && quote.isRealtime && Number.isFinite(age) && age >= -60_000 && age <= LIVE_MAX_AGE_MS;
    if (freshLive) return `${text.live}${suffix}`;
    if (quote.delayStatus === 'delayed' && quote.delayMinutes !== null) {
      return `${stateLabel(quote.state)} · ${text.delayed} ${Math.max(0, Math.round(quote.delayMinutes))} ${text.minutes}${suffix}`;
    }
    if (quote.isRealtime) return `${stateLabel(quote.state)} · ${text.realtimeSource}${suffix}`;
    return `${stateLabel(quote.state)} · ${text.realtimeUnverified}${suffix}`;
  }

  function expectedTickers(data, market) {
    return openPositions(data, market).map(p => String(p.ticker || p.symbol || '').trim().toUpperCase()).filter(Boolean);
  }

  function domCards(market) {
    const panel = document.getElementById(`str-market-${market.toLowerCase()}`);
    if (!panel) return [];
    return [...panel.querySelectorAll('.str-position:not(.str-position-empty)')];
  }

  function domTickers(market) {
    return domCards(market).map(card => String(card.querySelector('.str-company-name h3')?.textContent || '').trim().toUpperCase()).filter(Boolean);
  }

  function sameArray(a, b) {
    return a.length === b.length && a.every((value, index) => value === b[index]);
  }

  function structureChanged(data) {
    const hasPanels = document.getElementById('str-market-gpw') && document.getElementById('str-market-us');
    if (!hasPanels) return false;
    return ['GPW', 'US'].some(market => !sameArray(expectedTickers(data, market), domTickers(market)));
  }

  function reloadForStructure(data) {
    const version = String(data.updated_at || data.quote_enriched_at || 'unknown');
    const key = `stock-trading-structure-reload:${version}`;
    if (sessionStorage.getItem(key) === '1') return false;
    sessionStorage.setItem(key, '1');
    window.location.reload();
    return true;
  }

  function setSmall(metric, note, live) {
    let small = metric.querySelector('small');
    if (!small) {
      small = document.createElement('small');
      metric.appendChild(small);
    }
    small.textContent = note;
    small.classList.toggle('is-open-note', Boolean(live));
  }

  function updateCard(card, position, market) {
    const metrics = card.querySelectorAll('.str-metrics > div');
    if (metrics.length < 4) return;
    const entry = finiteNumber(position.entry ?? position.entry_price ?? position.open_price);
    const stop = finiteNumber(position.stop ?? position.sl ?? position.stop_loss);
    const target = finiteNumber(position.target ?? position.tp ?? position.take_profit);
    const quote = quoteFor(position);
    const mark = quote.price;

    const lastStrong = metrics[1].querySelector('strong');
    const stopStrong = metrics[2].querySelector('strong');
    const targetStrong = metrics[3].querySelector('strong');
    if (lastStrong) lastStrong.textContent = money(mark, market);
    if (stopStrong) stopStrong.textContent = money(stop, market);
    if (targetStrong) targetStrong.textContent = money(target, market);

    const age = quote.observedAt ? Date.now() - new Date(quote.observedAt).getTime() : Infinity;
    const trulyLive = quote.state === 'REGULAR' && quote.isRealtime && Number.isFinite(age) && age >= -60_000 && age <= LIVE_MAX_AGE_MS;
    setSmall(metrics[1], quoteNote(quote, market), trulyLive);
    metrics[1].title = [quote.provider, quote.observedAt || quote.receivedAt].filter(Boolean).join(' · ');

    const pnl = card.querySelector('.str-pnl');
    if (pnl && entry !== null && entry !== 0 && mark !== null) {
      const pct = ((mark - entry) / entry) * 100;
      const absolute = mark - entry;
      const strong = pnl.querySelector('strong');
      const amount = pnl.querySelector('b');
      if (strong) strong.textContent = percent(pct);
      if (amount) amount.textContent = `${absolute > 0 ? '+' : ''}${money(absolute, market)}`;
      pnl.classList.remove('is-neutral', 'is-positive', 'is-negative');
      pnl.classList.add(pct === 0 ? 'is-neutral' : pct > 0 ? 'is-positive' : 'is-negative');
    }
    card.dataset.quoteState = quote.state || 'REFERENCE';
    card.dataset.quoteObservedAt = quote.observedAt || '';
  }

  function updateMarketStatus(data, market, index) {
    const session = marketRow(data, market).quote_session;
    if (!session || typeof session !== 'object') return;
    const state = normalizeState(session.market_state);
    if (!state) return;
    const stateTimestamp = new Date(session.as_of || session.received_at || 0).getTime();
    const stateAge = Date.now() - stateTimestamp;
    if (!Number.isFinite(stateTimestamp) || !Number.isFinite(stateAge) || stateAge < -60_000 || stateAge > SESSION_STATE_MAX_AGE_MS) {
      return;
    }
    const nodes = root.querySelectorAll('.str-market-statuses .str-market-status');
    const node = nodes[index];
    if (!node) return;

    const em = node.querySelector('strong em');
    const small = node.querySelector('small');
    const regularOpen = state === 'REGULAR';
    node.classList.toggle('is-open', regularOpen);
    node.classList.toggle('is-closed', !regularOpen);
    node.dataset.marketState = state;
    if (em) {
      em.textContent = state === 'REGULAR' ? text.marketOpen : state === 'CLOSED' ? text.marketClosed : stateLabel(state);
    }
    if (small) {
      const when = localTime(session.as_of || session.received_at, market);
      const delay = session.delay_status === 'delayed' && finiteNumber(session.delay_minutes) !== null
        ? ` · ${text.delayed} ${Math.max(0, Math.round(Number(session.delay_minutes)))} ${text.minutes}`
        : session.is_realtime === true ? ` · ${text.realtimeSource}` : '';
      small.textContent = `${stateLabel(state)}${delay}${when ? ` · ${when}` : ''}`;
    }
  }

  function apply(data) {
    if (!data || typeof data !== 'object') return false;
    if (!document.getElementById('str-market-gpw') || !document.getElementById('str-market-us')) return false;
    if (structureChanged(data)) return reloadForStructure(data);

    ['GPW', 'US'].forEach((market, index) => {
      const positions = openPositions(data, market);
      const cards = domCards(market);
      cards.forEach((card, cardIndex) => {
        if (positions[cardIndex]) updateCard(card, positions[cardIndex], market);
      });
      updateMarketStatus(data, market, index);
    });
    root.dataset.quoteRefresh = 'active';
    root.dataset.quoteRefreshAt = new Date().toISOString();
    return true;
  }

  async function poll() {
    if (inFlight || document.hidden) return;
    inFlight = true;
    try {
      const response = await fetch(`/data/investments/stock_trading_portfolio.json?v=${Date.now()}`, {cache: 'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      latestData = await response.json();
      if (!apply(latestData)) {
        setTimeout(() => latestData && apply(latestData), 700);
        setTimeout(() => latestData && apply(latestData), 1800);
      }
    } catch (error) {
      console.warn('Stock Trading quote refresh:', error);
    } finally {
      inFlight = false;
    }
  }

  setTimeout(poll, 500);
  setInterval(poll, POLL_MS);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) poll();
  });
})();
