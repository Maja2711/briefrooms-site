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
    if (root.document.readyState === 'loading') {
      root.document.addEventListener('DOMContentLoaded', start, { once: true });
    } else {
      start();
    }
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var CONFIG = {
    pl: {
      href: '/pl/inwestycje/pozycje-tygodniowe.html',
      kicker: 'POZYCJA TYGODNIA',
      entry: 'WEJŚCIE',
      tp: 'TP',
      sl: 'SL',
      locale: 'pl-PL'
    },
    en: {
      href: '/en/investing/open-weekly-positions.html',
      kicker: 'WEEKLY TOP POSITION',
      entry: 'ENTRY',
      tp: 'TP',
      sl: 'SL',
      locale: 'en-GB'
    }
  };

  function configFor(lang) {
    return lang === 'en' ? CONFIG.en : CONFIG.pl;
  }

  function warsawDateParts(date) {
    var parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Europe/Warsaw',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit'
    }).formatToParts(date || new Date());
    var out = {};
    parts.forEach(function (part) {
      if (part.type === 'year' || part.type === 'month' || part.type === 'day') {
        out[part.type] = Number(part.value);
      }
    });
    return out;
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

  function decimalsFor(item) {
    return String(item && item.instrument_id || '') === 'eurusd' ? 5 : 2;
  }

  function formatPrice(value, item, lang) {
    var n = finiteNumber(value);
    if (n === null) return '—';
    var decimals = decimalsFor(item);
    return new Intl.NumberFormat(configFor(lang).locale, {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals
    }).format(n);
  }

  function injectStyle(document) {
    if (document.getElementById('weekly-top-position-style')) return;
    var style = document.createElement('style');
    style.id = 'weekly-top-position-style';
    style.textContent = [
      '.weekly-top-position{width:min(370px,100%);display:block;border:1px solid rgba(225,162,255,.30);border-radius:18px;padding:12px 14px;background:radial-gradient(180px 100px at 8% 0%,rgba(225,162,255,.15),transparent 70%),linear-gradient(145deg,rgba(255,255,255,.09),rgba(8,20,34,.76));box-shadow:0 14px 34px rgba(0,0,0,.22),inset 0 1px 0 rgba(255,255,255,.10);transition:transform .18s ease,border-color .18s ease}',
      '.weekly-top-position:hover{transform:translateY(-2px);border-color:rgba(225,162,255,.52)}',
      '.weekly-top-position__kicker{display:block;margin-bottom:5px;color:#cfa1f5;font-size:9px;font-weight:950;letter-spacing:.10em}',
      '.weekly-top-position__title{display:flex;align-items:center;gap:8px;font-size:15px;font-weight:950;line-height:1.15}',
      '.weekly-top-position__side{font-size:11px;font-weight:950;border-radius:999px;padding:4px 7px;border:1px solid currentColor}',
      '.weekly-top-position__side.long{color:#86ffb7;background:rgba(134,255,183,.07)}',
      '.weekly-top-position__side.short{color:#ff9e9e;background:rgba(255,120,120,.07)}',
      '.weekly-top-position__levels{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:10px}',
      '.weekly-top-position__level{min-width:0;padding-top:8px;border-top:1px solid rgba(255,255,255,.10)}',
      '.weekly-top-position__level b{display:block;color:#8197aa;font-size:8px;letter-spacing:.08em;margin-bottom:2px}',
      '.weekly-top-position__level span{display:block;color:#eef7ff;font-size:12px;font-weight:900;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
      '@media(max-width:900px){.main-head .section-head{align-items:flex-start;flex-direction:column}.weekly-top-position{margin-top:8px}}'
    ].join('');
    document.head.appendChild(style);
  }

  function render(document, item, lang) {
    var head = document.querySelector('.main-head .section-head');
    if (!head || !item) return false;
    var existing = document.getElementById('weekly-top-position');
    if (existing) existing.remove();
    injectStyle(document);

    var cfg = configFor(lang);
    var link = document.createElement('a');
    link.id = 'weekly-top-position';
    link.className = 'weekly-top-position';
    link.href = cfg.href;
    link.setAttribute('aria-label', cfg.kicker + ': ' + String(item.label_en || item.label_pl || item.instrument_id || ''));

    var kicker = document.createElement('span');
    kicker.className = 'weekly-top-position__kicker';
    kicker.textContent = cfg.kicker;
    link.appendChild(kicker);

    var title = document.createElement('div');
    title.className = 'weekly-top-position__title';
    var name = document.createElement('span');
    name.textContent = String(lang === 'en' ? (item.label_en || item.label_pl) : (item.label_pl || item.label_en) || item.instrument_id || '');
    title.appendChild(name);
    var side = document.createElement('span');
    side.className = 'weekly-top-position__side ' + String(item.direction).toLowerCase();
    side.textContent = String(item.direction).toUpperCase();
    title.appendChild(side);
    link.appendChild(title);

    var levels = document.createElement('div');
    levels.className = 'weekly-top-position__levels';
    [
      [cfg.entry, item.entry_price],
      [cfg.tp, item.risk_plan.take_profit_price],
      [cfg.sl, item.risk_plan.stop_loss_price]
    ].forEach(function (pair) {
      var level = document.createElement('div');
      level.className = 'weekly-top-position__level';
      var label = document.createElement('b');
      label.textContent = pair[0];
      var value = document.createElement('span');
      value.textContent = formatPrice(pair[1], item, lang);
      level.appendChild(label);
      level.appendChild(value);
      levels.appendChild(level);
    });
    link.appendChild(levels);
    head.appendChild(link);
    return true;
  }

  async function load(options) {
    var document = options && options.document;
    var fetchImpl = options && options.fetchImpl;
    var lang = options && options.lang === 'en' ? 'en' : 'pl';
    var logger = options && options.console || { warn: function () {} };
    if (!document || typeof fetchImpl !== 'function') return false;
    var weekId = isoWeekId(options && options.now || new Date());
    try {
      var response = await fetchImpl('/data/investments/weekly/' + weekId + '.json?v=' + Date.now(), { cache: 'no-store' });
      if (!response || !response.ok) throw new Error('weekly position request failed');
      var data = await response.json();
      var top = selectTopPosition(data && data.instruments);
      if (!top) return false;
      return render(document, top, lang);
    } catch (error) {
      logger.warn('BriefRooms weekly top-position shortcut unavailable.', error);
      return false;
    }
  }

  return {
    conviction: conviction,
    formatPrice: formatPrice,
    isoWeekId: isoWeekId,
    load: load,
    render: render,
    selectTopPosition: selectTopPosition,
    validPosition: validPosition,
    warsawDateParts: warsawDateParts
  };
});
