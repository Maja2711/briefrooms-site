(() => {
  'use strict';

  const lang = (window.BR_PORTFOLIO_10K?.lang || document.documentElement.lang)
    .toLowerCase()
    .startsWith('en') ? 'en' : 'pl';
  const isPl = lang === 'pl';
  const locale = isPl ? 'pl-PL' : 'en-US';
  const reportCurrency = isPl ? 'PLN' : 'USD';

  const T = isPl ? {
    tradesTitle: 'Historia operacji portfela',
    tradesSubtitle: 'Każde wykonane kupno i każda sprzedaż — także częściowe redukcje pozycji — są zapisywane chronologicznie.',
    executedAt: 'Data i czas',
    action: 'Decyzja',
    side: 'Operacja',
    instrument: 'Instrument',
    units: 'Jednostki',
    executionPrice: 'Cena wykonania',
    grossValue: 'Wartość',
    costs: 'Koszty',
    cashEffect: 'Wpływ na gotówkę',
    rationale: 'Uzasadnienie',
    buy: 'KUPNO',
    sell: 'SPRZEDAŻ',
    executed: 'wykonana',
    title: 'Zamknięte transakcje',
    subtitle: 'Pozycje sprzedane w całości wraz z datą zakupu, datą sprzedaży i ostatecznym wynikiem po kosztach.',
    purchaseDate: 'Data zakupu',
    purchasePrice: 'Cena zakupu',
    saleDate: 'Data sprzedaży',
    salePrice: 'Cena sprzedaży',
    purchaseCapital: 'Kapitał zakupu',
    saleValue: 'Wartość sprzedaży',
    result: 'Zysk / strata',
    status: 'Status',
    sold: 'sprzedana'
  } : {
    tradesTitle: 'Portfolio transaction history',
    tradesSubtitle: 'Every executed buy and sell — including partial position reductions — is recorded chronologically.',
    executedAt: 'Date and time',
    action: 'Decision',
    side: 'Trade',
    instrument: 'Instrument',
    units: 'Units',
    executionPrice: 'Execution price',
    grossValue: 'Value',
    costs: 'Costs',
    cashEffect: 'Cash effect',
    rationale: 'Rationale',
    buy: 'BUY',
    sell: 'SELL',
    executed: 'executed',
    title: 'Closed transactions',
    subtitle: 'Positions sold in full with purchase date, sale date and final after-cost result.',
    purchaseDate: 'Purchase date',
    purchasePrice: 'Purchase price',
    saleDate: 'Sale date',
    salePrice: 'Sale price',
    purchaseCapital: 'Purchase capital',
    saleValue: 'Sale proceeds',
    result: 'Profit / loss',
    status: 'Status',
    sold: 'sold'
  };

  const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[character]));

  const num = value => Number.isFinite(Number(value)) ? Number(value) : null;

  const dateText = value => {
    if (!value) return '—';
    const date = new Date(String(value).length === 10 ? `${value}T12:00:00Z` : value);
    return Number.isNaN(date.valueOf())
      ? String(value)
      : date.toLocaleDateString(locale, { year: 'numeric', month: 'short', day: '2-digit' });
  };

  const dateTimeText = value => {
    if (!value) return '—';
    const date = new Date(value);
    return Number.isNaN(date.valueOf())
      ? String(value)
      : date.toLocaleString(locale, {
          year: 'numeric',
          month: 'short',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit'
        });
  };

  const priceText = (value, currency) => {
    const amount = num(value);
    if (amount === null) return '—';
    const digits = currency === 'DKK' ? 4 : amount < 10 ? 4 : 2;
    return `${amount.toLocaleString(locale, {
      minimumFractionDigits: 2,
      maximumFractionDigits: digits
    })} ${currency || ''}`.trim();
  };

  function usdPlnRate(portfolio) {
    const direct = num(portfolio?.reporting_fx?.usd_pln);
    if (direct && direct > 0) return direct;

    for (const position of portfolio?.positions || []) {
      if (position?.currency !== 'USD') continue;
      const rate = num(position.current_fx_to_pln) || num(position.entry_fx_to_pln);
      if (rate && rate > 0) return rate;
    }
    return null;
  }

  function reportMoney(plnValue, portfolio, signed = false, transaction = null, currency = null) {
    let amount = num(plnValue);
    if (amount === null) return '—';

    if (!isPl) {
      const executionFx = num(transaction?.fx_to_pln);
      if (currency === 'USD' && executionFx && executionFx > 0) {
        amount /= executionFx;
      } else {
        const rate = usdPlnRate(portfolio);
        if (!rate) return '— USD';
        amount /= rate;
      }
    }

    const sign = signed && amount > 0 ? '+' : '';
    return `${sign}${amount.toLocaleString(locale, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    })} ${reportCurrency}`;
  }

  function percentText(value) {
    const amount = num(value);
    if (amount === null) return '—';
    const sign = amount > 0 ? '+' : '';
    return `${sign}${(amount * 100).toLocaleString(locale, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    })}%`;
  }

  function instrumentMap(portfolio) {
    const map = new Map();
    for (const position of [
      ...(portfolio?.positions || []),
      ...(portfolio?.closed_positions || [])
    ]) {
      const id = String(position?.id || position?.instrument_id || '').trim();
      if (id && !map.has(id)) map.set(id, position);
    }
    return map;
  }

  function sameExecutionMoment(a, b) {
    const first = new Date(a || '').valueOf();
    const second = new Date(b || '').valueOf();
    return Number.isFinite(first) && Number.isFinite(second) && Math.abs(first - second) < 60_000;
  }

  function inferredAction(transaction, transactions, portfolio) {
    const explicit = String(transaction?.action || transaction?.order_action || '').toUpperCase();
    if (['ADD', 'REDUCE', 'EXIT', 'REPLACE'].includes(explicit)) return explicit;

    const orderId = String(transaction?.order_id || '');
    const siblings = orderId
      ? transactions.filter(row => String(row?.order_id || '') === orderId)
      : [transaction];
    const hasBuy = siblings.some(row => String(row?.side || '').toUpperCase() === 'BUY');
    const hasSell = siblings.some(row => String(row?.side || '').toUpperCase() === 'SELL');
    if (hasBuy && hasSell) return 'REPLACE';

    const side = String(transaction?.side || '').toUpperCase();
    if (side === 'BUY') return 'ADD';
    if (side !== 'SELL') return 'TRADE';

    const instrumentId = String(transaction?.instrument_id || '');
    const matchingClose = (portfolio?.closed_positions || []).some(position =>
      String(position?.id || position?.instrument_id || '') === instrumentId
      && sameExecutionMoment(position?.exit_timestamp_utc, transaction?.executed_at)
    );
    if (matchingClose) return 'EXIT';

    const remainsActive = (portfolio?.positions || []).some(position =>
      String(position?.id || position?.instrument_id || '') === instrumentId
    );
    return remainsActive ? 'REDUCE' : 'EXIT';
  }

  function ensureHistoryStyle() {
    if (document.getElementById('portfolio-trade-history-style')) return;
    const style = document.createElement('style');
    style.id = 'portfolio-trade-history-style';
    style.textContent = `
      #portfolio-trade-history .trade-action{display:inline-flex;align-items:center;border-radius:999px;padding:3px 8px;font-size:11px;font-weight:900;letter-spacing:.02em;background:#eef2f7;color:#334155;white-space:nowrap}
      #portfolio-trade-history .trade-action.action-add{background:#e7f7ee;color:#138a47}
      #portfolio-trade-history .trade-action.action-reduce{background:#fff4db;color:#9a6700}
      #portfolio-trade-history .trade-action.action-exit{background:#feecec;color:#b42318}
      #portfolio-trade-history .trade-action.action-replace{background:#f1eafe;color:#6941c6}
      #portfolio-trade-history .trade-side{font-weight:800;white-space:nowrap}
      #portfolio-trade-history .trade-side.buy{color:#138a47}
      #portfolio-trade-history .trade-side.sell{color:#b42318}
      #portfolio-trade-history .trade-rationale{min-width:280px;max-width:480px;line-height:1.45}
      #portfolio-trade-history .trade-cash-positive{color:#138a47;font-weight:800}
      #portfolio-trade-history .trade-cash-negative{color:#b42318;font-weight:800}
    `;
    document.head.appendChild(style);
  }

  function renderTradeHistory(portfolio, registerPanel) {
    const transactions = (portfolio?.transactions || []).filter(transaction =>
      transaction?.executed_at && ['BUY', 'SELL'].includes(String(transaction?.side || '').toUpperCase())
    );
    if (!transactions.length) return null;

    ensureHistoryStyle();
    const instruments = instrumentMap(portfolio);
    let section = document.getElementById('portfolio-trade-history');
    if (!section) {
      section = document.createElement('section');
      section.id = 'portfolio-trade-history';
      section.className = 'panel';
      registerPanel.insertAdjacentElement('afterend', section);
    }

    const rows = [...transactions]
      .sort((a, b) => String(b.executed_at).localeCompare(String(a.executed_at)))
      .map(transaction => {
        const instrumentId = String(transaction.instrument_id || '');
        const instrument = instruments.get(instrumentId) || {};
        const side = String(transaction.side || '').toUpperCase();
        const action = inferredAction(transaction, transactions, portfolio);
        const quantity = num(transaction.quantity);
        const price = num(transaction.price);
        const fx = num(transaction.fx_to_pln);
        const costs = num(transaction.transaction_cost_pln) || 0;
        const gross = quantity !== null && price !== null && fx !== null
          ? quantity * price * fx
          : null;
        const cashEffect = gross === null
          ? null
          : side === 'SELL'
            ? gross - costs
            : -(gross + costs);
        const currency = instrument.currency || '';
        const rationale = isPl
          ? (transaction.rationale_pl || transaction.rationale_en || '—')
          : (transaction.rationale_en || transaction.rationale_pl || '—');
        const cashClass = cashEffect > 0
          ? 'trade-cash-positive'
          : cashEffect < 0
            ? 'trade-cash-negative'
            : '';

        return `<tr>
          <td>${esc(dateTimeText(transaction.executed_at))}</td>
          <td><span class="trade-action action-${esc(action.toLowerCase())}">${esc(action)}</span></td>
          <td><span class="trade-side ${side === 'BUY' ? 'buy' : 'sell'}">${esc(side === 'BUY' ? T.buy : T.sell)}</span></td>
          <td><b>${esc(instrument.broker_symbol || instrument.market_symbol || instrumentId || '—')}</b><br>${esc(instrument.label || '')}</td>
          <td>${quantity === null ? '—' : quantity.toLocaleString(locale, { maximumFractionDigits: 8 })}</td>
          <td>${esc(priceText(price, currency))}</td>
          <td>${esc(reportMoney(gross, portfolio, false, transaction, currency))}</td>
          <td>${esc(reportMoney(costs, portfolio, false, transaction, currency))}</td>
          <td class="${cashClass}">${esc(reportMoney(cashEffect, portfolio, true, transaction, currency))}</td>
          <td class="trade-rationale">${esc(rationale)}</td>
        </tr>`;
      }).join('');

    section.innerHTML = `
      <div class="panel-head">
        <div>
          <h2>${esc(T.tradesTitle)}</h2>
          <p>${esc(T.tradesSubtitle)}</p>
        </div>
      </div>
      <div class="table-scroll">
        <table class="audit">
          <thead><tr>
            <th>${esc(T.executedAt)}</th>
            <th>${esc(T.action)}</th>
            <th>${esc(T.side)}</th>
            <th>${esc(T.instrument)}</th>
            <th>${esc(T.units)}</th>
            <th>${esc(T.executionPrice)}</th>
            <th>${esc(T.grossValue)}</th>
            <th>${esc(T.costs)}</th>
            <th>${esc(T.cashEffect)}</th>
            <th>${esc(T.rationale)}</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;

    return section;
  }

  function renderClosedHistory(portfolio, registerPanel, tradeSection) {
    const closed = (portfolio?.closed_positions || []).filter(position =>
      position?.exit_timestamp_utc && num(position?.exit_value_pln) !== null
    );
    if (!closed.length) return null;

    let section = document.getElementById('closed-transactions-history');
    if (!section) {
      section = document.createElement('section');
      section.id = 'closed-transactions-history';
      section.className = 'panel';
      (tradeSection || registerPanel).insertAdjacentElement('afterend', section);
    }

    const rows = [...closed]
      .sort((a, b) => String(b.exit_timestamp_utc).localeCompare(String(a.exit_timestamp_utc)))
      .map(position => {
        const entryValue = num(position.entry_value_pln);
        const exitValue = num(position.exit_value_pln);
        const result = entryValue === null || exitValue === null ? null : exitValue - entryValue;
        const resultPercent = result === null || !entryValue ? null : result / entryValue;
        const tone = result > 0 ? 'positive' : result < 0 ? 'negative' : 'neutral';

        return `<tr>
          <td><b>${esc(position.broker_symbol || position.market_symbol || position.id || '—')}</b><br>${esc(position.label || '')}</td>
          <td>${esc(dateText(position.entry_date || position.entry_timestamp_utc))}</td>
          <td>${esc(priceText(position.entry_price, position.currency))}</td>
          <td>${esc(dateText(position.exit_timestamp_utc))}</td>
          <td>${esc(priceText(position.exit_price, position.currency))}</td>
          <td>${num(position.quantity) === null ? '—' : num(position.quantity).toLocaleString(locale, { maximumFractionDigits: 6 })}</td>
          <td>${esc(reportMoney(entryValue, portfolio))}</td>
          <td>${esc(reportMoney(exitValue, portfolio))}</td>
          <td class="${tone}"><b>${esc(reportMoney(result, portfolio, true))}</b><br><small>${esc(percentText(resultPercent))}</small></td>
          <td>${esc(T.sold)}</td>
        </tr>`;
      }).join('');

    section.innerHTML = `
      <div class="panel-head">
        <div>
          <h2>${esc(T.title)}</h2>
          <p>${esc(T.subtitle)}</p>
        </div>
      </div>
      <div class="table-scroll">
        <table class="audit">
          <thead><tr>
            <th>${esc(T.instrument)}</th>
            <th>${esc(T.purchaseDate)}</th>
            <th>${esc(T.purchasePrice)}</th>
            <th>${esc(T.saleDate)}</th>
            <th>${esc(T.salePrice)}</th>
            <th>${esc(T.units)}</th>
            <th>${esc(T.purchaseCapital)}</th>
            <th>${esc(T.saleValue)}</th>
            <th>${esc(T.result)}</th>
            <th>${esc(T.status)}</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;

    return section;
  }

  function render(portfolio) {
    const auditBody = document.getElementById('audit-body');
    const registerPanel = auditBody?.closest('section.panel');
    if (!registerPanel) return false;

    const tradeSection = renderTradeHistory(portfolio, registerPanel);
    const closedSection = renderClosedHistory(portfolio, registerPanel, tradeSection);
    if (!tradeSection && !closedSection) return false;

    document.body.dataset.closedTransactionsHistory = 'ready';
    document.body.dataset.portfolioTradeHistory = tradeSection ? 'ready' : 'empty';
    return true;
  }

  async function start() {
    try {
      const response = await fetch(`/data/portfolio10k/paper_portfolio.json?v=${Date.now()}`, {
        cache: 'no-store'
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const portfolio = await response.json();

      let attempts = 0;
      const apply = () => {
        render(portfolio);
        attempts += 1;
        if (attempts < 24 && document.body.dataset.closedTransactionsHistory !== 'ready') {
          window.setTimeout(apply, 300);
        }
      };

      apply();
      window.addEventListener('hashchange', () => window.setTimeout(apply, 0));
      document.addEventListener('click', event => {
        if (event.target.closest('[data-tab]')) window.setTimeout(apply, 0);
      });
    } catch (_) {
      // The current History view remains unchanged if transaction data is unavailable.
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
