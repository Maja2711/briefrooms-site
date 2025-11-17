// /scripts/hotbar.js
(function () {
  const bar = document.querySelector('.br-hotbar');
  const track = document.getElementById('br-hotbar-track');
  const timeEl = document.getElementById('br-hotbar-time');

  // Jeśli nie ma paska w HTML – nic nie robimy
  if (!bar || !track) return;

  // Sprawdzenie języka po ścieżce
  const isEN = location.pathname.startsWith('/en/');

  // UWAGA: korzystamy z istniejących plików news_summaries_*.json
  const jsonUrl = isEN
    ? '/.cache/news_summaries_en.json'
    : '/.cache/news_summaries_pl.json';

  fetch(jsonUrl, { cache: 'no-store' })
    .then((res) => {
      if (!res.ok) {
        throw new Error('HTTP ' + res.status);
      }
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

      // 1) Ustalamy najnowszą datę z kluczy "tytuł|YYYY-MM-DD" lub "v2|tytuł|YYYY-MM-DD"
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

      // 2) Bierzemy tylko wpisy z najnowszej daty
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

          // Obsługa kluczy typu "v2|Tytuł|2025-11-07"
          if (parts[0] === 'v2') {
            headlinePart = parts[1];
          } else {
            // Zwykłe "Tytuł|2025-11-07"
            headlinePart = parts[0];
          }

          // Usuwamy ewentualne nadmiarowe cudzysłowy na początku/końcu
          headlinePart = headlinePart.replace(/^"+|"+$/g, '');

          return {
            title: headlinePart.trim()
          };
        })
        .filter((item) => item.title && item.title.length > 0);

      // Jeśli dalej nic sensownego – chowamy pasek
      if (!items.length) {
        bar.style.display = 'none';
        return;
      }

      // 3) Czyścimy tor i wstawiamy linki
      track.innerHTML = '';
      items.forEach((item) => {
        const a = document.createElement('a');
        a.className = 'br-hotbar-item';
        // Link zawsze do Twojej strony z newsami
        a.href = isEN ? '/en/news.html' : '/pl/aktualnosci.html';
        a.textContent = item.title;
        track.appendChild(a);
      });

      // 4) Duplikat do płynnego przewijania
      const clone = track.cloneNode(true);
      // Drugi tor nie może mieć tego samego ID
      clone.removeAttribute('id');
      track.parentNode.appendChild(clone);

      // 5) Tekst z datą aktualizacji
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
