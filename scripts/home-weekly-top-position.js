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

  var CONFIG = {
    pl: {
      weeklyHref: '/pl/inwestycje/pozycje-tygodniowe.html',
      weeklyKicker: 'Faworyzowana pozycja · WEEKLY',
      entry: 'WEJŚCIE', tp: 'TP', sl: 'SL', more: 'Szczegóły →', locale: 'pl-PL'
    },
    en: {
      weeklyHref: '/en/investing/open-weekly-positions.html',
      weeklyKicker: 'Favored position · WEEKLY',
      entry: 'ENTRY', tp: 'TP', sl: 'SL', more: 'Details →', locale: 'en-US'
    }
  };

  var MARKETS = {
    gpw: {
      url: '/data/investments/gpw_daily_pick.json',
      decision: 'TRANSAKCJA',
      timeZone: 'Europe/Warsaw',
      href: '/pl/inwestycje/portfel-10k.html#overview',
      kicker: { pl: 'Daily Trade · GPW', en: 'Daily Trade · GPW' }
    },
    us: {
      url: '/data/investments/us_daily_stock.json',
      decision: 'TRADE',
      timeZone: 'America/New_York',
      href: '/en/investing/portfolio-10k.html#overview',
      kicker: { pl: 'Daily Trade · US MARKET', en: 'Daily Trade · US MARKET' }
    }
  };

  function configFor(lang) { return lang === 'en' ? CONFIG.en : CONFIG.pl; }

  function zonedDate(date, timeZone) {
    var parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: timeZone,
      year: 'numeric', month: '2-digit', day: '2-digit'
    }).formatToParts(date || new Date());
    var out = {};
    parts.forEach(function (part) { if (part.type !== 'literal') out[part.type] = part.value; });
    return out.year + '-' + out.month + '-' + out.day;
  }

  function warsawDateParts(date) {
    var value = zonedDate(date || new Date(), 'Europe/Warsaw').split('-');
    return { year: Number(value[0]), month: Number(value[1]), day: Number(value[2]) };
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

  function validPosition(item) {
    if (!item || item.trade_status !== 'open') return false;
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

  function selectTopPosition(items) {
    var candidates = (Array.isArray(items) ? items : []).filter(validPosition);
    if (!candidates.length) return null;
    candidates.sort(function (a, b) {
      var diff = conviction(b) - conviction(a);
      if (diff) return diff;
      return Math.abs(finiteNumber(b.score) || 0) - Math.abs(finiteNumber(a.score) || 0);
    });
    return candidates[0];
  }

  function decimalsFor(item) { return String(item && item.instrument_id || '') === 'eurusd' ? 5 : 2; }

  function formatPrice(value, item, lang) {
    var n = finiteNumber(value);
    if (n === null) return '—';
    var decimals = item && item.kind === 'daily' ? 2 : decimalsFor(item);
    return new Intl.NumberFormat(configFor(lang).locale, {
      minimumFractionDigits: decimals, maximumFractionDigits: decimals
    }).format(n);
  }

  function formatEntry(signal, lang) {
    if (signal.kind === 'daily' && Array.isArray(signal.entry_zone) && signal.entry_zone.length >= 2) {
      return formatPrice(signal.entry_zone[0], signal, lang) + '–' + formatPrice(signal.entry_zone[1], signal, lang);
    }
    return formatPrice(signal.entry_price, signal, lang);
  }

  function dailySignal(payload, market, now) {
    var cfg = MARKETS[market];
    if (!cfg || !payload || typeof payload !== 'object' || payload.decision !== cfg.decision) return null;
    if (now && String(payload.date || '') !== zonedDate(now, cfg.timeZone)) return null;
    var outcome = payload.outcome || {};
    if (String(outcome.status || '').toUpperCase() === 'RESOLVED') return null;
    var selection = payload.selection || {};
    var entry = Array.isArray(selection.entry_zone) ? selection.entry_zone : [];
    var low = finiteNumber(entry[0]);
    var high = finiteNumber(entry[1]);
    var stop = finiteNumber(selection.stop);
    var target = finiteNumber(selection.target);
    if ((!selection.ticker && !selection.symbol) || low === null || high === null || stop === null || target === null) return null;
    return {
      kind: 'daily', market: market, source: market + '_daily',
      instrument_id: String(selection.ticker || selection.symbol || '').toLowerCase(),
      label_pl: String(selection.name || selection.ticker || selection.symbol || ''),
      label_en: String(selection.name || selection.ticker || selection.symbol || ''),
      ticker: String(selection.ticker || selection.symbol || ''),
      direction: 'long', entry_zone: [low, high], entry_price: finiteNumber(selection.reference_price),
      stop_loss_price: stop, take_profit_price: target, score: finiteNumber(selection.score),
      valid_until: String(selection.valid_until || ''), payload_date: String(payload.date || ''),
      href: cfg.href
    };
  }

  function weeklySignal(item, lang) {
    if (!validPosition(item)) return null;
    var cfg = configFor(lang || 'pl');
    return {
      kind: 'weekly', market: 'weekly', source: 'weekly',
      instrument_id: item.instrument_id, label_pl: item.label_pl, label_en: item.label_en,
      ticker: item.symbol || item.instrument_id, direction: String(item.direction || '').toLowerCase(),
      entry_price: finiteNumber(item.entry_price),
      stop_loss_price: finiteNumber(item.risk_plan && item.risk_plan.stop_loss_price),
      take_profit_price: finiteNumber(item.risk_plan && item.risk_plan.take_profit_price),
      score: finiteNumber(item.score), conviction: conviction(item), href: cfg.weeklyHref
    };
  }

  function marketOrder(lang) { return lang === 'en' ? ['us', 'gpw'] : ['gpw', 'us']; }

  function chooseSignal(weeklyItems, dailyPayloads, lang, now) {
    var top = selectTopPosition(weeklyItems);
    if (top) return weeklySignal(top, lang);
    var payloads = dailyPayloads || {};
    var order = marketOrder(lang);
    for (var i = 0; i < order.length; i += 1) {
      var signal = dailySignal(payloads[order[i]], order[i], now);
      if (signal) return signal;
    }
    return null;
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

  function signalName(signal, lang) {
    var label = lang === 'en' ? (signal.label_en || signal.label_pl) : (signal.label_pl || signal.label_en);
    var ticker = String(signal.ticker || '').replace(/\.WA$/i, '');
    if (!label) return ticker || signal.instrument_id || '';
    if (signal.kind === 'daily' && ticker && label.toUpperCase().indexOf(ticker.toUpperCase()) === -1) return ticker + ' · ' + label;
    return String(label);
  }

  function kickerFor(signal, lang) {
    if (signal.kind === 'weekly') return configFor(lang).weeklyKicker;
    var market = MARKETS[signal.market];
    return market ? market.kicker[lang === 'en' ? 'en' : 'pl'] : 'Daily Trade';
  }

  function render(document, signal, lang) {
    if (!document || !signal) return false;
    var share = document.querySelector('.br-share-strip');
    var host = share && share.parentNode ? share.parentNode : document.querySelector('.main-head');
    if (!host) return false;
    var oldCard = document.getElementById('weekly-top-position');
    if (oldCard) oldCard.remove();
    var existing = document.getElementById('home-market-signal');
    if (existing) existing.remove();
    injectStyle(document);

    var cfg = configFor(lang);
    var kickerText = kickerFor(signal, lang);
    var link = document.createElement('a');
    link.id = 'home-market-signal';
    link.className = 'home-market-signal';
    link.href = signal.href || cfg.weeklyHref;
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
    side.textContent = String(signal.direction || 'long').toUpperCase();
    main.appendChild(side);
    link.appendChild(main);

    var levels = document.createElement('span');
    levels.className = 'home-market-signal__levels';
    var entry = document.createElement('span');
    entry.innerHTML = cfg.entry + ' <b>' + formatEntry(signal, lang) + '</b>';
    var tp = document.createElement('span');
    tp.innerHTML = cfg.tp + ' <b>' + formatPrice(signal.take_profit_price, signal, lang) + '</b>';
    var sl = document.createElement('span');
    sl.innerHTML = cfg.sl + ' <b>' + formatPrice(signal.stop_loss_price, signal, lang) + '</b>';
    levels.appendChild(entry); levels.appendChild(tp); levels.appendChild(sl);
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
      var top = selectTopPosition(weeklyData && weeklyData.instruments);
      if (top) return render(document, weeklySignal(top, lang), lang);
    } catch (error) {
      logger.warn('BriefRooms weekly market signal unavailable.', error);
    }

    var order = marketOrder(lang);
    for (var i = 0; i < order.length; i += 1) {
      var market = order[i];
      try {
        var payload = await fetchJson(fetchImpl, MARKETS[market].url);
        var daily = dailySignal(payload, market, now);
        if (daily) return render(document, daily, lang);
      } catch (error) {
        logger.warn('BriefRooms ' + market + ' daily market signal unavailable.', error);
      }
    }
    return false;
  }

  return {
    chooseSignal: chooseSignal,
    conviction: conviction,
    dailySignal: dailySignal,
    formatEntry: formatEntry,
    formatPrice: formatPrice,
    isoWeekId: isoWeekId,
    load: load,
    marketOrder: marketOrder,
    render: render,
    selectTopPosition: selectTopPosition,
    validPosition: validPosition,
    warsawDateParts: warsawDateParts,
    weeklySignal: weeklySignal,
    zonedDate: zonedDate
  };
});