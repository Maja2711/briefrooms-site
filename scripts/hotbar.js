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
    .then((res) => res.json())
    .then((raw) => {
      const keys = Object.keys(raw || {});
      if (!keys.length) {
        bar.style.display = 'none';
        return;
      }

      // Usuwamy v2|
      const cleanKeys = keys.filter((k) => !k.startsWith('v2|'));

      // Zamieniamy na struktury {title, date}
      const items = cleanKeys.map((k) => {
        const parts = k.replace(/^v2\|/, '').split('|');
        let title = (parts[0] || '').trim();
        let date = parts[1] || '';
        if (title.startsWith('"') && title.endsWith('"')) {
          title = title.slice(1, -1);
        }
        return { title, date };
      });

      // --- Kategorie ---
      const catKraj = [];
      const catWorld = [];
      const catSport = [];

      items.forEach((it) => {
        const t = it.title.toLowerCase();

        // PL kraj / polityka
        if (!isEN && /(polsk|sejm|rząd|premier|policja|ziobr|rpp|nbp|inflacj|straż|wojna|ukrain|gospodar)/.test(t)) {
          catKraj.push(it);
          return;
        }

        // EN/PL global
        if (/usa|uk |eu |un |euro|world|global|election/.test(t)) {
          catWorld.push(it);
          return;
        }

        // sport
        if (/mecz|liga|wynik|relacja|futbol|siatk|koszy|skoki|wta|atp|mistrz/.test(t)) {
          catSport.push(it);
          return;
        }

        // fallback → świat
        catWorld.push(it);
      });

      // FINALNE newsy
      const final = [
        ...catKraj.slice(-2),
        ...catWorld.slice(-2),
        ...catSport.slice(-2),
      ];

      if (!final.length) {
        bar.style.display = 'none';
        return;
      }

      // Render
      track.innerHTML = '';
      final.forEach((item) => {
        const a = document.createElement('a');
        a.className = 'br-hotbar-item';
        a.href = isEN ? '/en/news.html' : '/pl/aktualnosci.html';
        a.textContent = item.title;
        track.appendChild(a);
      });

      // Aktualizacja daty
      if (timeEl && final[final.length - 1].date) {
        timeEl.textContent = isEN
          ? 'updated: ' + final[final.length - 1].date
          : 'aktualizacja: ' + final[final.length - 1].date;
      }

      // --- PŁYNNE PRZEWIJANIE ---
      const clone = track.cloneNode(true);
      clone.id = 'br-hotbar-track-clone';
      track.parentNode.appendChild(clone);

      const style = document.createElement('style');
      style.textContent = `
        .br-hotbar-ticker { position: relative; overflow: hidden; }
        .br-hotbar-track, #br-hotbar-track-clone {
          position: absolute; top: 0; display: inline-flex; white-space: nowrap;
          animation: br-scroll 28s linear infinite;
        }
        #br-hotbar-track-clone { left: 100%; }
        @keyframes br-scroll {
          from { transform: translateX(0); }
          to   { transform: translateX(-100%); }
        }
      `;
      document.head.appendChild(style);
    })
    .catch((err) => {
      console.error('Hotbar error:', err);
      bar.style.display = 'none';
    });
})();
