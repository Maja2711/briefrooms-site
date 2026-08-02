(() => {
  'use strict';

  const lang = window.BR_PORTFOLIO_10K?.lang === 'en' ? 'en' : 'pl';
  const COPY = lang === 'pl' ? {
    tableExit: 'PLANOWANA SPRZEDAŻ',
    cardExit: 'SELL / WYJŚCIE',
    title: 'Aktualna decyzja BRACE',
    status: 'Status wykonania',
    event: 'Zdarzenie',
    thesis: 'Wpływ na tezę',
    rationale: 'Dlaczego BRACE wybrał SELL / EXIT',
    reportAction: 'Akcja po tym raporcie',
    waiting: 'Planowana sprzedaż całej pozycji na najbliższej dostępnej sesji',
    execution: 'Realizacja paper po pierwszym kompletnym 5-minutowym notowaniu po otwarciu rynku.',
    paper: 'Wyłącznie portfel modelowy — brak zlecenia u brokera.',
    modelAction: 'SELL / EXIT',
  } : {
    tableExit: 'SELL PLANNED',
    cardExit: 'SELL / EXIT',
    title: 'Current BRACE decision',
    status: 'Execution status',
    event: 'Event',
    thesis: 'Thesis effect',
    rationale: 'Why BRACE selected SELL / EXIT',
    reportAction: 'Action after this report',
    waiting: 'Full-position sale planned for the next available market session',
    execution: 'Paper execution after the first completed five-minute quote following market open.',
    paper: 'Model portfolio only — no brokerage order.',
    modelAction: 'SELL / EXIT',
  };

  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));

  async function json(path) {
    const response = await fetch(`${path}?v=${Date.now()}`, {cache: 'no-store'});
    if (!response.ok) throw new Error(path);
    return response.json();
  }

  function nextWeekday(value) {
    const date = new Date(value || Date.now());
    if (Number.isNaN(date.valueOf())) return null;
    date.setUTCDate(date.getUTCDate() + 1);
    while (date.getUTCDay() === 0 || date.getUTCDay() === 6) date.setUTCDate(date.getUTCDate() + 1);
    return date;
  }

  function dateText(date) {
    if (!date) return '';
    return new Intl.DateTimeFormat(lang === 'pl' ? 'pl-PL' : 'en-GB', {
      weekday: 'long', day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'Europe/Warsaw'
    }).format(date);
  }

  function reportMap(materialEvents) {
    const map = new Map();
    for (const report of materialEvents?.reports || []) {
      if (!report?.position_id) continue;
      const list = map.get(String(report.position_id)) || [];
      list.push(report);
      map.set(String(report.position_id), list);
    }
    for (const list of map.values()) {
      list.sort((a, b) => String(b.published_at || b.event_date || '').localeCompare(String(a.published_at || a.event_date || '')));
    }
    return map;
  }

  function decisionMap(publicState, orders, materialEvents) {
    const orderByDecision = new Map((orders?.orders || []).map(order => [String(order.decision_id), order]));
    const reportsByPosition = reportMap(materialEvents);
    const map = new Map();
    for (const decision of publicState?.pending_decisions || []) {
      if (!['EXIT', 'REDUCE', 'REPLACE', 'ADD'].includes(String(decision.action || ''))) continue;
      const order = orderByDecision.get(String(decision.decision_id)) || null;
      const reports = reportsByPosition.get(String(decision.instrument)) || [];
      const item = {...decision, order, reports};
      if (decision.instrument) map.set(String(decision.instrument), item);
    }
    return map;
  }

  function symbolFor(instrument) {
    const known = {novo: 'NOVOB.DK'};
    return known[instrument] || String(instrument || '').toUpperCase();
  }

  function currentPlan(decision) {
    const order = decision?.order;
    const waiting = order?.status === 'WAITING_FOR_MARKET';
    const session = waiting ? nextWeekday(order.signal_at || order.queued_at || decision.generated_at) : null;
    const status = waiting
      ? `${COPY.waiting}${session ? ` — ${dateText(session)}` : ''}. ${COPY.execution}`
      : String(order?.status || decision?.status || 'PROPOSED');
    return {status, session};
  }

  function latestMaterialReport(decision) {
    return (decision?.reports || []).find(report => report.impact === 'NEGATIVE' && ['HIGH', 'CRITICAL'].includes(String(report.severity || '')))
      || decision?.reports?.[0]
      || null;
  }

  function localField(object, base) {
    return object?.[`${base}_${lang}`] || '';
  }

  function applyPortfolioTable(decisions) {
    const table = document.getElementById('portfolio-table');
    if (!table || !table.children.length) return false;
    let changed = false;
    for (const [instrument, decision] of decisions) {
      const symbol = symbolFor(instrument);
      for (const row of table.querySelectorAll('tr')) {
        const rowSymbol = row.querySelector('td:first-child small')?.textContent?.trim();
        if (rowSymbol !== symbol) continue;
        const statusCell = row.querySelector('td:nth-child(5)');
        if (!statusCell) continue;
        let badge = statusCell.querySelector('.signal');
        if (!badge) {
          badge = document.createElement('span');
          badge.className = 'signal';
          statusCell.replaceChildren(badge);
        }
        if (badge.textContent !== COPY.tableExit || !badge.classList.contains('EXIT')) {
          badge.textContent = COPY.tableExit;
          badge.className = 'signal EXIT pending-exit';
          badge.title = lang === 'pl' ? decision.rationale_pl : decision.rationale_en;
          changed = true;
        }
      }
    }
    return changed;
  }

  function decisionBlock(decision) {
    const plan = currentPlan(decision);
    const report = latestMaterialReport(decision);
    const event = localField(report, 'summary');
    const thesis = localField(report, 'thesis_effect');
    const rationale = lang === 'pl' ? decision.rationale_pl : decision.rationale_en;
    return `<section class="brace-current-decision exit-pending" data-brace-decision-id="${esc(decision.decision_id)}">
      <div class="brace-current-decision__head"><span>${esc(COPY.title)}</span><strong>${esc(COPY.modelAction)}</strong></div>
      <dl>
        <div><dt>${esc(COPY.status)}</dt><dd>${esc(plan.status)}</dd></div>
        ${event ? `<div><dt>${esc(COPY.event)}</dt><dd>${esc(event)}</dd></div>` : ''}
        ${thesis ? `<div><dt>${esc(COPY.thesis)}</dt><dd>${esc(thesis)}</dd></div>` : ''}
        <div><dt>${esc(COPY.rationale)}</dt><dd>${esc(rationale || '')}</dd></div>
      </dl>
      <p>${esc(COPY.paper)}</p>
    </section>`;
  }

  function applyAnalyticsCards(decisions) {
    const root = document.getElementById('positions');
    if (!root || !root.querySelector('.position')) return false;
    let changed = false;
    for (const [instrument, decision] of decisions) {
      const symbol = symbolFor(instrument);
      for (const card of root.querySelectorAll('.position')) {
        if (card.querySelector('.symbol')?.textContent?.trim() !== symbol) continue;
        const flag = card.querySelector('.flag');
        if (flag && (flag.textContent !== COPY.cardExit || !flag.classList.contains('EXIT'))) {
          flag.textContent = COPY.cardExit;
          flag.className = 'flag EXIT pending-exit';
          changed = true;
        }
        const existing = card.querySelector('.brace-current-decision');
        if (!existing || existing.dataset.braceDecisionId !== String(decision.decision_id)) {
          if (existing) existing.remove();
          const reports = card.querySelector('.material-reports');
          if (reports) reports.insertAdjacentHTML('afterend', decisionBlock(decision));
          else card.querySelector('details')?.insertAdjacentHTML('beforebegin', decisionBlock(decision));
          changed = true;
        }
        card.classList.add('exit-pending-card');
      }
    }
    return changed;
  }

  function applyMaterialReportLabels(decisions) {
    const root = document.getElementById('positions');
    if (!root) return false;
    let changed = false;
    for (const [instrument] of decisions) {
      const symbol = symbolFor(instrument);
      for (const card of root.querySelectorAll('.position')) {
        if (card.querySelector('.symbol')?.textContent?.trim() !== symbol) continue;
        for (const report of card.querySelectorAll('.material-report')) {
          const action = report.querySelector('.model-action');
          if (!action || action.dataset.followUpLabelled === 'true') continue;
          const small = action.querySelector('small');
          if (small) small.textContent = COPY.reportAction;
          action.dataset.followUpLabelled = 'true';
          changed = true;
        }
      }
    }
    return changed;
  }

  function applyLatestDecision(decisions) {
    const root = document.getElementById('brace-decisions');
    if (!root) return false;
    const exit = [...decisions.values()].find(item => item.action === 'EXIT');
    if (!exit) return false;
    const symbol = symbolFor(exit.instrument);
    const id = `pending-${exit.decision_id}`;
    let row = root.querySelector(`[data-current-decision="${id}"]`);
    if (row) return false;
    row = document.createElement('div');
    row.className = 'decision-row current-exit-decision';
    row.dataset.currentDecision = id;
    row.innerHTML = `<span class="signal EXIT">${esc(COPY.tableExit)}</span><span><strong>${esc(symbol)}</strong><br><small>${esc((lang === 'pl' ? exit.rationale_pl : exit.rationale_en) || '')}</small></span><b>${Math.round(Number(exit.confidence || 0) * 100)}%</b>`;
    root.prepend(row);
    return true;
  }

  function applyAll(decisions) {
    applyPortfolioTable(decisions);
    applyAnalyticsCards(decisions);
    applyMaterialReportLabels(decisions);
    applyLatestDecision(decisions);
  }

  async function start() {
    try {
      const [publicState, orders, materialEvents] = await Promise.all([
        json('/data/portfolio10k/public/brace_engine_public.json'),
        json('/data/portfolio10k/paper_orders.json'),
        json('/data/investments/portfolio_10k_verified_material_events.json').catch(() => ({reports: []})),
      ]);
      const decisions = decisionMap(publicState, orders, materialEvents);
      if (!decisions.size) return;
      let attempts = 0;
      const render = () => {
        applyAll(decisions);
        attempts += 1;
        if (attempts < 32) setTimeout(render, attempts < 8 ? 250 : 750);
      };
      render();
      window.addEventListener('hashchange', () => setTimeout(() => applyAll(decisions), 0));
      document.addEventListener('click', event => {
        if (event.target.closest('[data-tab]')) setTimeout(() => applyAll(decisions), 0);
      });
    } catch (_) {
      // Base portfolio remains visible when decision-state enrichment is unavailable.
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
})();
