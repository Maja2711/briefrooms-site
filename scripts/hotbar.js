// /scripts/hotbar.js
(function () {
  const bar = document.querySelector('.br-hotbar');
  const track = document.getElementById('br-hotbar-track');
  const timeEl = document.getElementById('br-hotbar-time');

  // Jeśli nie ma paska w HTML – nie robimy nic
  if (!bar || !track) return;

  // Sprawdzamy, czy jesteśmy w EN czy PL
  const isEN = location.pathname.startsWith('/en/');
  const jsonUrl = isEN
    ? '/.cache/news_summaries_en.json'
    : '/.cache/news_summaries_pl.json';

  // Pobieramy JSON z GitHub Pages (bez cache przeglądarki)
  fetch(jsonUrl, { cache: 'no-store' })
    .then((res) => {
      if (!res.ok) {
        throw new Error('HTTP ' + res.status);
      }
      return res.json();
    })
    .then((raw) => {
      // raw jest obiektem:
      // { "tytuł|2025-11-07": { summary: "...", ... }, ... }
      const allKeys = Object.keys(raw || {});

      if (!allKeys.length) {
        bar.style.display = 'none';
        return;
      }

      // Usuwamy duble "v2|..." – bierzemy tylko pierwsze wersje
      const baseKeys = allKeys.filter((k) => !k.startsWith('v2|'));

      // Bierzemy ostatnie 6 nagłówków (najświeższe)
      const selectedKeys = baseKeys.slice(-6);

      const items = selectedKeys
        .map((key) => {
          let whole = key;

          // Na wszelki wypadek – usuń "v2|" jeśli by się trafiło
          if (whole.startsWith('v2|')) {
            whole = whole.slice(3);
          }

          // Format: TYTUŁ|YYYY-MM-DD
          const parts = whole.split('|');
          let title = parts[0] || '';

          // W PL pierwszy tytuł ma czasem cudzysłów na początku/końcu
          if (title.startsWith('"') && title.endsWith('"')) {
            title = title.slice(1, -1);
          }

          title = title.trim();
          if (!title) return null;

          return {
            title,
            url: isEN ? '/en/news.html' : '/pl/aktualnosci.html',
          };
        })
        .filter(Boolean);

      // Jeśli po filtrowaniu nic nie zostało – chowamy pasek
      if (!items.length) {
        bar.style.display = 'none';
        return;
      }

      // Czyścimy tor i wstawiamy linki
      track.innerHTML = '';
      items.forEach((item) => {
        const a = document.createElement('a');
        a.className = 'br-hotbar-item';
        a.href = item.url;
        a.textContent = item.title;
        track.appendChild(a);
      });

      // Prosta informacja o dacie aktualizacji – bierzemy datę z ostatniego klucza
      if (timeEl && selectedKeys.length) {
        const lastKey = selectedKeys[selectedKeys.length - 1];
        const parts = lastKey.split('|');
        const datePart = parts[parts.length - 1];

        if (datePart && /^\d{4}-\d{2}-\d{2}$/.test(datePart)) {
          timeEl.textContent = isEN
            ? 'updated: ' + datePart
            : 'aktualizacja: ' + datePart;
        }
      }
    })
    .catch((err) => {
      console.error('Hotbar error', err);
      // Błąd pobierania → chowamy pasek
      bar.style.display = 'none';
    });
})();
