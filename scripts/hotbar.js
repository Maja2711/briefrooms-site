/**
 * BriefRooms Hotbar Script (v4 – PL/EN + klikalne linki)
 * - Wykrywa język strony (PL/EN) na podstawie <html lang> i ścieżki /en/.
 * - Czyta dane z:
 *     /.cache/news_summaries_pl.json   (PL)
 *     /.cache/news_summaries_en.json   (EN)
 * - Oczekiwany format JSON:
 *     { "v2|Some summary|2025-11-19": "https://link-do-artykulu", ... }
 * - Buduje poziomy pasek z newsami, każdy wpis to <a href="URL">Tekst</a>.
 */

(function () {
  // ----------------------------------------
  // USTAWIENIA PODSTAWOWE
  // ----------------------------------------
  const htmlLang = (document.documentElement.getAttribute('lang') || '').toLowerCase();
  const path = window.location.pathname || '';
  const isEN = htmlLang === 'en' || path.startsWith('/en/');
  const lang = isEN ? 'en' : 'pl';

  const fileName = isEN ? 'news_summaries_en.json' : 'news_summaries_pl.json';
  const HOTBAR_URL = `/.cache/${fileName}`;
  const TRACK_ID = 'br-hotbar-track';
  const TIME_ID = 'br-hotbar-time';

  // Prędkość – piksele na sekundę (im mniejsza, tym wolniej pasek jedzie)
  const SPEED_PX_PER_SEC = 50;

  // ----------------------------------------
  // POMOCNICZE: ESCAPOWANIE HTML
  // ----------------------------------------
  function escapeHTML(str) {
    return String(str || '').replace(/[&<>"']/g, function (ch) {
      switch (ch) {
        case '&': return '&amp;';
        case '<': return '&lt;';
        case '>': return '&gt;';
        case '"': return '&quot;';
        case "'": return '&#39;';
        default: return ch;
      }
    });
  }

  function escapeAttr(str) {
    // dla prostoty używamy tego samego escapera co dla tekstu
    return escapeHTML(str);
  }

  // ----------------------------------------
  // ZEGAR W PASKU (po prawej)
  // ----------------------------------------
  function updateClock() {
    const el = document.getElementById(TIME_ID);
    if (!el) return;

    const now = new Date();
    const locale = isEN ? 'en-GB' : 'pl-PL';

    const timeString = now.toLocaleTimeString(locale, {
      hour: '2-digit',
      minute: '2-digit'
    });

    el.textContent = timeString;
  }

  // ----------------------------------------
  // Wczytanie JSON i zbudowanie treści paska
  // ----------------------------------------
  async function loadHotbar() {
    const track = document.getElementById(TRACK_ID);
    if (!track) return;

    try {
      const res = await fetch(`${HOTBAR_URL}?t=${Date.now()}`, { cache: 'no-store' });
      if (!res.ok) {
        throw new Error(`Hotbar HTTP ${res.status} (${fileName})`);
      }

      const raw = await res.json();
      const entries = Object.entries(raw || {});
      if (!entries.length) {
        setFallbackMessage(track);
        return;
      }

      // Parsowanie: key = "v2|Tekst|Data", value = URL
      const items = entries.map(([key, url]) => {
        if (typeof key !== 'string') return null;

        let safeUrl = (url || '').trim();
        // Fallback: jeśli z jakiegoś powodu URL jest pusty
        if (!safeUrl) {
          safeUrl = isEN ? '/en/news.html' : '/pl/aktualnosci.html';
        }

        const parts = key.split('|');
        let title = '';
        let date = '';

        if (parts[0] === 'v2') {
          title = parts[1] || '';
          date = parts[2] || '';
        } else {
          // legacy/fallback
          title = parts[1] || parts[0];
          date = parts[2] || '';
        }

        title = (title || '').trim().replace(/^"|"$/g, '');
        if (!title) return null;

        return {
          title,
          url: safeUrl,
          date: date || ''
        };
      }).filter(Boolean);

      if (!items.length) {
        setFallbackMessage(track);
        return;
      }

      // Separator między newsami
      const separatorHTML = '<span class="sep">•</span>';

      // Funkcja budująca jeden „pakiet” newsów (bez duplikacji)
      function buildOnce() {
        return items.map(item => {
          const t = escapeHTML(item.title);
          const u = escapeAttr(item.url);
          const d = escapeAttr(item.date);
          return `<a href="${u}" target="_blank" rel="noopener" data-hotbar-date="${d}">${t}</a>`;
        }).join(separatorHTML) + separatorHTML;
      }

      // Wstawiamy dwa razy, żeby animacja mogła płynnie zapętlać
      const once = buildOnce();
      track.innerHTML = once + once;

      // Ustawienie prędkości animacji na podstawie szerokości
      requestAnimationFrame(function () {
        const totalWidth = track.scrollWidth / 2; // połowa, bo treść zduplikowana
        if (totalWidth > 0) {
          const duration = totalWidth / SPEED_PX_PER_SEC; // sekundy
          track.style.animationDuration = `${duration}s`;
          track.style.animationName = 'ticker-scroll';
          track.style.animationTimingFunction = 'linear';
          track.style.animationIterationCount = 'infinite';
        }
      });

    } catch (err) {
      console.warn('Hotbar error:', err);
      setFallbackMessage(track);
      track.style.animation = 'none';
    }
  }

  function setFallbackMessage(track) {
    const msg = isEN
      ? 'BriefRooms — concise summaries.'
      : 'BriefRooms — krótkie podsumowania.';
    track.innerHTML = `<span>${escapeHTML(msg)}</span>`;
  }

  // ----------------------------------------
  // START
  // ----------------------------------------
  document.addEventListener('DOMContentLoaded', function () {
    updateClock();
    setInterval(updateClock, 1000);
    loadHotbar();
  });

})();
