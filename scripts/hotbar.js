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

      // Usuwamy klucze typu "v2|..."
      const cleanKeys = keys.filter((k) => !k.startsWith('v2|'));

      // Parsujemy: "tytuł|2025-11-05" -> { title, date }
      let items = cleanKeys.map((k) => {
        const parts = k.replace(/^v2\|/, '').split('|');
        let title = (parts[0] || '').trim();
        const date = (parts[1] || '').trim();

        // Czasem tytuł jest w cudzysłowie
        if (title.startsWith('"') && title.endsWith('"')) {
          title = title.slice(1, -1);
        }

        return { title, date };
      });

      if (!items.length) {
        bar.style.display = 'none';
        return;
      }

      // Sortujemy od najnowszych (YYYY-MM-DD)
      items.sort((a, b) => {
        if (a.date < b.date) return 1;
        if (a.date > b.date) return -1;
        return 0;
      });

      // Normalizacja – usuwamy ogonki (ż -> z, ą -> a itd.)
      const normalize = (s) => {
        const lower = s.toLowerCase();
        if (typeof lower.normalize === 'function') {
          return lower
            .normalize('NFD')
            .replace(/[\u0300-\u036f]/g, '');
        }
        return lower;
      };

      // Heurystyki kategorii
      const krajRe = /(polsk|sejm|senat|rzad|premier|prezydent|policj|straz|sad|prokuratur|ziobr|posl|rpp|nbp|inflacj|gospodark|budzet|zlot|kolej|torach|cpk)/;
      const worldRe = /(usa| uk | un |euro|ue |unia europejsk|niemiec|francj|chin|rosj|ukrain|izrael|iran|nato|world|global|election)/;
      const sportRe = /(mecz|liga|wynik|relacja|futbol|pilkarsk|siatk|koszy|skoki|wta|atp|mistrz|superpuchar|liga mistrz|liga konferencj)/;

      const catKraj = [];
      const catWorld = [];
      const catSport = [];
      const seen = new Set();

      items.forEach((it) => {
        if (!it.title) return;
        if (seen.has(it.title)) return;
        seen.add(it.title);

        const t = normalize(it.title);

        if (krajRe.test(t)) {
          catKraj.push(it);
          return;
        }
        if (worldRe.test(t)) {
          catWorld.push(it);
          return;
        }
        if (sportRe.test(t)) {
          catSport.push(it);
          return;
        }

        // Fallback – na PL stronie wrzucamy do "kraj",
        // na EN stronie do "world"
        if (!isEN) {
          catKraj.push(it);
        } else {
          catWorld.push(it);
        }
      });

      // Budujemy finalną listę – max ~8 pozycji
      const final = [];

      // Zawsze pokazujemy 2 najnowsze ogólnie
      for (let i = 0; i < items.length && final.length < 2; i++) {
        final.push(items[i]);
      }

      const pushSome = (source, max) => {
        for (let i = 0; i < source.length && final.length < 8 && i < max; i++) {
          if (!final.includes(source[i])) {
            final.push(source[i]);
          }
        }
      };

      // Dociągamy: kraj → świat → sport
      pushSome(catKraj, 4);   // co najmniej kilka PL
      pushSome(catWorld, 3);
      pushSome(catSport, 3);

      if (!final.length) {
        bar.style.display = 'none';
        return;
      }

      // Render HTML
      track.innerHTML = '';
      final.forEach((item) => {
        const a = document.createElement('a');
        a.className = 'br-hotbar-item';
        a.href = isEN ? '/en/news.html' : '/pl/aktualnosci.html';
        a.textContent = item.title;
        track.appendChild(a);
      });

      // Duplikujemy treść w poziomie – potrzebne do płynnego loopa
      track.innerHTML += track.innerHTML;

      // Ustawiamy podstawowe style inline,
      // żeby nie kłócić się z istniejącym CSS-em
      const ticker = track.parentNode;
      ticker.style.overflow = 'hidden';
      ticker.style.whiteSpace = 'nowrap';

      track.style.display = 'inline-block';
      track.style.whiteSpace = 'nowrap';
      track.style.animation = 'none'; // wyłączamy ewentualną animację z CSS

      // Godzina / data aktualizacji – bierzemy z najnowszego
      const latestDate = final[0].date || items[0].date;
      if (timeEl && latestDate) {
        timeEl.textContent = isEN
          ? 'updated: ' + latestDate
          : 'aktualizacja: ' + latestDate;
      }

      // --------------------------
      //  PŁYNNE PRZEWIJANIE (JS)
      // --------------------------
      let pos = 0;
      const speed = 40; // px na sekundę

      function step(timestamp) {
        if (!step.last) step.last = timestamp;
        const dt = (timestamp - step.last) / 1000;
        step.last = timestamp;

        const width = track.scrollWidth / 2; // szerokość jednego „zestawu”
        if (width > 0) {
          pos += speed * dt;
          if (pos >= width) {
            pos -= width; // zawijamy bez skoku
          }
          ticker.scrollLeft = pos;
        }

        requestAnimationFrame(step);
      }

      requestAnimationFrame(step);
    })
    .catch((err) => {
      console.error('Hotbar error', err);
      bar.style.display = 'none';
    });
})();
