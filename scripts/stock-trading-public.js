(() => {
  'use strict';

  const root = document.getElementById('stock-trading-portfolio-root');
  if (!root) return;

  const lang = (document.documentElement.lang || 'en').toLowerCase().startsWith('pl') ? 'pl' : 'en';
  const text = lang === 'pl' ? {
    loading: 'Ładowanie portfela Stock Trading…',
    title: 'Aktywny portfel Stock Trading',
    policy: 'Brak stałego terminu zamknięcia. Maksymalnie 3 spółki GPW i 3 spółki USA. Wolny slot nie wymusza transakcji. SL i TP są obowiązkowe i analizowane codziennie; model może je zmienić lub zamknąć pozycję natychmiast.',
    gpw: 'GPW', us: 'USA', open: 'otwarte', cash: 'CASH / wolny slot',
    entry: 'Wejście', mark: 'Bieżąca', sl: 'SL', tp: 'TP', score: 'Score wejścia', opened: 'Otwarto',
    empty: 'Brak otwartych pozycji — CASH jest prawidłowym stanem.',
    error: 'Nie udało się załadować aktualnego portfela Stock Trading.'
  } : {
    loading: 'Loading Stock Trading portfolio…',
    title: 'Active Stock Trading portfolio',
    policy: 'No fixed holding deadline. Maximum 3 GPW and 3 US stocks. An empty slot never forces a trade. SL and TP are mandatory and reviewed daily; the model may change them or close a position immediately.',
    gpw: 'GPW', us: 'US', open: 'open', cash: 'CASH / free slot',
    entry: 'Entry', mark: 'Mark', sl: 'SL', tp: 'TP', score: 'Entry score', opened: 'Opened',
    empty: 'No open positions — CASH is a valid state.',
    error: 'The current Stock Trading portfolio could not be loaded.'
  };

  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const num = value => Number.isFinite(Number(value)) ? Number(value).toLocaleString(lang === 'pl' ? 'pl-PL' : 'en-US', {maximumFractionDigits: 4}) : '—';
  const positions = (data, market) => (((data || {}).markets || {})[market] || {}).open_positions || [];

  function card(position) {
    return `<article class="engine-card">
      <span class="engine-chip">${esc(position.market)} · ${esc(position.ticker || position.symbol)}</span>
      <h3>${esc(position.name || position.ticker || position.symbol)}</h3>
      <p><strong>${text.entry}:</strong> ${num(position.entry)} · <strong>${text.mark}:</strong> ${num(position.last_mark)}</p>
      <p><strong>${text.sl}:</strong> ${num(position.stop)} · <strong>${text.tp}:</strong> ${num(position.target)}</p>
      <p><strong>${text.score}:</strong> ${num(position.entry_score)} · <strong>${text.opened}:</strong> ${esc(position.opened_at || '—')}</p>
    </article>`;
  }

  async function render() {
    root.innerHTML = `<p>${text.loading}</p>`;
    try {
      const response = await fetch(`/data/investments/stock_trading_portfolio.json?v=${Date.now()}`, {cache: 'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      const gpw = positions(data, 'GPW');
      const us = positions(data, 'US');
      const all = [...gpw, ...us];
      root.innerHTML = `
        <div class="section-head"><div><h3>${text.title}</h3><p>${text.policy}</p></div></div>
        <p><strong>${text.gpw}:</strong> ${gpw.length}/3 ${text.open} · ${3 - gpw.length}/3 ${text.cash} &nbsp; | &nbsp; <strong>${text.us}:</strong> ${us.length}/3 ${text.open} · ${3 - us.length}/3 ${text.cash}</p>
        ${all.length ? `<div class="daily-grid daily-grid-two">${all.map(card).join('')}</div>` : `<p>${text.empty}</p>`}`;
    } catch (error) {
      root.innerHTML = `<p>${text.error}</p>`;
    }
  }

  render();
})();
