(() => {
  'use strict';

  const cfg = window.BR_PORTFOLIO_10K || { lang: 'pl' };
  const lang = cfg.lang === 'en' ? 'en' : 'pl';
  const locale = lang === 'en' ? 'en-US' : 'pl-PL';
  const currency = lang === 'en' ? 'USD' : 'PLN';
  const endpoint = '/data/ai_tournament/public.json';
  const $ = selector => document.querySelector(selector);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[character]));
  const money = value => Number(value || 0).toLocaleString(locale, {
    style: 'currency', currency, maximumFractionDigits: 2
  });
  const pct = value => Number.isFinite(Number(value))
    ? `${Number(value) >= 0 ? '+' : ''}${(Number(value) * 100).toFixed(2)}%`
    : '—';
  const dateLabel = value => {
    const text = String(value || '');
    if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return text || '—';
    const [year, month, day] = text.split('-').map(Number);
    return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString(locale, {
      day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC'
    });
  };

  const T = lang === 'en' ? {
    nav: 'AI Tournament', overviewTitle: 'AI TOURNAMENT',
    overviewSubtitle: 'Five locked model portfolios compared under the same capital, costs and market data.',
    detailTitle: 'AI TOURNAMENT',
    detailSubtitle: 'A clear comparison of returns, portfolio values and locked decisions. No hindsight edits.',
    research: 'RESEARCH PROJECT', auditable: 'AUDITABLE EXPERIMENT',
    active: 'active', scheduled: 'ready', finished: 'finished', error: 'agent error',
    ranking: 'AGENT RANKING', performance: 'TOURNAMENT PERFORMANCE', summary: 'CURRENT SUMMARY',
    basics: 'BASIC INFORMATION', rules: 'TOURNAMENT RULES', result: 'Return', value: 'Portfolio value',
    cash: 'Cash', alpha: 'Alpha', thesis: 'Locked thesis', expand: 'Expand', collapse: 'Collapse',
    holdings: 'Locked allocation', firstExecution: 'First execution', agents: 'Agents', account: 'Currency / capital',
    start: 'Start', update: 'Updated daily', lastSession: 'Last session', realPrices: 'Real market prices',
    equalCapital: 'Equal starting capital', noRebalance: 'Rebalancing disabled', costsIncluded: 'Costs included',
    rankRule: 'Ranking uses cumulative return; drawdown and Sharpe break ties.',
    liveStatus: 'Tournament active', paperOnly: 'Public paper portfolios — not investment advice.',
    noData: 'The tournament is waiting for its first completed market round.', unchanged: 'unchanged', up: 'up', down: 'down',
    currentRanking: 'Current ranking', currentReturns: 'Current returns', openFull: 'Open full AI Tournament →',
    latestUpdate: 'Latest update', compactMethod: 'Same rules for every agent'
  } : {
    nav: 'AI Tournament', overviewTitle: 'AI TOURNAMENT',
    overviewSubtitle: 'Pięć zablokowanych portfeli modeli porównywanych przy tym samym kapitale, kosztach i danych rynkowych.',
    detailTitle: 'AI TOURNAMENT',
    detailSubtitle: 'Czytelne porównanie wyników, wartości portfeli i zablokowanych decyzji. Bez poprawiania po fakcie.',
    research: 'PROJEKT BADAWCZY', auditable: 'AUDYTOWALNY EKSPERYMENT',
    active: 'aktywny', scheduled: 'gotowy', finished: 'zakończony', error: 'błąd agenta',
    ranking: 'RANKING AGENTÓW', performance: 'WYNIK TURNIEJU', summary: 'PODSUMOWANIE',
    basics: 'PODSTAWOWE INFORMACJE', rules: 'ZASADY TURNIEJU', result: 'Wynik', value: 'Wartość portfela',
    cash: 'Gotówka', alpha: 'Alpha', thesis: 'Zablokowana teza', expand: 'Rozwiń', collapse: 'Zwiń',
    holdings: 'Zablokowana alokacja', firstExecution: 'Pierwsze wykonanie', agents: 'Liczba agentów', account: 'Waluta / kapitał',
    start: 'Start', update: 'Aktualizacja codziennie', lastSession: 'Ostatnia sesja', realPrices: 'Rzeczywiste rynki i ceny',
    equalCapital: 'Równe portfele startowe', noRebalance: 'Rebalansowanie wyłączone', costsIncluded: 'Prowizje i poślizg wliczone',
    rankRule: 'Ranking według skumulowanego wyniku; drawdown i Sharpe rozstrzygają remis.',
    liveStatus: 'Turniej aktywny', paperOnly: 'Publiczne portfele modelowe — to nie jest porada inwestycyjna.',
    noData: 'Turniej czeka na pierwszą zakończoną rundę rynkową.', unchanged: 'bez zmian', up: 'awans', down: 'spadek',
    currentRanking: 'Aktualny ranking', currentReturns: 'Aktualne wyniki', openFull: 'Otwórz pełny AI Tournament →',
    latestUpdate: 'Ostatnia aktualizacja', compactMethod: 'Te same zasady dla każdego agenta'
  };

  const THEMES = {
    BRACE: { color: '#f2b51d', soft: '#fff3c8', icon: 'BR' },
    Gemini: { color: '#2f73ed', soft: '#e8f0ff', icon: '✦' },
    DeepSeek: { color: '#7057e8', soft: '#eeeaff', icon: 'DS' },
    Claude: { color: '#19a7a6', soft: '#e2f7f5', icon: 'C' },
    'GPT-5.6 Thinking': { color: '#7e8da2', soft: '#edf1f5', icon: 'AI' }
  };

  function theme(agentId) {
    return THEMES[agentId] || { color: '#59718f', soft: '#edf2f7', icon: String(agentId || '?').slice(0, 2) };
  }

  function selectedMetrics(row) {
    return lang === 'en' ? (row.metrics_usd || row.metrics || {}) : (row.metrics_pln || row.metrics || {});
  }

  function selectedValue(metrics) {
    return lang === 'en' ? metrics.portfolio_value_usd : metrics.portfolio_value_pln;
  }

  function selectedCashWeight(row) {
    return lang === 'en' ? (row.cash_weight_usd ?? row.cash_weight) : (row.cash_weight_pln ?? row.cash_weight);
  }

  function startingCapital(data) {
    return lang === 'en' ? data.tournament?.starting_capital_usd : data.tournament?.starting_capital_pln;
  }

  function statusLabel(row) {
    if (row.status === 'AGENT_ERROR') return T.error;
    if (row.status === 'ACTIVE') return T.active;
    if (row.status === 'FINISHED') return T.finished;
    return T.scheduled;
  }

  function returnValue(row) {
    return Number(selectedMetrics(row).return_pct || 0);
  }

  function updateStaticLabels() {
    document.querySelectorAll('[data-tab="agents"]').forEach(button => {
      const span = button.querySelector('span');
      if (span) span.textContent = T.nav;
      else button.textContent = T.nav;
    });
    const projectLink = document.querySelector('.i10k-projects a[href="#agents"]');
    if (projectLink) projectLink.textContent = T.nav;

    const previewCard = $('#agents-preview')?.closest('.agents-wide');
    if (previewCard) {
      const heading = previewCard.querySelector('.card-head h2');
      const paragraph = previewCard.querySelector('.card-head p');
      const chip = previewCard.querySelector('.research-chip');
      const button = previewCard.querySelector('[data-tab="agents"]');
      if (heading) heading.textContent = T.overviewTitle;
      if (paragraph) paragraph.textContent = T.overviewSubtitle;
      if (chip) chip.textContent = T.research;
      if (button) button.textContent = T.openFull;
    }

    const detailCard = $('#agent-cards')?.closest('.page-card');
    if (detailCard) {
      const heading = detailCard.querySelector('.card-head h2');
      const paragraph = detailCard.querySelector('.card-head p');
      const chip = detailCard.querySelector('.research-chip');
      if (heading) heading.textContent = T.detailTitle;
      if (paragraph) paragraph.textContent = T.detailSubtitle;
      if (chip) chip.textContent = T.auditable;
      detailCard.querySelectorAll(':scope > h3, :scope > .agent-method').forEach(node => { node.hidden = true; });
    }
  }

  function installStyles() {
    if ($('#ai-tournament-redesign-style')) return;
    const style = document.createElement('style');
    style.id = 'ai-tournament-redesign-style';
    style.textContent = `
      #agents-preview,#agent-cards,#agent-log{display:block!important;width:100%!important;min-width:0!important;min-height:0!important;height:auto!important;overflow:visible!important}
      .agents-preview-grid,.agent-cards{grid-template-columns:none!important}.aitx-shell{width:100%;min-width:0;color:#172b45}
      .aitx-meta{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 15px}.aitx-chip{display:inline-flex;align-items:center;gap:7px;min-height:34px;padding:7px 11px;border:1px solid #dce5ef;border-radius:10px;background:#f8fafc;color:#51627a;font-size:11px;font-weight:800}.aitx-chip b{color:#172b45}.aitx-dot{width:8px;height:8px;border-radius:50%;background:#20a968;box-shadow:0 0 0 4px rgba(32,169,104,.12)}
      .aitx-overview{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(420px,.95fr);gap:18px;align-items:stretch}.aitx-left-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;min-width:0}
      .aitx-card{min-width:0;border:1px solid #dde5ef;border-radius:16px;background:#fff;box-shadow:0 8px 24px rgba(28,47,73,.045);padding:15px}.aitx-card.performance{grid-column:1/-1}.aitx-card-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:13px}.aitx-card-head h3{margin:0;color:#172b45;font-size:12px;letter-spacing:.02em}.aitx-card-head p{margin:4px 0 0;color:#748197;font-size:11px;line-height:1.4}.aitx-card-head .aitx-soft{padding:5px 8px;border-radius:8px;background:#f1f5f9;color:#5c6e84;font-size:10px;font-weight:800}
      .aitx-chart{display:block;width:100%;height:auto;min-height:170px}.aitx-chart-grid{stroke:#e7edf4;stroke-width:1}.aitx-chart-axis{fill:#7a889a;font-size:10px;font-weight:700}.aitx-chart-line{fill:none;stroke-width:3;stroke-linecap:round;stroke-linejoin:round}.aitx-chart-dot{stroke:#fff;stroke-width:2}
      .aitx-bars{display:grid;gap:10px}.aitx-bar-row{display:grid;grid-template-columns:minmax(115px,1fr) minmax(90px,2fr) 64px;align-items:center;gap:10px;font-size:11px}.aitx-bar-name{display:flex;align-items:center;gap:8px;min-width:0;font-weight:800}.aitx-mini-rank{display:grid;place-items:center;width:23px;height:23px;border-radius:8px;background:#edf2f7;color:#33465f;font-size:10px}.aitx-bar-track{height:8px;border-radius:999px;background:#edf2f7;overflow:hidden}.aitx-bar-track i{display:block;height:100%;min-width:4px;border-radius:inherit}.aitx-bar-value{text-align:right;font-weight:900;color:#172b45}
      .aitx-info-list{display:grid;gap:8px}.aitx-info-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:9px 10px;border-radius:10px;background:#f8fafc;color:#617086;font-size:11px}.aitx-info-row b{color:#172b45;text-align:right}.aitx-rule-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.aitx-rule{display:flex;align-items:center;gap:8px;padding:9px;border-radius:10px;background:#f8fafc;color:#4f6076;font-size:10px;font-weight:750}.aitx-rule i{display:grid;place-items:center;width:22px;height:22px;border-radius:7px;background:#e7f7ef;color:#168f55;font-style:normal;font-weight:900}
      .aitx-ranking-panel{display:flex;flex-direction:column;min-width:0}.aitx-ranking-list{display:grid;gap:9px}.aitx-rank-row{display:grid;grid-template-columns:38px minmax(130px,1fr) 90px 120px 86px 72px;align-items:center;gap:10px;padding:11px 12px;border:1px solid #e1e8f1;border-radius:13px;background:#fff}.aitx-rank-number{display:grid;place-items:center;width:32px;height:32px;border-radius:10px;background:#edf2f7;font-weight:900}.aitx-rank-row:first-child .aitx-rank-number{background:#fff1bd;color:#795700}.aitx-agent{display:flex;align-items:center;gap:10px;min-width:0}.aitx-agent-icon{display:grid;place-items:center;flex:0 0 auto;width:34px;height:34px;border-radius:11px;font-size:11px;font-weight:950}.aitx-agent-copy{min-width:0}.aitx-agent-copy strong{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px}.aitx-agent-copy small{display:flex;align-items:center;gap:6px;margin-top:3px;color:#748197;font-size:10px}.aitx-agent-copy small i{width:7px;height:7px;border-radius:50%;background:#22aa6a}.aitx-cell{text-align:right;min-width:0}.aitx-cell small{display:block;color:#8490a0;font-size:9px;text-transform:uppercase;letter-spacing:.02em}.aitx-cell strong{display:block;margin-top:3px;color:#172b45;font-size:12px;white-space:nowrap}.aitx-cell.return strong{color:#13945a}.aitx-change{font-size:10px;font-weight:850;color:#718096;text-align:right}.aitx-change.up{color:#14864a}.aitx-change.down{color:#c84646}
      .aitx-detail{display:grid;grid-template-columns:minmax(300px,.72fr) minmax(0,1.8fr);gap:16px;align-items:start}.aitx-detail-side{display:grid;gap:14px;position:sticky;top:16px}.aitx-agent-list{display:grid;gap:10px}.aitx-agent-card{border:1px solid #dfe7f0;border-radius:15px;background:#fff;box-shadow:0 7px 20px rgba(29,47,73,.04);overflow:hidden}.aitx-agent-main{display:grid;grid-template-columns:40px minmax(150px,1.2fr) repeat(4,minmax(78px,.72fr));align-items:center;gap:10px;padding:13px 14px}.aitx-agent-rank{display:grid;place-items:center;width:34px;height:34px;border-radius:11px;background:#edf2f7;font-weight:950}.aitx-agent-card:first-child .aitx-agent-rank{background:#fff1bd;color:#775500}.aitx-metric{text-align:right}.aitx-metric small{display:block;color:#8490a0;font-size:9px}.aitx-metric strong{display:block;margin-top:4px;color:#172b45;font-size:12px;white-space:nowrap}.aitx-metric.primary strong{color:#13945a;font-size:14px}
      .aitx-thesis{border-top:1px solid #edf1f5;background:#fbfcfe}.aitx-thesis summary{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 14px;cursor:pointer;list-style:none;color:#4e6077;font-size:11px;font-weight:850}.aitx-thesis summary::-webkit-details-marker{display:none}.aitx-thesis-toggle{display:inline-flex;align-items:center;gap:7px;color:#1c68d4}.aitx-thesis-toggle .close{display:none}.aitx-thesis[open] .aitx-thesis-toggle .open{display:none}.aitx-thesis[open] .aitx-thesis-toggle .close{display:inline}.aitx-chevron{display:inline-block;transition:transform .2s ease}.aitx-thesis[open] .aitx-chevron{transform:rotate(180deg)}.aitx-thesis-body{padding:0 14px 14px;color:#52637a;font-size:11px;line-height:1.6}.aitx-thesis-body p{margin:0}.aitx-holdings{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}.aitx-holdings span{padding:5px 7px;border-radius:8px;background:#edf3f8;color:#33465e;font-size:9px;font-weight:850}
      .aitx-status-strip{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:14px;padding:11px 13px;border:1px solid #dfe7f0;border-radius:12px;background:#f8fafc;color:#5b6c82;font-size:10px}.aitx-status-strip strong{color:#178d55}.aitx-empty{padding:18px;border:1px solid #dfe7f0;border-radius:14px;background:#f8fafc;color:#5f7086;font-size:12px;line-height:1.5}
      @media(max-width:1180px){.aitx-overview,.aitx-detail{grid-template-columns:1fr}.aitx-detail-side{position:static}.aitx-ranking-panel{min-height:0}}@media(max-width:900px){.aitx-rank-row{grid-template-columns:36px minmax(120px,1fr) 82px 110px 72px}.aitx-rank-row .aitx-cell.cash{display:none}.aitx-agent-main{grid-template-columns:36px minmax(140px,1fr) repeat(3,minmax(76px,.7fr))}.aitx-agent-main .aitx-metric.alpha{display:none}}@media(max-width:680px){.aitx-left-grid{grid-template-columns:1fr}.aitx-card.performance{grid-column:auto}.aitx-rule-list{grid-template-columns:1fr}.aitx-rank-row{grid-template-columns:34px minmax(105px,1fr) 72px}.aitx-rank-row .aitx-cell.value,.aitx-rank-row .aitx-cell.cash{display:none}.aitx-change{display:none}.aitx-agent-main{grid-template-columns:34px minmax(120px,1fr) 72px}.aitx-agent-main .aitx-metric:not(.primary){display:none}.aitx-card{padding:12px}.aitx-meta{gap:6px}.aitx-chip{font-size:10px}}
    `;
    document.head.appendChild(style);
  }

  function icon(agentId) {
    const item = theme(agentId);
    return `<span class="aitx-agent-icon" style="background:${item.soft};color:${item.color}">${esc(item.icon)}</span>`;
  }

  function changeLabel(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number === 0) return `<span class="aitx-change">${esc(T.unchanged)}</span>`;
    if (number > 0) return `<span class="aitx-change up">↑ ${number} ${esc(T.up)}</span>`;
    return `<span class="aitx-change down">↓ ${Math.abs(number)} ${esc(T.down)}</span>`;
  }

  function meta(data) {
    const rows = data.leaderboard || data.participants || [];
    const execution = data.tournament?.execution_time ? String(data.tournament.execution_time).replace('_us_regular_session_open', '') : data.tournament?.start_date;
    return `<div class="aitx-meta"><span class="aitx-chip"><i class="aitx-dot"></i><b>${rows.length}</b> ${esc(T.agents)}</span><span class="aitx-chip"><b>${esc(T.start)}:</b> ${esc(dateLabel(data.tournament?.start_date))}</span><span class="aitx-chip"><b>${esc(T.firstExecution)}:</b> ${esc(dateLabel(execution))}</span><span class="aitx-chip">↻ ${esc(T.update)}</span><span class="aitx-chip"><b>${esc(T.lastSession)}:</b> ${esc(dateLabel(data.latest_session))}</span></div>`;
  }

  function currentBars(rows) {
    if (!rows.length) return `<div class="aitx-empty">${esc(T.noData)}</div>`;
    const maximum = Math.max(...rows.map(row => Math.abs(returnValue(row))), 0.0001);
    return `<div class="aitx-bars">${rows.map(row => {
      const value = returnValue(row);
      const item = theme(row.agent_id);
      const width = Math.max(5, Math.min(100, Math.abs(value) / maximum * 100));
      return `<div class="aitx-bar-row"><span class="aitx-bar-name"><i class="aitx-mini-rank">${esc(row.rank || '—')}</i>${esc(row.agent_id)}</span><span class="aitx-bar-track"><i style="width:${width.toFixed(1)}%;background:${value < 0 ? '#d95050' : item.color}"></i></span><span class="aitx-bar-value">${pct(value)}</span></div>`;
    }).join('')}</div>`;
  }

  function historyReturn(item) {
    const value = lang === 'en' ? (item?.return_pct_usd ?? item?.return_pct) : (item?.return_pct_pln ?? item?.return_pct);
    return Number.isFinite(Number(value)) ? Number(value) : null;
  }

  function performanceVisual(data, rows) {
    const history = Array.isArray(data.history) ? data.history.slice(-14) : [];
    if (history.length < 2) return currentBars(rows);
    const series = rows.map(row => ({
      agentId: row.agent_id,
      values: history.map(point => historyReturn((point.leaderboard || []).find(entry => entry.agent_id === row.agent_id)))
    })).filter(item => item.values.filter(Number.isFinite).length >= 2);
    if (!series.length) return currentBars(rows);

    const values = series.flatMap(item => item.values.filter(Number.isFinite));
    let minimum = Math.min(0, ...values);
    let maximum = Math.max(0, ...values);
    const spread = Math.max(maximum - minimum, 0.01);
    minimum -= spread * 0.12;
    maximum += spread * 0.12;
    const width = 560, height = 205, left = 44, right = 12, top = 12, bottom = 34;
    const innerWidth = width - left - right, innerHeight = height - top - bottom;
    const x = index => left + index / (history.length - 1) * innerWidth;
    const y = value => top + (maximum - value) / (maximum - minimum) * innerHeight;
    const grid = Array.from({ length: 5 }, (_, index) => {
      const value = maximum - index / 4 * (maximum - minimum);
      const py = y(value);
      return `<line class="aitx-chart-grid" x1="${left}" x2="${width - right}" y1="${py}" y2="${py}"></line><text class="aitx-chart-axis" x="2" y="${py + 3}">${(value * 100).toFixed(1)}%</text>`;
    }).join('');
    const lines = series.map(item => {
      const color = theme(item.agentId).color;
      const points = item.values.map((value, index) => Number.isFinite(value) ? `${x(index)},${y(value)}` : null).filter(Boolean).join(' ');
      let lastIndex = -1;
      item.values.forEach((value, index) => { if (Number.isFinite(value)) lastIndex = index; });
      const lastValue = item.values[lastIndex];
      return `<polyline class="aitx-chart-line" stroke="${color}" points="${points}"></polyline>${Number.isFinite(lastValue) ? `<circle class="aitx-chart-dot" fill="${color}" cx="${x(lastIndex)}" cy="${y(lastValue)}" r="4"></circle>` : ''}`;
    }).join('');
    return `<svg class="aitx-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(T.performance)}">${grid}${lines}<text class="aitx-chart-axis" x="${left}" y="${height - 9}">${esc(dateLabel(history[0]?.session_date))}</text><text class="aitx-chart-axis" text-anchor="end" x="${width - right}" y="${height - 9}">${esc(dateLabel(history[history.length - 1]?.session_date))}</text></svg>`;
  }

  function basicsCard(data, rows) {
    return `<article class="aitx-card"><div class="aitx-card-head"><div><h3>${esc(T.basics)}</h3></div></div><div class="aitx-info-list"><div class="aitx-info-row"><span>${esc(T.firstExecution)}</span><b>${esc(dateLabel(data.tournament?.execution_time?.slice(0, 10) || data.tournament?.start_date))}</b></div><div class="aitx-info-row"><span>${esc(T.agents)}</span><b>${rows.length}</b></div><div class="aitx-info-row"><span>${esc(T.account)}</span><b>${currency} / ${money(startingCapital(data))}</b></div></div></article>`;
  }

  function rulesCard(data) {
    const rules = data.rules || {};
    return `<article class="aitx-card"><div class="aitx-card-head"><div><h3>${esc(T.rules)}</h3><p>${esc(T.compactMethod)}</p></div></div><div class="aitx-rule-list"><div class="aitx-rule"><i>✓</i>${esc(T.realPrices)}</div><div class="aitx-rule"><i>✓</i>${esc(T.equalCapital)}</div><div class="aitx-rule"><i>✓</i>${esc(T.noRebalance)}</div><div class="aitx-rule"><i>✓</i>${esc(T.costsIncluded)}</div></div><div class="aitx-status-strip"><span>${esc(T.rankRule)}</span><strong>${rules.buy_and_hold ? 'BUY & HOLD' : '—'}</strong></div></article>`;
  }

  function rankingRows(rows) {
    if (!rows.length) return `<div class="aitx-empty">${esc(T.noData)}</div>`;
    return `<div class="aitx-ranking-list">${rows.map(row => {
      const metrics = selectedMetrics(row);
      return `<div class="aitx-rank-row"><span class="aitx-rank-number">${esc(row.rank || '—')}</span><span class="aitx-agent">${icon(row.agent_id)}<span class="aitx-agent-copy"><strong>${esc(row.agent_id)}</strong><small><i></i>${esc(statusLabel(row))}</small></span></span><span class="aitx-cell return"><small>${esc(T.result)}</small><strong>${pct(metrics.return_pct)}</strong></span><span class="aitx-cell value"><small>${esc(T.value)}</small><strong>${money(selectedValue(metrics))}</strong></span><span class="aitx-cell cash"><small>${esc(T.cash)}</small><strong>${pct(selectedCashWeight(row))}</strong></span>${changeLabel(row.rank_change)}</div>`;
    }).join('')}</div>`;
  }

  function preview(data) {
    const target = $('#agents-preview');
    if (!target) return;
    const rows = data.leaderboard || [];
    target.innerHTML = `<div class="aitx-shell">${meta(data)}<div class="aitx-overview"><div class="aitx-left-grid"><article class="aitx-card performance"><div class="aitx-card-head"><div><h3>${esc(T.performance)}</h3><p>${esc(T.currentReturns)}</p></div><span class="aitx-soft">${esc(dateLabel(data.latest_session))}</span></div>${performanceVisual(data, rows)}</article>${basicsCard(data, rows)}${rulesCard(data)}</div><article class="aitx-card aitx-ranking-panel"><div class="aitx-card-head"><div><h3>${esc(T.ranking)}</h3><p>${esc(T.currentRanking)}</p></div><span class="aitx-soft">${rows.length}</span></div>${rankingRows(rows)}</article></div><div class="aitx-status-strip"><span>${esc(T.paperOnly)}</span><strong>● ${esc(T.liveStatus)}</strong></div></div>`;
  }

  function thesis(row) {
    const rationale = row.latest_decision?.rationale || row.error || T.noData;
    const holdings = (row.positions || []).map(position => `<span>${esc(position.ticker)} ${(Number(position.weight || 0) * 100).toFixed(1)}%</span>`).join('');
    return `<details class="aitx-thesis"><summary><span>${esc(T.thesis)}</span><span class="aitx-thesis-toggle"><span class="open">${esc(T.expand)}</span><span class="close">${esc(T.collapse)}</span><i class="aitx-chevron">⌄</i></span></summary><div class="aitx-thesis-body"><p>${esc(rationale)}</p>${holdings ? `<div class="aitx-holdings" aria-label="${esc(T.holdings)}">${holdings}</div>` : ''}</div></details>`;
  }

  function agentCards(rows) {
    if (!rows.length) return `<div class="aitx-empty">${esc(T.noData)}</div>`;
    return `<div class="aitx-agent-list">${rows.map(row => {
      const metrics = selectedMetrics(row);
      return `<article class="aitx-agent-card"><div class="aitx-agent-main"><span class="aitx-agent-rank">${esc(row.rank || '—')}</span><span class="aitx-agent">${icon(row.agent_id)}<span class="aitx-agent-copy"><strong>${esc(row.agent_id)}</strong><small><i></i>${esc(statusLabel(row))}</small></span></span><span class="aitx-metric primary"><small>${esc(T.result)}</small><strong>${pct(metrics.return_pct)}</strong></span><span class="aitx-metric"><small>${esc(T.value)}</small><strong>${money(selectedValue(metrics))}</strong></span><span class="aitx-metric"><small>${esc(T.cash)}</small><strong>${pct(selectedCashWeight(row))}</strong></span><span class="aitx-metric alpha"><small>${esc(T.alpha)}</small><strong>${pct(metrics.alpha_pct)}</strong></span></div>${thesis(row)}</article>`;
    }).join('')}</div>`;
  }

  function cards(data) {
    const target = $('#agent-cards');
    if (!target) return;
    const rows = data.leaderboard || [];
    target.innerHTML = `<div class="aitx-shell">${meta(data)}<div class="aitx-detail"><aside class="aitx-detail-side"><article class="aitx-card"><div class="aitx-card-head"><div><h3>${esc(T.performance)}</h3><p>${esc(T.currentReturns)}</p></div></div>${performanceVisual(data, rows)}</article><article class="aitx-card"><div class="aitx-card-head"><div><h3>${esc(T.summary)}</h3><p>${esc(T.currentRanking)}</p></div></div>${currentBars(rows)}</article>${rulesCard(data)}</aside><section class="aitx-card aitx-ranking-panel"><div class="aitx-card-head"><div><h3>${esc(T.ranking)}</h3><p>${esc(T.rankRule)}</p></div><span class="aitx-soft">${rows.length}</span></div>${agentCards(rows)}</section></div></div>`;
  }

  function history(data) {
    const target = $('#agent-log');
    if (!target) return;
    const count = Array.isArray(data.history) ? data.history.length : 0;
    target.innerHTML = `<div class="aitx-status-strip"><span>${esc(T.paperOnly)}</span><strong>● ${esc(T.liveStatus)} · ${esc(T.latestUpdate)} ${esc(dateLabel(data.latest_session))}${count ? ` · ${count}` : ''}</strong></div>`;
  }

  async function load() {
    installStyles();
    updateStaticLabels();
    try {
      const response = await fetch(`${endpoint}?v=${Date.now()}`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (data.schema_version !== 'ai-tournament-v1') throw new Error('unsupported schema');
      preview(data); cards(data); history(data);
    } catch (_) {
      const note = `<div class="aitx-empty">${esc(T.noData)}</div>`;
      if ($('#agents-preview')) $('#agents-preview').innerHTML = note;
      if ($('#agent-cards')) $('#agent-cards').innerHTML = note;
      if ($('#agent-log')) $('#agent-log').innerHTML = '';
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', load, { once: true });
  else load();
  setInterval(load, 15 * 60 * 1000);
})();
