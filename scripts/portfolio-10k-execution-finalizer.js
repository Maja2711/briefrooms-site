(() => {
  'use strict';

  const lang = window.BR_PORTFOLIO_10K?.lang === 'en' ? 'en' : 'pl';
  const soldLabel = lang === 'pl' ? 'SPRZEDANO' : 'SOLD';
  const symbols = {novo: 'NOVOB.DK'};

  async function loadExecutedExits() {
    const response = await fetch(`/data/portfolio10k/paper_orders.json?v=${Date.now()}`, {cache: 'no-store'});
    if (!response.ok) throw new Error(`paper-orders:${response.status}`);
    const payload = await response.json();
    return (payload.orders || []).filter(order =>
      order?.status === 'PAPER_EXECUTED' &&
      order?.action === 'EXIT' &&
      order?.sell_instrument
    );
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

  function updatePositionCount() {
    const count = document.getElementById('positions-count');
    const table = document.getElementById('portfolio-table');
    if (count && table) count.textContent = String(table.querySelectorAll('tr').length);
  }

  function apply(orders) {
    for (const order of orders) {
      const symbol = symbolFor(order);
      removeFromPortfolioTable(symbol);
      removeFromActiveCards(symbol);
      finalizeDecisionRows(symbol);
    }
    updatePositionCount();
  }

  async function start() {
    try {
      const orders = await loadExecutedExits();
      if (!orders.length) return;
      let attempts = 0;
      const render = () => {
        apply(orders);
        attempts += 1;
        if (attempts < 40) setTimeout(render, attempts < 10 ? 200 : 750);
      };
      render();
      window.addEventListener('hashchange', () => setTimeout(() => apply(orders), 0));
      document.addEventListener('click', event => {
        if (event.target.closest('[data-tab]')) setTimeout(() => apply(orders), 0);
      });
    } catch (_) {
      // The reconciled portfolio JSON remains the primary source of truth.
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
})();
