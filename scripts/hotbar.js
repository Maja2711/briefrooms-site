// /scripts/hotbar.js
// Pasek HOT NEWS bez fallbacka:
// - jeśli JSON z newsami jest OK → wyświetla nagłówki,
// - jeśli JSON jest pusty / błąd → pasek jest ukryty.

(function () {
  // Szukamy paska
  var bar = document.querySelector('.br-hotbar');
  if (!bar) return;

  var track  = bar.querySelector('.br-hotbar-track');
  var timeEl = bar.querySelector('.br-hotbar-time');

  if (!track) return;

  // Język strony (pl / en)
  var lang = (document.documentElement.lang || 'pl').toLowerCase();
  var isPL = lang.indexOf('pl') === 0;

  // Skąd bierzemy JSON (PL / EN)
  // Te pliki będzie nam generował GitHub Actions
  var jsonUrl = isPL
    ? '/.cache/news_hotbar_pl.json'
    : '/.cache/news_hotbar_en.json';

  // Funkcja pomocnicza – całkowite ukrycie paska
  function hideBar() {
    bar.style.display = 'none';
  }

  // Pobranie JSON
  fetch(jsonUrl, { cache: 'no-store' })
    .then(function (resp) {
      if (!resp.ok) {
        throw new Error('HTTP ' + resp.status);
      }
      return resp.json();
    })
    .then(function (data) {
      // Brak poprawnych danych → chowamy pasek
      if (!data || !Array.isArray(data.items) || data.items.length === 0) {
        hideBar();
        return;
      }

      var items = data.items.slice();

      // Jeśli jakieś są oznaczone is_hot = true, pokazujemy tylko je
      var hot = items.filter(function (it) { return it && it.is_hot; });
      if (hot.length > 0) {
        items = hot;
      }

      // Czyścimy tor przewijania
      track.innerHTML = '';

      // Dodajemy linki
      items.forEach(function (item) {
        if (!item || !item.title) return;

        var a = document.createElement('a');
        a.className = 'br-hotbar-item';
        a.textContent = item.title;

        if (item.url) {
          a.href = item.url;
          a.target = '_blank';
          a.rel = 'noopener noreferrer';
        } else {
          // Domyślny URL, gdyby w JSON nie było linku
          a.href = isPL ? '/pl/aktualnosci.html' : '/en/news.html';
        }

        track.appendChild(a);
      });

      // Po filtrze nic nie zostało → chowamy pasek
      if (!track.children.length) {
        hideBar();
        return;
      }

      // Ustawiamy godzinę aktualizacji, jeśli jest w JSON
      if (timeEl && data.updated_at) {
        var dt = new Date(data.updated_at);
        if (!isNaN(dt.getTime())) {
          var hh = String(dt.getHours()).padStart(2, '0');
          var mm = String(dt.getMinutes()).padStart(2, '0');
          timeEl.textContent = isPL
            ? 'Aktualizacja: ' + hh + ':' + mm
            : 'Updated: ' + hh + ':' + mm;
        }
      }
    })
    .catch(function (err) {
      console.error('Hotbar error:', err);
      // Jakikolwiek błąd → pasek znika
      hideBar();
    });
})();

