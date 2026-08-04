(() => {
  'use strict';

  const lang = window.BR_PORTFOLIO_10K?.lang === 'en' ? 'en' : 'pl';
  const locale = lang === 'pl' ? 'pl-PL' : 'en-US';
  const soldLabel = lang === 'pl' ? 'SPRZEDANO' : 'SOLD';
  const cashLabel = lang === 'pl' ? 'Gotówka' : 'Cash';
  const symbols = {novo: 'NOVOB.DK'};

  const money = value => new Intl.NumberFormat(locale, {
    style: 'currency',
    currency: 'PLN',
    maximumFractionDigits: 2,
  }).format(Number(value || 0));

  const pct = value => `${Number(value || 0) >= 0 ? '+' : ''}${(Number(value || 0) * 100).toFixed(2)}%`;

  async function json(path) {
    const response = await fetch(`${path}?v=${Date.now()}`, {cache: 'no-store'});
    if (!response.ok) throw new Error(`${path}:${response.status}`);
    return response.json();
  }

  async function loadState() {
    const [orders, baseline, paper] = await Promise.all([
      json('/data/portfolio10k/paper_orders.json'),
      json('/data/investments/portfolio_10k.json'),
      json('/data/portfolio10k/paper_portfolio.json'),
    ]);
    const executed = (orders.orders || []).filter(order =>
      order?.status === 'PAPER_EXECUTED' &&
      order?.action === 'EXIT' &&
      order?.sell_instrument
    );
    return {executed, baseline, paper};
  }

  function symbolFor(order) {
    const id = String(order.sell_instrument || '').toLowerCase();
    return symbols[id] || String(order.sell_instrument || '').toUpperCase();
  }

  function removeFromPortfolioTable(symbol) {
    const table = document.getElementById('portfolio-table');
    if (!table) return;
    for (const row of table.querySelectorAll('tr')) {
      const current = row.querySelector('td:first-child small')?.textContent?.trim();
      if (current === symbol) row.remove();
    }
  }

  function removeFromActiveCards(symbol) {
    const root = document.getElementById('positions');
    if (!root) return;
    for (const card of root.querySelectorAll('.position')) {
      if (card.querySelector('.symbol')?.textContent?.trim() === symbol) card.remove();
    }
  }

  function removeFromAllocation(label) {
    const root = document.getElementById('allocation-list');
    if (!root || !label) return;
    for (const row of root.querySelectorAll('.allocation-row')) {
      if (row.querySelector('span')?.textContent?.trim() === label) row.remove();
    }
  }

  function finalizeDecisionRows(symbol) {
    const root = document.getElementById('brace-decisions');
    if (!root) return;
    for (const row of root.querySelectorAll('.decision-row')) {
      if (row.querySelector('strong')?.textContent?.trim() !== symbol) continue;
      const badge = row.querySelector('.signal');
      if (badge) {
        badge.textContent = soldLabel;
        badge.className = 'signal SOLD';
      }
    }
  }

  function authoritativeSummary(state) {
    const exited = new Set(state.executed.map(order => String(order.sell_instrument).toLowerCase()));
    const active = (state.baseline.positions || []).filter(position =>
      position?.status === 'active' && !exited.has(String(position.id || '').toLowerCase())
    );
    const cash = Number(state.paper.cash_pln || 0);
    const invested = active.reduce((sum, position) => sum + Number(position.current_value_pln || 0), 0);
    const total = invested + cash;
    const starting = Number(state.baseline.starting_capital_pln || state.paper.starting_capital_pln || 10000);
    return {active, cash, invested, total, ret: starting ? total / starting - 1 : 0};
  }

  function updateSummary(state) {
    const summary = authoritativeSummary(state);
    const cash = document.getElementById('cash-value');
    const invested = document.getElementById('invested-value');
    const value = document.getElementById('portfolio-value');
    const result = document.getElementById('portfolio-return');
    const count = document.getElementById('positions-count');

    if (cash) cash.textContent = money(summary.cash);
    if (invested) invested.textContent = money(summary.invested);
    if (value) value.textContent = money(summary.total);
    if (result) {
      result.textContent = pct(summary.ret);
      result.className = summary.ret >= 0 ? 'positive' : 'negative';
    }
    if (count) count.textContent = String(summary.active.length);

    const allocation = document.getElementById('allocation-list');
    if (allocation && summary.cash > 0) {
      let row = allocation.querySelector('[data-execution-cash="true"]');
      if (!row) {
        row = document.createElement('div');
        row.className = 'allocation-row';
        row.dataset.executionCash = 'true';
        row.innerHTML = '<i style="background:#6d7a90"></i><span></span><b></b>';
        allocation.append(row);
      }
      row.querySelector('span').textContent = cashLabel;
      row.querySelector('b').textContent = `${summary.total ? (summary.cash / summary.total * 100).toFixed(1) : '0.0'}%`;
    }
  }

  function apply(state) {
    const positionsById = new Map((state.baseline.positions || []).map(position => [String(position.id || '').toLowerCase(), position]));
    for (const order of state.executed) {
      const symbol = symbolFor(order);
      const position = positionsById.get(String(order.sell_instrument || '').toLowerCase());
      removeFromPortfolioTable(symbol);
      removeFromActiveCards(symbol);
      removeFromAllocation(position?.label);
      finalizeDecisionRows(symbol);
    }
    updateSummary(state);
  }

  async function start() {
    try {
      const state = await loadState();
      if (!state.executed.length) return;
      let attempts = 0;
      const render = () => {
        apply(state);
        attempts += 1;
        if (attempts < 40) setTimeout(render, attempts < 10 ? 200 : 750);
      };
      render();
      window.addEventListener('hashchange', () => setTimeout(() => apply(state), 0));
      document.addEventListener('click', event => {
        if (event.target.closest('[data-tab]')) setTimeout(() => apply(state), 0);
      });
    } catch (_) {
      // Reconciled portfolio JSON remains the backend source of truth.
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
})();
