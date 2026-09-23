(() => {
  'use strict';

  const root = document.getElementById('stock-trading-portfolio-root');
  if (!root) return;

  const isPl = (document.documentElement.lang || '').toLowerCase().startsWith('pl');
  const locale = isPl ? 'pl-PL' : 'en-US';
  const POLL_MS = 15_000;
  const LIVE_MAX_AGE_MS = 150_000;
  // A persisted quote_session is useful only while it is fresh. Scheduled
  // publishers can be delayed, so an overnight CLOSED snapshot must never
  // overwrite the session state calculated in the browser for the current
  // Warsaw/New York clock.
  const SESSION_STATE_MAX_AGE_MS = 10 * 60_000;
  let inFlight = false;
  let latestData = null;
  let latestRuntime = null;
  const PENDING_MAX_AGE_MS = 30 * 60_000;

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

  function pendingSymbol(runtime, market) {
    const audit = Array.isArray(runtime && runtime.audit) ? runtime.audit : [];
    const now = Date.now();
    for (let i = audit.length - 1; i >= 0; i -= 1) {
      const row = audit[i];
      const runAt = new Date(row && row.run_at || 0).getTime();
      if (!Number.isFinite(runAt) || now - runAt < -60_000 || now - runAt > PENDING_MAX_AGE_MS) continue;
      const actions = Array.isArray(row && row.actions)
        ? row.actions.filter(action => String(action && action.market || '').toUpperCase() === market)
        : [];
      if (!actions.length) continue;
      const pending = actions
        .filter(action => action && action.action === 'ready_waiting_fresh_quote')
        .sort((a, b) => Number(a.deep_rank ?? 999999) - Number(b.deep_rank ?? 999999));
      return pending.length ? String(pending[0].symbol || pending[0].ticker || '').toUpperCase() : '';
    }
    return '';
  }

  function domPendingSymbol(market) {
    const panel = document.getElementById(`str-market-${market.toLowerCase()}`);
    const node = panel && panel.querySelector('[data-pending-symbol]');
    return String(node && node.dataset.pendingSymbol || '').toUpperCase();
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

  function structureChanged(data, runtime) {
    const hasPanels = document.getElementById('str-market-gpw') && document.getElementById('str-market-us');
    if (!hasPanels) return false;
    return ['GPW', 'US'].some(market => {
      if (!sameArray(expectedTickers(data, market), domTickers(market))) return true;
      return pendingSymbol(runtime, market) !== domPendingSymbol(market);
    });
  }

  function reloadForStructure(data, runtime) {
    const version = [data.updated_at || data.quote_enriched_at || 'unknown', runtime && runtime.last_run_at || 'no-runtime'].join(':');
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
    const quantity = finiteNumber(position.quantity ?? position.shares ?? position.position_size);
    const notional = finiteNumber(position.entry_notional ?? position.target_position_notional);

    const lastStrong = metrics[1].querySelector('strong');
    const stopStrong = metrics[2].querySelector('strong');
    const targetStrong = metrics[3].querySelector('strong');
    if (lastStrong) lastStrong.textContent = money(mark, market);
    if (stopStrong) stopStrong.textContent = money(stop, market);
    if (targetStrong) targetStrong.textContent = money(target, market);

    metrics[1].querySelectorAll('small').forEach(node => node.remove());
    metrics[1].title = [quote.provider, quote.observedAt || quote.receivedAt].filter(Boolean).join(' · ');

    const pnl = card.querySelector('.str-pnl');
    if (pnl && entry !== null && entry !== 0 && mark !== null) {
      const pct = ((mark - entry) / entry) * 100;
      const absolute = quantity !== null
        ? (mark - entry) * quantity
        : notional !== null
          ? (pct / 100) * notional
          : null;
      const strong = pnl.querySelector('strong');
      const amount = pnl.querySelector('b');
      if (strong) strong.textContent = percent(pct);
      if (amount) amount.textContent = absolute === null
        ? '—'
        : `${absolute > 0 ? '+' : ''}${money(absolute, market)}`;
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

  function apply(data, runtime) {
    if (!data || typeof data !== 'object') return false;
    if (!document.getElementById('str-market-gpw') || !document.getElementById('str-market-us')) return false;
    if (structureChanged(data, runtime)) return reloadForStructure(data, runtime);

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
      const stamp = Date.now();
      const [response, runtimeResponse] = await Promise.all([
        fetch(`/data/investments/stock_trading_portfolio.json?v=${stamp}`, {cache: 'no-store'}),
        fetch(`/data/investments/stock_trading_v2_production_state.json?v=${stamp}`, {cache: 'no-store'})
      ]);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      latestData = await response.json();
      latestRuntime = runtimeResponse.ok ? await runtimeResponse.json() : null;
      if (!apply(latestData, latestRuntime)) {
        setTimeout(() => latestData && apply(latestData, latestRuntime), 700);
        setTimeout(() => latestData && apply(latestData, latestRuntime), 1800);
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
