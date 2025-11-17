// /scripts/hotbar.js — FULL PRODUCTION VERSION
(function () {
  const bar = document.querySelector('.br-hotbar');
  const track = document.getElementById('br-hotbar-track');
  const timeEl = document.getElementById('br-hotbar-time');

  if (!bar || !track) return;

  const isEN = location.pathname.startsWith('/en/');
  const jsonUrl = isEN
    ? '/.cache/news_summaries_en.json'
    : '/.cache/news_summaries_pl.json';

  // ============================================================
  // 1) POBIERANIE JSON
  // ============================================================
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

      // Upewnij się, że track jest pusty
      track.innerHTML = '';

      // ============================================================
      // 2) USUWAMY ELEMENTY typu v2| (Twoje fetch script je czasem dodają)
      // ============================================================
      const cleanKeys = keys.filter((k) => !k.startsWith('v2|'));

      // ============================================================
      // 3) Parsowanie tytułu + daty
      // ============================================================
      const items = cleanKeys
        .map((k) => {
          const p = k.replace(/^v2\|/, '').split('|');
          let title = (p[0] || '').trim();
          const date = (p[1] || '').trim();

          // Usuwamy cudzysłów
          if (title.startsWith('"') && title.endsWith('"')) {
            title = title.slice(1, -1);
          }

          return { title, date };
        })
        .filter((it) => it.title && it.title.length > 3);

      if (!items.length) {
        bar.style.display = 'none';
        return;
      }

      // ============================================================
      // 4) KATEGORIZACJA (PL/EN osobno)
      // ============================================================
      const catKraj = [];
      const catWorld = [];
      const catSport = [];

      items.forEach((it) => {
        const t = it.title.toLowerCase();

        if (!isEN) {
          // PL — kraj/polityka/gospodarka
          if (
            /(polsk|sejm|rząd|premier|policja|straż|ziobr|rpp|nbp|inflacj|stopy|budżet|gospodar|wojn|ukrain)/u.test(t)
          ) {
            catKraj.push(it);
            return;
          }
        } else {
          // EN — local + global politics
          if (
            /(uk |britain|scotland|wales|ireland|labour|tory|sunak|starmer|downing|white house|senate|congress|election|us )/u.test(
              t
            )
          ) {
            catWorld.push(it);
            return;
          }
        }

        // Sport PL/EN
        if (
          /(mecz|liga|wynik|relacja|futbol|siatk|koszy|skoki|wta|atp|mistrz|premier league|champions league|nba|f1|formula|tennis|match)/u.test(
            t
          )
        ) {
          catSport.push(it);
          return;
        }

        // Fallback do Świata
        catWorld.push(it);
      });

      // ============================================================
      // 5) BUDOWA KOŃCOWEJ LISTY — maks 6 newsów
      //    2x Kraj + 2x Świat + 2x Sport
      // ============================================================
      const final = [
        ...catKraj.slice(-2),
        ...catWorld.slice(-2),
        ...catSport.slice(-2),
      ];

      if (!final.length) {
        bar.style.display = 'none';
        return;
      }

      // ============================================================
      // 6) RENDER — WSZYSTKO DO TRACK
      // ============================================================
      final.forEach((item) => {
        const a = document.createElement('a');
        a.href = isEN ? '/en/news.html' : '/pl/aktualnosci.html';
        a.className = 'br-hotbar-item';
        a.textContent = item.title;
        track.appendChild(a);
      });

      // ============================================================
      // 7) DATA AKTUALIZACJI
      // ============================================================
      if (timeEl) {
        const lastDate = final[final.length - 1].date;
        if (lastDate) {
          timeEl.textContent = isEN
            ? 'updated: ' + lastDate
            : 'aktualizacja: ' + lastDate;
        }
      }

      // ============================================================
      // 8) ANIMACJA — CIĄGŁA, PŁYNNA, BEZ SKOKÓW
      // ============================================================
      const clone = track.cloneNode(true);
      clone.id = 'br-hotbar-track-clone';
      clone.classList.add('clone');
      track.parentNode.appendChild(clone);

      // CSS w runtime — zapobiega konfliktom z site.css
      const style = document.createElement('style');
      style.textContent = `
        .br-hotbar-ticker {
          position: relative;
          overflow: hidden;
          white-space: nowrap;
        }
        .br-hotbar-track,
        #br-hotbar-track-clone {
          position: absolute;
          top: 0;
          left: 0;
          display: inline-flex;
          white-space: nowrap;
          animation: br-scroll 32s linear infinite;
        }
        #br-hotbar-track-clone {
          left: 100%;
        }
        @keyframes br-scroll {
          from { transform: translateX(0); }
          to   { transform: translateX(-100%); }
        }
      `;
      document.head.appendChild(style);
    })
    .catch((err) => {
      console.error('HOTBAR ERROR:', err);
      bar.style.display = 'none';
    });
})();
