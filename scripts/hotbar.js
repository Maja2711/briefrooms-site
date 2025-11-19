/**
 * BriefRooms Hotbar Script (v3 - Multi-language Support, hardened)
 * Obsługuje pasek "HOT NEWS" oraz zegar.
 * Automatycznie wykrywa język strony (PL/EN).
 */

(function () {
  const lang = document.documentElement.lang || 'pl';

  const fileName =
    lang === 'en' ? 'news_summaries_en.json' : 'news_summaries_pl.json';

  const HOTBAR_URL = `/.cache/${fileName}`;
  const TRACK_ID = 'br-hotbar-track';
  const TIME_ID = 'br-hotbar-time';

  const SPEED_PX_PER_SEC = 50;

  function updateClock() {
    const el = document.getElementById(TIME_ID);
    if (!el) return;

    const now = new Date();
    const locale = lang === 'en' ? 'en-GB' : 'pl-PL';

    const timeString = now.toLocaleTimeString(locale, {
      hour: '2-digit',
      minute: '2-digit',
    });
    el.textContent = timeString;
  }

  async function loadHotbar() {
    const track = document.getElementById(TRACK_ID);
    if (!track) return;

    try {
      const res = await fetch(`${HOTBAR_URL}?t=${Date.now()}`, {
        cache: 'no-store',
      });
      if (!res.ok) throw new Error(`Brak pliku: ${fileName}`);

      const data = await res.json();
      const keys = Object.keys(data || {});

      const messages = keys
        .map((k) => {
          // klucz w formacie "v2|Message|Date" – usuwamy prefix wersji
          const withoutVer = k.replace(/^v\d+\|/, '');
          const parts = withoutVer.split('|');
          if (parts.length === 1) return parts[0].trim();
          if (parts.length >= 2) {
            // wszystko oprócz ostatniego elementu (daty) traktujemy jako tekst
            return parts.slice(0, -1).join('|').trim();
          }
          return null;
        })
        .filter((txt) => txt && txt.length > 0);

      if (!messages.length) {
        const msg =
          lang === 'en'
            ? 'Welcome to BriefRooms. Choose a room to start reading.'
            : 'Witamy w BriefRooms. Wybierz pokój tematyczny.';
        track.innerHTML = `<span>${msg}</span>`;
        track.style.animation = 'none';
        return;
      }

      const separator = '<span class="sep">•</span>';

      let htmlContent = messages
        .map(
          (msg) =>
            `<span style="display:inline-flex; align-items:center; padding-top:1px;">${msg}</span>`
        )
        .join(separator);

      htmlContent += separator;

      track.innerHTML = htmlContent + htmlContent;

      requestAnimationFrame(() => {
        const trackWidth = track.scrollWidth / 2;
        if (trackWidth > 0) {
          const duration = trackWidth / SPEED_PX_PER_SEC;
          track.style.animationDuration = `${duration}s`;
          track.style.animationName = 'ticker-scroll';
          track.style.animationTimingFunction = 'linear';
          track.style.animationIterationCount = 'infinite';
        }
      });
    } catch (err) {
      console.warn('Hotbar error:', err);
      const msg =
        lang === 'en'
          ? 'BriefRooms — concise summaries.'
          : 'BriefRooms — krótkie podsumowania.';
      track.innerHTML = `<span>${msg}</span>`;
      track.style.animation = 'none';
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    updateClock();
    setInterval(updateClock, 1000);
    loadHotbar();
  });
})();
