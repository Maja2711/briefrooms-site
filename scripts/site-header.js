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
      { section: 'news', label: 'Informacje', href: '/pl/aktualnosci.html', icon: '▤' },
      { section: 'investing', label: 'Inwestycje', href: '/pl/inwestycje.html', icon: '↗' },
      { section: 'science', label: 'Nauka', href: '/pl/nauka.html', icon: '⚗' },
      { section: 'health', label: 'Zdrowie', href: '/pl/zdrowie.html', icon: '♡' },
      { section: 'geopolitics', label: 'Geopolityka', href: '/pl/geopolityka.html', icon: '◎' },
      { section: 'about', label: 'O nas', href: '/pl/o-projekcie.html', icon: 'i' }
    ],
    en: [
      { section: 'news', label: 'Information', href: '/en/news.html', icon: '▤' },
      { section: 'investing', label: 'Investing', href: '/en/investing.html', icon: '↗' },
      { section: 'science', label: 'Science', href: '/en/science.html', icon: '⚗' },
      { section: 'health', label: 'Health', href: '/en/health.html', icon: '♡' },
      { section: 'geopolitics', label: 'Geopolitics', href: '/en/geopolitics.html', icon: '◎' },
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
    '/pl/geo/niewidzialny-front-baltyku.html': '/en/geo/baltic-invisible-front.html',
    '/en/geo/baltic-invisible-front.html': '/pl/geo/niewidzialny-front-baltyku.html',
    '/pl/geo/upadek-hegemona.html': '/en/geo/falling-hegemon.html',
    '/en/geo/falling-hegemon.html': '/pl/geo/upadek-hegemona.html',
    '/pl/geo/usa-chiny-2025.html': '/en/geo/usa-china-2025.html',
    '/en/geo/usa-china-2025.html': '/pl/geo/usa-chiny-2025.html',
    '/pl/geo/ziemie-rzadkie.html': '/en/geo/rare-earths.html',
    '/en/geo/rare-earths.html': '/pl/geo/ziemie-rzadkie.html',
    '/pl/zdrowie/cholesterol.html': '/en/health/cholesterol.html',
    '/en/health/cholesterol.html': '/pl/zdrowie/cholesterol.html',
    '/pl/zdrowie/kalkulator-cholesterolu.html': '/en/health/cholesterol-calculator.html',
    '/en/health/cholesterol-calculator.html': '/pl/zdrowie/kalkulator-cholesterolu.html',
    '/pl/zdrowie/kalkulator-score2.html': '/en/health/score2-calculator.html',
    '/en/health/score2-calculator.html': '/pl/zdrowie/kalkulator-score2.html',
    '/pl/zdrowie/kalkulator-kalorii-makro.html': '/en/health/calorie-macro-calculator.html',
    '/en/health/calorie-macro-calculator.html': '/pl/zdrowie/kalkulator-kalorii-makro.html',
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
    '/en/investing/spx-scenarios-2026.html': '/pl/inwestycje/spx-scenariusze-2026.html',
    '/pl/inwestycje/brace-spx-lab.html': '/en/investing/brace-spx-lab.html',
    '/en/investing/brace-spx-lab.html': '/pl/inwestycje/brace-spx-lab.html',
    '/pl/inwestycje/daily-trading.html': '/en/investing/daily-trading.html',
    '/en/investing/daily-trading.html': '/pl/inwestycje/daily-trading.html',
    '/pl/inwestycje/long-view.html': '/en/investing/long-view.html',
    '/en/investing/long-view.html': '/pl/inwestycje/long-view.html',
    '/pl/inwestycje/pozycje-tygodniowe.html': '/en/investing/weekly-positions.html',
    '/en/investing/weekly-positions.html': '/pl/inwestycje/pozycje-tygodniowe.html'
  };

  function normalizePath(pathname) {
    var path = String(pathname || '/').split(/[?#]/, 1)[0].replace(/\\/g, '/');
    if (!path.startsWith('/')) path = '/' + path;
    path = path.replace(/\/+/g, '/');
    return path.replace(/^\/(pl|en)\/index\.html$/i, '/$1/');
  }

  function languageForPath(pathname, documentLanguage) {
    var path = normalizePath(pathname);
    if (path.indexOf('/en/') === 0) return 'en';
    if (path.indexOf('/pl/') === 0) return 'pl';
    return String(documentLanguage || '').toLowerCase().indexOf('en') === 0 ? 'en' : 'pl';
  }

  function sectionForPath(pathname) {
    var path = normalizePath(pathname);
    if (/^\/(pl\/aktualnosci|en\/news)(?:\.html|\/|$)/.test(path) || /^\/(pl|en)\/brief/.test(path)) return 'news';
    if (/^\/(pl\/inwestycje|en\/investing)(?:\.html|\/|$)/.test(path)) return 'investing';
    if (/^\/(pl\/nauka|en\/science)(?:\.html|\/|$)/.test(path)) return 'science';
    if (/^\/(pl\/zdrowie|en\/health)(?:\.html|\/|$)/.test(path)) return 'health';
    if (/^\/pl\/(?:geopolityka(?:\.html)?|geo\/|usa-chiny-2025\.html)/.test(path) || /^\/en\/(?:geopolitics(?:\.html)?|geo\/)/.test(path)) return 'geopolitics';
    if (/^\/pl\/(?:o-projekcie|kontakt|metodologia)(?:\.html|\/|$)/.test(path) || /^\/en\/(?:about|contact|methodology)(?:\.html|\/|$)/.test(path)) return 'about';
    return '';
  }

  function counterpartForPath(pathname, alternatePath) {
    var path = normalizePath(pathname);
    if (alternatePath) return normalizePath(alternatePath);
    if (COUNTERPARTS[path]) return COUNTERPARTS[path];
    return languageForPath(path) === 'en' ? '/pl/' : '/en/';
  }

  function alternatePathFromDocument(doc, targetLanguage, locationObject) {
    if (!doc || typeof doc.querySelector !== 'function') return '';
    var link = doc.querySelector('link[rel~="alternate"][hreflang="' + targetLanguage + '"]');
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
      header: 'Nawigacja serwisu', navigation: 'Główne działy BriefRooms', home: 'BriefRooms — strona główna',
      open: 'Otwórz menu', close: 'Zamknij menu', switchLanguage: 'Przejdź do wersji angielskiej'
    } : {
      header: 'Site navigation', navigation: 'BriefRooms main sections', home: 'BriefRooms home',
      open: 'Open menu', close: 'Close menu', switchLanguage: 'Switch to Polish'
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
      var icon = createElement(doc, 'span', 'br-site-header__icon', item.icon);
      icon.setAttribute('aria-hidden', 'true');
      link.appendChild(icon);
      link.appendChild(createElement(doc, 'span', 'br-site-header__label', item.label));
      var handle = createElement(doc, 'span', 'br-site-header__handle');
      handle.setAttribute('aria-hidden', 'true');
      link.appendChild(handle);
      if (item.section === activeSection) {
        link.classList.add('is-active');
        link.setAttribute('aria-current', 'page');
      }
      nav.appendChild(link);
    });

    var actions = createElement(doc, 'div', 'br-site-header__actions');
    var alternatePath = counterpartForPath(locationObject.pathname);
    var documentAlternate = alternatePathFromDocument(doc, otherLanguage, locationObject);
    if (documentAlternate) alternatePath = documentAlternate;
    var languageLink = createElement(doc, 'a', 'br-site-header__lang', otherLanguage.toUpperCase());
    languageLink.href = alternatePath;
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
      if (firstLink && windowObject.requestAnimationFrame) windowObject.requestAnimationFrame(function () { firstLink.focus(); });
    }

    toggle.addEventListener('click', function () { host.classList.contains('is-open') ? closeMenu(false) : openMenu(); });
    nav.addEventListener('click', function (event) { if (event.target.closest('a')) closeMenu(false); });
    doc.addEventListener('keydown', function (event) { if (event.key === 'Escape') closeMenu(true); });
    doc.addEventListener('click', function (event) { if (!host.contains(event.target)) closeMenu(false); });
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