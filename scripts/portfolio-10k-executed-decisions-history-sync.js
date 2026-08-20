(() => {
  'use strict';

  window.__BR_BRACE_EXECUTION_HISTORY_AUTHORITY__ = true;

  const lang = (window.BR_PORTFOLIO_10K?.lang || document.documentElement.lang || 'pl')
    .toLowerCase().startsWith('en') ? 'en' : 'pl';
  const isPl = lang === 'pl';
  const locale = isPl ? 'pl-PL' : 'en-US';

  const T = isPl ? {
    title: 'OSTATNIE ZREALIZOWANE DECYZJE (BRACE)',
    history: 'Pełna historia',
    empty: 'Brak zrealizowanych decyzji BRACE.',
    strength: 'Siła decyzji',
    executed: 'Realizacja',
    price: 'cena',
    units: 'jednostki',
    costs: 'koszty',
    reason: 'Powód',
    bought: 'kupiono',
    sold: 'sprzedano',
    replace: 'ROTACJA ZREALIZOWANA',
    replaceText: 'Zrealizowano rotację',
    add: 'ZAKUP ZREALIZOWANY',
    addText: 'Dodano pozycję',
    reduce: 'REDUKCJA ZREALIZOWANA',
    reduceText: 'Zmniejszono pozycję',
    exit: 'SPRZEDAŻ ZREALIZOWANA',
    exitText: 'Zamknięto pozycję',
    active: 'aktywna'
  } : {
    title: 'LATEST EXECUTED DECISIONS (BRACE)',
    history: 'Full history',
    empty: 'No BRACE decisions have been executed yet.',
    strength: 'Decision strength',
    executed: 'Executed',
    price: 'price',
    units: 'units',
    costs: 'costs',
    reason: 'Reason',
    bought: 'bought',
    sold: 'sold',
    replace: 'ROTATION EXECUTED',
    replaceText: 'Executed rotation',
    add: 'PURCHASE EXECUTED',
    addText: 'Added position',
    reduce: 'REDUCTION EXECUTED',
    reduceText: 'Reduced position',
    exit: 'SALE EXECUTED',
    exitText: 'Closed position',
    active: 'active'
  };

  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
  const num = value => Number.isFinite(Number(value)) ? Number(value) : null;

  async function getJson(path) {
    const response = await fetch(`${path}?v=${Date.now()}`, { cache: 'no-store' });
    if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
    return response.json();
  }

  function metadataMap(portfolio, universe) {
    const map = new Map();
    for (const item of universe?.instruments || []) {
      if (!item?.instrument_id) continue;
      map.set(String(item.instrument_id), {
        symbol: item.broker_symbol || item.data_symbol || String(item.instrument_id).toUpperCase(),
        label: item.label || '',
        currency: item.currency || ''
      });
    }
    for (const item of [...(portfolio?.positions || []), ...(portfolio?.closed_positions || [])]) {
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

  function metaFor(id, meta) {
    return meta.get(String(id || '')) || {
      symbol: String(id || '').toUpperCase(), label: '', currency: ''
    };
  }

  function actionFor(group, portfolio) {
    const hasBuy = group.transactions.some(tx => String(tx.side || '').toUpperCase() === 'BUY');
    const hasSell = group.transactions.some(tx => String(tx.side || '').toUpperCase() === 'SELL');
    if (hasBuy && hasSell) return 'REPLACE';
    if (hasBuy) return 'ADD';
    if (!hasSell) return 'TRADE';
    const sell = group.transactions.find(tx => String(tx.side || '').toUpperCase() === 'SELL');
    const id = String(sell?.instrument_id || '');
    const remainsActive = (portfolio?.positions || []).some(position =>
      String(position?.id || position?.instrument_id || '') === id
    );
    return remainsActive ? 'REDUCE' : 'EXIT';
  }

  function completedExecutions(portfolio) {
    const groups = new Map();
    for (const tx of portfolio?.transactions || []) {
      if (!tx?.executed_at || !['BUY', 'SELL'].includes(String(tx.side || '').toUpperCase())) continue;
      const key = String(tx.order_id || tx.transaction_id || `${tx.instrument_id}:${tx.executed_at}`);
      if (!groups.has(key)) groups.set(key, { order_id: key, transactions: [] });
      groups.get(key).transactions.push(tx);
    }

    return [...groups.values()].map(group => {
      const sell = group.transactions.find(tx => String(tx.side || '').toUpperCase() === 'SELL');
      const buy = group.transactions.find(tx => String(tx.side || '').toUpperCase() === 'BUY');
      const latest = [...group.transactions].sort((a, b) =>
        String(b.executed_at || '').localeCompare(String(a.executed_at || ''))
      )[0] || {};
      const action = actionFor(group, portfolio);
      const rationaleTx = group.transactions.find(tx => isPl ? tx.rationale_pl : tx.rationale_en)
        || group.transactions[0] || {};
      return {
        order_id: group.order_id,
        decision_id: latest.decision_id || null,
        action,
        sell_instrument: sell?.instrument_id || null,
        buy_instrument: buy?.instrument_id || null,
        executed_at: latest.executed_at || '',
        rationale_pl: rationaleTx.rationale_pl || '',
        rationale_en: rationaleTx.rationale_en || '',
        transactions: group.transactions
      };
    }).filter(item => ['ADD', 'REDUCE', 'EXIT', 'REPLACE'].includes(item.action))
      .sort((a, b) => String(b.executed_at).localeCompare(String(a.executed_at)))
      .slice(0, 3);
  }

  function actionCopy(action) {
    if (action === 'REPLACE') return { badge: T.replace, text: T.replaceText, cls: 'REPLACE' };
    if (action === 'ADD') return { badge: T.add, text: T.addText, cls: 'ADD' };
    if (action === 'REDUCE') return { badge: T.reduce, text: T.reduceText, cls: 'REDUCE' };
    return { badge: T.exit, text: T.exitText, cls: 'EXIT' };
  }

  function dateText(value) {
    const date = new Date(value);
    if (Number.isNaN(date.valueOf())) return String(value || '');
    return date.toLocaleString(locale, {
      year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
    });
  }

  function priceText(value, currency) {
    const amount = num(value);
    if (amount === null) return '';
    return `${amount.toLocaleString(locale, { maximumFractionDigits: 4 })}${currency ? ` ${currency}` : ''}`;
  }

  function unitsText(value) {
    const amount = num(value);
    return amount === null ? '' : amount.toLocaleString(locale, { maximumFractionDigits: 8 });
  }

  function transactionText(tx, instrument) {
    if (!tx) return '';
    const side = String(tx.side || '').toUpperCase();
    const parts = [];
    const units = unitsText(tx.quantity);
    if (units) parts.push(`${side === 'BUY' ? T.bought : T.sold} ${units} ${T.units}`);
    const price = priceText(tx.price, instrument.currency);
    if (price) parts.push(`${T.price}: ${price}`);
    const cost = num(tx.transaction_cost_pln);
    if (cost !== null) parts.push(`${T.costs}: ${cost.toLocaleString(locale, { maximumFractionDigits: 2 })} PLN`);
    return parts.join(' · ');
  }

  function row(item, meta) {
    const action = item.action;
    const copy = actionCopy(action);
    const sellMeta = metaFor(item.sell_instrument, meta);
    const buyMeta = metaFor(item.buy_instrument, meta);
    const instrumentMeta = action === 'ADD' ? buyMeta : sellMeta;
    const symbol = action === 'REPLACE' ? `${sellMeta.symbol} → ${buyMeta.symbol}` : instrumentMeta.symbol;
    const parts = [];

    if (action === 'REPLACE') {
      parts.push(`${copy.text}: ${sellMeta.symbol} → ${buyMeta.symbol}.`);
      const sellTx = item.transactions.find(tx => String(tx.side || '').toUpperCase() === 'SELL');
      const buyTx = item.transactions.find(tx => String(tx.side || '').toUpperCase() === 'BUY');
      const sold = transactionText(sellTx, sellMeta);
      const bought = transactionText(buyTx, buyMeta);
      if (sold) parts.push(`${sellMeta.symbol}: ${sold}.`);
      if (bought) parts.push(`${buyMeta.symbol}: ${bought}.`);
    } else {
      parts.push(`${copy.text}: ${instrumentMeta.symbol}.`);
      const details = transactionText(item.transactions[0], instrumentMeta);
      if (details) parts.push(`${details}.`);
    }
    if (item.executed_at) parts.push(`${T.executed}: ${dateText(item.executed_at)}.`);
    const rationale = isPl ? item.rationale_pl : item.rationale_en;
    if (rationale) parts.push(`${T.reason}: ${rationale}`);

    return `<div class="decision-row completed-brace-decision" data-executed-order="${esc(item.order_id)}" data-history-order="${esc(item.order_id)}">
      <span class="signal ${esc(copy.cls)} execution-complete">${esc(copy.badge)}</span>
      <span><strong>${esc(symbol)}</strong><br><small>${esc(parts.join(' '))}</small></span>
      <b class="decision-strength"><small>${esc(T.strength)}</small><span>${esc(action)}</span></b>
    </div>`;
  }

  function ensureStyles() {
    if (document.getElementById('brace-history-authority-style')) return;
    const style = document.createElement('style');
    style.id = 'brace-history-authority-style';
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

  function apply(root, executions, meta) {
    ensureStyles();
    const article = root.closest('article');
    const heading = article?.querySelector('.card-head h2');
    const button = article?.querySelector('.card-head .text-button');
    if (heading) heading.textContent = T.title;
    if (button) {
      button.textContent = T.history;
      button.dataset.tab = 'history';
    }
    root.dataset.braceDecisionMode = 'transaction-history';
    root.dataset.braceDecisionSource = 'paper_portfolio.transactions';
    root.innerHTML = executions.length
      ? executions.map(item => row(item, meta)).join('')
      : `<p class="brace-empty">${esc(T.empty)}</p>`;
    document.body.dataset.braceExecutions = 'transaction-history';
  }

  function positionWasEntered(position) {
    const status = String(position?.status || '').toLowerCase();
    if (['active', 'paper_active'].includes(status)) return true;
    return num(position?.quantity) > 0
      && num(position?.entry_price) !== null
      && Boolean(position?.entry_date || position?.entry_timestamp_utc);
  }

  function syncAuditStatuses(portfolio) {
    const audit = document.getElementById('audit-body');
    if (!audit) return false;
    const bySymbol = new Map();
    for (const position of portfolio?.positions || []) {
      for (const symbol of [position?.broker_symbol, position?.market_symbol]) {
        const key = String(symbol || '').trim().toUpperCase();
        if (key) bySymbol.set(key, position);
      }
    }
    let changed = false;
    for (const row of audit.querySelectorAll('tr')) {
      const symbol = row.querySelector('td:first-child b')?.textContent?.trim().toUpperCase();
      const position = bySymbol.get(symbol || '');
      const statusCell = row.querySelector('td:last-child');
      if (!position || !statusCell || !positionWasEntered(position)) continue;
      if (statusCell.textContent.trim() !== T.active) {
        statusCell.textContent = T.active;
        statusCell.dataset.executionStatus = 'active';
        changed = true;
      }
    }
    return changed;
  }

  async function start() {
    const root = document.getElementById('brace-decisions');
    if (!root) return;
    try {
      const [portfolio, universe] = await Promise.all([
        getJson('/data/portfolio10k/paper_portfolio.json'),
        getJson('/data/portfolio10k/universe.json').catch(() => ({ instruments: [] }))
      ]);
      const meta = metadataMap(portfolio, universe);
      const executions = completedExecutions(portfolio);
      let rendering = false;
      const render = () => {
        if (rendering) return;
        rendering = true;
        apply(root, executions, meta);
        syncAuditStatuses(portfolio);
        rendering = false;
      };
      render();

      const observer = new MutationObserver(() => {
        if (rendering) return;
        const hasAuthorityRows = executions.length
          ? root.querySelectorAll('[data-history-order]').length === executions.length
          : root.dataset.braceDecisionSource === 'paper_portfolio.transactions';
        if (!hasAuthorityRows) queueMicrotask(render);
      });
      observer.observe(root, { childList: true, subtree: false });

      const audit = document.getElementById('audit-body');
      if (audit) {
        const auditObserver = new MutationObserver(() => queueMicrotask(() => syncAuditStatuses(portfolio)));
        auditObserver.observe(audit, { childList: true, subtree: true, characterData: true });
      }
      window.addEventListener('hashchange', () => setTimeout(render, 0));
      document.addEventListener('click', event => {
        if (event.target.closest('[data-tab]')) setTimeout(render, 0);
      });
    } catch (error) {
      console.warn('BRACE executed-decision history sync unavailable', error);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
