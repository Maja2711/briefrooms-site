(() => {
  'use strict';

  const cfg = window.BR_PORTFOLIO_10K || { lang: 'pl' };
  const lang = cfg.lang === 'en' ? 'en' : 'pl';
  const locale = lang === 'en' ? 'en-US' : 'pl-PL';
  const currency = lang === 'en' ? 'USD' : 'PLN';
  const endpoint = '/data/ai_tournament/public.json';
  const $ = selector => document.querySelector(selector);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
  const money = value => Number(value || 0).toLocaleString(locale, {
    style: 'currency', currency, maximumFractionDigits: 2
  });
  const pct = value => Number.isFinite(Number(value))
    ? `${Number(value) >= 0 ? '+' : ''}${(Number(value) * 100).toFixed(2)}%`
    : '—';
  const metric = value => Number.isFinite(Number(value)) ? Number(value).toFixed(2) : '—';

  const T = lang === 'en' ? {
    scheduled: 'ready for first execution', active: 'active', finished: 'finished', error: 'agent error',
    lastSession: 'Last closed US session', locked: 'Portfolios locked · no rebalancing',
    place: 'Place', value: 'Portfolio value', ret: 'Return', alpha: 'Alpha', drawdown: 'Max drawdown', sharpe: 'Sharpe',
    positions: 'Positions', cash: 'Cash', decision: 'Locked thesis', noDecision: 'Awaiting first execution',
    daily: 'Daily standings after market close', date: 'Session', disclaimer: 'A public paper-portfolio experiment. Not investment advice.',
    unavailable: 'The tournament is ready and waiting for its first completed market round.',
    rankRule: 'Ranking: cumulative USD return; drawdown and Sharpe break ties.',
    account: 'USD account · no FX translation', execution: 'First execution',
    up: 'up', down: 'down', same: 'unchanged'
  } : {
    scheduled: 'gotowy do pierwszego wykonania', active: 'aktywny', finished: 'zakończony', error: 'błąd agenta',
    lastSession: 'Ostatnia zamknięta sesja USA', locked: 'Portfele zablokowane · bez rebalansowania',
    place: 'Miejsce', value: 'Wartość portfela', ret: 'Wynik', alpha: 'Alpha', drawdown: 'Maks. obsunięcie', sharpe: 'Sharpe',
    positions: 'Pozycje', cash: 'Gotówka', decision: 'Zablokowana teza', noDecision: 'Oczekiwanie na pierwsze wykonanie',
    daily: 'Codzienne miejsca po zamknięciu rynku', date: 'Sesja', disclaimer: 'Publiczny eksperyment portfeli modelowych. To nie jest porada inwestycyjna.',
    unavailable: 'Turniej jest gotowy i czeka na pierwszą zakończoną rundę rynkową.',
    rankRule: 'Ranking: skumulowany wynik USD; przy remisie drawdown i Sharpe.',
    account: 'Rachunek PLN · z codziennym wpływem USD/PLN', execution: 'Pierwsze wykonanie',
    up: 'awans', down: 'spadek', same: 'bez zmian'
  };

  function installStyles() {
    if ($('#ai-tournament-public-style')) return;
    const style = document.createElement('style');
    style.id = 'ai-tournament-public-style';
    style.textContent = `
      .ait-meta{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 16px}.ait-meta span{padding:7px 10px;border:1px solid #dbe3ed;border-radius:999px;background:#f7f9fc;color:#46566b;font-size:12px;font-weight:700}
      .ait-ranking{display:grid;gap:9px}.ait-row{display:grid;grid-template-columns:42px minmax(120px,1fr) 120px 120px 90px;align-items:center;gap:10px;padding:12px 14px;border:1px solid #dfe6ef;border-radius:14px;background:#fff}.ait-rank{display:grid;place-items:center;width:34px;height:34px;border-radius:11px;background:#edf2f8;font-weight:900}.ait-row:first-child .ait-rank{background:#fff1bd;color:#7b5700}.ait-agent strong{display:block}.ait-agent small{display:block;margin-top:3px;color:#718096}.ait-number{text-align:right}.ait-number strong{display:block}.ait-number small{color:#7b8797}.ait-change{font-size:11px;font-weight:800}.ait-change.up{color:#14864a}.ait-change.down{color:#c84646}.ait-change.same{color:#718096}
      .ait-summary{padding:14px;border:1px solid #dfe6ef;border-radius:14px;background:#f8fafc}.ait-summary strong{display:block;font-size:28px}.ait-summary span{color:#68778a;font-size:12px}.ait-card-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.ait-card{border:1px solid #dfe6ef;border-radius:16px;padding:16px;background:#fff}.ait-card-head{display:flex;justify-content:space-between;gap:12px}.ait-card-head h3{margin:2px 0 0;font-size:21px}.ait-card-head .ait-rank{flex:0 0 auto}.ait-status{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:#66788d}.ait-kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:14px 0}.ait-kpis div{padding:9px;border-radius:11px;background:#f4f7fb}.ait-kpis small{display:block;color:#778597;font-size:10px}.ait-kpis strong{display:block;margin-top:3px;font-size:14px}.ait-decision{padding-top:12px;border-top:1px solid #edf0f5;color:#445368;font-size:12px;line-height:1.5}.ait-holdings{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}.ait-holdings span{padding:5px 7px;border-radius:8px;background:#eef3f8;font-size:10px;font-weight:800}.ait-history-wrap{overflow:auto;margin-top:18px}.ait-history{width:100%;border-collapse:collapse;font-size:12px}.ait-history th,.ait-history td{padding:10px;border-bottom:1px solid #e4e9f0;text-align:left;white-space:nowrap}.ait-history th{color:#596a7f;background:#f6f8fb}.ait-history-ranks{display:flex;gap:8px;flex-wrap:wrap}.ait-history-ranks span{font-weight:800}.ait-note{margin-top:14px;padding:12px 14px;border-radius:12px;background:#f3f6fa;color:#59697d;font-size:12px;line-height:1.5}
      @media(max-width:900px){.ait-row{grid-template-columns:38px 1fr 100px}.ait-row .ait-number:nth-of-type(n+3){display:none}.ait-card-grid{grid-template-columns:1fr}}@media(max-width:560px){.ait-row{grid-template-columns:34px 1fr 85px;padding:10px}.ait-card{padding:13px}.ait-kpis{grid-template-columns:repeat(2,1fr)}}`;
    document.head.appendChild(style);
  }

  function selectedMetrics(row) {
    return lang === 'en'
      ? (row.metrics_usd || row.metrics || {})
      : (row.metrics_pln || row.metrics || {});
  }

  function selectedValue(metrics) {
    return lang === 'en' ? metrics.portfolio_value_usd : metrics.portfolio_value_pln;
  }

  function selectedCashWeight(row) {
    return lang === 'en'
      ? (row.cash_weight_usd ?? row.cash_weight)
      : (row.cash_weight_pln ?? row.cash_weight);
  }

  function startingCapital(data) {
    return lang === 'en'
      ? data.tournament?.starting_capital_usd
      : data.tournament?.starting_capital_pln;
  }

  function statusLabel(row) {
    if (row.status === 'AGENT_ERROR') return T.error;
    if (row.status === 'ACTIVE') return T.active;
    if (row.status === 'FINISHED') return T.finished;
    return T.scheduled;
  }

  function changeLabel(value) {
    if (!Number.isFinite(Number(value)) || Number(value) === 0) return `<span class="ait-change same">${T.same}</span>`;
    if (Number(value) > 0) return `<span class="ait-change up">↑ ${Number(value)} ${T.up}</span>`;
    return `<span class="ait-change down">↓ ${Math.abs(Number(value))} ${T.down}</span>`;
  }

  function meta(data) {
    const execution = String(data.tournament?.execution_time || '').replace('_us_regular_session_open', '');
    return `<div class="ait-meta"><span>${esc(T.account)}</span><span>${esc(T.locked)}</span><span>${esc(T.execution)}: ${esc(execution || '—')}</span><span>${esc(T.rankRule)}</span></div>`;
  }

  function preview(data) {
    const target = $('#agents-preview');
    if (!target) return;
    const rows = data.leaderboard || [];
    if (!rows.length) {
      const participants = data.participants || [];
      target.innerHTML = participants.length ? `${meta(data)}<div class="ait-ranking">${participants.map(participant => `
        <div class="ait-row"><span class="ait-rank">—</span><span class="ait-agent"><strong>${esc(participant.agent_id)}</strong><small>${esc(T.scheduled)} · ${esc(participant.model || '')}</small></span><span class="ait-number"><strong>—</strong><small>${esc(T.ret)}</small></span><span class="ait-number"><strong>${money(startingCapital(data))}</strong><small>${esc(T.value)}</small></span><span class="ait-change same">${esc(T.same)}</span></div>`).join('')}</div>` : `<div class="ait-summary"><strong>0</strong><span>${esc(T.unavailable)}</span></div>`;
      return;
    }
    target.innerHTML = `${meta(data)}<div class="ait-ranking">${rows.map(row => {
      const metrics = selectedMetrics(row);
      return `<div class="ait-row"><span class="ait-rank">${row.rank}</span><span class="ait-agent"><strong>${esc(row.agent_id)}</strong><small>${esc(statusLabel(row))} · ${esc(row.model || '')}</small></span><span class="ait-number"><strong>${pct(metrics.return_pct)}</strong><small>${esc(T.ret)}</small></span><span class="ait-number"><strong>${money(selectedValue(metrics))}</strong><small>${esc(T.value)}</small></span><span>${changeLabel(row.rank_change)}</span></div>`;
    }).join('')}</div>`;
  }

  function cards(data) {
    const target = $('#agent-cards');
    if (!target) return;
    const rows = data.leaderboard || [];
    if (!rows.length) {
      const participants = data.participants || [];
      target.innerHTML = participants.length ? `${meta(data)}<div class="ait-card-grid">${participants.map(participant => `<article class="ait-card"><div class="ait-card-head"><div><small class="ait-status">${esc(T.scheduled)} · ${esc(participant.model || '')}</small><h3>${esc(participant.agent_id)}</h3></div><span class="ait-rank">—</span></div><div class="ait-kpis"><div><small>${esc(T.value)}</small><strong>${money(startingCapital(data))}</strong></div><div><small>${esc(T.ret)}</small><strong>—</strong></div><div><small>${esc(T.cash)}</small><strong>—</strong></div></div><div class="ait-decision"><b>${esc(T.decision)}:</b> ${esc(T.noDecision)}</div></article>`).join('')}</div>` : `<div class="ait-note">${esc(T.unavailable)}</div>`;
      return;
    }
    target.innerHTML = `${meta(data)}<div class="ait-card-grid">${rows.map(row => {
      const metrics = selectedMetrics(row);
      const decision = row.latest_decision;
      const rationale = decision?.rationale || row.error || T.noDecision;
      const holdings = (row.positions || []).map(pos => `<span>${esc(pos.ticker)} ${(Number(pos.weight || 0) * 100).toFixed(1)}%</span>`).join('');
      return `<article class="ait-card"><div class="ait-card-head"><div><small class="ait-status">${esc(statusLabel(row))} · ${esc(row.model || '')}</small><h3>${esc(row.agent_id)}</h3>${changeLabel(row.rank_change)}</div><span class="ait-rank">${row.rank}</span></div><div class="ait-kpis"><div><small>${esc(T.value)}</small><strong>${money(selectedValue(metrics))}</strong></div><div><small>${esc(T.ret)}</small><strong>${pct(metrics.return_pct)}</strong></div><div><small>${esc(T.alpha)}</small><strong>${pct(metrics.alpha_pct)}</strong></div><div><small>${esc(T.drawdown)}</small><strong>${pct(metrics.max_drawdown_pct)}</strong></div><div><small>${esc(T.sharpe)}</small><strong>${metric(metrics.sharpe)}</strong></div><div><small>${esc(T.cash)}</small><strong>${pct(selectedCashWeight(row))}</strong></div></div><div class="ait-decision"><b>${esc(T.decision)}:</b> ${esc(rationale)}</div><div class="ait-holdings">${holdings || `<span>${esc(T.cash)}</span>`}</div></article>`;
    }).join('')}</div>`;
  }

  function history(data) {
    const target = $('#agent-log');
    if (!target) return;
    const rows = data.history || [];
    if (!rows.length) {
      target.innerHTML = `<div class="ait-note">${esc(T.unavailable)}<br>${esc(data[lang === 'en' ? 'disclaimer_en' : 'disclaimer_pl'] || T.disclaimer)}</div>`;
      return;
    }
    target.innerHTML = `<h3>${esc(T.daily)}</h3><div class="ait-history-wrap"><table class="ait-history"><thead><tr><th>${esc(T.date)}</th><th>${esc(T.place)}</th></tr></thead><tbody>${rows.map(row => `<tr><td>${esc(row.session_date || '')}</td><td><div class="ait-history-ranks">${(row.leaderboard || []).map(item => {
      const ret = lang === 'en' ? (item.return_pct_usd ?? item.return_pct) : (item.return_pct_pln ?? item.return_pct);
      return `<span>${item.rank}. ${esc(item.agent_id)} ${pct(ret)}</span>`;
    }).join('')}</div></td></tr>`).join('')}</tbody></table></div><div class="ait-note">${esc(data[lang === 'en' ? 'disclaimer_en' : 'disclaimer_pl'] || T.disclaimer)}</div>`;
  }

  async function load() {
    installStyles();
    try {
      const response = await fetch(`${endpoint}?v=${Date.now()}`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (data.schema_version !== 'ai-tournament-v1') throw new Error('unsupported schema');
      preview(data); cards(data); history(data);
    } catch (_) {
      const note = `<div class="ait-note">${esc(T.unavailable)}</div>`;
      if ($('#agents-preview')) $('#agents-preview').innerHTML = note;
      if ($('#agent-cards')) $('#agent-cards').innerHTML = note;
      if ($('#agent-log')) $('#agent-log').innerHTML = note;
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', load, { once: true });
  else load();
  setInterval(load, 15 * 60 * 1000);
})();
