(() => {
  'use strict';

  const config = window.BR_PORTFOLIO_10K || {};
  const lang = config.lang === 'en' ? 'en' : 'pl';
  const locale = lang === 'en' ? 'en-US' : 'pl-PL';
  const currency = lang === 'en' ? 'USD' : 'PLN';
  const label = lang === 'en' ? 'Starting capital' : 'Kapitał startowy';

  function portfolioUrl() {
    return `/data/investments/portfolio_10k.json?v=${Date.now()}`;
  }

  function startingCapital(data) {
    const raw = lang === 'en' && data?.base_currency === 'USD'
      ? (data.starting_capital_usd ?? data.starting_capital_pln)
      : data?.starting_capital_pln;
    const value = Number(raw);
    return Number.isFinite(value) && value > 0 ? value : null;
  }

  function formatMoney(value) {
    return new Intl.NumberFormat(locale, {
      style: 'currency',
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(value);
  }

  function apply(value) {
    const valueNode = document.getElementById('invested-value');
    if (!valueNode) return false;
    const labelNode = valueNode.parentElement?.querySelector('small');
    if (labelNode && labelNode.textContent !== label) labelNode.textContent = label;
    const formatted = formatMoney(value);
    if (valueNode.textContent !== formatted) valueNode.textContent = formatted;
    valueNode.dataset.metric = 'starting-capital';
    valueNode.title = lang === 'en'
      ? 'Original portfolio capital, not current market value.'
      : 'Pierwotny kapitał portfela, a nie jego bieżąca wartość rynkowa.';
    return true;
  }

  async function init() {
    try {
      const response = await fetch(portfolioUrl(), { cache: 'no-store' });
      if (!response.ok) throw new Error('portfolio-data');
      const data = await response.json();
      const value = startingCapital(data);
      if (value === null || !apply(value)) return;

      const node = document.getElementById('invested-value');
      if (!node || typeof MutationObserver === 'undefined') return;
      const observer = new MutationObserver(() => apply(value));
      observer.observe(node, { childList: true, characterData: true, subtree: true });
    } catch (_) {
      // Leave the base dashboard usable when the data source is unavailable.
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
