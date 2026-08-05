(() => {
  'use strict';

  const cfg = window.BR_PORTFOLIO_10K || { lang: 'pl' };
  const lang = cfg.lang === 'en' ? 'en' : 'pl';
  const endpoint = '/data/ai_tournament/public.json';
  const state = { data: null, observer: null };
  const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[character]));

  const T = lang === 'en' ? {
    title: 'CURRENT SUMMARY',
    subtitle: 'Key figures without repeating the ranking.',
    leader: 'Leader',
    lead: 'Lead over second',
    average: 'Average return',
    spread: 'Best-to-worst spread',
    over: 'over',
    agents: 'agents',
    pp: 'pp',
    noData: 'The tournament is waiting for its first completed market round.'
  } : {
    title: 'PODSUMOWANIE',
    subtitle: 'Najważniejsze liczby bez powtarzania rankingu.',
    leader: 'Lider',
    lead: 'Przewaga nad drugim',
    average: 'Średni wynik',
    spread: 'Rozpiętość wyników',
    over: 'nad',
    agents: 'agentów',
    pp: 'p.p.',
    noData: 'Turniej czeka na pierwszą zakończoną rundę rynkową.'
  };

  const pct = value => Number.isFinite(Number(value))
    ? `${Number(value) >= 0 ? '+' : ''}${(Number(value) * 100).toFixed(2)}%`
    : '—';

  const pp = value => Number.isFinite(Number(value))
    ? `${Number(value) >= 0 ? '+' : ''}${(Number(value) * 100).toFixed(2)} ${T.pp}`
    : '—';

  const tone = value => Number(value) >= 0 ? 'positive' : 'negative';

  function selectedMetrics(row) {
    return lang === 'en' ? (row.metrics_usd || row.metrics || {}) : (row.metrics_pln || row.metrics || {});
  }

  function returnValue(row) {
    return Number(selectedMetrics(row).return_pct || 0);
  }

  function summary(rows) {
    const ranked = [...rows].sort((a, b) => {
      const rankA = Number.isFinite(Number(a.rank)) ? Number(a.rank) : Number.MAX_SAFE_INTEGER;
      const rankB = Number.isFinite(Number(b.rank)) ? Number(b.rank) : Number.MAX_SAFE_INTEGER;
      return rankA - rankB || returnValue(b) - returnValue(a);
    });
    if (!ranked.length) return null;

    const values = ranked.map(returnValue);
    const leader = ranked[0];
    const second = ranked[1] || null;
    const worst = ranked.reduce((current, row) => returnValue(row) < returnValue(current) ? row : current, ranked[0]);
    const average = values.reduce((sum, value) => sum + value, 0) / values.length;
    const bestReturn = Math.max(...values);
    const worstReturn = Math.min(...values);

    return {
      leader,
      leaderReturn: returnValue(leader),
      second,
      worst,
      lead: second ? returnValue(leader) - returnValue(second) : 0,
      average,
      spread: bestReturn - worstReturn
    };
  }

  function installStyles() {
    if (document.getElementById('ait-summary-style')) return;
    const style = document.createElement('style');
    style.id = 'ait-summary-style';
    style.textContent = `
      .ait-summary-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}
      .ait-summary-stat{min-width:0;padding:12px;border:1px solid #e2e9f1;border-radius:12px;background:linear-gradient(145deg,#fbfdff,#f5f8fb)}
      .ait-summary-stat small{display:block;color:#7f8da0;font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.045em}
      .ait-summary-stat strong{display:block;margin-top:6px;color:#172b45;font-size:15px;line-height:1.2;overflow-wrap:anywhere}
      .ait-summary-stat strong.positive{color:#12935a}.ait-summary-stat strong.negative{color:#cf4747}
      .ait-summary-stat span{display:block;margin-top:4px;color:#62738a;font-size:10px;line-height:1.35}
      @media(max-width:680px){.ait-summary-grid{grid-template-columns:1fr 1fr}.ait-summary-stat{padding:10px}.ait-summary-stat strong{font-size:14px}}
      @media(max-width:430px){.ait-summary-grid{grid-template-columns:1fr}}
    `;
    document.head.appendChild(style);
  }

  function findSummaryCard() {
    const side = document.querySelector('#agent-cards .aitx-detail-side');
    if (!side) return null;
    return [...side.querySelectorAll(':scope > .aitx-card')].find(card => {
      const heading = card.querySelector('.aitx-card-head h3')?.textContent?.trim();
      return heading === 'PODSUMOWANIE' || heading === 'CURRENT SUMMARY';
    }) || null;
  }

  function render() {
    if (!state.data) return;
    const card = findSummaryCard();
    if (!card) return;

    const rows = state.data.leaderboard || state.data.participants || [];
    const stats = summary(rows);
    const signature = JSON.stringify(rows.map(row => [row.agent_id, row.rank, returnValue(row)]));
    if (card.dataset.summarySignature === signature && card.querySelector('.ait-summary-grid')) return;

    card.dataset.summarySignature = signature;
    if (!stats) {
      card.innerHTML = `<div class="aitx-card-head"><div><h3>${esc(T.title)}</h3><p>${esc(T.subtitle)}</p></div></div><div class="aitx-empty">${esc(T.noData)}</div>`;
      return;
    }

    const secondNote = stats.second ? `${T.over} ${stats.second.agent_id}` : '—';
    card.innerHTML = `
      <div class="aitx-card-head"><div><h3>${esc(T.title)}</h3><p>${esc(T.subtitle)}</p></div></div>
      <div class="ait-summary-grid">
        <div class="ait-summary-stat"><small>${esc(T.leader)}</small><strong>${esc(stats.leader.agent_id)}</strong><span>${pct(stats.leaderReturn)}</span></div>
        <div class="ait-summary-stat"><small>${esc(T.lead)}</small><strong class="positive">${pp(stats.lead)}</strong><span>${esc(secondNote)}</span></div>
        <div class="ait-summary-stat"><small>${esc(T.average)}</small><strong class="${tone(stats.average)}">${pct(stats.average)}</strong><span>${rows.length} ${esc(T.agents)}</span></div>
        <div class="ait-summary-stat"><small>${esc(T.spread)}</small><strong>${pp(stats.spread)}</strong><span>${esc(stats.leader.agent_id)} ↔ ${esc(stats.worst.agent_id)}</span></div>
      </div>`;
  }

  function observe() {
    const root = document.getElementById('agent-cards');
    if (!root) return;
    if (state.observer) state.observer.disconnect();
    state.observer = new MutationObserver(() => window.requestAnimationFrame(render));
    state.observer.observe(root, { childList: true, subtree: true });
  }

  async function load() {
    installStyles();
    observe();
    try {
      const response = await fetch(`${endpoint}?summary=${Date.now()}`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (data.schema_version !== 'ai-tournament-v1') throw new Error('unsupported schema');
      state.data = data;
      render();
      window.setTimeout(render, 300);
      window.setTimeout(render, 1000);
    } catch (_) {
      // The base tournament renderer remains usable when this optional summary layer fails.
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', load, { once: true });
  else load();
})();
