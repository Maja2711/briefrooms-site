(function () {
  'use strict';

  function getLang() {
    var lang = (document.documentElement.getAttribute('lang') || '').toLowerCase();
    return lang.indexOf('en') === 0 || location.pathname.indexOf('/en/') === 0 ? 'en' : 'pl';
  }

  function getItems(lang) {
    if (lang === 'en') {
      return [
        ['Latest news briefs', '/en/news.html'],
        ['Geopolitics, health, science and investing summaries', '/en/'],
        ['Open the News room for current updates', '/en/news.html']
      ];
    }
    return [
      ['Najnowsze aktualności i krótkie briefy', '/pl/aktualnosci.html'],
      ['Geopolityka, zdrowie, nauka i inwestycje w krótkiej formie', '/pl/'],
      ['Kliknij Aktualności, aby zobaczyć bieżące podsumowania', '/pl/aktualnosci.html']
    ];
  }

  function addTicker() {
    var track = document.getElementById('br-hotbar-track');
    if (!track) return;

    var lang = getLang();
    var items = getItems(lang);
    track.innerHTML = '';

    function addSet() {
      items.forEach(function (item) {
        var link = document.createElement('a');
        link.className = 'br-hotbar-item';
        link.href = item[1];
        link.textContent = item[0];
        track.appendChild(link);

        var sep = document.createElement('span');
        sep.className = 'br-hotbar-sep';
        sep.textContent = ' • ';
        track.appendChild(sep);
      });
    }

    addSet();
    addSet();

    var time = document.getElementById('br-hotbar-time');
    if (time) {
      time.textContent = new Date().toLocaleTimeString(lang === 'en' ? 'en-GB' : 'pl-PL', { hour: '2-digit', minute: '2-digit' });
    }

    window.requestAnimationFrame(function () {
      var distance = Math.max(600, track.scrollWidth / 2);
      track.style.setProperty('--br-hotbar-distance', '-' + distance + 'px');
      track.style.setProperty('--br-hotbar-duration', '34s');
      track.classList.add('is-ready');
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', addTicker);
  } else {
    addTicker();
  }
})();
