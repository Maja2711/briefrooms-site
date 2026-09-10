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
    openPositions: 'otwarte pozycje', freeSlots: 'wolne sloty',
    marketOpen: 'rynek otwarty', marketClosed: 'rynek zamknięty',
    sessionLive: 'Sesja trwa', outsideSession: 'Poza godzinami sesji',
    portfolioData: 'Dane portfela',
    openTickets: 'Otwarte tikety',
    openLead: 'Aktywne pozycje z naszych analiz. Śledź wyniki i zarządzaj ryzykiem.',
    allTickets: 'Zobacz wszystkie tikety',
    active: 'Aktywna pozycja', availableSlots: 'Dostępne sloty',
    noActive: 'Brak aktywnej pozycji', noActiveText: 'Brak otwartej pozycji na tym rynku.', cash: 'CASH / wolny slot',
    market: 'Rynek', sector: 'Sektor', status: 'Status', noPosition: 'Brak pozycji',
    entry: 'Cena wejścia', last: 'Ostatni kurs', closedMarket: 'rynek zamknięty',
    sl: 'SL', tp: 'TP', entered: 'Wejście', pnl: 'P&L (od wejścia)',
    summary: 'Podsumowanie wyników', summaryLead: 'Twoje wyniki w liczbach. Konsekwencja buduje przewagę.',
    totalReturn: 'Łączny zwrot', winRate: 'Win rate', avgRR: 'Średnie R:R', avgHold: 'Średni czas trzymania',
    noClosed: 'Brak zamkniętych transakcji',
    days: 'dni', tradesShort: 'trans.',
    last30: 'Ostatnie 30 dni', last90: 'Ostatnie 90 dni', allTime: 'Od początku',
    history: 'Historia tradingu', historyLead: 'Pełna historia Twoich transakcji. Analizuj, wyciągaj wnioski, rozwijaj się.',
    export: 'Eksportuj historię',
    ticker: 'Ticker', company: 'Spółka', entryDate: 'Data wejścia', exitDate: 'Data wyjścia',
    entryPrice: 'Cena wejścia', exitPrice: 'Cena wyjścia', result: 'Wynik', returnPct: 'Zwrot %', closed: 'Zamknięta',
    noHistory: 'Brak zamkniętych transakcji do pokazania.',
    noHistorySub: 'Historia pojawi się automatycznie po zamknięciu pierwszej pozycji.',
    score: 'Score wejścia', marketChip: 'Rynek', sectorUnknown: '—',
    semiconductors: 'Półprzewodniki', technology: 'Technologia', materials: 'Surowce', financials: 'Finanse', energy: 'Energia',
    expand: 'Pokaż wszystkie', collapse: 'Pokaż mniej',
    empty: '—'
  } : {
    loading: 'Loading Trading Room…',
    error: 'Stock Trading data could not be loaded.',
    retry: 'Try again',
    gpw: 'GPW Market', us: 'US Market',
    openPositions: 'open positions', freeSlots: 'free slots',
    marketOpen: 'market open', marketClosed: 'market closed',
    sessionLive: 'Session live', outsideSession: 'Outside session hours',
    portfolioData: 'Portfolio data',
    openTickets: 'Open tickets',
    openLead: 'Active positions from our research. Track performance and manage risk.',
    allTickets: 'See all tickets',
    active: 'Active position', availableSlots: 'Available slots',
    noActive: 'No active position', noActiveText: 'No open position in this market.', cash: 'CASH / free slot',
    market: 'Market', sector: 'Sector', status: 'Status', noPosition: 'No position',
    entry: 'Entry price', last: 'Last price', closedMarket: 'market closed',
    sl: 'SL', tp: 'TP', entered: 'Entry', pnl: 'P&L (since entry)',
    summary: 'Performance summary', summaryLead: 'Your results in numbers. Consistency builds an edge.',
    totalReturn: 'Total return', winRate: 'Win rate', avgRR: 'Average R:R', avgHold: 'Average holding time',
    noClosed: 'No closed trades',
    days: 'days', tradesShort: 'trades',
    last30: 'Last 30 days', last90: 'Last 90 days', allTime: 'All time',
    history: 'Trading history', historyLead: 'Your complete trading history. Analyse, learn and improve.',
    export: 'Export history',
    ticker: 'Ticker', company: 'Company', entryDate: 'Entry date', exitDate: 'Exit date',
    entryPrice: 'Entry price', exitPrice: 'Exit price', result: 'Result', returnPct: 'Return %', closed: 'Closed',
    noHistory: 'No closed trades to show yet.',
    noHistorySub: 'History will appear automatically after the first position is closed.',
    score: 'Entry score', marketChip: 'Market', sectorUnknown: '—',
    semiconductors: 'Semiconductors', technology: 'Technology', materials: 'Materials', financials: 'Financials', energy: 'Energy',
    expand: 'Show all', collapse: 'Show less',
    empty: '—'
  };

  const state = { data: null, period: '30', expanded: false };
  const asNumber = (v) => Number.isFinite(Number(v)) ? Number(v) : null;
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
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

  const marketData = market => state.data?.markets?.[market] || {};
  const openPositions = market => Array.isArray(marketData(market).open_positions) ? marketData(market).open_positions : [];
  const closedPositions = market => Array.isArray(marketData(market).closed_positions) ? marketData(market).closed_positions : [];
  const maxPositions = market => Number(marketData(market).max_open_positions || 3);
  const currency = market => market === 'GPW' ? 'PLN' : 'USD';

  function money(value, market) {
    const n = asNumber(value);
    if (n === null) return T.empty;
    return new Intl.NumberFormat(locale, {
      style: 'currency', currency: currency(market), minimumFractionDigits: 2, maximumFractionDigits: 2
    }).format(n);
  }

  function percent(value, signed = true) {
    const n = asNumber(value);
    if (n === null) return T.empty;
    const sign = signed && n > 0 ? '+' : '';
    return `${sign}${n.toLocaleString(locale, {minimumFractionDigits: 2, maximumFractionDigits: 2})}%`;
  }

  function dateTime(value, market, withTime = true) {
    if (!value) return T.empty;
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return esc(value);
    return new Intl.DateTimeFormat(locale, {
      timeZone: market === 'US' ? 'America/New_York' : 'Europe/Warsaw',
      day: '2-digit', month: '2-digit', year: 'numeric',
      ...(withTime ? {hour: '2-digit', minute: '2-digit'} : {})
    }).format(d);
  }

  function portfolioTimestamp() {
    const value = state.data?.updated_at;
    if (!value) return T.empty;
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return T.empty;
    return new Intl.DateTimeFormat(locale, {
      timeZone: 'Europe/Warsaw', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit'
    }).format(d);
  }

  function marketClock(market) {
    const zone = market === 'US' ? 'America/New_York' : 'Europe/Warsaw';
    const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', {
      timeZone: zone, weekday: 'short', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
    }).formatToParts(new Date()).map(p => [p.type, p.value]));
    const weekday = ['Mon','Tue','Wed','Thu','Fri'].includes(parts.weekday);
    const mins = Number(parts.hour) * 60 + Number(parts.minute);
    const start = market === 'US' ? 570 : 540;
    const end = market === 'US' ? 960 : 1020;
    return {
      isOpen: weekday && mins >= start && mins < end,
      time: `${parts.hour}:${parts.minute}`,
      date: `${parts.day}.${parts.month}.${parts.year}`
    };
  }

  function marketFlag(market) {
    return market === 'GPW'
      ? '<span class="str-flag str-flag-pl" aria-hidden="true"></span>'
      : '<span class="str-flag str-flag-us" aria-hidden="true"><i></i></span>';
  }

  function icon(name) {
    const icons = {
      target: '<svg viewBox="0 0 24 24"><circle cx="11" cy="13" r="7"/><circle cx="11" cy="13" r="2"/><path d="M16 8l5-5M17 3h4v4"/></svg>',
      chart: '<svg viewBox="0 0 24 24"><path d="M4 20V11M10 20V5M16 20v-8M22 20V3"/><path d="M3 9l6-5 5 4 7-6"/></svg>',
      trend: '<svg viewBox="0 0 24 24"><path d="M3 18l6-6 4 4 8-10"/><path d="M16 6h5v5"/></svg>',
      trophy: '<svg viewBox="0 0 24 24"><path d="M8 4h8v4c0 4-2 7-4 7s-4-3-4-7V4z"/><path d="M8 6H4v2c0 3 2 5 5 5M16 6h4v2c0 3-2 5-5 5M12 15v4M8 21h8"/></svg>',
      scale: '<svg viewBox="0 0 24 24"><path d="M12 3v18M7 21h10M4 8h16M7 8l-3 6h6L7 8zM17 8l-3 6h6l-3-6z"/></svg>',
      clock: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v6h5"/></svg>',
      history: '<svg viewBox="0 0 24 24"><path d="M6 3h9l4 4v14H6z"/><path d="M15 3v5h4M9 12h7M9 16h7"/></svg>',
      wallet: '<svg viewBox="0 0 24 24"><path d="M4 7h14a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V7z"/><path d="M5 7l10-4 2 4M15 12h6v4h-6a2 2 0 010-4z"/></svg>',
      download: '<svg viewBox="0 0 24 24"><path d="M12 3v12M7 10l5 5 5-5M4 20h16"/></svg>',
      calendar: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4M17 3v4M3 10h18"/></svg>'
    };
    return icons[name] || '';
  }

  function sectorLabel(value) {
    const raw = String(value || '').trim();
    if (!raw) return T.sectorUnknown;
    const key = raw.toLowerCase().replace(/[\s_-]+/g, '');
    const map = {
      semiconductors: T.semiconductors,
      semiconductor: T.semiconductors,
      technology: T.technology,
      tech: T.technology,
      materials: T.materials,
      rawmaterials: T.materials,
      financials: T.financials,
      finance: T.financials,
      energy: T.energy
    };
    return map[key] || raw;
  }

  function companyMark(ticker, market) {
    const label = String(ticker || '?').slice(0,4).toUpperCase();
    return `<div class="str-company-mark ${market === 'US' ? 'is-us' : 'is-gpw'}" aria-label="${esc(label)}">
      <svg viewBox="0 0 64 44" aria-hidden="true">
        <path d="M7 31c8-1 10-15 18-11 6 3 4 10 10 9 9-2 8-19 19-22"/>
        <circle cx="54" cy="7" r="3"/>
      </svg>
      <strong>${esc(label)}</strong>
    </div>`;
  }

  function marketTab(market) {
    const open = openPositions(market).length;
    const max = maxPositions(market);
    const free = Math.max(0, max - open);
    return `<button class="str-market-tab ${market === 'GPW' ? 'is-active' : ''}" type="button" data-market-jump="${market}">
      <span class="str-market-tab-title">${marketFlag(market)}<b>${esc(market === 'GPW' ? T.gpw : T.us)}</b></span>
      <small>${open}/${max} ${esc(T.openPositions)} · ${free} ${esc(T.freeSlots)}</small>
    </button>`;
  }

  function marketStatus(market) {
    const clock = marketClock(market);
    const marketName = market === 'GPW' ? 'GPW' : 'USA';
    return `<div class="str-market-status ${clock.isOpen ? 'is-open' : 'is-closed'}">
      <span class="str-status-dot"></span>
      <div><strong>${marketName}: <em>${esc(clock.isOpen ? T.marketOpen : T.marketClosed)}</em></strong>
      <small>${esc(clock.isOpen ? T.sessionLive : T.outsideSession)} · ${esc(clock.date)}, ${esc(clock.time)}</small></div>
    </div>`;
  }

  function openPositionCard(position, market, index) {
    const entry = firstNumber(position, ['entry','entry_price','open_price']);
    const mark = firstNumber(position, ['last_mark','mark','current_price','close_price']);
    const stop = firstNumber(position, ['stop','sl','stop_loss']);
    const target = firstNumber(position, ['target','tp','take_profit']);
    const p = entry !== null && mark !== null && entry !== 0 ? ((mark - entry) / entry) * 100 : null;
    const abs = entry !== null && mark !== null ? mark - entry : null;
    const clock = marketClock(market);
    const ticker = String(position.ticker || position.symbol || '—').toUpperCase();
    const name = position.name || ticker;
    const score = firstNumber(position, ['entry_score','score']);
    const sector = sectorLabel(position.sector);
    const hiddenClass = !state.expanded && index > 0 ? ' is-extra' : '';

    return `<article class="str-position${hiddenClass}">
      <div class="str-position-head">
        <span class="str-market-badge">${marketFlag(market)}<b>${market === 'GPW' ? 'GPW' : 'USA'}</b></span>
        <span class="str-active-badge"><i></i>${esc(T.active)}</span>
      </div>
      <div class="str-position-body">
        ${companyMark(ticker, market)}
        <div class="str-position-main">
          <div class="str-company-name"><h3>${esc(ticker)}</h3><span>${esc(name)}</span></div>
          <div class="str-metrics">
            <div><span>${esc(T.entry)}</span><strong>${money(entry, market)}</strong></div>
            <div class="str-metric-last"><span>${esc(T.last)}</span><strong>${money(mark, market)}</strong>${clock.isOpen ? '<small class="is-open-note">live</small>' : `<small>${esc(T.closedMarket)}</small>`}</div>
            <div><span>${esc(T.sl)}</span><strong>${money(stop, market)}</strong></div>
            <div><span>${esc(T.tp)}</span><strong>${money(target, market)}</strong></div>
          </div>
          <div class="str-position-footer">
            <div class="str-position-meta">
              <span>${esc(T.entered)}: <b>${dateTime(position.opened_at, market, true)}</b></span>
              <span class="str-meta-pill">${esc(T.sector)}: ${esc(sector)}</span>
              <span class="str-meta-pill">${esc(T.marketChip)}: ${market === 'GPW' ? 'GPW' : 'USA'}</span>
              ${score !== null ? `<span class="str-meta-pill">${esc(T.score)}: ${score.toLocaleString(locale,{maximumFractionDigits:2})}</span>` : ''}
            </div>
            <div class="str-pnl ${p === null || p === 0 ? 'is-neutral' : p > 0 ? 'is-positive' : 'is-negative'}">
              <span>${esc(T.pnl)}</span>
              <strong>${percent(p)}</strong>
              <b>${abs === null ? T.empty : `${abs > 0 ? '+' : ''}${money(abs, market)}`}</b>
            </div>
          </div>
        </div>
      </div>
    </article>`;
  }

  function emptyMarketCard(market) {
    const free = Math.max(0, maxPositions(market) - openPositions(market).length);
    return `<article class="str-position str-position-empty">
      <div class="str-position-head">
        <span class="str-market-badge">${marketFlag(market)}<b>${market === 'GPW' ? 'GPW' : 'USA'}</b></span>
        <span class="str-slots-badge"><i></i>${esc(T.availableSlots)} ${free}</span>
      </div>
      <div class="str-empty-body">
        <div class="str-empty-visual">${icon('wallet')}</div>
        <div><h3>${esc(T.noActive)}</h3><p>${esc(T.noActiveText)}</p><strong>${esc(T.cash)}</strong></div>
      </div>
      <div class="str-empty-meta">
        <span class="str-meta-pill">${esc(T.market)}: ${market === 'GPW' ? 'GPW' : 'USA'}</span>
        <span class="str-meta-pill">${esc(T.sector)}: —</span>
        <span class="str-meta-pill">${esc(T.status)}: ${esc(T.noPosition)}</span>
      </div>
    </article>`;
  }

  function marketTicketPanel(market) {
    const positions = openPositions(market);
    return `<div class="str-market-ticket-panel" id="str-market-${market.toLowerCase()}">
      ${positions.length ? positions.map((p,i) => openPositionCard(p, market, i)).join('') : emptyMarketCard(market)}
    </div>`;
  }

  const closedAt = p => firstValue(p, ['closed_at','exit_at','closed_on','exit_date','resolved_at']);
  const entryAt = p => firstValue(p, ['opened_at','entry_at','opened_on','entry_date']);
  const rowMarket = p => String(p.market || '').toUpperCase() === 'US' ? 'US' : 'GPW';

  function rowReturn(p) {
    const explicit = firstNumber(p, ['return_percent','return_pct','pnl_percent','result_percent']);
    if (explicit !== null) return explicit;
    const entry = firstNumber(p, ['entry','entry_price','open_price']);
    const exit = firstNumber(p, ['exit','exit_price','close_price','closed_mark','last_mark']);
    if (entry === null || exit === null || entry === 0) return null;
    return ((exit-entry)/entry)*100;
  }

  function rowResult(p) {
    const explicit = firstNumber(p, ['pnl','pnl_amount','result_amount','profit_loss']);
    if (explicit !== null) return explicit;
    const entry = firstNumber(p, ['entry','entry_price','open_price']);
    const exit = firstNumber(p, ['exit','exit_price','close_price','closed_mark','last_mark']);
    if (entry === null || exit === null) return null;
    return exit-entry;
  }

  function allClosed() {
    return ['GPW','US'].flatMap(market => closedPositions(market).map(p => ({...p, __market: market})))
      .sort((a,b) => new Date(closedAt(b)||0) - new Date(closedAt(a)||0));
  }

  function filteredClosed() {
    const rows = allClosed();
    if (state.period === 'all') return rows;
    const days = state.period === '30' ? 30 : 90;
    const cutoff = Date.now() - days*86400000;
    return rows.filter(p => {
      const t = new Date(closedAt(p)||0).getTime();
      return Number.isFinite(t) && t >= cutoff;
    });
  }

  function summaryStats() {
    const rows = filteredClosed();
    const returns = rows.map(rowReturn).filter(v => v !== null);
    const cumulative = returns.length ? (returns.reduce((acc,r) => acc*(1+r/100),1)-1)*100 : 0;
    const wins = returns.filter(v => v > 0).length;
    const rr = rows.map(p => firstNumber(p,['realized_r','r_multiple','reward_risk'])).filter(v => v !== null);
    const holds = rows.map(p => {
      const a = new Date(entryAt(p)||0).getTime();
      const b = new Date(closedAt(p)||0).getTime();
      return Number.isFinite(a)&&Number.isFinite(b)&&b>=a ? (b-a)/86400000 : null;
    }).filter(v => v !== null);
    return {
      count: rows.length,
      cumulative,
      winRate: returns.length ? wins/returns.length*100 : null,
      avgRR: rr.length ? rr.reduce((a,b)=>a+b,0)/rr.length : null,
      avgHold: holds.length ? holds.reduce((a,b)=>a+b,0)/holds.length : null
    };
  }

  function metricCard(iconName, label, value, note, cls='') {
    return `<article class="str-summary-card ${cls}">
      <span class="str-summary-icon">${icon(iconName)}</span>
      <div><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></div>
    </article>`;
  }

  function summarySection() {
    const s = summaryStats();
    return `<section class="str-section str-summary-section">
      <div class="str-section-header str-section-header-with-filter">
        <div class="str-section-heading"><span class="str-section-icon">${icon('chart')}</span><div><h2>${esc(T.summary)}</h2><p>${esc(T.summaryLead)}</p></div></div>
        <div class="str-period-filter" role="group" aria-label="${esc(T.summary)}">
          <button type="button" data-period="30" class="${state.period==='30'?'is-active':''}">${esc(T.last30)}</button>
          <button type="button" data-period="90" class="${state.period==='90'?'is-active':''}">${esc(T.last90)}</button>
          <button type="button" data-period="all" class="${state.period==='all'?'is-active':''}">${esc(T.allTime)}</button>
        </div>
      </div>
      <div class="str-summary-grid">
        ${metricCard('trend',T.totalReturn,percent(s.cumulative,false),s.count ? `${s.count} ${T.tradesShort}` : T.noClosed,s.cumulative>0?'is-positive':s.cumulative<0?'is-negative':'')}
        ${metricCard('trophy',T.winRate,s.winRate===null?'—':percent(s.winRate,false),s.count ? `${s.count} ${T.tradesShort}` : T.noClosed)}
        ${metricCard('scale',T.avgRR,s.avgRR===null?'—':`${s.avgRR.toLocaleString(locale,{minimumFractionDigits:1,maximumFractionDigits:2})} : 1`,s.count ? `${s.count} ${T.tradesShort}` : T.noClosed)}
        ${metricCard('clock',T.avgHold,s.avgHold===null?'—':`${s.avgHold.toLocaleString(locale,{minimumFractionDigits:1,maximumFractionDigits:1})} ${T.days}`,s.count ? `${s.count} ${T.tradesShort}` : T.noClosed)}
      </div>
    </section>`;
  }

  function historyRows() {
    const rows = filteredClosed();
    if (!rows.length) {
      return `<div class="str-history-empty"><span>${icon('history')}</span><div><strong>${esc(T.noHistory)}</strong><small>${esc(T.noHistorySub)}</small></div></div>`;
    }
    return rows.map(p => {
      const market = p.__market || rowMarket(p);
      const ticker = String(p.ticker || p.symbol || '—').toUpperCase();
      const ret = rowReturn(p);
      const result = rowResult(p);
      const entry = firstNumber(p,['entry','entry_price','open_price']);
      const exit = firstNumber(p,['exit','exit_price','close_price','closed_mark','last_mark']);
      const tone = ret === null || ret === 0 ? 'neutral' : ret > 0 ? 'positive' : 'negative';
      return `<div class="str-history-row" role="row">
        <strong role="cell">${esc(ticker)}</strong>
        <span role="cell">${esc(p.name || ticker)}</span>
        <span role="cell" class="str-history-market">${marketFlag(market)}${market==='US'?'USA':'GPW'}</span>
        <span role="cell">${dateTime(entryAt(p),market,false)}</span>
        <span role="cell">${dateTime(closedAt(p),market,false)}</span>
        <span role="cell">${money(entry,market)}</span>
        <span role="cell">${money(exit,market)}</span>
        <span role="cell" class="is-${tone}">${result===null?'—':`${result>0?'+':''}${money(result,market)}`}</span>
        <span role="cell"><b class="str-return-pill is-${tone}">${percent(ret)}</b></span>
        <span role="cell"><b class="str-closed-pill">${esc(T.closed)}</b></span>
      </div>`;
    }).join('');
  }

  function historySection() {
    return `<section class="str-section str-history-section">
      <div class="str-section-header">
        <div class="str-section-heading"><span class="str-section-icon">${icon('history')}</span><div><h2>${esc(T.history)}</h2><p>${esc(T.historyLead)}</p></div></div>
        <button class="str-export" type="button" data-export>${icon('download')}<span>${esc(T.export)}</span></button>
      </div>
      <div class="str-history-table" role="table" aria-label="${esc(T.history)}">
        <div class="str-history-head" role="row">
          <span role="columnheader">${esc(T.ticker)}</span><span role="columnheader">${esc(T.company)}</span><span role="columnheader">${esc(T.market)}</span>
          <span role="columnheader">${esc(T.entryDate)}</span><span role="columnheader">${esc(T.exitDate)}</span><span role="columnheader">${esc(T.entryPrice)}</span>
          <span role="columnheader">${esc(T.exitPrice)}</span><span role="columnheader">${esc(T.result)}</span><span role="columnheader">${esc(T.returnPct)}</span><span role="columnheader">${esc(T.status)}</span>
        </div>
        <div class="str-history-body">${historyRows()}</div>
      </div>
    </section>`;
  }

  function render() {
    const openTotal = openPositions('GPW').length + openPositions('US').length;
    const hasExtras = openPositions('GPW').length > 1 || openPositions('US').length > 1;
    root.innerHTML = `
      <section class="str-market-overview">
        <div class="str-market-tabs" role="navigation" aria-label="Markets">${marketTab('GPW')}${marketTab('US')}</div>
        <div class="str-market-statuses">${marketStatus('GPW')}${marketStatus('US')}</div>
        <div class="str-data-stamp"><span>${icon('calendar')}</span><div><small>${esc(T.portfolioData)}</small><strong>${esc(portfolioTimestamp())}</strong></div></div>
      </section>

      <section class="str-section str-open-section">
        <div class="str-section-header">
          <div class="str-section-heading"><span class="str-section-icon">${icon('target')}</span><div><h2>${esc(T.openTickets)}</h2><p>${esc(T.openLead)}</p></div></div>
          ${hasExtras ? `<button class="str-show-all" type="button" data-toggle-extra>${esc(state.expanded?T.collapse:T.allTickets)} <span>→</span></button>` : `<span class="str-open-count">${openTotal} ${esc(T.openPositions)}</span>`}
        </div>
        <div class="str-open-grid">${marketTicketPanel('GPW')}${marketTicketPanel('US')}</div>
      </section>

      ${summarySection()}
      ${historySection()}
    `;

    root.querySelectorAll('[data-market-jump]').forEach(btn => btn.addEventListener('click', () => {
      root.querySelectorAll('.str-market-tab').forEach(x => x.classList.toggle('is-active', x === btn));
      const target = document.getElementById(`str-market-${btn.dataset.marketJump.toLowerCase()}`);
      target?.scrollIntoView({behavior:'smooth',block:'center'});
      target?.classList.add('str-focus-pulse');
      setTimeout(() => target?.classList.remove('str-focus-pulse'), 900);
    }));

    root.querySelectorAll('[data-period]').forEach(btn => btn.addEventListener('click', () => {
      state.period = btn.dataset.period;
      render();
    }));

    root.querySelector('[data-toggle-extra]')?.addEventListener('click', () => {
      state.expanded = !state.expanded;
      render();
    });

    root.querySelector('[data-export]')?.addEventListener('click', exportCsv);
  }

  function exportCsv() {
    const rows = filteredClosed();
    const header = [T.ticker,T.company,T.market,T.entryDate,T.exitDate,T.entryPrice,T.exitPrice,T.result,T.returnPct,T.status];
    const csvRows = [header];
    rows.forEach(p => {
      const market = p.__market || rowMarket(p);
      const ticker = String(p.ticker || p.symbol || '—').toUpperCase();
      const entry = firstNumber(p,['entry','entry_price','open_price']);
      const exit = firstNumber(p,['exit','exit_price','close_price','closed_mark','last_mark']);
      const result = rowResult(p);
      const ret = rowReturn(p);
      csvRows.push([
        ticker,p.name || ticker,market==='US'?'USA':'GPW',dateTime(entryAt(p),market,false),dateTime(closedAt(p),market,false),
        entry ?? '',exit ?? '',result ?? '',ret ?? '',T.closed
      ]);
    });
    const csv = '\ufeff' + csvRows.map(row => row.map(v => `"${String(v).replaceAll('"','""')}"`).join(';')).join('\n');
    const blob = new Blob([csv],{type:'text/csv;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `briefrooms-stock-trading-${state.period}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  async function load() {
    root.innerHTML = `<div class="str-loading"><span></span><p>${esc(T.loading)}</p></div>`;
    try {
      const res = await fetch(`/data/investments/stock_trading_portfolio.json?v=${Date.now()}`, {cache:'no-store'});
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      state.data = await res.json();
      render();
    } catch (err) {
      console.error('Stock Trading Room:', err);
      root.innerHTML = `<div class="str-error"><strong>${esc(T.error)}</strong><button type="button">${esc(T.retry)}</button></div>`;
      root.querySelector('button')?.addEventListener('click', load);
    }
  }

  load();
})();
