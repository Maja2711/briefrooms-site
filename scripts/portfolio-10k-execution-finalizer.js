(() => {
  'use strict';

  const lang = (window.BR_PORTFOLIO_10K?.lang || document.documentElement.lang)
    .toLowerCase()
    .startsWith('en') ? 'en' : 'pl';
  const isPolish = lang === 'pl';
  const locale = isPolish ? 'pl-PL' : 'en-US';

  const COPY = isPolish ? {
    cardTitle: 'OSTATNIE ZREALIZOWANE DECYZJE (BRACE)',
    viewHistory: 'Pełna historia',
    noExecuted: 'Brak zrealizowanych decyzji BRACE.',
    noPending: 'Brak decyzji oczekujących na wykonanie.',
    executed: 'Realizacja',
    price: 'cena',
    units: 'jednostki',
    costs: 'koszty',
    reason: 'Powód',
    strength: 'Siła decyzji',
    reduce: 'REDUKCJA ZREALIZOWANA',
    reduceText: 'Zmniejszono pozycję',
    exit: 'SPRZEDAŻ ZREALIZOWANA',
    exitText: 'Zamknięto pozycję',
    add: 'ZAKUP ZREALIZOWANY',
    addText: 'Dodano pozycję',
    replace: 'ROTACJA ZREALIZOWANA',
    replaceText: 'Zrealizowano rotację',
    sold: 'sprzedano',
    bought: 'kupiono'
  } : {
    cardTitle: 'LATEST EXECUTED DECISIONS (BRACE)',
    viewHistory: 'Full history',
    noExecuted: 'No BRACE decisions have been executed yet.',
    noPending: 'No decisions are awaiting execution.',
    executed: 'Executed',
    price: 'price',
    units: 'units',
    costs: 'costs',
    reason: 'Reason',
    strength: 'Decision strength',
    reduce: 'REDUCTION EXECUTED',
    reduceText: 'Reduced position',
    exit: 'SALE EXECUTED',
    exitText: 'Closed position',
    add: 'PURCHASE EXECUTED',
    addText: 'Added position',
    replace: 'ROTATION EXECUTED',
    replaceText: 'Executed rotation',
    sold: 'sold',
    bought: 'bought'
  };

  const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[character]));

  async function json(path) {
    const response = await fetch(`${path}?v=${Date.now()}`, { cache: 'no-store' });
    if (!response.ok) throw new Error(path);
    return response.json();
  }

  function number(value) {
    const result = Number(value);
    return Number.isFinite(result) ? result : null;
  }

  function metadataMap(paperPortfolio, universe) {
    const map = new Map();
    for (const item of universe?.instruments || []) {
      if (!item?.instrument_id) continue;
      map.set(String(item.instrument_id), {
        symbol: item.broker_symbol || item.data_symbol || String(item.instrument_id).toUpperCase(),
        label: item.label || '',
        currency: item.currency || ''
      });
    }
    for (const item of [
      ...(paperPortfolio?.positions || []),
      ...(paperPortfolio?.closed_positions || [])
    ]) {
      const id = String(item?.id || item?.instrument_id || '');
      if (!id) continue;
      map.set(id, {
        symbol: item.broker_symbol || item.market_symbol || id.toUpperCase(),
        label: item.label || '',
        currency: item.currency || ''
      });
    }
    return map;
  }

  function metaFor(instrument, meta) {
    return meta.get(String(instrument || '')) || {
      symbol: String(instrument || '').toUpperCase(),
      label: '',
      currency: ''
    };
  }

  function orderTransactions(order, paperPortfolio) {
    return (paperPortfolio?.transactions || []).filter(transaction =>
      String(transaction?.order_id || '') === String(order?.order_id || '')
    );
  }

  function formatDate(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.valueOf())) return String(value);
    return date.toLocaleString(locale, {
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit'
    });
  }

  function formatPrice(value, currency) {
    const amount = number(value);
    if (amount === null) return '';
    return `${amount.toLocaleString(locale, { maximumFractionDigits: 4 })}${currency ? ` ${currency}` : ''}`;
  }

  function formatUnits(value) {
    const amount = number(value);
    if (amount === null) return '';
    return amount.toLocaleString(locale, { maximumFractionDigits: 8 });
  }

  function actionCopy(action) {
    const key = String(action || '').toUpperCase();
    if (key === 'REDUCE') return { badge: COPY.reduce, text: COPY.reduceText, className: 'REDUCE' };
    if (key === 'ADD') return { badge: COPY.add, text: COPY.addText, className: 'ADD' };
    if (key === 'REPLACE') return { badge: COPY.replace, text: COPY.replaceText, className: 'REPLACE' };
    return { badge: COPY.exit, text: COPY.exitText, className: 'EXIT' };
  }

  function transactionDetail(transaction, instrumentMeta) {
    if (!transaction) return '';
    const parts = [];
    const side = String(transaction.side || '').toUpperCase();
    const units = formatUnits(transaction.quantity);
    if (units) parts.push(`${side === 'BUY' ? COPY.bought : COPY.sold} ${units} ${COPY.units}`);
    const price = formatPrice(transaction.price, instrumentMeta.currency);
    if (price) parts.push(`${COPY.price}: ${price}`);
    const cost = number(transaction.transaction_cost_pln);
    if (cost !== null) parts.push(`${COPY.costs}: ${cost.toLocaleString(locale, { maximumFractionDigits: 2 })} PLN`);
    return parts.join(' · ');
  }

  function decisionForOrder(order, pending) {
    return (pending?.decisions || []).find(item => String(item?.decision_id || '') === String(order?.decision_id || ''))
      || null;
  }

  function reportIdsForOrder(order, pending) {
    const decision = decisionForOrder(order, pending);
    const directIds = decision?.material_event_context?.latest_report_ids || [];
    if (directIds.length) return directIds.map(String);

    const instrument = String(order?.action || '').toUpperCase() === 'ADD'
      ? order?.buy_instrument
      : order?.sell_instrument;
    const recommendation = (pending?.recommendations || []).find(item => String(item?.instrument || '') === String(instrument || ''));
    return (recommendation?.material_event_context?.latest_report_ids || []).map(String);
  }

  function materialReason(order, pending, reports, decisionContext) {
    const ids = reportIdsForOrder(order, pending);
    if (!ids.length) return '';

    const contextByReport = new Map((decisionContext?.contexts || []).map(item => [String(item?.report_id || ''), item]));
    for (const id of ids) {
      const context = contextByReport.get(id);
      const enriched = isPolish ? context?.reason_pl : context?.reason_en;
      if (enriched) return enriched;
    }

    const reportById = new Map((reports?.reports || []).map(item => [String(item?.id || ''), item]));
    for (const id of ids) {
      const report = reportById.get(id);
      if (!report) continue;
      const summary = isPolish ? report.summary_pl : report.summary_en;
      if (summary) return summary;
      const title = isPolish ? report.title_pl : report.title_en;
      if (title) return title;
    }
    return '';
  }

  function executionText(order, paperPortfolio, meta, pending, reports, decisionContext) {
    const action = String(order.action || '').toUpperCase();
    const transactions = orderTransactions(order, paperPortfolio);
    const parts = [];

    if (action === 'REPLACE') {
      const sellMeta = metaFor(order.sell_instrument, meta);
      const buyMeta = metaFor(order.buy_instrument, meta);
      parts.push(`${COPY.replaceText}: ${sellMeta.symbol} → ${buyMeta.symbol}.`);
      const sellTx = transactions.find(tx => String(tx.side).toUpperCase() === 'SELL');
      const buyTx = transactions.find(tx => String(tx.side).toUpperCase() === 'BUY');
      const sellDetail = transactionDetail(sellTx, sellMeta);
      const buyDetail = transactionDetail(buyTx, buyMeta);
      if (sellDetail) parts.push(`${sellMeta.symbol}: ${sellDetail}.`);
      if (buyDetail) parts.push(`${buyMeta.symbol}: ${buyDetail}.`);
    } else {
      const instrument = action === 'ADD' ? order.buy_instrument : order.sell_instrument;
      const instrumentMeta = metaFor(instrument, meta);
      parts.push(`${actionCopy(action).text}: ${instrumentMeta.symbol}.`);
      const transaction = transactions[0];
      const detail = transactionDetail(transaction, instrumentMeta);
      if (detail) parts.push(`${detail}.`);
      else {
        const price = formatPrice(order.execution?.price, instrumentMeta.currency);
        if (price) parts.push(`${COPY.price}: ${price}.`);
      }
    }

    if (order.executed_at) parts.push(`${COPY.executed}: ${formatDate(order.executed_at)}.`);

    const material = materialReason(order, pending, reports, decisionContext);
    if (material) {
      parts.push(`${COPY.reason}: ${material}`);
    } else {
      const rationale = isPolish ? order.rationale_pl : order.rationale_en;
      if (rationale) parts.push(rationale);
    }
    return parts.join(' ');
  }

  function ensureDecisionStyles() {
    if (document.getElementById('brace-executed-decisions-style')) return;
    const style = document.createElement('style');
    style.id = 'brace-executed-decisions-style';
    style.textContent = `
      #brace-decisions .signal.REDUCE{background:#fff4db;color:#9a6700}
      #brace-decisions .signal.ADD{background:#e7f7ee;color:#138a47}
      #brace-decisions .signal.REPLACE{background:#f1eafe;color:#6941c6}
      #brace-decisions .signal.EXIT{background:#feecec;color:#b42318}
      #brace-decisions .completed-brace-decision small{line-height:1.45}
      #brace-decisions .decision-strength{display:flex;flex-direction:column;align-items:flex-end;gap:2px;white-space:nowrap}
      #brace-decisions .decision-strength small{font-size:9px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:#7c8aa0}
      #brace-decisions .decision-strength span{font-size:13px;font-weight:800;color:#172b4d}
    `;
    document.head.appendChild(style);
  }

  function completedOrders(orders) {
    return (orders?.orders || [])
      .filter(order => order?.status === 'PAPER_EXECUTED' && ['ADD', 'REDUCE', 'EXIT', 'REPLACE'].includes(String(order?.action || '').toUpperCase()))
      .sort((a, b) => String(b.executed_at || '').localeCompare(String(a.executed_at || '')))
      .slice(0, 3);
  }

  function decisionRow(order, paperPortfolio, meta, pending, reports, decisionContext) {
    const action = String(order.action || '').toUpperCase();
    const copy = actionCopy(action);
    const instrument = action === 'ADD' ? order.buy_instrument : order.sell_instrument;
    const symbol = action === 'REPLACE'
      ? `${metaFor(order.sell_instrument, meta).symbol} → ${metaFor(order.buy_instrument, meta).symbol}`
      : metaFor(instrument, meta).symbol;
    return `<div class="decision-row completed-brace-decision" data-executed-order="${esc(order.order_id || '')}">
      <span class="signal ${esc(copy.className)} execution-complete">${esc(copy.badge)}</span>
      <span><strong>${esc(symbol)}</strong><br><small>${esc(executionText(order, paperPortfolio, meta, pending, reports, decisionContext))}</small></span>
      <b class="decision-strength"><small>${esc(COPY.strength)}</small><span>${esc(action)}</span></b>
    </div>`;
  }

  function renderExecutedDecisions(orders, paperPortfolio, universe, pending, reports, decisionContext) {
    const root = document.getElementById('brace-decisions');
    if (!root) return false;
    ensureDecisionStyles();

    const article = root.closest('article');
    const heading = article?.querySelector('.card-head h2');
    if (heading) heading.textContent = COPY.cardTitle;
    const button = article?.querySelector('.card-head .text-button');
    if (button) {
      button.textContent = COPY.viewHistory;
      button.dataset.tab = 'history';
    }

    const meta = metadataMap(paperPortfolio, universe);
    const completed = completedOrders(orders);
    root.dataset.braceDecisionMode = 'executed-only';
    root.innerHTML = completed.length
      ? completed.map(order => decisionRow(order, paperPortfolio, meta, pending, reports, decisionContext)).join('')
      : `<p class="brace-empty">${esc(COPY.noExecuted)}</p>`;
    document.body.dataset.braceExecutions = 'reconciled';
    return true;
  }

  function removeCurrentPositionCards(order, meta) {
    const action = String(order.action || '').toUpperCase();
    if (!['EXIT', 'REPLACE'].includes(action) || !order.sell_instrument) return false;

    const instrumentMeta = metaFor(order.sell_instrument, meta);
    const symbol = instrumentMeta.symbol;
    const instrument = String(order.sell_instrument || '').toLowerCase();
    let changed = false;

    const braceRoot = document.getElementById('brace-positions');
    for (const card of braceRoot?.querySelectorAll('.brace-position') || []) {
      if (card.querySelector('.symbol')?.textContent?.trim() === symbol) {
        card.remove();
        changed = true;
      }
    }

    const controlRoot = document.getElementById('brace-control-root');
    for (const card of controlRoot?.querySelectorAll('.control-recommendations .recommendation') || []) {
      if (card.querySelector('b')?.textContent?.trim() === symbol) {
        card.remove();
        changed = true;
      }
    }

    const pendingSection = controlRoot?.querySelector('.control-columns > section:nth-child(3)');
    for (const row of pendingSection?.querySelectorAll('.control-list article') || []) {
      const content = (row.textContent || '').toLowerCase();
      if (content.includes(instrument) || content.includes(symbol.toLowerCase())) {
        row.remove();
        changed = true;
      }
    }
    const pendingList = pendingSection?.querySelector('.control-list');
    if (pendingList && !pendingList.children.length) {
      pendingList.outerHTML = `<p class="brace-empty">${esc(COPY.noPending)}</p>`;
      changed = true;
    }
    return changed;
  }

  async function startExecutionReconciliation() {
    try {
      const [orders, paperPortfolio, universe, pending, reports, decisionContext] = await Promise.all([
        json('/data/portfolio10k/paper_orders.json'),
        json('/data/portfolio10k/paper_portfolio.json'),
        json('/data/portfolio10k/universe.json').catch(() => ({ instruments: [] })),
        json('/data/portfolio10k/pending_decisions.json').catch(() => ({ decisions: [], recommendations: [] })),
        json('/data/investments/portfolio_10k_material_reports.json').catch(() => ({ reports: [] })),
        json('/data/portfolio10k/decision_context.json').catch(() => ({ contexts: [] }))
      ]);
      const meta = metadataMap(paperPortfolio, universe);
      const render = () => {
        renderExecutedDecisions(orders, paperPortfolio, universe, pending, reports, decisionContext);
        for (const order of completedOrders(orders)) removeCurrentPositionCards(order, meta);
      };

      render();
      let attempts = 0;
      const retry = () => {
        render();
        attempts += 1;
        if (attempts < 48) window.setTimeout(retry, attempts < 12 ? 300 : 700);
      };
      window.setTimeout(retry, 300);

      const root = document.getElementById('brace-decisions');
      if (root && !root.dataset.executedObserver) {
        root.dataset.executedObserver = 'true';
        const observer = new MutationObserver(() => {
          const foreignRow = [...root.children].some(child =>
            child.classList?.contains('decision-row') && !child.dataset.executedOrder
          );
          if (foreignRow) window.setTimeout(render, 0);
        });
        observer.observe(root, { childList: true });
      }

      window.addEventListener('hashchange', () => window.setTimeout(render, 0));
      document.addEventListener('click', event => {
        if (event.target.closest('[data-tab]')) window.setTimeout(render, 0);
      });
    } catch (_) {
      // The base investment room remains usable if BRACE execution data is unavailable.
    }
  }

  function replacePolishPlaceholders() {
    if (!isPolish) return;
    const braceImpact = document.getElementById('brace-impact');
    if (braceImpact && /ładowanie|sprawdzanie/i.test(braceImpact.textContent || '')) {
      braceImpact.textContent = 'Ocena BRACE jest aktualizowana niezależnie od bieżących danych portfela.';
    }
    document.body.dataset.investmentPlaceholders = 'finalized';
  }

  const start = () => {
    window.setTimeout(replacePolishPlaceholders, 500);
    startExecutionReconciliation();
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
