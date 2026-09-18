(function () {
  'use strict';

  var STOCK_LABEL = 'BRIEFROOMS STOCK TRADING · OPEN';
  var WEEKLY_LABEL = 'BRIEFROOMS TRADING ENGINE · WEEKLY';

  function text(node) {
    return String(node && node.textContent || '').trim().toUpperCase();
  }

  function isCanonicalWeeklyName(value) {
    var s = String(value || '').toUpperCase();
    return s.indexOf('EUR/USD') !== -1 ||
      s.indexOf('EURUSD') !== -1 ||
      s.indexOf('S&P 500') !== -1 ||
      s.indexOf('SP500') !== -1 ||
      s.indexOf('ES=F') !== -1 ||
      s.indexOf('BTC/USD') !== -1 ||
      s.indexOf('BTC-USD') !== -1 ||
      s.indexOf('BTCUSD') !== -1;
  }

  function enforce() {
    var link = document.getElementById('home-market-signal');
    if (!link) return;

    var kicker = link.querySelector('.home-market-signal__kicker');
    var name = link.querySelector('.home-market-signal__name');
    if (!kicker || !name) return;

    var kind = String(link.getAttribute('data-signal-kind') || '').toLowerCase();
    var href = String(link.getAttribute('href') || '').toLowerCase();
    var canonicalWeekly = isCanonicalWeeklyName(text(name));

    var stock = kind === 'stock' ||
      href.indexOf('/stock-trading') !== -1 ||
      (!canonicalWeekly && text(kicker).indexOf('WEEKLY') !== -1);

    var weekly = !stock && (
      kind === 'weekly' ||
      href.indexOf('pozycje-tygodniowe') !== -1 ||
      href.indexOf('open-weekly-positions') !== -1
    );

    if (stock) {
      link.setAttribute('data-signal-kind', 'stock');
      if (kicker.textContent !== STOCK_LABEL) kicker.textContent = STOCK_LABEL;
      var lang = document.documentElement.lang === 'en' ? 'en' : 'pl';
      var target = lang === 'en' ? '/en/investing/stock-trading.html' : '/pl/inwestycje/stock-trading.html';
      if (link.getAttribute('href') !== target) link.setAttribute('href', target);
      return;
    }

    if (weekly && canonicalWeekly) {
      link.setAttribute('data-signal-kind', 'weekly');
      if (kicker.textContent !== WEEKLY_LABEL) kicker.textContent = WEEKLY_LABEL;
    }
  }

  function start() {
    enforce();
    if (typeof MutationObserver === 'function') {
      new MutationObserver(enforce).observe(document.documentElement, {
        childList: true,
        subtree: true,
        characterData: true,
        attributes: true,
        attributeFilter: ['href', 'data-signal-kind']
      });
    }
    [100, 500, 1500, 4000].forEach(function (ms) {
      setTimeout(enforce, ms);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();