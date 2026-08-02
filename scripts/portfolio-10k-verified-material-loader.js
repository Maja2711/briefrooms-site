(() => {
  'use strict';
  const lang = window.BR_PORTFOLIO_10K?.lang === 'en' ? 'en' : 'pl';
  const portfolioPath = lang === 'en'
    ? '/data/investments/portfolio_10k_usd.json'
    : '/data/investments/portfolio_10k.json';
  let state = null;

  async function getJson(path) {
    const response = await fetch(`${path}?v=${Date.now()}`, {cache:'no-store'});
    if (!response.ok) throw new Error(path);
    return response.json();
  }

  function mergeReports(base, verified) {
    const byId = new Map();
    for (const report of [...(base?.reports || []), ...(verified?.reports || [])]) {
      if (report?.id) byId.set(String(report.id), report);
    }
    return [...byId.values()].sort((a,b) => String(b.published_at || b.event_date || '').localeCompare(String(a.published_at || a.event_date || '')));
  }

  function render() {
    if (!state || !window.BRMaterialReports) return;
    document.querySelectorAll('#positions .position').forEach(card => {
      const symbol = card.querySelector('.symbol')?.textContent?.trim();
      const position = state.positions.find(item => item.broker_symbol === symbol);
      const current = card.querySelector('.material-reports');
      if (!position || !current) return;
      const wrapper = document.createElement('div');
      wrapper.innerHTML = window.BRMaterialReports.renderForPosition({
        reports: state.reports,
        position,
        lang,
      });
      const replacement = wrapper.firstElementChild;
      if (replacement) current.replaceWith(replacement);
    });
  }

  async function load() {
    try {
      const [portfolio, base, verified] = await Promise.all([
        getJson(portfolioPath),
        getJson('/data/investments/portfolio_10k_material_reports.json').catch(() => ({reports:[]})),
        getJson('/data/investments/portfolio_10k_verified_material_events.json').catch(() => ({reports:[]})),
      ]);
      state = {positions: portfolio.positions || [], reports: mergeReports(base, verified)};
      render();
      const root = document.getElementById('positions');
      if (root && typeof MutationObserver !== 'undefined') {
        new MutationObserver(render).observe(root, {childList:true, subtree:true});
      }
    } catch (_) {
      // The original public renderer remains available when enrichment cannot load.
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', load, {once:true});
  else load();
})();
