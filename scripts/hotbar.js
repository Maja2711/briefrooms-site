// /scripts/hotbar.js
(function () {
  const bar = document.querySelector('.br-hotbar');
  const track = document.getElementById('br-hotbar-track');
  const timeEl = document.getElementById('br-hotbar-time');

  // Jeśli brak paska w HTML – przerwij
  if (!bar || !track) return;

  // Język strony
  const isEN = location.pathname.startsWith('/en/');

  // Używamy faktycznie istniejących plików
  const jsonUrl = isEN
    ? '/.cache/news_summaries_en.json'
    : '/.cache/news_summaries_pl.json';

  fetch(jsonUrl, { cache: 'no-store' })
    .then((res) => {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.json();
    })
    .then((dataObj) => {
      if (!dataObj || typeof dataObj !== 'object') {
        bar.style.display = 'none';
        return;
      }

      const entries = Object.entries(dataObj);
      if (!entries.length) {
        bar.style.display = 'none';
        return;
      }

      // 1) Wyciągamy najnowszą datę z końcówek "|2025-11-07"
      let latestDate = null;

      for (const [key] of entries) {
        const parts = key.split('|');
        const dateStr = parts[parts.length - 1]; // ostatni element

        if (/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
          if (!latestDate || dateStr > latestDate) {
            latestDate = dateStr;
          }
        }
      }

      // 2) Bierzemy newsy tylko z najnowszej daty
      const MAX_ITEMS = 8;

      const items = entries
        .filter(([key]) => {
          if (!latestDate) return true;
          return key.endsWith(latestDate);
        })
        .slice(0, MAX_ITEMS)
        .map(([key]) => {
          const parts = key.split('|');

          let headlinePart;

          // Format: v2|Tytuł|Data
          if (parts[0] === 'v2') {
            headlinePart = parts[1];
          } else {
            // Format: Tytuł|Data
            headlinePart = parts[0];
          }

          // Usuwamy nadmiarowe cudzysłowy
          headlinePart = headlinePart.replace(/^"+|"+$/g, '');

          return { title: headlinePart.trim() };
        })
        .filter((item) => item.title && item.title.length > 0);

      if (!items.length) {
        bar.style.display = 'none';
        return;
      }

      // 3) Tworzymy zawartość paska
      track.innerHTML = '';

      items.forEach((item) => {
        const a = document.createElement('a');
        a.className = 'br-hotbar-item';
        a.href = isEN ? '/en/news.html' : '/pl/aktualnosci.html';
        a.textContent = item.title;
        track.appendChild(a);
      });

      // 4) Duplikat do płynnego scrollu
      const clone = track.cloneNode(true);
      clone.removeAttribute('id');
      track.parentNode.appendChild(clone);

      // 5) Znacznik czasu
      if (timeEl && latestDate) {
        timeEl.textContent = isEN
          ? `updated: ${latestDate}`
          : `aktualizacja: ${latestDate}`;
      }
    })
    .catch((err) => {
      console.error('Hotbar error', err);
      bar.style.display = 'none';
    });
})();
