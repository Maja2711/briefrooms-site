(function (root, factory) {
  'use strict';

  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;

  if (root.document) {
    var start = function () {
      api.load({
        document: root.document,
        fetchImpl: root.fetch ? root.fetch.bind(root) : null,
        lang: root.document.documentElement.lang === 'en' ? 'en' : 'pl',
        now: new Date(),
        console: root.console
      });
    };
    if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded', start, { once: true });
    else start();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var WEEKLY_ALLOWLIST = {
    eurusd: true,
    sp500_futures: true,
    btcusd: true
  };

  var CONFIG = {
    pl: {
      weeklyHref: '/pl/inwestycje/pozycje-tygodniowe.html',
      stockHref: '/pl/inwestycje/stock-trading.html',
      weeklyKicker: 'BRIEFROOMS TRADING ENGINE · WEEKLY',
      stockKicker: 'BRIEFROOMS TRADING ENGINE · STOCK TRADING',
      entry: 'WEJŚCIE', tp: 'TP', sl: 'SL', more: 'Szczegóły →', locale: 'pl-PL'
    },
    en: {
      weeklyHref: '/en/investing/open-weekly-positions.html',
      stockHref: '/en/investing/stock-trading.html',
      weeklyKicker: 'BRIEFROOMS TRADING ENGINE · WEEKLY',
      stockKicker: 'BRIEFROOMS TRADING ENGINE · STOCK TRADING',
      entry: 'ENTRY', tp: 'TP', sl: 'SL', more: 'Details →', locale: 'en-US'
    }
  };

  function configFor(lang) { return lang === 'en' ? CONFIG.en : CONFIG.pl; }

  function warsawDateParts(date) {
    var parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Europe/Warsaw',
      year: 'numeric', month: '2-digit', day: '2-digit'
    }).formatToParts(date || new Date());
    var out = {};
    parts.forEach(function (part) { if (part.type !== 'literal') out[part.type] = part.value; });
    return { year: Number(out.year), month: Number(out.month), day: Number(out.day) };
  }

  function isoWeekId(date) {
    var p = warsawDateParts(date || new Date());
    var d = new Date(Date.UTC(p.year, p.month - 1, p.day));
    var day = d.getUTCDay() || 7;
    d.setUTCDate(d.getUTCDate() + 4 - day);
    var isoYear = d.getUTCFullYear();
    var yearStart = new Date(Date.UTC(isoYear, 0, 1));
    var week = Math.ceil((((d - yearStart) / 86400000) + 1) / 7);
    return String(isoYear) + '-W' + String(week).padStart(2, '0');
  }

  function finiteNumber(value) {
    var n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function isCanonicalWeeklyInstrument(item) {
    return !!(item && WEEKLY_ALLOWLIST[String(item.instrument_id || '').toLowerCase()]);
  }

  function validWeeklyPosition(item) {
    if (!isCanonicalWeeklyInstrument(item) || item.trade_status !== 'open') return false;
    var direction = String(item.direction || '').toLowerCase();
    if (direction !== 'long' && direction !== 'short') return false;
    if (finiteNumber(item.entry_price) === null) return false;
    var risk = item.risk_plan || {};
    if (String(risk.direction || '').toLowerCase() !== direction) return false;
    return finiteNumber(risk.stop_loss_price) !== null && finiteNumber(risk.take_profit_price) !== null;
  }

  function conviction(item) {
    var value = finiteNumber(item && item.continuous_entry_decision && item.continuous_entry_decision.conviction);
    return value === null ? -Infinity : value;
  }

  function selectTopWeeklyPosition(items) {
    var candidates = (Array.isArray(items) ? items : []).filter(validWeeklyPosition);
    if (!candidates.length) return null;
    candidates.sort(function (a, b) {
      var diff = conviction(b) - conviction(a);
      if (diff) return diff;
      return Math.abs(finiteNumber(b.score) || 0) - Math.abs(finiteNumber(a.score) || 0);
    });
    return candidates[0];
  }

  function stockDirection(item) {
    var explicit = String(item && item.direction || '').toLowerCase();
    if (explicit === 'long' || explicit === 'short') return explicit;
    var entry = finiteNumber(item && item.entry);
    var stop = finiteNumber(item && item.stop);
    var target = finiteNumber(item && item.target);
    if (entry === null || stop === null || target === null) return null;
    if (target > entry && stop < entry) return 'long';
    if (target < entry && stop > entry) return 'short';
    return null;
  }

  function validStockPosition(item) {
    if (!item || String(item.status || '').toUpperCase() !== 'OPEN') return false;
    if (!String(item.ticker || item.symbol || '').trim()) return false;
    if (!stockDirection(item)) return false;
    return finiteNumber(item.entry) !== null &&
      finiteNumber(item.stop) !== null &&
      finiteNumber(item.target) !== null;
  }

  function stockScore(item) {
    var thesis = finiteNumber(item && item.thesis_score);
    var entry = finiteNumber(item && item.entry_score);
    return {
      thesis: thesis === null ? -Infinity : thesis,
      entry: entry === null ? -Infinity : entry
    };
  }

  function selectTopStockPosition(portfolio) {
    var markets = portfolio && portfolio.markets || {};
    var candidates = [];
    Object.keys(markets).forEach(function (market) {
      var positions = markets[market] && markets[market].open_positions;
      (Array.isArray(positions) ? positions : []).forEach(function (item) {
        if (!validStockPosition(item)) return;
        var copy = Object.assign({}, item);
        copy._market = market;
        candidates.push(copy);
      });
    });
    if (!candidates.length) return null;
    candidates.sort(function (a, b) {
      var sa = stockScore(a);
      var sb = stockScore(b);
      if (sb.thesis !== sa.thesis) return sb.thesis - sa.thesis;
      if (sb.entry !== sa.entry) return sb.entry - sa.entry;
      return String(b.opened_at || '').localeCompare(String(a.opened_at || ''));
    });
    return candidates[0];
  }

  function weeklySignal(item, lang) {
    if (!validWeeklyPosition(item)) return null;
    var cfg = configFor(lang || 'pl');
    return {
      kind: 'weekly',
      instrument_id: String(item.instrument_id || ''),
      label_pl: item.label_pl,
      label_en: item.label_en,
      ticker: item.symbol || item.instrument_id,
      direction: String(item.direction || '').toLowerCase(),
      entry_price: finiteNumber(item.entry_price),
      stop_loss_price: finiteNumber(item.risk_plan && item.risk_plan.stop_loss_price),
      take_profit_price: finiteNumber(item.risk_plan && item.risk_plan.take_profit_price),
      score: finiteNumber(item.score),
      conviction: conviction(item),
      href: cfg.weeklyHref
    };
  }

  function stockSignal(item, lang) {
    if (!validStockPosition(item)) return null;
    var cfg = configFor(lang || 'pl');
    return {
      kind: 'stock',
      instrument_id: String(item.ticker || item.symbol || '').toLowerCase(),
      label_pl: String(item.name || item.ticker || item.symbol || ''),
      label_en: String(item.name || item.ticker || item.symbol || ''),
      ticker: String(item.ticker || item.symbol || ''),
      direction: stockDirection(item),
      entry_price: finiteNumber(item.entry),
      stop_loss_price: finiteNumber(item.stop),
      take_profit_price: finiteNumber(item.target),
      score: finiteNumber(item.entry_score),
      thesis_score: finiteNumber(item.thesis_score),
      market: item._market || item.market || '',
      href: cfg.stockHref
    };
  }

  function chooseSignal(weeklyItems, portfolio, lang) {
    var weekly = selectTopWeeklyPosition(weeklyItems);
    if (weekly) return weeklySignal(weekly, lang);
    var stock = selectTopStockPosition(portfolio);
    if (stock) return stockSignal(stock, lang);
    return null;
  }

  function decimalsFor(signal) {
    return signal && signal.kind === 'weekly' && String(signal.instrument_id || '') === 'eurusd' ? 5 : 2;
  }

  function formatPrice(value, signal, lang) {
    var n = finiteNumber(value);
    if (n === null) return '—';
    var decimals = decimalsFor(signal);
    return new Intl.NumberFormat(configFor(lang).locale, {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals
    }).format(n);
  }

  function signalName(signal, lang) {
    var label = lang === 'en' ? (signal.label_en || signal.label_pl) : (signal.label_pl || signal.label_en);
    var ticker = String(signal.ticker || '').replace(/\.WA$/i, '');
    if (!label) return ticker || signal.instrument_id || '';
    if (signal.kind === 'stock' && ticker && String(label).toUpperCase().indexOf(ticker.toUpperCase()) === -1) {
      return ticker + ' · ' + String(label);
    }
    return String(label);
  }

  function kickerFor(signal, lang) {
    var cfg = configFor(lang);
    return signal.kind === 'weekly' ? cfg.weeklyKicker : cfg.stockKicker;
  }

  function injectStyle(document) {
    if (document.getElementById('home-market-signal-style')) return;
    var style = document.createElement('style');
    style.id = 'home-market-signal-style';
    style.textContent = [
      '.home-market-signal{margin-top:10px;display:flex;align-items:center;gap:12px;width:100%;min-height:42px;padding:9px 12px;border:1px solid rgba(225,162,255,.30);border-radius:14px;background:linear-gradient(90deg,rgba(225,162,255,.11),rgba(56,214,201,.055));box-shadow:inset 0 1px 0 rgba(255,255,255,.07);transition:border-color .18s ease,background .18s ease}',
      '.home-market-signal:hover{border-color:rgba(225,162,255,.52);background:linear-gradient(90deg,rgba(225,162,255,.16),rgba(56,214,201,.08))}',
      '.home-market-signal__kicker{flex:0 0 auto;color:#d9b7f7;font-size:9px;font-weight:950;letter-spacing:.07em;text-transform:uppercase}',
      '.home-market-signal__main{display:flex;align-items:center;gap:8px;min-width:0;flex:1}',
      '.home-market-signal__name{font-size:13px;font-weight:950;color:#f2f8ff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
      '.home-market-signal__side{flex:0 0 auto;font-size:9px;font-weight:950;border-radius:999px;padding:3px 6px;border:1px solid currentColor}',
      '.home-market-signal__side.long{color:#86ffb7;background:rgba(134,255,183,.07)}',
      '.home-market-signal__side.short{color:#ff9e9e;background:rgba(255,120,120,.07)}',
      '.home-market-signal__levels{display:flex;align-items:center;gap:10px;flex-wrap:wrap;color:#9fb2c8;font-size:10px}',
      '.home-market-signal__levels b{color:#dfeaf5;font-size:11px}',
      '.home-market-signal__cta{flex:0 0 auto;color:#75eee5;font-size:10px;font-weight:950}',
      '@media(max-width:760px){.home-market-signal{align-items:flex-start;flex-wrap:wrap}.home-market-signal__kicker{width:100%}.home-market-signal__main{min-width:170px}.home-market-signal__levels{order:3;width:100%;gap:8px}.home-market-signal__cta{margin-left:auto}}'
    ].join('');
    document.head.appendChild(style);
  }

  function removeSignal(document) {
    if (!document) return;
    var oldCard = document.getElementById('weekly-top-position');
    if (oldCard) oldCard.remove();
    var existing = document.getElementById('home-market-signal');
    if (existing) existing.remove();
  }

  function render(document, signal, lang) {
    if (!document || !signal) return false;
    var share = document.querySelector('.br-share-strip');
    var host = share && share.parentNode ? share.parentNode : document.querySelector('.main-head');
    if (!host) return false;
    removeSignal(document);
    injectStyle(document);

    var cfg = configFor(lang);
    var kickerText = kickerFor(signal, lang);
    var link = document.createElement('a');
    link.id = 'home-market-signal';
    link.className = 'home-market-signal';
    link.href = signal.href;
    link.setAttribute('aria-label', kickerText + ': ' + signalName(signal, lang));

    var kicker = document.createElement('span');
    kicker.className = 'home-market-signal__kicker';
    kicker.textContent = kickerText;
    link.appendChild(kicker);

    var main = document.createElement('span');
    main.className = 'home-market-signal__main';
    var name = document.createElement('span');
    name.className = 'home-market-signal__name';
    name.textContent = signalName(signal, lang);
    main.appendChild(name);
    var side = document.createElement('span');
    side.className = 'home-market-signal__side ' + signal.direction;
    side.textContent = String(signal.direction || '').toUpperCase();
    main.appendChild(side);
    link.appendChild(main);

    var levels = document.createElement('span');
    levels.className = 'home-market-signal__levels';
    var entry = document.createElement('span');
    entry.innerHTML = cfg.entry + ' <b>' + formatPrice(signal.entry_price, signal, lang) + '</b>';
    var tp = document.createElement('span');
    tp.innerHTML = cfg.tp + ' <b>' + formatPrice(signal.take_profit_price, signal, lang) + '</b>';
    var sl = document.createElement('span');
    sl.innerHTML = cfg.sl + ' <b>' + formatPrice(signal.stop_loss_price, signal, lang) + '</b>';
    levels.appendChild(entry);
    levels.appendChild(tp);
    levels.appendChild(sl);
    link.appendChild(levels);

    var cta = document.createElement('span');
    cta.className = 'home-market-signal__cta';
    cta.textContent = cfg.more;
    link.appendChild(cta);

    if (share && share.nextSibling) host.insertBefore(link, share.nextSibling);
    else host.appendChild(link);
    return true;
  }

  async function fetchJson(fetchImpl, url) {
    var response = await fetchImpl(url + (url.indexOf('?') === -1 ? '?' : '&') + 'v=' + Date.now(), { cache: 'no-store' });
    if (!response || !response.ok) throw new Error('market signal request failed: ' + url);
    return response.json();
  }

  async function load(options) {
    var document = options && options.document;
    var fetchImpl = options && options.fetchImpl;
    var lang = options && options.lang === 'en' ? 'en' : 'pl';
    var now = options && options.now || new Date();
    var logger = options && options.console || { warn: function () {} };
    if (!document || typeof fetchImpl !== 'function') return false;

    var weekId = isoWeekId(now);
    try {
      var weeklyData = await fetchJson(fetchImpl, '/data/investments/weekly/' + weekId + '.json');
      var weekly = selectTopWeeklyPosition(weeklyData && weeklyData.instruments);
      if (weekly) return render(document, weeklySignal(weekly, lang), lang);
    } catch (error) {
      logger.warn('BriefRooms canonical weekly market signal unavailable.', error);
    }

    try {
      var portfolio = await fetchJson(fetchImpl, '/data/investments/stock_trading_portfolio.json');
      var stock = selectTopStockPosition(portfolio);
      if (stock) return render(document, stockSignal(stock, lang), lang);
    } catch (error) {
      logger.warn('BriefRooms Stock Trading portfolio signal unavailable.', error);
    }

    removeSignal(document);
    return false;
  }

  return {
    WEEKLY_ALLOWLIST: WEEKLY_ALLOWLIST,
    chooseSignal: chooseSignal,
    conviction: conviction,
    formatPrice: formatPrice,
    isCanonicalWeeklyInstrument: isCanonicalWeeklyInstrument,
    isoWeekId: isoWeekId,
    load: load,
    render: render,
    selectTopStockPosition: selectTopStockPosition,
    selectTopWeeklyPosition: selectTopWeeklyPosition,
    stockDirection: stockDirection,
    stockSignal: stockSignal,
    validStockPosition: validStockPosition,
    validWeeklyPosition: validWeeklyPosition,
    warsawDateParts: warsawDateParts,
    weeklySignal: weeklySignal
  };
});