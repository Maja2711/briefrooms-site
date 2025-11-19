/**
 * BriefRooms Hotbar Script (v3 - Multi-language Support)
 * Obsługuje pasek "HOT NEWS" oraz zegar.
 * Automatycznie wykrywa język strony (PL/EN).
 */

(function() {
  // 1. Wykrywanie języka na podstawie tagu <html lang="xx">
  const lang = document.documentElement.lang || 'pl';
  
  // 2. Wybór pliku w zależności od języka
  const fileName = (lang === 'en') 
    ? 'news_summaries_en.json' 
    : 'news_summaries_pl.json';
    
  const HOTBAR_URL = `/.cache/${fileName}`;
  const TRACK_ID = 'br-hotbar-track';
  const TIME_ID = 'br-hotbar-time';
  
  // Prędkość animacji (piksele na sekundę).
  const SPEED_PX_PER_SEC = 50; 

  /**
   * Obsługa zegara (formatowanie zależne od języka)
   */
  function updateClock() {
    const el = document.getElementById(TIME_ID);
    if (!el) return;

    const now = new Date();
    // Ustaw locale na podstawie wykrytego języka (pl-PL lub en-US/en-GB)
    const locale = (lang === 'en') ? 'en-GB' : 'pl-PL';
    
    const timeString = now.toLocaleTimeString(locale, {
      hour: '2-digit',
      minute: '2-digit'
    });
    el.textContent = timeString;
  }

  /**
   * Pobieranie i renderowanie paska
   */
  async function loadHotbar() {
    const track = document.getElementById(TRACK_ID);
    if (!track) return;

    try {
      const res = await fetch(`${HOTBAR_URL}?t=${Date.now()}`);
      if (!res.ok) throw new Error(`Brak pliku: ${fileName}`);
      
      const data = await res.json();
      const keys = Object.keys(data);

      // Parsowanie kluczy: "v2|Message|Date"
      const messages = keys
        .map(k => {
          const parts = k.split('|');
          return parts.length >= 2 ? parts[1] : null;
        })
        .filter(txt => txt && txt.length > 0);

      if (messages.length === 0) {
        // Fallback message
        const msg = (lang === 'en') 
          ? 'Welcome to BriefRooms. Choose a room to start reading.'
          : 'Witamy w BriefRooms. Wybierz pokój tematyczny.';
        track.innerHTML = `<span>${msg}</span>`;
        return;
      }

      // Separator
      const separator = '<span class="sep">•</span>';
      
      // Budowanie HTML
      let htmlContent = messages.map(msg => {
        return `<span>${msg}</span>`;
      }).join(separator);

      htmlContent += separator;

      // Wstawienie i duplikacja dla pętli
      track.innerHTML = htmlContent;
      track.innerHTML += htmlContent;

      // Obliczanie animacji
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
      const msg = (lang === 'en') 
          ? 'BriefRooms — concise summaries.'
          : 'BriefRooms — krótkie podsumowania.';
      track.innerHTML = `<span>${msg}</span>`;
    }
  }

  // Start
  document.addEventListener('DOMContentLoaded', () => {
    updateClock();
    setInterval(updateClock, 1000);
    loadHotbar();
  });

})();
