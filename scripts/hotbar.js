// /scripts/hotbar.js
(function () {
  const bar = document.querySelector('.br-hotbar');
  const track = document.getElementById('br-hotbar-track');
  const timeEl = document.getElementById('br-hotbar-time');

  if (!bar || !track) return;

  const isEN = location.pathname.startsWith('/en/');
  const jsonUrl = isEN
    ? '/.cache/news_summaries_en.json'
    : '/.cache/news_summaries_pl.json';

  fetch(jsonUrl, { cache: 'no-store' })
    .then((res) => {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.json();
    })
    .then((raw) => {
      const keys = Object.keys(raw || {});

      if (!keys.length) {
        bar.style.display = 'none';
        return;
      }

      // --- Usuwamy duplikaty typu "v2|..."
      const cleanKeys = keys.filter((k) => !k.startsWith('v2|'));

      // --- Parsowanie
      const items = cleanKeys.map((k) => {
        const parts = k.replace(/^v2\|/, '').split('|');
        let title = parts[0] || '';
        const date = parts[1] || '';

        if (title.startsWith('"') && title.endsWith('"')) {
          title = title.slice(1, -1);
        }

        return { title: title.trim(), date };
      });

      // --------------------------
      //  KATEGORYZACJA NEWSÓW
      // --------------------------
      const catKraj = [];
      const catWorld = [];
      const catSport = [];

      items.forEach((it) => {
        const t = it.title.toLowerCase();

        // Polska / polityka / gospodarka
        if (/(polsk|sejm|rząd|premier|policja|ziobr|rpp|nbp|inflacj|straż|straz|wojna|ukraina|gospodar)/u.test(t)) {
          catKraj.push(it);
          return;
        }

        // Świat
        if (/usa|uk |eu |un |euro|world|global|election|nato|germany|france/.test(t)) {
          catWorld.push(it);
          return;
        }

        // Sport
        if (/mecz|liga|wynik|relacja|futbol|siatk|koszy|skoki|wta|atp|mistrz|ekstraklasa/.test(t)) {
          catSport.push(it);
          return;
        }

        // Reszta → Świat
        catWorld.push(it);
      });

      // --------------------------
      //  FINALNE 6 NEWSÓW
      // --------------------------
      const final = [
        ...catKraj.slice(-2),
        ...catWorld.slice(-2),
        ...catSport.slice(-2),
      ];

      if (!final.length) {
        bar.style.display = 'none';
        return;
      }

      // --------------------------
      //  Render listy
      // --------------------------
      track.innerHTML = '';

      final.forEach((item) => {
        // Automatyczne obcięcie bardzo długich tytułów
        if (item.title.length > 120) {
          item.title = item.title.slice(0, 117) + '...';
        }

        const a = document.createElement('a');
        a.className = 'br-hotbar-item';
        a.href = isEN ? '/en/news.html' : '/pl/aktualnosci.html';
        a.textContent = item.title;
        track.appendChild(a);
      });

      // --------------------------
      //  Data aktualizacji
      // --------------------------
      const lastWithDate = final.find((x) => x.date) || null;
      if (timeEl && lastWithDate) {
        timeEl.textContent = isEN
          ? 'updated: ' + lastWithDate.date
          : 'aktualizacja: ' + lastWithDate.date;
      }

      // --------------------------
      //  PŁYNNE PRZEWIJANIE (bez skoków!)
      // --------------------------

      // Klon toru
      const clone = track.cloneNode(true);
      clone.id = 'br-hotbar-track-clone';
      clone.classList.add('clone');
      track.parentNode.appendChild(clone);

      // Dynamiczne wyliczenie szerokości przewijania
      const totalWidth = track.scrollWidth;
      document.documentElement.style.setProperty('--scroll-width', totalWidth + 'px');

      // CSS animacji — dynamiczna długość
      const style = document.createElement('style');
      style.textContent = `
        .br-hotbar-ticker {
          position: relative;
          overflow: hidden;
          white-space: nowrap;
        }
        .br-hotbar-track,
        #br-hotbar-track-clone {
          display: inline-flex;
          position: absolute;
          top: 0;
          white-space: nowrap;
          animation: br-scroll 30s linear infinite;
        }
        #br-hotbar-track-clone {
          left: var(--scroll-width);
        }
        @keyframes br-scroll {
          from { transform: translateX(0); }
          to   { transform: translateX(calc(-1 * var(--scroll-width))); }
        }
      `;
      document.head.appendChild(style);
    })
    .catch((err) => {
      console.error('Hotbar error', err);
      bar.style.display = 'none';
    });
})();
