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
  const braceUrl = '/data/portfolio10k/public/brace_engine_public.json';
  const CONTROLLER_VERSION = 'resilient-v10';
  const LIVE_MAX_AGE_MS = 2 * 60 * 60 * 1000;
  const CACHE_MAX_AGE_MS = 6 * 60 * 60 * 1000;
  const RETRY_DELAYS_MS = [5000, 15000, 30000, 60000];
  const COLORS = ['#15964d', '#2768c7', '#7050c8', '#d99a25', '#22a2a8', '#d35d76', '#8291a8', '#bcc4ce'];
  const NAV_ORDER = ['news', 'investing', 'health', 'science', 'geopolitics', 'about'];

  const T = isEn ? {
    active: 'ACTIVE', live: 'LIVE', cached: 'CACHED', stale: 'STALE', loading: 'Loading data…', unavailable: 'Data temporarily unavailable',
    portfolio: '10K Portfolio', benchmark: 'Benchmark', rule: 'Rule',
    fallback: 'This section is available. Its detailed data is temporarily being refreshed.',
    braceUnavailable: 'BRACE data is temporarily unavailable. Portfolio data remains active.',
    market: 'Market data', values: 'values in USD', position: 'Position',
    error: 'The investment room recovered its navigation, but current portfolio data could not be loaded.'
  } : {
    active: 'AKTYWNY', live: 'LIVE', cached: 'CACHED', stale: 'STALE', loading: 'Ładowanie danych…', unavailable: 'Dane chwilowo niedostępne',
    portfolio: 'Portfel 10K', benchmark: 'Benchmark', rule: 'Zasada',
    fallback: 'Ta sekcja jest dostępna. Jej szczegółowe dane są chwilowo odświeżane.',
    braceUnavailable: 'Dane BRACE są chwilowo niedostępne. Dane portfela pozostają aktywne.',
    market: 'Dane rynkowe', values: 'wartości w PLN', position: 'Pozycja',
    error: 'Pokój Inwestycje odzyskał nawigację, ale bieżących danych portfela nie udało się załadować.'
  };

  const state = {
    portfolio: null,
    brace: null,
    bound: false,
    loaded: false,
    portfolioPromise: null,
    bracePromise: null,
    retryAttempt: 0,
    retryTimer: null,
    braceRetryAttempt: 0,
    braceRetryTimer: null,
    dataSource: ''
  };
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

  function cacheKey(kind) {
    return `briefrooms:investment-room:${kind}:${lang}:v10`;
  }

  function readCache(kind, validator) {
    try {
      const cached = JSON.parse(window.localStorage.getItem(cacheKey(kind)) || 'null');
      const savedAt = Number(cached?.savedAt || 0);
      const ageMs = Date.now() - savedAt;
      if (!cached || !savedAt || ageMs < 0 || ageMs > CACHE_MAX_AGE_MS) return null;
      return validator(cached.payload) ? { payload: cached.payload, savedAt, ageMs } : null;
    } catch (_) {
      return null;
    }
  }

  function writeCache(kind, payload) {
    try {
      window.localStorage.setItem(cacheKey(kind), JSON.stringify({ savedAt: Date.now(), payload }));
    } catch (_) {
      // Storage can be disabled; the network path remains fully functional.
    }
  }

  function validPortfolio(payload) {
    const start = isEn ? payload?.starting_capital_usd : payload?.starting_capital_pln;
    return Boolean(payload && Array.isArray(payload.positions) && Number(start) > 0 && payload.last_updated_at);
  }

  function validBrace(payload) {
    return Boolean(payload?.controller_status && payload?.generated_at && Array.isArray(payload?.position_recommendations));
  }

  function payloadAgeMs(payload, field) {
    const value = payload?.[field];
    const timestamp = value ? new Date(value).valueOf() : NaN;
    return Number.isFinite(timestamp) ? Math.max(0, Date.now() - timestamp) : Infinity;
  }

  function freshnessState(payload, source, field) {
    const ageMs = payloadAgeMs(payload, field);
    if (ageMs > LIVE_MAX_AGE_MS) return 'STALE';
    return source === 'network' ? 'LIVE' : 'CACHED';
  }

  function braceStatusLabel(status) {
    const value = String(status || 'BRACE').toUpperCase();
    return ({
      PROBATIONARY_CONTROL: 'PROBATIONARY',
      ACTIVE_PAPER_CONTROL: 'ACTIVE PAPER',
      ACTIVE_CONTROL: 'ACTIVE',
      ACTIVE_BASELINE: 'BASELINE',
      FALLBACK_BASELINE: 'FALLBACK',
      SAFE_MODE: 'SAFE MODE',
      DEGRADED: 'DEGRADED',
      SUSPENDED: 'SUSPENDED'
    })[value] || value.replaceAll('_', ' ');
  }

  async function fetchJson(url, timeoutMs = 8000, cacheBust = false) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const requestUrl = cacheBust
        ? `${url}${url.includes('?') ? '&' : '?'}retry=${Date.now()}`
        : url;
      const response = await window.fetch(requestUrl, {
        cache: cacheBust ? 'no-store' : 'default', signal: controller.signal
      });
      if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
      return await response.json();
    } finally {
      window.clearTimeout(timer);
    }
  }

  async function fetchJsonResilient(url, validator) {
    let firstError = null;
    for (const attempt of [
      { timeoutMs: 8000, cacheBust: false },
      { timeoutMs: 12000, cacheBust: true }
    ]) {
      try {
        const payload = await fetchJson(url, attempt.timeoutMs, attempt.cacheBust);
        if (!validator(payload)) throw new Error(`${url}: invalid payload`);
        return payload;
      } catch (error) {
        firstError ||= error;
      }
    }
    throw firstError || new Error(`${url}: unavailable`);
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

  function renderPortfolio(portfolio, source = 'network') {
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
    const freshness = freshnessState(portfolio, source, 'last_updated_at');
    setText('#data-status', `${T[freshness.toLowerCase()]} · ${currency}`);
    setText('#data-freshness', `${T.market}: ${portfolio.last_market_session || '—'} · ${T.values}`);
    setText('#broker-note', isEn
      ? (portfolio.broker_note_en || portfolio.broker_note_pl || '')
      : (portfolio.broker_note_pl || portfolio.broker_note_en || ''));

    const returnElement = $('#portfolio-return');
    if (returnElement) returnElement.className = result >= 0 ? 'positive' : 'negative';
    const badge = $('.live-badge');
    if (badge) {
      badge.innerHTML = `<i></i> ${T[freshness.toLowerCase()]}`;
      badge.dataset.automationStatus = freshness.toLowerCase();
    }

    renderAllocation(positions);
    renderBenchmark(portfolio, result);
    renderPortfolioTable(positions);
    renderRules(portfolio);
    $('.stable-room-error')?.remove();
    document.body.dataset.investmentData = 'ready';
    document.body.dataset.investmentCurrency = currency;
    document.body.dataset.investmentDataSource = source;
    document.body.dataset.investmentFreshness = freshness.toLowerCase();
    document.body.dataset.investmentNetwork = freshness.toLowerCase();
    state.loaded = true;
    state.dataSource = source;
  }

  function renderBrace(brace, source = 'network') {
    state.brace = brace;
    const recommendations = Array.isArray(brace?.position_recommendations) ? brace.position_recommendations : [];
    const scores = recommendations.map(item => num(item.final_score, NaN)).filter(Number.isFinite);
    const confidences = recommendations.map(item => num(item.confidence, NaN)).filter(Number.isFinite);
    const score = scores.length ? scores.reduce((a,b)=>a+b,0) / scores.length : 0;
    const confidence = confidences.length ? confidences.reduce((a,b)=>a+b,0) / confidences.length * 100 : 0;
    const decisionCounts = recommendations.reduce((acc,item) => {
      const key = String(item.action || 'HOLD').toUpperCase();
      acc[key] = (acc[key] || 0) + 1;
      return acc;
    }, {});
    const freshness = freshnessState(brace, source, 'generated_at');
    const status = braceStatusLabel(brace?.controller_status);
    setText('#brace-score', score.toFixed(1));
    setText('#side-brace-score', score.toFixed(0));
    setText('#brace-confidence', `${confidence.toFixed(1)}%`);
    const track = $('#brace-track');
    if (track) track.style.width = `${Math.min(score, 100)}%`;
    const counts = $('#brace-counts');
    if (counts) counts.innerHTML = Object.entries(decisionCounts)
      .slice(0, 4).map(([key, value]) => `<span>${escapeHtml(key)}: <b>${escapeHtml(value)}</b></span>`).join('');
    $('[data-brace-status]').forEach(element => {
      element.textContent = `${status} · ${freshness}`;
      element.dataset.braceFreshness = freshness.toLowerCase();
    });
    const impact = isEn
      ? `BRACE controls the paper portfolio in ${status} mode and currently assesses ${recommendations.length} active positions.`
      : `BRACE steruje portfelem paper w trybie ${status} i obecnie ocenia ${recommendations.length} aktywnych pozycji.`;
    setText('#brace-impact', impact);
    document.body.dataset.investmentBrace = freshness.toLowerCase();
  }

  function ensurePanelsAreUsable() {
    $$('.i10k-panel').forEach(panel => {
      if (!panel.dataset.panel) return;
      if (!panel.textContent.trim()) panel.innerHTML = `<div class="method-note">${T.fallback}</div>`;
    });
    document.body.dataset.investmentPanels = 'ready';
  }

  function showError(error) {
    document.body.dataset.investmentNetwork = 'degraded';
    if (state.loaded) return;
    setText('#data-status', T.unavailable);
    const panel = $('.i10k-panel.active') || $('.i10k-panel[data-panel="overview"]');
    if (panel && !panel.querySelector('.stable-room-error')) {
      const retryLabel = isEn ? 'Retry now' : 'Spróbuj ponownie';
      panel.insertAdjacentHTML('afterbegin', `<div class="error-box stable-room-error">${escapeHtml(T.error)}<br><small>${escapeHtml(error?.message || error || '')}</small><br><button type="button" class="text-button" data-investment-retry>${escapeHtml(retryLabel)}</button></div>`);
    }
    document.body.dataset.investmentData = 'error';
  }

  function scheduleRetry() {
    if (state.retryTimer) return;
    const delay = RETRY_DELAYS_MS[Math.min(state.retryAttempt, RETRY_DELAYS_MS.length - 1)];
    state.retryAttempt += 1;
    state.retryTimer = window.setTimeout(() => {
      state.retryTimer = null;
      loadPortfolio();
    }, delay);
  }

  function clearRetry() {
    state.retryAttempt = 0;
    if (state.retryTimer) window.clearTimeout(state.retryTimer);
    state.retryTimer = null;
  }

  function scheduleBraceRetry() {
    if (state.braceRetryTimer) return;
    const delay = RETRY_DELAYS_MS[Math.min(state.braceRetryAttempt, RETRY_DELAYS_MS.length - 1)];
    state.braceRetryAttempt += 1;
    state.braceRetryTimer = window.setTimeout(() => {
      state.braceRetryTimer = null;
      loadBrace();
    }, delay);
  }

  function clearBraceRetry() {
    state.braceRetryAttempt = 0;
    if (state.braceRetryTimer) window.clearTimeout(state.braceRetryTimer);
    state.braceRetryTimer = null;
  }

  function loadPortfolio() {
    if (state.portfolioPromise) return state.portfolioPromise;
    const cached = !state.loaded ? readCache('portfolio', validPortfolio) : null;
    if (cached) renderPortfolio(cached.payload, 'cache');
    state.portfolioPromise = (async () => {
      try {
        const portfolio = await fetchJsonResilient(portfolioUrl, validPortfolio);
        renderPortfolio(portfolio, 'network');
        writeCache('portfolio', portfolio);
        clearRetry();
        return true;
      } catch (error) {
        showError(error);
        scheduleRetry();
        return state.loaded;
      } finally {
        state.portfolioPromise = null;
      }
    })();
    return state.portfolioPromise;
  }

  function loadBrace() {
    if (state.bracePromise) return state.bracePromise;
    const cached = !state.brace ? readCache('brace', validBrace) : null;
    if (cached) renderBrace(cached.payload, 'cache');
    state.bracePromise = (async () => {
      try {
        const brace = await fetchJsonResilient(braceUrl, validBrace);
        renderBrace(brace, 'network');
        writeCache('brace', brace);
        clearBraceRetry();
        return true;
      } catch (_) {
        if (!state.brace) setText('#brace-impact', T.braceUnavailable);
        document.body.dataset.investmentBrace = state.brace
          ? freshnessState(state.brace, 'cache', 'generated_at').toLowerCase()
          : 'error';
        scheduleBraceRetry();
        return Boolean(state.brace);
      } finally {
        state.bracePromise = null;
      }
    })();
    return state.bracePromise;
  }

  function load() {
    ensurePanelsAreUsable();
    loadBrace();
    return loadPortfolio();
  }

  function start() {
    bindTabs();
    reorderHeader();
    const observer = new MutationObserver(() => {
      if (reorderHeader()) observer.disconnect();
    });
    if (!reorderHeader()) observer.observe(document.documentElement, { childList: true, subtree: true });
    window.setTimeout(() => observer.disconnect(), 8000);
    document.body.dataset.investmentController = CONTROLLER_VERSION;
    document.addEventListener('click', event => {
      if (!event.target.closest('[data-investment-retry]')) return;
      event.preventDefault();
      clearRetry();
      load();
    });
    window.addEventListener('online', () => load());
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && (
        document.body.dataset.investmentNetwork !== 'healthy'
        || document.body.dataset.investmentBrace !== 'ready'
      )) load();
    });
    load().catch(showError);
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
        currency: document.body.dataset.investmentCurrency || '',
        controller: document.body.dataset.investmentController || '',
        source: document.body.dataset.investmentDataSource || '',
        network: document.body.dataset.investmentNetwork || '',
        brace: document.body.dataset.investmentBrace || ''
      };
    }
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
})();
