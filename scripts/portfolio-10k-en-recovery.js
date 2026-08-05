(() => {
  'use strict';

  const htmlLang = String(document.documentElement.lang || '').toLowerCase();
  if (!htmlLang.startsWith('en')) return;

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const colors = ['#15964d', '#2768c7', '#7050c8', '#d99a25', '#22a2a8', '#d35d76', '#8291a8', '#bcc4ce'];
  const state = { bound: false, loaded: false };

  function number(value, fallback = 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function valueOf(item, usdKey, legacyKey) {
    if (item && Number.isFinite(Number(item[usdKey]))) return Number(item[usdKey]);
    return number(item && item[legacyKey]);
  }

  function money(value) {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    }).format(number(value));
  }

  function pct(value) {
    const n = number(value);
    return `${n >= 0 ? '+' : ''}${(n * 100).toFixed(2)}%`;
  }

  function activateTab(name, scroll = true) {
    const panel = $(`.i10k-panel[data-panel="${CSS.escape(name)}"]`);
    if (!panel) return false;
    $$('[data-tab]').forEach(button => button.classList.toggle('active', button.dataset.tab === name));
    $$('.i10k-panel').forEach(item => item.classList.toggle('active', item.dataset.panel === name));
    history.replaceState(null, '', `#${name}`);
    if (scroll) window.scrollTo({ top: 0, behavior: 'smooth' });
    return true;
  }

  function bindTabs() {
    if (state.bound) return;
    state.bound = true;
    document.addEventListener('click', event => {
      const trigger = event.target.closest('.i10k-app [data-tab]');
      if (!trigger || !trigger.dataset.tab) return;
      if (!activateTab(trigger.dataset.tab)) return;
      event.preventDefault();
    }, true);

    const hash = location.hash.slice(1);
    if (hash) activateTab(hash, false);
    document.body.dataset.enInvestmentTabs = 'ready';
  }

  async function fetchJson(url, timeoutMs = 12000) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const separator = url.includes('?') ? '&' : '?';
      const response = await fetch(`${url}${separator}recovery=${Date.now()}`, {
        cache: 'no-store',
        signal: controller.signal
      });
      if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
      return await response.json();
    } finally {
      window.clearTimeout(timer);
    }
  }

  function activePositions(portfolio) {
    return (portfolio.positions || []).filter(position => position.status === 'active');
  }

  function portfolioValue(portfolio) {
    return activePositions(portfolio).reduce(
      (sum, position) => sum + valueOf(position, 'current_value_usd', 'current_value_pln'),
      0
    ) + valueOf(portfolio, 'cash_usd', 'cash_pln');
  }

  function setText(selector, text) {
    const element = $(selector);
    if (element) element.textContent = text;
  }

  function renderPortfolio(portfolio) {
    const positions = activePositions(portfolio);
    const cash = valueOf(portfolio, 'cash_usd', 'cash_pln');
    const total = portfolioValue(portfolio);
    const start = valueOf(portfolio, 'starting_capital_usd', 'starting_capital_pln') || 10000;
    const result = total / start - 1;
    const benchmarkReturn = number(portfolio.benchmark && portfolio.benchmark.return_percent);

    setText('#portfolio-value', money(total));
    setText('#portfolio-return', pct(result));
    setText('#cash-value', money(cash));
    setText('#invested-value', money(total - cash));
    setText('#positions-count', String(positions.length));
    setText('#updated-at', portfolio.last_updated_at ? new Date(portfolio.last_updated_at).toLocaleString('en-GB') : '—');
    setText('#data-status', 'ACTIVE · USD');
    setText('#data-freshness', `Market data: ${portfolio.last_market_session || '—'} · values in USD`);
    setText('#benchmark-return', pct(benchmarkReturn));

    const returnElement = $('#portfolio-return');
    if (returnElement) returnElement.className = result >= 0 ? 'positive' : 'negative';

    const donut = $('#allocation-donut');
    const allocationList = $('#allocation-list');
    if (donut && allocationList) {
      let cursor = 0;
      const stops = positions.map((position, index) => {
        const weight = Math.max(0, number(position.current_weight ?? position.target_weight));
        const startPct = cursor * 100;
        cursor += weight;
        return `${colors[index % colors.length]} ${startPct}% ${Math.min(cursor * 100, 100)}%`;
      });
      if (cursor < 1) stops.push(`#edf1f6 ${cursor * 100}% 100%`);
      donut.style.background = `conic-gradient(${stops.join(',')})`;
      allocationList.innerHTML = positions.slice(0, 8).map((position, index) => `
        <div class="allocation-row">
          <i style="background:${colors[index % colors.length]}"></i>
          <span>${position.label || position.broker_symbol || 'Position'}</span>
          <b>${(number(position.current_weight ?? position.target_weight) * 100).toFixed(1)}%</b>
        </div>`).join('');
    }

    const benchmarkHtml = [
      ['10K Portfolio', result],
      [portfolio.benchmark?.label || 'Benchmark', benchmarkReturn]
    ].map(([label, value]) => `
      <div class="bar-row">
        <span>${label}</span>
        <div class="bar-track"><i style="width:${Math.min(100, Math.max(4, (number(value) + 0.12) / 0.32 * 100))}%"></i></div>
        <b class="${number(value) >= 0 ? 'positive' : 'negative'}">${pct(value)}</b>
      </div>`).join('');
    const benchmarkBars = $('#benchmark-bars');
    const benchmarkFull = $('#benchmark-full');
    if (benchmarkBars) benchmarkBars.innerHTML = benchmarkHtml;
    if (benchmarkFull) benchmarkFull.innerHTML = benchmarkHtml;

    const table = $('#portfolio-table');
    if (table) {
      table.innerHTML = positions.map(position => {
        const pnl = number(position.pnl_percent);
        return `
          <tr>
            <td><strong>${position.label || position.broker_symbol || 'Position'}</strong><br><small>${position.broker_symbol || ''}</small></td>
            <td>${(number(position.current_weight ?? position.target_weight) * 100).toFixed(1)}%</td>
            <td>${money(valueOf(position, 'current_value_usd', 'current_value_pln'))}</td>
            <td class="${pnl >= 0 ? 'positive' : 'negative'}">${pct(pnl)}</td>
            <td><span class="signal ${position.review_flag || 'HOLD'}">${position.review_flag || 'HOLD'}</span></td>
            <td>${position.thesis_en || position.thesis_pl || '—'}</td>
          </tr>`;
      }).join('');
    }

    const rules = portfolio.methodology?.rules || [];
    const rulesGrid = $('#rules-grid');
    if (rulesGrid) {
      rulesGrid.innerHTML = rules.map((rule, index) => `<div><dt>Rule ${index + 1}</dt><dd>${rule}</dd></div>`).join('');
    }
    setText('#objective', portfolio.methodology?.objective_en || '—');
    setText('#broker-note', portfolio.broker_note_en || 'Model portfolio, not a brokerage account.');

    const badge = $('.live-badge');
    if (badge) {
      badge.innerHTML = '<i></i> LIVE';
      badge.dataset.automationStatus = 'healthy';
    }

    document.body.dataset.enInvestmentData = 'ready';
    state.loaded = true;
  }

  function renderBrace(brace) {
    const score = number(brace.portfolio?.score);
    const confidence = number(brace.portfolio?.confidence);
    setText('#brace-score', score.toFixed(1));
    setText('#side-brace-score', score.toFixed(0));
    setText('#brace-confidence', `${confidence.toFixed(1)}%`);
    const track = $('#brace-track');
    if (track) track.style.width = `${Math.min(score, 100)}%`;

    const counts = $('#brace-counts');
    if (counts) {
      counts.innerHTML = Object.entries(brace.portfolio?.decision_counts || {})
        .slice(0, 4)
        .map(([key, value]) => `<span>${key}: <b>${value}</b></span>`)
        .join('');
    }

    setText(
      '#brace-impact',
      `BRACE analysed ${brace.portfolio?.positions_reviewed || 0} positions. Market regime: ${brace.market_context?.regime || '—'}.`
    );

    const decisions = $('#brace-decisions');
    if (decisions) {
      decisions.innerHTML = (brace.positions || []).slice(0, 3).map(position => `
        <div class="decision-row">
          <span class="signal ${position.decision?.code || 'HOLD'}">${position.decision?.code || 'HOLD'}</span>
          <span><strong>${position.broker_symbol || position.id || '—'}</strong><br><small>${position.strongest_argument || 'No material change.'}</small></span>
          <b>${number(position.confidence).toFixed(0)}%</b>
        </div>`).join('');
    }
  }

  function showPortfolioError(message) {
    setText('#data-status', 'DATA TEMPORARILY UNAVAILABLE');
    const active = $('.i10k-panel.active');
    if (active && !active.querySelector('.en-recovery-error')) {
      active.insertAdjacentHTML(
        'afterbegin',
        `<div class="error-box en-recovery-error">The English investment data could not be loaded: ${message}. The navigation remains available.</div>`
      );
    }
  }

  async function load() {
    bindTabs();

    const portfolioPromise = fetchJson('/data/investments/portfolio_10k_usd.json');
    const bracePromise = fetchJson('/data/investments/portfolio_10k_brace.json');
    const [portfolioResult, braceResult] = await Promise.allSettled([portfolioPromise, bracePromise]);

    if (portfolioResult.status === 'fulfilled') {
      renderPortfolio(portfolioResult.value);
    } else {
      showPortfolioError(portfolioResult.reason?.message || 'unknown portfolio error');
    }

    if (braceResult.status === 'fulfilled') {
      renderBrace(braceResult.value);
    } else {
      setText('#brace-impact', 'BRACE data is temporarily unavailable. Portfolio data remains active.');
    }
  }

  function start() {
    bindTabs();
    load().catch(error => showPortfolioError(error?.message || 'unexpected error'));
    window.setTimeout(() => {
      if (!state.loaded && $('#data-status')?.textContent?.includes('Loading')) {
        load().catch(() => {});
      }
    }, 6000);
  }

  window.BriefRoomsEnglishInvestmentRecovery = {
    activateTab,
    reload: load
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
