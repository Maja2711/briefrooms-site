// /scripts/hotbar.js
(function () {
  const bar = document.querySelector('.br-hotbar');
  const track = document.getElementById('br-hotbar-track');
  const timeEl = document.getElementById('br-hotbar-time');

  if (!bar || !track) return;

  // PL czy EN – sprawdzamy po ścieżce
  const isEN = location.pathname.startsWith('/en/');
  const jsonUrl = isEN
    ? '/.cache/news_hotbar_en.json'
    : '/.cache/news_hotbar_pl.json';

  // Pobieramy JSON z GitHub Pages (bez cache przeglądarki)
  fetch(jsonUrl, { cache: 'no-store' })
    .then((res) => {
      if (!res.ok) {
        throw new Error('HTTP ' + res.status);
      }
      return res.json();
    })
    .then((data) => {
      const items = Array.isArray(data.items) ? data.items : [];

      // JEŚLI BRAK DANYCH → chowamy pasek
      if (!items.length) {
        bar.style.display = 'none';
        return;
      }

      // Czyścimy tor i wstawiamy linki
      track.innerHTML = '';
      items.forEach((item) => {
        const a = document.createElement('a');
        a.className = 'br-hotbar-item';
        a.href = item.url || (isEN ? '/en/news.html' : '/pl/aktualnosci.html');
        a.textContent = item.title || '';
        track.appendChild(a);
      });

      // Duplikat do płynnego przewijania
      const clone = track.cloneNode(true);
      track.parentNode.appendChild(clone);

      // Czas aktualizacji (z pola updated_at)
      if (timeEl && data.updated_at) {
        const d = new Date(data.updated_at);
        if (!isNaN(d.getTime())) {
          const pad = (n) => String(n).padStart(2, '0');
          timeEl.textContent = `aktualizacja: ${pad(
            d.getHours()
          )}:${pad(d.getMinutes())} UTC`;
        }
      }
    })
    .catch((err) => {
      console.error('Hotbar error', err);
      // Błąd pobierania → chowamy pasek
      bar.style.display = 'none';
    });
})();


