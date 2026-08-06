(() => {
  'use strict';

  // Compatibility entry point kept intentionally small. The portfolio JSON and
  // the bilingual dashboard controller remain the only sources of live values.
  // This script finalises static Polish placeholders and reconciles completed
  // paper executions in BRACE-only presentation layers.

  const lang = (window.BR_PORTFOLIO_10K?.lang || document.documentElement.lang)
    .toLowerCase()
    .startsWith('en') ? 'en' : 'pl';
  const isPolish = lang === 'pl';
  const locale = isPolish ? 'pl-PL' : 'en-US';
  const COPY = isPolish ? {
    completed: 'SPRZEDAŻ ZREALIZOWANA',
    sold: 'Pozycja została sprzedana w portfelu modelowym',
    executed: 'Realizacja',
    price: 'cena',
    noPending: 'Brak decyzji oczekujących na wykonanie.'
  } : {
    completed: 'SALE COMPLETED',
    sold: 'The position was sold in the model portfolio',
    executed: 'Executed',
    price: 'price',
    noPending: 'No decisions are awaiting execution.'
  };

  const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[character]));

  async function json(path) {
    const response = await fetch(`${path}?v=${Date.now()}`, { cache: 'no-store' });
    if (!response.ok) throw new Error(path);
    return response.json();
  }

  function symbolFor(instrument) {
    return { novo: 'NOVOB.DK' }[instrument] || String(instrument || '').toUpperCase();
  }

  function executionText(order) {
    const symbol = symbolFor(order.sell_instrument);
    const parts = [`${COPY.sold}: ${symbol}.`];
    if (order.executed_at) {
      const date = new Date(order.executed_at);
      if (!Number.isNaN(date.valueOf())) parts.push(`${COPY.executed}: ${date.toLocaleString(locale)}.`);
    }
    const price = Number(order.execution?.price);
    if (Number.isFinite(price)) {
      parts.push(`${COPY.price}: ${price.toLocaleString(locale, { maximumFractionDigits: 4 })}.`);
    }
    return parts.join(' ');
  }

  function applyLatestDecision(order) {
    const root = document.getElementById('brace-decisions');
    if (!root) return false;
    const symbol = symbolFor(order.sell_instrument);
    const decisionId = String(order.decision_id || order.order_id || symbol);
    const stableId = `pending-${decisionId}`;

    for (const row of root.querySelectorAll('.decision-row')) {
      const rowId = row.dataset.currentDecision || '';
      const rowText = row.textContent || '';
      if (rowId.endsWith(decisionId) || rowText.includes(symbol)) row.remove();
    }

    const row = document.createElement('div');
    row.className = 'decision-row completed-exit-decision';
    // Keep the original pending id so the older overlay recognises the row and
    // does not reinsert the stale "planned sale" version while it is retrying.
    row.dataset.currentDecision = stableId;
    row.dataset.executionStatus = 'PAPER_EXECUTED';
    row.innerHTML = `<span class="signal EXIT execution-complete">${esc(COPY.completed)}</span><span><strong>${esc(symbol)}</strong><br><small>${esc(executionText(order))}</small></span><b>${Math.round(Number(order.confidence || 0) * 100)}%</b>`;
    root.prepend(row);
    return true;
  }

  function removeCurrentPositionCards(order) {
    const symbol = symbolFor(order.sell_instrument);
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

  function reconcileCompletedOrders(orders) {
    const completed = (orders?.orders || []).filter(order =>
      order?.status === 'PAPER_EXECUTED' && order?.action === 'EXIT' && order?.sell_instrument
    );
    for (const order of completed) {
      applyLatestDecision(order);
      removeCurrentPositionCards(order);
    }
    if (completed.length) document.body.dataset.braceExecutions = 'reconciled';
  }

  async function startExecutionReconciliation() {
    try {
      const orders = await json('/data/portfolio10k/paper_orders.json');
      let attempts = 0;
      const render = () => {
        reconcileCompletedOrders(orders);
        attempts += 1;
        if (attempts < 48) window.setTimeout(render, attempts < 12 ? 300 : 700);
      };
      render();
      window.addEventListener('hashchange', () => window.setTimeout(() => reconcileCompletedOrders(orders), 0));
      document.addEventListener('click', event => {
        if (event.target.closest('[data-tab]')) window.setTimeout(() => reconcileCompletedOrders(orders), 0);
      });
    } catch (_) {
      // The base investment room remains unchanged if paper-order data is unavailable.
    }
  }

  function replacePolishPlaceholders() {
    if (!isPolish) return;

    const projectionOverview = document.getElementById('projection-overview');
    if (projectionOverview && /ładowanie|sprawdzanie/i.test(projectionOverview.textContent || '')) {
      projectionOverview.innerHTML = `
        <div><b>Scenariusz bazowy</b><span>warunki, katalizatory i ryzyka</span></div>
        <div><b>Wariant wzrostowy / spadkowy</b><span>jawne założenia zamiast jednej ceny docelowej</span></div>
        <div><b>Ocena trafności</b><span>kalibracja i późniejszy pomiar wyników</span></div>`;
    }

    const projectionsPanel = document.querySelector('.i10k-panel[data-panel="projections"] .page-card');
    if (projectionsPanel && /ładowanie|sprawdzanie/i.test(projectionsPanel.textContent || '')) {
      projectionsPanel.innerHTML = `
        <div class="card-head">
          <div>
            <h2>PROJEKCJE</h2>
            <p>Ta sekcja nie prezentuje arbitralnej prognozy. Scenariusze będą publikowane wraz z założeniami, poziomem pewności i późniejszą oceną trafności.</p>
          </div>
        </div>
        <div class="projection-policy">
          <div><b>Scenariusz bazowy</b><span>warunki, katalizatory i ryzyka</span></div>
          <div><b>Wariant wzrostowy / spadkowy</b><span>jawne założenia, nie jedna cena docelowa</span></div>
          <div><b>Pewność</b><span>kalibracja na wynikach historycznych</span></div>
          <div><b>Trafność</b><span>Brier score i trafność przedziałów</span></div>
        </div>`;
    }

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
