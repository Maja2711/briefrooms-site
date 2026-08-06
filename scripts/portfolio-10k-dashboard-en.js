(() => {
  'use strict';

  if (window.__BR_PORTFOLIO_ROOM_STABLE__) return;
  window.__BR_PORTFOLIO_ROOM_STABLE__ = true;

  const config = window.BR_PORTFOLIO_10K || {};
  const lang = config.lang === 'en' || document.documentElement.lang.toLowerCase().startsWith('en') ? 'en' : 'pl';
  const isEn = lang === 'en';
  const locale = isEn ? 'en-US' : 'pl-PL';
  const currency = isEn ? 'USD' : 'PLN';
  const portfolioUrl = isEn
    ? '/data/investments/portfolio_10k_usd.json'
    : '/data/investments/portfolio_10k.json';
  const braceUrl = '/data/investments/portfolio_10k_brace.json';
  const COLORS = ['#15964d', '#2768c7', '#7050c8', '#d99a25', '#22a2a8', '#d35d76', '#8291a8', '#bcc4ce'];
  const NAV_ORDER = ['news', 'investing', 'health', 'science', 'geopolitics', 'about'];

  const T = isEn ? {
    active: 'ACTIVE', loading: 'Loading data…', unavailable: 'Data temporarily unavailable',
    portfolio: '10K Portfolio', benchmark: 'Benchmark', rule: 'Rule',
    fallback: 'This section is available. Its detailed data is temporarily being refreshed.',
    braceUnavailable: 'BRACE data is temporarily unavailable. Portfolio data remains active.',
    market: 'Market data', values: 'values in USD', position: 'Position',
    error: 'The investment room recovered its navigation, but current portfolio data could not be loaded.'
  } : {
    active: 'AKTYWNY', loading: 'Ładowanie danych…', unavailable: 'Dane chwilowo niedostępne',
    portfolio: 'Portfel 10K', benchmark: 'Benchmark', rule: 'Zasada',
    fallback: 'Ta sekcja jest dostępna. Jej szczegółowe dane są chwilowo odświeżane.',
    braceUnavailable: 'Dane BRACE są chwilowo niedostępne. Dane portfela pozostają aktywne.',
    market: 'Dane rynkowe', values: 'wartości w PLN', position: 'Pozycja',
    error: 'Pokój Inwestycje odzyskał nawigację, ale bieżących danych portfela nie udało się załadować.'
  };

  const state = { portfolio: null, brace: null, bound: false, loaded: false };
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const num = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[character]));

  function valueOf(object, usdKey, plnKey) {
    if (isEn && Number.isFinite(Number(object?.[usdKey]))) return Number(object[usdKey]);
    return num(object?.[plnKey]);
  }

  function money(value) {
    return new Intl.NumberFormat(locale, {
      style: 'currency', currency, minimumFractionDigits: 2, maximumFractionDigits: 2
    }).format(num(value));
  }

  function pct(value) {
    const number = num(value);
    return `${number >= 0 ? '+' : ''}${(number * 100).toFixed(2)}%`;
  }

  function setText(selector, value) {
    const element = $(selector);
    if (element) element.textContent = String(value ?? '—');
  }

  function reorderHeader() {
    const nav = $('#site-header .br-site-header__nav');
    if (!nav) return false;
    const links = new Map($$(':scope > a[data-section]', nav).map(link => [link.dataset.section, link]));
    NAV_ORDER.forEach(section => {
      const link = links.get(section);
      if (link) nav.appendChild(link);
    });
    nav.dataset.investmentOrder = NAV_ORDER.join('-');
    return true;
  }

  function activateTab(name, scroll = false) {
    const panel = $(`.i10k-panel[data-panel="${name}"]`);
    if (!panel) return false;
    $$('[data-tab]').forEach(trigger => trigger.classList.toggle('active', trigger.dataset.tab === name));
    $$('.i10k-panel').forEach(item => item.classList.toggle('active', item.dataset.panel === name));
    try { history.replaceState(null, '', `#${name}`); } catch (_) {}
    if (scroll) window.scrollTo({ top: 0, behavior: 'smooth' });
    document.body.dataset.investmentActiveTab = name;
    return true;
  }

  function bindTabs() {
    if (state.bound) return;
    state.bound = true;
    document.addEventListener('click', event => {
      const trigger = event.target.closest('.i10k-app [data-tab]');
      if (!trigger?.dataset.tab) return;
      if (activateTab(trigger.dataset.tab, true)) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    }, true);
    window.addEventListener('hashchange', () => {
      const name = location.hash.slice(1);
      if (name) activateTab(name, false);
    });
    const initial = location.hash.slice(1) || 'overview';
    activateTab(initial, false);
    document.body.dataset.investmentTabs = 'ready';
  }

  async function fetchJson(url, timeoutMs = 12000) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const separator = url.includes('?') ? '&' : '?';
      const response = await window.fetch(`${url}${separator}stable=${Date.now()}`, {
        cache: 'no-store', signal: controller.signal
      });
      if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
      return await response.json();
    } finally {
      window.clearTimeout(timer);
    }
  }

  function activePositions(portfolio) {
    return (portfolio?.positions || []).filter(position => position.status === 'active');
  }

  function totalValue(portfolio) {
    return activePositions(portfolio).reduce(
      (sum, position) => sum + valueOf(position, 'current_value_usd', 'current_value_pln'), 0
    ) + valueOf(portfolio, 'cash_usd', 'cash_pln');
  }

  function renderAllocation(positions) {
    const donut = $('#allocation-donut');
    const list = $('#allocation-list');
    if (!donut || !list) return;
    let cursor = 0;
    const stops = positions.map((position, index) => {
      const weight = Math.max(0, num(position.current_weight ?? position.target_weight));
      const start = cursor * 100;
      cursor += weight;
      return `${COLORS[index % COLORS.length]} ${start}% ${Math.min(cursor * 100, 100)}%`;
    });
    if (cursor < 1) stops.push(`#edf1f6 ${cursor * 100}% 100%`);
    donut.style.background = `conic-gradient(${stops.join(',')})`;
    list.innerHTML = positions.slice(0, 8).map((position, index) => `
      <div class="allocation-row">
        <i style="background:${COLORS[index % COLORS.length]}"></i>
        <span>${escapeHtml(position.label || position.broker_symbol || T.position)}</span>
        <b>${(num(position.current_weight ?? position.target_weight) * 100).toFixed(1)}%</b>
      </div>`).join('');
  }

  function renderBenchmark(portfolio, result) {
    const benchmarkReturn = num(portfolio?.benchmark?.return_percent);
    const rows = [
      [T.portfolio, result],
      [portfolio?.benchmark?.label || T.benchmark, benchmarkReturn]
    ];
    const html = rows.map(([label, value]) => `
      <div class="bar-row">
        <span>${escapeHtml(label)}</span>
        <div class="bar-track"><i style="width:${Math.min(100, Math.max(4, (num(value) + 0.12) / 0.32 * 100))}%"></i></div>
        <b class="${num(value) >= 0 ? 'positive' : 'negative'}">${pct(value)}</b>
      </div>`).join('');
    const compact = $('#benchmark-bars');
    const full = $('#benchmark-full');
    if (compact) compact.innerHTML = html;
    if (full) full.innerHTML = html;
    setText('#benchmark-return', pct(benchmarkReturn));
  }

  function renderPortfolioTable(positions) {
    const table = $('#portfolio-table');
    if (!table) return;
    table.innerHTML = positions.map(position => {
      const pnl = num(position.pnl_percent);
      const thesis = isEn ? (position.thesis_en || position.thesis_pl) : (position.thesis_pl || position.thesis_en);
      return `
        <tr>
          <td><strong>${escapeHtml(position.label || position.broker_symbol || T.position)}</strong><br><small>${escapeHtml(position.broker_symbol || '')}</small></td>
          <td>${(num(position.current_weight ?? position.target_weight) * 100).toFixed(1)}%</td>
          <td>${money(valueOf(position, 'current_value_usd', 'current_value_pln'))}</td>
          <td class="${pnl >= 0 ? 'positive' : 'negative'}">${pct(pnl)}</td>
          <td><span class="signal ${escapeHtml(position.review_flag || 'HOLD')}">${escapeHtml(position.review_flag || 'HOLD')}</span></td>
          <td>${escapeHtml(thesis || '—')}</td>
        </tr>`;
    }).join('');
  }

  function renderRules(portfolio) {
    const grid = $('#rules-grid');
    if (grid) {
      grid.innerHTML = (portfolio?.methodology?.rules || []).map((rule, index) =>
        `<div><dt>${T.rule} ${index + 1}</dt><dd>${escapeHtml(rule)}</dd></div>`
      ).join('');
    }
    setText('#objective', isEn
      ? (portfolio?.methodology?.objective_en || portfolio?.methodology?.objective_pl || '—')
      : (portfolio?.methodology?.objective_pl || portfolio?.methodology?.objective_en || '—'));
  }

  function renderPortfolio(portfolio) {
    state.portfolio = portfolio;
    const positions = activePositions(portfolio);
    const cash = valueOf(portfolio, 'cash_usd', 'cash_pln');
    const total = totalValue(portfolio);
    const start = valueOf(portfolio, 'starting_capital_usd', 'starting_capital_pln') || 10000;
    const result = total / start - 1;

    setText('#portfolio-value', money(total));
    setText('#portfolio-return', pct(result));
    setText('#cash-value', money(cash));
    setText('#invested-value', money(total - cash));
    setText('#positions-count', positions.length);
    setText('#updated-at', portfolio.last_updated_at
      ? new Date(portfolio.last_updated_at).toLocaleString(locale)
      : '—');
    setText('#data-status', `${T.active} · ${currency}`);
    setText('#data-freshness', `${T.market}: ${portfolio.last_market_session || '—'} · ${T.values}`);
    setText('#broker-note', isEn
      ? (portfolio.broker_note_en || portfolio.broker_note_pl || '')
      : (portfolio.broker_note_pl || portfolio.broker_note_en || ''));

    const returnElement = $('#portfolio-return');
    if (returnElement) returnElement.className = result >= 0 ? 'positive' : 'negative';
    const badge = $('.live-badge');
    if (badge) {
      badge.innerHTML = `<i></i> ${T.active}`;
      badge.dataset.automationStatus = 'healthy';
    }

    renderAllocation(positions);
    renderBenchmark(portfolio, result);
    renderPortfolioTable(positions);
    renderRules(portfolio);
    document.body.dataset.investmentData = 'ready';
    document.body.dataset.investmentCurrency = currency;
    state.loaded = true;
  }

  function renderBrace(brace) {
    state.brace = brace;
    const score = num(brace?.portfolio?.score);
    const confidence = num(brace?.portfolio?.confidence);
    setText('#brace-score', score.toFixed(1));
    setText('#side-brace-score', score.toFixed(0));
    setText('#brace-confidence', `${confidence.toFixed(1)}%`);
    const track = $('#brace-track');
    if (track) track.style.width = `${Math.min(score, 100)}%`;
    const counts = $('#brace-counts');
    if (counts) counts.innerHTML = Object.entries(brace?.portfolio?.decision_counts || {})
      .slice(0, 4).map(([key, value]) => `<span>${escapeHtml(key)}: <b>${escapeHtml(value)}</b></span>`).join('');
    const impact = isEn
      ? `BRACE analysed ${brace?.portfolio?.positions_reviewed || 0} positions. Market regime: ${brace?.market_context?.regime || '—'}.`
      : `BRACE przeanalizował ${brace?.portfolio?.positions_reviewed || 0} pozycji. Reżim rynku: ${brace?.market_context?.regime || '—'}.`;
    setText('#brace-impact', impact);
  }

  function ensurePanelsAreUsable() {
    $$('.i10k-panel').forEach(panel => {
      if (!panel.dataset.panel) return;
      if (!panel.textContent.trim()) panel.innerHTML = `<div class="method-note">${T.fallback}</div>`;
    });
    document.body.dataset.investmentPanels = 'ready';
  }

  function showError(error) {
    setText('#data-status', T.unavailable);
    const panel = $('.i10k-panel.active') || $('.i10k-panel[data-panel="overview"]');
    if (panel && !panel.querySelector('.stable-room-error')) {
      panel.insertAdjacentHTML('afterbegin', `<div class="error-box stable-room-error">${escapeHtml(T.error)}<br><small>${escapeHtml(error?.message || error || '')}</small></div>`);
    }
    document.body.dataset.investmentData = 'error';
  }

  async function load() {
    const [portfolioResult, braceResult] = await Promise.allSettled([
      fetchJson(portfolioUrl), fetchJson(braceUrl)
    ]);
    if (portfolioResult.status === 'fulfilled') renderPortfolio(portfolioResult.value);
    else showError(portfolioResult.reason);
    if (braceResult.status === 'fulfilled') renderBrace(braceResult.value);
    else setText('#brace-impact', T.braceUnavailable);
    ensurePanelsAreUsable();
    return portfolioResult.status === 'fulfilled';
  }

  function start() {
    bindTabs();
    reorderHeader();
    const observer = new MutationObserver(() => {
      if (reorderHeader()) observer.disconnect();
    });
    if (!reorderHeader()) observer.observe(document.documentElement, { childList: true, subtree: true });
    window.setTimeout(() => observer.disconnect(), 8000);
    load().catch(showError);
    window.setTimeout(() => {
      if (!state.loaded) load().catch(showError);
    }, 5000);
  }

  window.BriefRoomsInvestmentRoom = {
    activateTab,
    reload: load,
    state,
    audit() {
      return {
        tabsReady: document.body.dataset.investmentTabs === 'ready',
        dataReady: document.body.dataset.investmentData === 'ready',
        panelsReady: document.body.dataset.investmentPanels === 'ready',
        activeTab: document.body.dataset.investmentActiveTab || '',
        currency: document.body.dataset.investmentCurrency || ''
      };
    }
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
})();
