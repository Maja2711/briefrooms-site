(function (root, factory) {
  'use strict';

  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.BriefRoomsSiteHeader = api;

  if (root.document) {
    if (root.document.readyState === 'loading') {
      root.document.addEventListener('DOMContentLoaded', function () {
        api.init(root.document, root.location, root);
      }, { once: true });
    } else {
      api.init(root.document, root.location, root);
    }
  }
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  var NAVIGATION = {
    pl: [
      { section: 'news', label: 'Aktualności', href: '/pl/aktualnosci.html', icon: '▤' },
      { section: 'geopolitics', label: 'Geopolityka', href: '/pl/geopolityka.html', icon: '◎' },
      { section: 'health', label: 'Zdrowie', href: '/pl/zdrowie.html', icon: '♡' },
      { section: 'science', label: 'Nauka', href: '/pl/nauka.html', icon: '⚗' },
      { section: 'investing', label: 'Inwestycje', href: '/pl/inwestycje.html', icon: '↗' },
      { section: 'about', label: 'O nas', href: '/pl/o-projekcie.html', icon: 'i' }
    ],
    en: [
      { section: 'news', label: 'News', href: '/en/news.html', icon: '▤' },
      { section: 'geopolitics', label: 'Geopolitics', href: '/en/geopolitics.html', icon: '◎' },
      { section: 'health', label: 'Health', href: '/en/health.html', icon: '♡' },
      { section: 'science', label: 'Science', href: '/en/science.html', icon: '⚗' },
      { section: 'investing', label: 'Investing', href: '/en/investing.html', icon: '↗' },
      { section: 'about', label: 'About', href: '/en/about.html', icon: 'i' }
    ]
  };

  var COUNTERPARTS = {
    '/pl/': '/en/',
    '/en/': '/pl/',
    '/pl/aktualnosci.html': '/en/news.html',
    '/en/news.html': '/pl/aktualnosci.html',
    '/pl/geopolityka.html': '/en/geopolitics.html',
    '/en/geopolitics.html': '/pl/geopolityka.html',
    '/pl/zdrowie.html': '/en/health.html',
    '/en/health.html': '/pl/zdrowie.html',
    '/pl/nauka.html': '/en/science.html',
    '/en/science.html': '/pl/nauka.html',
    '/pl/inwestycje.html': '/en/investing.html',
    '/en/investing.html': '/pl/inwestycje.html',
    '/pl/o-projekcie.html': '/en/about.html',
    '/en/about.html': '/pl/o-projekcie.html',
    '/pl/kontakt.html': '/en/contact.html',
    '/en/contact.html': '/pl/kontakt.html',
    '/pl/metodologia.html': '/en/methodology.html',
    '/en/methodology.html': '/pl/metodologia.html',
    '/pl/geo/rosja-drony-paliwa-zboze.html': '/en/geo/russia-drones-fuel-grain.html',
    '/en/geo/russia-drones-fuel-grain.html': '/pl/geo/rosja-drony-paliwa-zboze.html',
    '/pl/geo/upadek-hegemona.html': '/en/geo/falling-hegemon.html',
    '/en/geo/falling-hegemon.html': '/pl/geo/upadek-hegemona.html',
    '/pl/geo/usa-chiny-2025.html': '/en/geo/usa-china-2025.html',
    '/pl/usa-chiny-2025.html': '/en/geo/usa-china-2025.html',
    '/en/geo/usa-china-2025.html': '/pl/geo/usa-chiny-2025.html',
    '/pl/geo/ziemie-rzadkie.html': '/en/geo/rare-earths.html',
    '/en/geo/rare-earths.html': '/pl/geo/ziemie-rzadkie.html',
    '/pl/zdrowie/cholesterol.html': '/en/health/cholesterol.html',
    '/en/health/cholesterol.html': '/pl/zdrowie/cholesterol.html',
    '/pl/zdrowie/kalkulator-cholesterolu.html': '/en/health/cholesterol-calculator.html',
    '/en/health/cholesterol-calculator.html': '/pl/zdrowie/kalkulator-cholesterolu.html',
    '/pl/zdrowie/protokol-zywnosciowy.html': '/en/health/food-protocol.html',
    '/en/health/food-protocol.html': '/pl/zdrowie/protokol-zywnosciowy.html',
    '/pl/nauka/ciemny-tlen.html': '/en/science/dark-oxygen.html',
    '/en/science/dark-oxygen.html': '/pl/nauka/ciemny-tlen.html',
    '/pl/nauka/baterie-sodowo-jonowe.html': '/en/science/sodium-ion-batteries.html',
    '/en/science/sodium-ion-batteries.html': '/pl/nauka/baterie-sodowo-jonowe.html',
    '/pl/nauka/mucholowka-biomechanika.html': '/en/science/venus-flytrap-biomechanics.html',
    '/en/science/venus-flytrap-biomechanics.html': '/pl/nauka/mucholowka-biomechanika.html',
    '/pl/inwestycje/portfel-10k.html': '/en/investing/portfolio-10k.html',
    '/en/investing/portfolio-10k.html': '/pl/inwestycje/portfel-10k.html',
    '/pl/inwestycje/prognozy-tygodniowe.html': '/en/investing/weekly-forecasts.html',
    '/en/investing/weekly-forecasts.html': '/pl/inwestycje/prognozy-tygodniowe.html',
    '/pl/inwestycje/spx-scenariusze-2026.html': '/en/investing/spx-scenarios-2026.html',
    '/en/investing/spx-scenarios-2026.html': '/pl/inwestycje/spx-scenariusze-2026.html'
  };

  function normalizePath(pathname) {
    var path = String(pathname || '/').split(/[?#]/, 1)[0].replace(/\\/g, '/');
    if (!path.startsWith('/')) path = '/' + path;
    path = path.replace(/\/+/g, '/');
    path = path.replace(/^\/(pl|en)\/index\.html$/i, '/$1/');
    return path;
  }

  function languageForPath(pathname, documentLanguage) {
    var path = normalizePath(pathname);
    if (path.indexOf('/en/') === 0) return 'en';
    if (path.indexOf('/pl/') === 0) return 'pl';
    return String(documentLanguage || '').toLowerCase().indexOf('en') === 0 ? 'en' : 'pl';
  }

  function sectionForPath(pathname) {
    var path = normalizePath(pathname);
    if (/^\/(pl\/aktualnosci|en\/news)(?:\.html|\/|$)/.test(path)) return 'news';
    if (/^\/pl\/(?:geopolityka(?:\.html)?|geo\/|usa-chiny-2025\.html)/.test(path) ||
        /^\/en\/(?:geopolitics(?:\.html)?|geo\/)/.test(path)) return 'geopolitics';
    if (/^\/(?:pl\/zdrowie|en\/health)(?:\.html|\/|$)/.test(path)) return 'health';
    if (/^\/(?:pl\/nauka|en\/science)(?:\.html|\/|$)/.test(path)) return 'science';
    if (/^\/(?:pl\/inwestycje|en\/investing)(?:\.html|\/|$)/.test(path)) return 'investing';
    if (/^\/pl\/(?:o-projekcie|kontakt|metodologia)(?:\.html|\/|$)/.test(path) ||
        /^\/en\/(?:about|contact|methodology)(?:\.html|\/|$)/.test(path)) return 'about';
    return '';
  }

  function counterpartForPath(pathname, alternatePath) {
    var path = normalizePath(pathname);
    if (alternatePath) return normalizePath(alternatePath);
    if (COUNTERPARTS[path]) return COUNTERPARTS[path];
    return languageForPath(path) === 'en' ? '/pl/' : '/en/';
  }

  function alternatePathFromDocument(doc, targetLanguage, locationObject) {
    var selector = 'link[rel~="alternate"][hreflang="' + targetLanguage + '"]';
    var link = doc.querySelector(selector);
    if (!link || !link.getAttribute('href')) return '';
    try {
      var url = new URL(link.getAttribute('href'), locationObject.href);
      if (url.origin !== locationObject.origin && url.hostname !== 'briefrooms.com' && url.hostname !== 'www.briefrooms.com') return '';
      return url.pathname;
    } catch (error) {
      return '';
    }
  }

  function createElement(doc, tag, className, textContent) {
    var element = doc.createElement(tag);
    if (className) element.className = className;
    if (textContent) element.textContent = textContent;
    return element;
  }

  function init(doc, locationObject, windowObject) {
    if (!doc.body) return null;

    var host = doc.getElementById('site-header');
    if (!host) {
      host = doc.createElement('header');
      host.id = 'site-header';
      doc.body.insertBefore(host, doc.body.firstChild);
    }
    if (host.getAttribute('data-ready') === 'true') return host;

    var language = languageForPath(locationObject.pathname, doc.documentElement.lang);
    var otherLanguage = language === 'pl' ? 'en' : 'pl';
    var activeSection = sectionForPath(locationObject.pathname);
    var labels = language === 'pl' ? {
      header: 'Nawigacja serwisu',
      navigation: 'Wejścia do pokoi BriefRooms',
      home: 'BriefRooms — strona główna',
      open: 'Otwórz menu pokoi',
      close: 'Zamknij menu pokoi',
      switchLanguage: 'Przejdź do wersji angielskiej',
      roomPrefix: 'Otwórz pokój'
    } : {
      header: 'Site navigation',
      navigation: 'BriefRooms room entrances',
      home: 'BriefRooms home',
      open: 'Open room menu',
      close: 'Close room menu',
      switchLanguage: 'Switch to Polish',
      roomPrefix: 'Open room'
    };

    host.className = 'br-site-header';
    host.setAttribute('data-ready', 'true');
    host.setAttribute('data-section', activeSection || 'none');
    host.setAttribute('aria-label', labels.header);
    host.textContent = '';

    var inner = createElement(doc, 'div', 'br-site-header__inner');
    var brand = createElement(doc, 'a', 'br-site-header__brand');
    brand.href = '/' + language + '/';
    brand.setAttribute('aria-label', labels.home);
    brand.appendChild(createElement(doc, 'span', 'br-site-header__mark', 'BRs'));
    brand.appendChild(createElement(doc, 'span', 'br-site-header__name', 'BriefRooms'));

    var nav = createElement(doc, 'nav', 'br-site-header__nav');
    nav.id = 'br-site-navigation';
    nav.setAttribute('aria-label', labels.navigation);
    NAVIGATION[language].forEach(function (item) {
      var link = createElement(doc, 'a', 'br-site-header__link');
      link.href = item.href;
      link.setAttribute('data-section', item.section);
      link.setAttribute('aria-label', labels.roomPrefix + ' ' + item.label);

      var icon = createElement(doc, 'span', 'br-site-header__icon', item.icon);
      icon.setAttribute('aria-hidden', 'true');
      var label = createElement(doc, 'span', 'br-site-header__label', item.label);
      var handle = createElement(doc, 'span', 'br-site-header__handle');
      handle.setAttribute('aria-hidden', 'true');
      link.appendChild(icon);
      link.appendChild(label);
      link.appendChild(handle);

      if (item.section === activeSection) {
        link.classList.add('is-active');
        link.setAttribute('aria-current', 'page');
      }
      nav.appendChild(link);
    });

    var actions = createElement(doc, 'div', 'br-site-header__actions');
    var alternatePath = alternatePathFromDocument(doc, otherLanguage, locationObject);
    var languageLink = createElement(doc, 'a', 'br-site-header__lang', otherLanguage.toUpperCase());
    languageLink.href = counterpartForPath(locationObject.pathname, alternatePath);
    languageLink.setAttribute('hreflang', otherLanguage);
    languageLink.setAttribute('lang', otherLanguage);
    languageLink.setAttribute('aria-label', labels.switchLanguage);

    var toggle = createElement(doc, 'button', 'br-site-header__toggle');
    toggle.type = 'button';
    toggle.setAttribute('aria-controls', nav.id);
    toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-label', labels.open);
    toggle.appendChild(createElement(doc, 'span', 'br-site-header__toggle-lines'));

    actions.appendChild(languageLink);
    actions.appendChild(toggle);
    inner.appendChild(brand);
    inner.appendChild(nav);
    inner.appendChild(actions);
    host.appendChild(inner);

    function closeMenu(returnFocus) {
      if (!host.classList.contains('is-open')) return;
      host.classList.remove('is-open');
      toggle.setAttribute('aria-expanded', 'false');
      toggle.setAttribute('aria-label', labels.open);
      if (returnFocus) toggle.focus();
    }

    function openMenu() {
      host.classList.add('is-open');
      toggle.setAttribute('aria-expanded', 'true');
      toggle.setAttribute('aria-label', labels.close);
      var firstLink = nav.querySelector('a');
      if (firstLink && windowObject.requestAnimationFrame) {
        windowObject.requestAnimationFrame(function () { firstLink.focus(); });
      }
    }

    toggle.addEventListener('click', function () {
      if (host.classList.contains('is-open')) closeMenu(false);
      else openMenu();
    });

    nav.addEventListener('click', function (event) {
      if (event.target.closest('a')) closeMenu(false);
    });

    doc.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') closeMenu(true);
    });

    doc.addEventListener('click', function (event) {
      if (host.classList.contains('is-open') && !host.contains(event.target)) closeMenu(false);
    });

    if (windowObject.matchMedia) {
      var mobileQuery = windowObject.matchMedia('(max-width: 819px)');
      var onViewportChange = function (event) { if (!event.matches) closeMenu(false); };
      if (mobileQuery.addEventListener) mobileQuery.addEventListener('change', onViewportChange);
      else if (mobileQuery.addListener) mobileQuery.addListener(onViewportChange);
    }

    doc.documentElement.classList.add('br-has-site-header');
    return host;
  }

  return {
    navigation: NAVIGATION,
    counterparts: COUNTERPARTS,
    normalizePath: normalizePath,
    languageForPath: languageForPath,
    sectionForPath: sectionForPath,
    counterpartForPath: counterpartForPath,
    init: init
  };
});

(function (root) {
  'use strict';
  if (!root || !root.document || !root.fetch) return;

  var doc = root.document;
  var DATA_URL = '/data/investments/daily_market_alert.json';

  function ready(callback) {
    if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', callback, { once: true });
    else callback();
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (character) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[character];
    });
  }

  function localized(value, language) {
    if (value && typeof value === 'object') return value[language] || value.en || value.pl || '';
    return value == null ? '' : value;
  }

  function normalizePath(pathname) {
    return String(pathname || '').split(/[?#]/, 1)[0].replace(/\/+/g, '/');
  }

  function isInvestmentHub(pathname) {
    var path = normalizePath(pathname);
    return path === '/pl/inwestycje.html' || path === '/en/investing.html';
  }

  function injectStyles() {
    if (doc.getElementById('br-daily-market-alert-styles')) return;
    var style = doc.createElement('style');
    style.id = 'br-daily-market-alert-styles';
    style.textContent = [
      '.br-daily-alert{margin:0 0 22px;border:1px solid rgba(56,214,201,.30);border-radius:22px;background:linear-gradient(135deg,rgba(56,214,201,.10),rgba(3,10,18,.64) 52%,rgba(245,158,11,.08));box-shadow:0 20px 48px rgba(0,0,0,.24),inset 0 1px 0 rgba(255,255,255,.08);overflow:hidden}',
      '.br-daily-alert__toggle{display:grid;width:100%;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:18px;padding:18px 20px;border:0;background:transparent;color:inherit;text-align:left;cursor:pointer}',
      '.br-daily-alert__eyebrow{display:block;margin-bottom:5px;color:#79eee3;font-size:10px;font-weight:950;letter-spacing:.08em;text-transform:uppercase}',
      '.br-daily-alert__title-row{display:flex;flex-wrap:wrap;align-items:center;gap:9px}',
      '.br-daily-alert__title{margin:0;font-size:22px;line-height:1.1;letter-spacing:-.025em}',
      '.br-daily-alert__regime{display:inline-flex;align-items:center;min-height:24px;padding:4px 9px;border:1px solid rgba(255,191,63,.30);border-radius:999px;background:rgba(255,191,63,.10);color:#ffd36f;font-size:10px;font-weight:900}',
      '.br-daily-alert__snapshot{margin:7px 0 0;color:#aebfd0;font-size:12px;line-height:1.45}',
      '.br-daily-alert__meta{display:flex;align-items:center;justify-content:flex-end;gap:12px;color:#91a6ba;font-size:10px;font-weight:850;white-space:nowrap}',
      '.br-daily-alert__meta-copy{display:flex;flex-direction:column;align-items:flex-end;gap:3px}',
      '.br-daily-alert__edition{color:#c5d4e2;font-size:9px;font-weight:900}',
      '.br-daily-alert__action{display:flex;flex-direction:column;align-items:center;gap:3px}',
      '.br-daily-alert__expand{color:#9ffff6;font-size:9px;font-weight:950;letter-spacing:.045em;text-transform:uppercase}',
      '.br-daily-alert__freshness.is-stale{color:#ffbf3f}',
      '.br-daily-alert__chevron{display:inline-grid;width:28px;height:28px;place-items:center;border:1px solid rgba(255,255,255,.13);border-radius:50%;font-size:16px;transition:transform .2s ease}',
      '.br-daily-alert.is-open .br-daily-alert__chevron{transform:rotate(180deg)}',
      '.br-daily-alert__body{padding:20px;border-top:1px solid rgba(255,255,255,.10)}',
      '.br-daily-alert__summary{max-width:960px;margin:0 0 12px;color:#c7d6e4;font-size:13px;line-height:1.65}',
      '.br-daily-alert__session-note{margin:0 0 17px;padding:10px 12px;border:1px solid rgba(56,214,201,.16);border-radius:12px;background:rgba(56,214,201,.055);color:#aee5df;font-size:11px;line-height:1.55}',
      '.br-daily-alert__grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:13px}',
      '.br-daily-alert__card{min-width:0;padding:16px;border:1px solid rgba(255,255,255,.10);border-radius:17px;background:rgba(3,10,18,.52);box-shadow:inset 0 1px 0 rgba(255,255,255,.045)}',
      '.br-daily-alert__instrument-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:12px}',
      '.br-daily-alert__instrument h3{margin:0 0 3px;font-size:18px;letter-spacing:-.02em}',
      '.br-daily-alert__class{color:#7f95aa;font-size:9px;font-weight:850;letter-spacing:.05em;text-transform:uppercase}',
      '.br-daily-alert__market{text-align:right}',
      '.br-daily-alert__price{display:block;font-size:16px;font-weight:950}',
      '.br-daily-alert__change{display:inline-block;margin-top:3px;font-size:11px;font-weight:950}',
      '.br-daily-alert__change.is-up{color:#52e38b}',
      '.br-daily-alert__change.is-down{color:#ff6b83}',
      '.br-daily-alert__label{display:block;margin:13px 0 5px;color:#7f95aa;font-size:9px;font-weight:900;letter-spacing:.07em;text-transform:uppercase}',
      '.br-daily-alert__reason{margin:0;color:#d5e2ed;font-size:12px;line-height:1.6}',
      '.br-daily-alert__levels{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:14px}',
      '.br-daily-alert__level{padding:9px 10px;border:1px solid rgba(255,255,255,.08);border-radius:11px;background:rgba(255,255,255,.025)}',
      '.br-daily-alert__level small{display:block;margin-bottom:3px;color:#7f95aa;font-size:8px;font-weight:900;letter-spacing:.06em;text-transform:uppercase}',
      '.br-daily-alert__level b{font-size:13px}',
      '.br-daily-alert__trigger{margin:12px 0 0;padding-top:11px;border-top:1px solid rgba(255,255,255,.08);color:#b7c8d7;font-size:11px;line-height:1.55}',
      '.br-daily-alert__scenarios{display:grid;gap:8px;margin-top:12px}',
      '.br-daily-alert__scenario{display:grid;grid-template-columns:34px minmax(0,1fr);align-items:center;gap:8px}',
      '.br-daily-alert__probability{color:#9ffff6;font-size:10px;font-weight:950}',
      '.br-daily-alert__scenario-copy{min-width:0}',
      '.br-daily-alert__scenario-copy span{display:block;overflow:hidden;color:#aebfd0;font-size:9px;line-height:1.25;text-overflow:ellipsis;white-space:nowrap}',
      '.br-daily-alert__bar{height:3px;margin-top:4px;border-radius:999px;background:rgba(255,255,255,.08);overflow:hidden}',
      '.br-daily-alert__bar i{display:block;height:100%;border-radius:inherit;background:linear-gradient(90deg,#38d6c9,#ffbf3f)}',
      '.br-daily-alert__footer{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px;margin-top:16px;padding-top:13px;border-top:1px solid rgba(255,255,255,.08);color:#71879b;font-size:9px;line-height:1.45}',
      '.br-daily-alert__sources{display:flex;flex-wrap:wrap;gap:8px}',
      '.br-daily-alert__sources a{color:#8fe9e0;text-decoration:underline;text-decoration-color:rgba(143,233,224,.28);text-underline-offset:3px}',
      '@media(max-width:980px){.br-daily-alert__grid{grid-template-columns:1fr}.br-daily-alert__card{padding:15px}}',
      '@media(max-width:620px){.br-daily-alert__toggle{grid-template-columns:1fr;padding:16px}.br-daily-alert__meta{justify-content:space-between}.br-daily-alert__meta-copy{align-items:flex-start}.br-daily-alert__body{padding:15px}.br-daily-alert__title{font-size:20px}.br-daily-alert__snapshot{font-size:11px}.br-daily-alert__reason{font-size:11.5px}}',
      '@media(prefers-reduced-motion:reduce){.br-daily-alert__chevron{transition:none}}'
    ].join('');
    doc.head.appendChild(style);
  }

  function labelsFor(language) {
    return language === 'pl' ? {
      eyebrow: 'Alert rynkowy dnia',
      title: 'Daily Market Alert',
      current: 'Aktualny alert',
      stale: 'Ostatni dostępny alert',
      updated: 'Aktualizacja',
      expand: 'Rozwiń',
      collapse: 'Zwiń',
      openingEdition: 'Alert po otwarciu',
      precloseEdition: 'Aktualizacja przed zamknięciem',
      reason: 'Co nowego i dlaczego rynek reaguje',
      support: 'Wsparcie',
      resistance: 'Opór',
      trigger: 'Co zmieni obraz',
      horizon: 'Scenariusze: 1–3 sesje',
      sources: 'Źródła',
      disclaimer: 'Scenariusze są oceną probabilistyczną, nie rekomendacją inwestycyjną.'
    } : {
      eyebrow: 'Daily market alert',
      title: 'Daily Market Alert',
      current: 'Current alert',
      stale: 'Latest available alert',
      updated: 'Updated',
      expand: 'Expand',
      collapse: 'Collapse',
      openingEdition: 'Post-open alert',
      precloseEdition: 'Pre-close update',
      reason: 'What is new and why the market is reacting',
      support: 'Support',
      resistance: 'Resistance',
      trigger: 'What changes the picture',
      horizon: 'Scenarios: next 1–3 sessions',
      sources: 'Sources',
      disclaimer: 'Scenarios are probabilistic assessments, not investment recommendations.'
    };
  }

  function formatUpdated(value, language) {
    var date = new Date(value);
    if (!Number.isFinite(date.getTime())) return String(value || '');
    try {
      return new Intl.DateTimeFormat(language === 'pl' ? 'pl-PL' : 'en-GB', {
        day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Warsaw'
      }).format(date);
    } catch (error) {
      return date.toISOString().slice(0, 16).replace('T', ' ');
    }
  }

  function isStale(value) {
    var timestamp = new Date(value).getTime();
    return !Number.isFinite(timestamp) || (Date.now() - timestamp) > 36 * 60 * 60 * 1000;
  }

  function scenarioMarkup(scenarios, language) {
    return (Array.isArray(scenarios) ? scenarios : []).map(function (scenario) {
      var probability = Math.max(0, Math.min(100, Number(scenario.probability) || 0));
      return '<div class="br-daily-alert__scenario">' +
        '<strong class="br-daily-alert__probability">' + probability + '%</strong>' +
        '<div class="br-daily-alert__scenario-copy"><span>' + escapeHtml(localized(scenario.label, language)) + '</span>' +
        '<div class="br-daily-alert__bar" aria-hidden="true"><i style="width:' + probability + '%"></i></div></div>' +
      '</div>';
    }).join('');
  }

  function instrumentMarkup(instrument, language, labels) {
    var directionClass = instrument.direction === 'up' ? ' is-up' : instrument.direction === 'down' ? ' is-down' : '';
    return '<article class="br-daily-alert__card">' +
      '<div class="br-daily-alert__instrument-head">' +
        '<div class="br-daily-alert__instrument"><h3>' + escapeHtml(instrument.name) + '</h3><span class="br-daily-alert__class">' + escapeHtml(localized(instrument.asset_class, language)) + '</span></div>' +
        '<div class="br-daily-alert__market"><span class="br-daily-alert__price">' + escapeHtml(instrument.price) + '</span><span class="br-daily-alert__change' + directionClass + '">' + escapeHtml(instrument.change) + '</span></div>' +
      '</div>' +
      '<span class="br-daily-alert__label">' + labels.reason + '</span>' +
      '<p class="br-daily-alert__reason">' + escapeHtml(localized(instrument.reason, language)) + '</p>' +
      '<div class="br-daily-alert__levels">' +
        '<div class="br-daily-alert__level"><small>' + labels.support + '</small><b>' + escapeHtml(instrument.support) + '</b></div>' +
        '<div class="br-daily-alert__level"><small>' + labels.resistance + '</small><b>' + escapeHtml(instrument.resistance) + '</b></div>' +
      '</div>' +
      '<span class="br-daily-alert__label">' + labels.trigger + '</span>' +
      '<p class="br-daily-alert__trigger">' + escapeHtml(localized(instrument.trigger, language)) + '</p>' +
      '<span class="br-daily-alert__label">' + labels.horizon + '</span>' +
      '<div class="br-daily-alert__scenarios">' + scenarioMarkup(instrument.scenarios, language) + '</div>' +
    '</article>';
  }

  function sourceMarkup(sources) {
    return (Array.isArray(sources) ? sources : []).map(function (source) {
      var safeUrl = /^https:\/\//i.test(String(source.url || '')) ? source.url : '#';
      return '<a href="' + escapeHtml(safeUrl) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(source.name) + '</a>';
    }).join('');
  }

  function render(data, language, anchor) {
    if (!data || !Array.isArray(data.instruments) || !data.instruments.length) return;
    injectStyles();

    var labels = labelsFor(language);
    var stale = isStale(data.updated_at);
    var editionLabel = data.edition === 'preclose' ? labels.precloseEdition : labels.openingEdition;
    var sessionNote = data.preclose_check && data.preclose_check.note ? localized(data.preclose_check.note, language) : '';
    var snapshot = data.instruments.map(function (instrument) {
      return instrument.name + ' ' + instrument.change;
    }).join(' · ');

    var section = doc.createElement('section');
    section.id = 'br-daily-market-alert';
    section.className = 'br-daily-alert';
    section.setAttribute('aria-label', labels.title);
    section.innerHTML =
      '<button class="br-daily-alert__toggle" type="button" aria-expanded="false" aria-controls="br-daily-market-alert-body">' +
        '<span><span class="br-daily-alert__eyebrow">' + labels.eyebrow + '</span>' +
        '<span class="br-daily-alert__title-row"><strong class="br-daily-alert__title">' + labels.title + '</strong><span class="br-daily-alert__regime">' + escapeHtml(localized(data.market_regime, language)) + '</span></span>' +
        '<span class="br-daily-alert__snapshot">' + escapeHtml(snapshot) + '</span></span>' +
        '<span class="br-daily-alert__meta"><span class="br-daily-alert__meta-copy"><span class="br-daily-alert__freshness' + (stale ? ' is-stale' : '') + '">' + (stale ? labels.stale : labels.current) + '</span><span class="br-daily-alert__edition">' + escapeHtml(editionLabel) + ' · ' + labels.updated + ': ' + escapeHtml(formatUpdated(data.updated_at, language)) + '</span></span><span class="br-daily-alert__action"><span class="br-daily-alert__expand">' + labels.expand + '</span><span class="br-daily-alert__chevron" aria-hidden="true">⌄</span></span></span>' +
      '</button>' +
      '<div class="br-daily-alert__body" id="br-daily-market-alert-body" hidden>' +
        '<p class="br-daily-alert__summary">' + escapeHtml(localized(data.summary, language)) + '</p>' +
        (sessionNote ? '<p class="br-daily-alert__session-note">' + escapeHtml(sessionNote) + '</p>' : '') +
        '<div class="br-daily-alert__grid">' + data.instruments.map(function (instrument) { return instrumentMarkup(instrument, language, labels); }).join('') + '</div>' +
        '<div class="br-daily-alert__footer"><span>' + labels.disclaimer + '</span><span class="br-daily-alert__sources"><b>' + labels.sources + ':</b> ' + sourceMarkup(data.sources) + '</span></div>' +
      '</div>';

    if (anchor.parentNode) anchor.parentNode.insertBefore(section, anchor.nextSibling);

    var toggle = section.querySelector('.br-daily-alert__toggle');
    var body = section.querySelector('.br-daily-alert__body');
    var expandLabel = section.querySelector('.br-daily-alert__expand');
    toggle.addEventListener('click', function () {
      var open = toggle.getAttribute('aria-expanded') === 'true';
      toggle.setAttribute('aria-expanded', open ? 'false' : 'true');
      body.hidden = open;
      section.classList.toggle('is-open', !open);
      if (expandLabel) expandLabel.textContent = open ? labels.expand : labels.collapse;
    });
  }

  ready(function () {
    if (!isInvestmentHub(root.location && root.location.pathname)) return;
    if (doc.getElementById('br-daily-market-alert')) return;
    var anchor = doc.querySelector('#daily-market-alert-anchor');
    if (!anchor) return;
    var language = normalizePath(root.location.pathname).indexOf('/en/') === 0 ? 'en' : 'pl';
    root.fetch(DATA_URL + '?v=' + Date.now(), { cache: 'no-store' })
      .then(function (response) {
        if (!response.ok) throw new Error('daily-market-alert');
        return response.json();
      })
      .then(function (data) { render(data, language, anchor); })
      .catch(function () { /* The page remains fully usable when alert data is unavailable. */ });
  });
})(typeof window !== 'undefined' ? window : null);
