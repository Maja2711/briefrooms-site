(() => {
  'use strict';

  const root = document.getElementById('stock-trading-portfolio-root');
  if (!root) return;

  const isPl = (document.documentElement.lang || 'pl').toLowerCase().startsWith('pl');
  const locale = isPl ? 'pl-PL' : 'en-US';
  const T = isPl ? {
    loading: 'Ładowanie Trading Room…',
    error: 'Nie udało się załadować danych Stock Trading.',
    retry: 'Spróbuj ponownie',
    gpw: 'Rynek GPW', us: 'Rynek USA',
    openTickets: 'Otwarte tikety',
    openLead: 'Aktywne pozycje. Każdy tiket pokazuje wejście, ryzyko, cel i bieżący wynik.',
    active: 'Aktywna pozycja',
    slots: 'otwarte pozycje', cash: 'wolne sloty',
    entry: 'Cena wejścia', last: 'Ostatni kurs', closePrice: 'Kurs zamknięcia', closedMarket: 'rynek zamknięty',
    marketOpen: 'rynek otwarty', marketClosed: 'rynek zamknięty',
    sl: 'SL', tp: 'TP', pnl: 'P&L od wejścia',
    opened: 'Wejście', sector: 'Sektor', score: 'Score wejścia',
    noOpen: 'Brak otwartych pozycji na tym rynku.',
    noOpenSub: 'CASH jest prawidłowym stanem — system nie wymusza transakcji.',
    summary: 'Podsumowanie wyników',
    summaryLead: 'Statystyki liczone wyłącznie z zamkniętych pozycji Stock Trading.',
    totalReturn: 'Skumulowany zwrot', winRate: 'Win rate', avgR: 'Średnie R:R', avgHold: 'Średni czas trzymania',
    trades: 'transakcji', setupAvg: 'średnia relacja zysk/ryzyko', days: 'dni',
    history: 'Historia tradingu',
    historyLead: 'Pełna historia zamkniętych pozycji dla wybranego rynku.',
    export: 'Eksportuj CSV',
    ticker: 'Ticker', company: 'Spółka', market: 'Rynek', entryDate: 'Data wejścia', exitDate: 'Data wyjścia',
    entryPrice: 'Cena wejścia', exitPrice: 'Cena wyjścia', result: 'Wynik', returnPct: 'Zwrot %', status: 'Status',
    closed: 'Zamknięta',
    noHistory: 'Brak zamkniętych transakcji do pokazania.',
    noHistorySub: 'Historia i statystyki pojawią się automatycznie po zamknięciu pierwszej pozycji.',
    last30: 'Ostatnie 30 dni', last90: 'Ostatnie 90 dni', all: 'Od początku',
    roiNote: 'Skumulowany zwrot jest liczony z procentowych zmian cen zamkniętych pozycji. Bez danych o wielkości pozycji nie jest to stopa zwrotu całego portfela kapitałowego.',
    updated: 'Dane portfela',
    stale: 'ostatni zapis',
    ariaMarket: 'Wybierz rynek',
    liveData: 'dane bieżące',
    emptyValue: '—'
  } : {
    loading: 'Loading Trading Room…',
    error: 'Stock Trading data could not be loaded.',
    retry: 'Try again',
    gpw: 'GPW Market', us: 'US Market',
    openTickets: 'Open tickets',
    openLead: 'Active positions with entry, risk, target and current performance in one place.',
    active: 'Active position',
    slots: 'open positions', cash: 'free slots',
    entry: 'Entry price', last: 'Last price', closePrice: 'Closing price', closedMarket: 'market closed',
    marketOpen: 'market open', marketClosed: 'market closed',
    sl: 'SL', tp: 'TP', pnl: 'P&L since entry',
    opened: 'Entry', sector: 'Sector', score: 'Entry score',
    noOpen: 'No open positions in this market.',
    noOpenSub: 'CASH is a valid state — the system never forces a trade.',
    summary: 'Performance summary',
    summaryLead: 'Statistics use closed Stock Trading positions only.',
    totalReturn: 'Cumulative return', winRate: 'Win rate', avgR: 'Average R:R', avgHold: 'Average holding time',
    trades: 'trades', setupAvg: 'average reward/risk', days: 'days',
    history: 'Trading history',
    historyLead: 'Complete closed-position history for the selected market.',
    export: 'Export CSV',
    ticker: 'Ticker', company: 'Company', market: 'Market', entryDate: 'Entry date', exitDate: 'Exit date',
    entryPrice: 'Entry price', exitPrice: 'Exit price', result: 'Result', returnPct: 'Return %', status: 'Status',
    closed: 'Closed',
    noHistory: 'No closed trades to show yet.',
    noHistorySub: 'History and statistics will appear automatically after the first position is closed.',
    last30: 'Last 30 days', last90: 'Last 90 days', all: 'All time',
    roiNote: 'Cumulative return compounds percentage price changes of closed positions. Without position-size data it is not a capital-weighted portfolio return.',
    updated: 'Portfolio data',
    stale: 'last saved',
    ariaMarket: 'Select market',
    liveData: 'live data',
    emptyValue: '—'
  };

  const state = { market: 'GPW', period: 'all', data: null };

  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));

  const asNumber = (value) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  };

  const firstNumber = (obj, keys) => {
    for (const key of keys) {
      const n = asNumber(obj?.[key]);
      if (n !== null) return n;
    }
    return null;
  };

  const firstValue = (obj, keys) => {
    for (const key of keys) {
      if (obj?.[key] !== undefined && obj?.[key] !== null && obj?.[key] !== '') return obj[key];
    }
    return null;
  };

  const marketData = (market) => state.data?.markets?.[market] || {};
  const openPositions = (market) => Array.isArray(marketData(market).open_positions) ? marketData(market).open_positions : [];
  const closedPositions = (market) => Array.isArray(marketData(market).closed_positions) ? marketData(market).closed_positions : [];

  const currency = (market) => market === 'GPW' ? 'PLN' : 'USD';
  const money = (value, market) => {
    const n = asNumber(value);
    if (n === null) return T.emptyValue;
    return new Intl.NumberFormat(locale, {
      style: 'currency', currency: currency(market), minimumFractionDigits: 2, maximumFractionDigits: 2
    }).format(n);
  };

  const pct = (value, signed = true) => {
    const n = asNumber(value);
    if (n === null) return T.emptyValue;
    const sign = signed && n > 0 ? '+' : '';
    return `${sign}${n.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`;
  };

  const dateFmt = (value, market, withTime = false) => {
    if (!value) return T.emptyValue;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return esc(value);
    return new Intl.DateTimeFormat(locale, {
      timeZone: market === 'GPW' ? 'Europe/Warsaw' : 'America/New_York',
      day: '2-digit', month: '2-digit', year: 'numeric',
      ...(withTime ? { hour: '2-digit', minute: '2-digit' } : {})
    }).format(date);
  };

  const marketClock = (market) => {
    const zone = market === 'GPW' ? 'Europe/Warsaw' : 'America/New_York';
    const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', {
      timeZone: zone, weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
    }).formatToParts(new Date()).map((part) => [part.type, part.value]));
    const weekday = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'].includes(parts.weekday);
    const minutes = Number(parts.hour) * 60 + Number(parts.minute);
    const open = market === 'GPW' ? 9 * 60 : 9 * 60 + 30;
    const close = market === 'GPW' ? 17 * 60 : 16 * 60;
    return { open: weekday && minutes >= open && minutes < close, zone };
  };

  const positionReturn = (position) => {
    const entry = firstNumber(position, ['entry', 'entry_price', 'open_price']);
    const exit = firstNumber(position, ['exit', 'exit_price', 'close_price', 'closed_mark', 'last_mark']);
    const explicit = firstNumber(position, ['return_percent', 'return_pct', 'pnl_percent', 'result_percent']);
    if (explicit !== null) return explicit;
    if (entry === null || exit === null || entry === 0) return null;
    return ((exit - entry) / entry) * 100;
  };

  const positionResult = (position) => {
    const explicit = firstNumber(position, ['pnl', 'pnl_amount', 'result_amount', 'profit_loss']);
    if (explicit !== null) return explicit;
    const entry = firstNumber(position, ['entry', 'entry_price', 'open_price']);
    const exit = firstNumber(position, ['exit', 'exit_price', 'close_price', 'closed_mark', 'last_mark']);
    if (entry === null || exit === null) return null;
    return exit - entry;
  };

  const tone = (value) => {
    const n = asNumber(value);
    if (n === null || n === 0) return 'neutral';
    return n > 0 ? 'positive' : 'negative';
  };

  const marketIcon = (market) => market === 'GPW'
    ? '<span class="str-flag str-flag-pl" aria-hidden="true"></span>'
    : '<span class="str-flag str-flag-us" aria-hidden="true">🇺🇸</span>';

  const statusHtml = (market) => {
    const clock = marketClock(market);
    const label = clock.open ? T.marketOpen : T.marketClosed;
    return `<span class="str-market-state ${clock.open ? 'is-open' : 'is-closed'}"><i></i>${esc(label)}</span>`;
  };

  function openCard(position, market) {
    const entry = firstNumber(position, ['entry', 'entry_price']);
    const mark = firstNumber(position, ['last_mark', 'mark', 'current_price']);
    const stop = firstNumber(position, ['stop', 'sl', 'stop_loss']);
    const target = firstNumber(position, ['target', 'tp', 'take_profit']);
    const changePct = entry !== null && mark !== null && entry !== 0 ? ((mark - entry) / entry) * 100 : null;
    const changeAbs = entry !== null && mark !== null ? mark - entry : null;
    const clock = marketClock(market);
    const ticker = position.ticker || position.symbol || '—';
    const name = position.name || ticker;
    const sector = position.sector || null;
    const score = firstNumber(position, ['entry_score', 'score']);

    return `<article class="str-ticket">
      <div class="str-ticket-top">
        <span class="str-market-badge">${marketIcon(market)} ${market === 'GPW' ? 'GPW' : 'USA'}</span>
        <span class="str-active-badge"><i></i>${esc(T.active)}</span>
      </div>
      <div class="str-ticket-title">
        <div class="str-symbol-mark">${esc(String(ticker).slice(0, 4))}</div>
        <div><h3>${esc(ticker)}</h3><p>${esc(name)}</p></div>
      </div>
      <div class="str-levels">
        <div class="str-level"><span>${esc(T.entry)}</span><strong>${money(entry, market)}</strong></div>
        <div class="str-level str-level-mark"><span>${esc(clock.open ? T.last : T.closePrice)}</span><strong>${money(mark, market)}</strong>${clock.open ? '' : `<small>${esc(T.closedMarket)}</small>`}</div>
        <div class="str-level"><span>${esc(T.sl)}</span><strong>${money(stop, market)}</strong></div>
        <div class="str-level"><span>${esc(T.tp)}</span><strong>${money(target, market)}</strong></div>
      </div>
      <div class="str-ticket-bottom">
        <div class="str-ticket-meta">
          <span>${esc(T.opened)}: <b>${dateFmt(position.opened_at, market, true)}</b></span>
          ${sector ? `<span class="str-chip">${esc(T.sector)}: ${esc(sector)}</span>` : ''}
          ${score !== null ? `<span class="str-chip">${esc(T.score)}: ${score.toLocaleString(locale, {maximumFractionDigits: 2})}</span>` : ''}
        </div>
        <div class="str-pnl ${tone(changePct)}"><span>${esc(T.pnl)}</span><strong>${pct(changePct)}</strong><b>${changeAbs === null ? T.emptyValue : `${changeAbs > 0 ? '+' : ''}${money(changeAbs, market)}`}</b></div>
      </div>
    </article>`;
  }

  function emptyOpen(market) {
    const max = Number(marketData(market).max_open_positions || 3);
    return `<div class="str-empty str-empty-open">
      <div class="str-empty-icon" aria-hidden="true">◎</div>
      <div><strong>${esc(T.noOpen)}</strong><p>${esc(T.noOpenSub)}</p></div>
      <span class="str-cash-pill">0/${max} · CASH</span>
    </div>`;
  }

  const closedAt = (position) => firstValue(position, ['closed_at', 'exit_at', 'closed_on', 'exit_date', 'resolved_at']);
  const entryAt = (position) => firstValue(position, ['opened_at', 'entry_at', 'opened_on', 'entry_date']);

  function filteredClosed(market) {
    const rows = [...closedPositions(market)];
    rows.sort((a, b) => new Date(closedAt(b) || 0) - new Date(closedAt(a) || 0));
    if (state.period === 'all') return rows;
    const days = state.period === '30' ? 30 : 90;
    const cutoff = Date.now() - days * 86400000;
    return rows.filter((row) => {
      const d = new Date(closedAt(row) || 0).getTime();
      return Number.isFinite(d) && d >= cutoff;
    });
  }

  function stats(market) {
    const rows = filteredClosed(market);
    const returns = rows.map(positionReturn).filter((v) => v !== null);
    const wins = returns.filter((v) => v > 0).length;
    const cumulative = returns.reduce((acc, r) => acc * (1 + r / 100), 1) - 1;
    const rrValues = rows.map((p) => firstNumber(p, ['realized_r', 'r_multiple', 'reward_risk'])).filter((v) => v !== null);
    const holdDays = rows.map((p) => {
      const a = new Date(entryAt(p) || 0).getTime();
      const b = new Date(closedAt(p) || 0).getTime();
      if (!a || !b || b < a) return null;
      return (b - a) / 86400000;
    }).filter((v) => v !== null);
    return {
      rows,
      cumulative: returns.length ? cumulative * 100 : null,
      winRate: returns.length ? wins / returns.length * 100 : null,
      avgR: rrValues.length ? rrValues.reduce((a, b) => a + b, 0) / rrValues.length : null,
      avgHold: holdDays.length ? holdDays.reduce((a, b) => a + b, 0) / holdDays.length : null
    };
  }

  function kpiCards(market) {
    const s = stats(market);
    return `<div class="str-kpis">
      <article class="str-kpi"><span class="str-kpi-icon">↗</span><div><small>${esc(T.totalReturn)}</small><strong class="${tone(s.cumulative)}">${pct(s.cumulative)}</strong><p>${s.rows.length} ${esc(T.trades)}</p></div></article>
      <article class="str-kpi"><span class="str-kpi-icon">✓</span><div><small>${esc(T.winRate)}</small><strong>${s.winRate === null ? T.emptyValue : pct(s.winRate, false)}</strong><p>${s.rows.length} ${esc(T.trades)}</p></div></article>
      <article class="str-kpi"><span class="str-kpi-icon">⚖</span><div><small>${esc(T.avgR)}</small><strong>${s.avgR === null ? T.emptyValue : `${s.avgR.toLocaleString(locale, {minimumFractionDigits: 2, maximumFractionDigits: 2})} : 1`}</strong><p>${esc(T.setupAvg)}</p></div></article>
      <article class="str-kpi"><span class="str-kpi-icon">◷</span><div><small>${esc(T.avgHold)}</small><strong>${s.avgHold === null ? T.emptyValue : `${s.avgHold.toLocaleString(locale, {maximumFractionDigits: 1})} ${esc(T.days)}`}</strong><p>${s.rows.length ? esc(T.closed) : esc(T.noHistory)}</p></div></article>
    </div>`;
  }

  function historyRows(market) {
    const rows = stats(market).rows;
    if (!rows.length) return `<div class="str-empty str-empty-history"><div class="str-empty-icon" aria-hidden="true">▤</div><div><strong>${esc(T.noHistory)}</strong><p>${esc(T.noHistorySub)}</p></div></div>`;

    const body = rows.map((position) => {
      const ticker = position.ticker || position.symbol || '—';
      const name = position.name || ticker;
      const entry = firstNumber(position, ['entry', 'entry_price', 'open_price']);
      const exit = firstNumber(position, ['exit', 'exit_price', 'close_price', 'closed_mark', 'last_mark']);
      const result = positionResult(position);
      const ret = positionReturn(position);
      return `<tr>
        <td><strong>${esc(ticker)}</strong></td>
        <td>${esc(name)}</td>
        <td><span class="str-table-market">${marketIcon(market)} ${market === 'GPW' ? 'GPW' : 'USA'}</span></td>
        <td>${dateFmt(entryAt(position), market)}</td>
        <td>${dateFmt(closedAt(position), market)}</td>
        <td>${money(entry, market)}</td>
        <td>${money(exit, market)}</td>
        <td class="${tone(result)}">${result === null ? T.emptyValue : `${result > 0 ? '+' : ''}${money(result, market)}`}</td>
        <td><span class="str-return-pill ${tone(ret)}">${pct(ret)}</span></td>
        <td><span class="str-status-pill">${esc(T.closed)}</span></td>
      </tr>`;
    }).join('');

    return `<div class="str-table-wrap"><table class="str-history-table">
      <thead><tr><th>${esc(T.ticker)}</th><th>${esc(T.company)}</th><th>${esc(T.market)}</th><th>${esc(T.entryDate)}</th><th>${esc(T.exitDate)}</th><th>${esc(T.entryPrice)}</th><th>${esc(T.exitPrice)}</th><th>${esc(T.result)}</th><th>${esc(T.returnPct)}</th><th>${esc(T.status)}</th></tr></thead>
      <tbody>${body}</tbody>
    </table></div>`;
  }

  function marketTabs() {
    return ['GPW', 'US'].map((market) => {
      const active = state.market === market;
      const open = openPositions(market).length;
      const max = Number(marketData(market).max_open_positions || 3);
      return `<button class="str-market-tab ${active ? 'active' : ''}" type="button" role="tab" aria-selected="${active}" data-market="${market}">
        <span class="str-tab-label">${marketIcon(market)} <b>${esc(market === 'GPW' ? T.gpw : T.us)}</b></span>
        <small>${open}/${max} ${esc(T.slots)} · ${Math.max(0, max - open)} ${esc(T.cash)}</small>
      </button>`;
    }).join('');
  }

  function periodButtons() {
    return [
      ['30', T.last30], ['90', T.last90], ['all', T.all]
    ].map(([value, label]) => `<button type="button" class="str-period ${state.period === value ? 'active' : ''}" data-period="${value}">${esc(label)}</button>`).join('');
  }

  function renderDashboard() {
    const market = state.market;
    const opens = openPositions(market);
    const updateAt = state.data?.updated_at;
    root.innerHTML = `
      <section class="str-market-bar">
        <div class="str-market-tabs" role="tablist" aria-label="${esc(T.ariaMarket)}">${marketTabs()}</div>
        <div class="str-market-meta">
          <div>${marketIcon(market)}<span><b>${market === 'GPW' ? 'GPW' : 'USA'}</b>${statusHtml(market)}</span></div>
          <div class="str-updated"><span>${esc(T.updated)}</span><b>${dateFmt(updateAt, market, true)}</b></div>
        </div>
      </section>

      <section class="str-section" aria-labelledby="str-open-heading">
        <div class="str-section-head"><div><span class="str-section-icon">◎</span><div><h2 id="str-open-heading">${esc(T.openTickets)}</h2><p>${esc(T.openLead)}</p></div></div></div>
        <div class="str-ticket-grid ${opens.length === 1 ? 'one' : ''}">${opens.length ? opens.map((p) => openCard(p, market)).join('') : emptyOpen(market)}</div>
      </section>

      <section class="str-section str-results" aria-labelledby="str-results-heading">
        <div class="str-section-head str-results-head">
          <div><span class="str-section-icon">↗</span><div><h2 id="str-results-heading">${esc(T.summary)}</h2><p>${esc(T.summaryLead)}</p></div></div>
          <div class="str-periods" aria-label="${esc(T.summary)}">${periodButtons()}</div>
        </div>
        ${kpiCards(market)}
      </section>

      <section class="str-section" aria-labelledby="str-history-heading">
        <div class="str-section-head">
          <div><span class="str-section-icon">▤</span><div><h2 id="str-history-heading">${esc(T.history)}</h2><p>${esc(T.historyLead)}</p></div></div>
          <button class="str-export" type="button" id="str-export-history" ${stats(market).rows.length ? '' : 'disabled'}>⇩ ${esc(T.export)}</button>
        </div>
        ${historyRows(market)}
        <p class="str-roi-note">${esc(T.roiNote)}</p>
      </section>`;

    bindEvents();
  }

  function bindEvents() {
    root.querySelectorAll('[data-market]').forEach((button) => button.addEventListener('click', () => {
      state.market = button.dataset.market;
      try { sessionStorage.setItem('briefrooms-stock-market', state.market); } catch (_) {}
      renderDashboard();
    }));
    root.querySelectorAll('[data-period]').forEach((button) => button.addEventListener('click', () => {
      state.period = button.dataset.period;
      renderDashboard();
    }));
    root.querySelector('#str-export-history')?.addEventListener('click', exportCsv);
  }

  function exportCsv() {
    const market = state.market;
    const rows = stats(market).rows;
    if (!rows.length) return;
    const headers = [T.ticker, T.company, T.market, T.entryDate, T.exitDate, T.entryPrice, T.exitPrice, T.result, T.returnPct, T.status];
    const values = rows.map((position) => {
      const entry = firstNumber(position, ['entry', 'entry_price', 'open_price']);
      const exit = firstNumber(position, ['exit', 'exit_price', 'close_price', 'closed_mark', 'last_mark']);
      return [
        position.ticker || position.symbol || '', position.name || '', market,
        dateFmt(entryAt(position), market), dateFmt(closedAt(position), market),
        entry ?? '', exit ?? '', positionResult(position) ?? '', positionReturn(position) ?? '', T.closed
      ];
    });
    const quote = (v) => `"${String(v ?? '').replaceAll('"', '""')}"`;
    const csv = '\uFEFF' + [headers, ...values].map((row) => row.map(quote).join(';')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `briefrooms-stock-trading-${market.toLowerCase()}-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  }

  async function load() {
    root.innerHTML = `<div class="str-loading"><span></span><p>${esc(T.loading)}</p></div>`;
    try {
      const response = await fetch(`/data/investments/stock_trading_portfolio.json?v=${Date.now()}`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      state.data = await response.json();
      try {
        const saved = sessionStorage.getItem('briefrooms-stock-market');
        if (saved === 'GPW' || saved === 'US') state.market = saved;
      } catch (_) {}
      renderDashboard();
    } catch (error) {
      root.innerHTML = `<div class="str-error"><strong>${esc(T.error)}</strong><button type="button" id="str-retry">${esc(T.retry)}</button></div>`;
      root.querySelector('#str-retry')?.addEventListener('click', load);
    }
  }

  load();
})();
