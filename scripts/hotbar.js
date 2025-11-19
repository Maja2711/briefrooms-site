/**
 * BriefRooms Hotbar Script (v2)
 * Obsługuje pasek "HOT NEWS" oraz zegar.
 * Czyta dane z: /.cache/news_summaries_pl.json
 */

(function() {
  const HOTBAR_URL = '/.cache/news_summaries_pl.json';
  const TRACK_ID = 'br-hotbar-track';
  const TIME_ID = 'br-hotbar-time';
  
  // Prędkość animacji (piksele na sekundę). Im mniej, tym wolniej.
  const SPEED_PX_PER_SEC = 50; 

  /**
   * 1. Obsługa zegara (czas lokalny PL)
   */
  function updateClock() {
    const el = document.getElementById(TIME_ID);
    if (!el) return;

    const now = new Date();
    // Formatowanie czasu: HH:MM
    const timeString = now.toLocaleTimeString('pl-PL', {
      hour: '2-digit',
      minute: '2-digit'
    });
    el.textContent = timeString;
  }

  /**
   * 2. Pobieranie i renderowanie paska
   */
  async function loadHotbar() {
    const track = document.getElementById(TRACK_ID);
    if (!track) return;

    try {
      // Cache-busting (?t=...) aby nie czytać starego pliku
      const res = await fetch(`${HOTBAR_URL}?t=${Date.now()}`);
      if (!res.ok) throw new Error('Brak pliku hotbar json');
      
      const data = await res.json();
      const keys = Object.keys(data);

      // Parsowanie kluczy: "v2|Tekst Wiadomości|Data"
      // Wyciągamy tylko Tekst (index 1)
      const messages = keys
        .map(k => {
          const parts = k.split('|');
          return parts.length >= 2 ? parts[1] : null;
        })
        .filter(txt => txt && txt.length > 0);

      if (messages.length === 0) {
        track.innerHTML = '<span>Witamy w BriefRooms. Wybierz pokój tematyczny.</span>';
        return;
      }

      // Budujemy HTML
      // Używamy separatora (kropka lub +++)
      const separator = '<span class="sep" style="margin:0 15px; opacity:0.5">•</span>';
      
      // Tworzymy ciągły string
      let htmlContent = messages.map(msg => {
        // Dodajemy style inline, aby upewnić się, że tekst nie jest ucięty
        return `<span style="display:inline-flex; align-items:center; padding-top:1px;">${msg}</span>`;
      }).join(separator);

      // Dodajemy separator na końcu ostatniego elementu przed powtórzeniem
      htmlContent += separator;

      // Wstawiamy treść RAZ
      track.innerHTML = htmlContent;

      // DUPLIKACJA TREŚCI (dla płynnej pętli)
      // Kopiujemy treść tyle razy, aby wypełnić ekran + zapas
      // W najprostszym wariancie CSS 'marquee', duplikujemy całość raz.
      track.innerHTML += htmlContent;

      // Obliczanie czasu animacji
      // Musimy poczekać chwilę aż przeglądarka przeliczy szerokość
      requestAnimationFrame(() => {
        const trackWidth = track.scrollWidth / 2; // Dzielimy na 2, bo zduplikowaliśmy treść
        
        // Jeśli treść jest krótsza niż ekran, nie animujemy (lub centrujemy)
        // Ale tu zakładamy, że newsów jest sporo -> animacja
        if (trackWidth > 0) {
          const duration = trackWidth / SPEED_PX_PER_SEC;
          
          // Ustawiamy zmienną CSS lub styl bezpośrednio
          track.style.animationDuration = `${duration}s`;
          
          // Dodajemy klasę uruchamiającą animację (jeśli nie jest dodana domyślnie)
          track.style.animationName = 'ticker-scroll';
          track.style.animationTimingFunction = 'linear';
          track.style.animationIterationCount = 'infinite';
        }
      });

    } catch (err) {
      console.warn('Hotbar error:', err);
      track.innerHTML = '<span>BriefRooms — krótkie podsumowania dnia.</span>';
    }
  }

  // Start
  document.addEventListener('DOMContentLoaded', () => {
    updateClock();
    setInterval(updateClock, 1000);
    loadHotbar();
  });

})();
