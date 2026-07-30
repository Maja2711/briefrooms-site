(() => {
  'use strict';
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const raw = typeof input === 'string' ? input : input?.url || '';
    const isPortfolio = raw.includes('/data/investments/portfolio_10k.json');
    if (!isPortfolio) return originalFetch(input, init);
    const redirected = raw.replace(
      '/data/investments/portfolio_10k.json',
      '/data/investments/portfolio_10k_usd.json'
    );
    const response = await originalFetch(redirected, init);
    if (!response.ok) return response;
    const payload = await response.json();
    payload.base_currency = 'USD';
    payload.reporting_currency = 'USD';
    payload.reporting_fx = {...(payload.reporting_fx || {}), usd_pln: 1};
    return new Response(JSON.stringify(payload), {
      status: response.status,
      statusText: response.statusText,
      headers: {'Content-Type': 'application/json; charset=utf-8'}
    });
  };
})();
