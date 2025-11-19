/**
 * scripts/hotbar.js
 * * Skrypt do pobierania i wyświetlania aktualnych nagłówków wiadomości (Hotbar).
 * Zakłada, że nagłówki są zapisane w formacie JSON w pliku .cache/news_summaries_[lang].json
 */

// Konfiguracja
const HOTBAR_CONFIG = {
    HOTBAR_ID: 'news-hotbar',
    JSON_PATH_TEMPLATE: '/.cache/news_summaries_{lang}.json',
    UPDATE_INTERVAL_MS: 15000, // Co 15 sekund
    PAUSE_ON_HOVER_MS: 60000, // Pauza na 60 sekund po najechaniu myszą
    MAX_ITEMS: 15 // Maksymalna liczba nagłówków do wyświetlenia
};

let hotbarTimer;
let currentItems = [];
let itemIndex = 0;
let isHovering = false;
let isPaused = false;

// Pobiera ustawiony język dokumentu (np. 'pl', 'en')
function getLanguage() {
    return document.documentElement.lang || 'pl';
}

// Pobiera dane z pliku JSON
async function fetchNewsData(lang) {
    const path = HOTBAR_CONFIG.JSON_PATH_TEMPLATE.replace('{lang}', lang);
    try {
        const response = await fetch(path, { cache: 'no-cache' });
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        // Klucze w JSON to tytuły w formacie 'v2|TYTUŁ|DATA'. Wyciągamy sam TYTUŁ.
        return Object.keys(data)
                     .filter(key => key.startsWith('v2|'))
                     .map(key => key.split('|')[1])
                     .slice(0, HOTBAR_CONFIG.MAX_ITEMS);
    } catch (e) {
        console.error('Błąd ładowania danych Hotbar:', e);
        return [];
    }
}

// Wyświetla kolejny nagłówek
function showNextItem() {
    const hotbar = document.getElementById(HOTBAR_CONFIG.HOTBAR_ID);
    if (!hotbar || isPaused || currentItems.length === 0) return;

    // Pobierz bieżący nagłówek
    const text = currentItems[itemIndex];
    
    // Używamy transition dla płynnego ukrycia/pojawienia
    hotbar.style.opacity = '0';

    setTimeout(() => {
        hotbar.textContent = text;
        hotbar.style.opacity = '1';
        
        // Przejście do kolejnego elementu
        itemIndex = (itemIndex + 1) % currentItems.length;
    }, 500); // Czas musi pasować do przejścia CSS (.hotbar-transition)

    // Zaplanuj następne wyświetlenie
    startHotbarTimer();
}

// Restartuje timer wyświetlania
function startHotbarTimer() {
    clearTimeout(hotbarTimer);
    if (isPaused) return;
    hotbarTimer = setTimeout(showNextItem, HOTBAR_CONFIG.UPDATE_INTERVAL_MS);
}

// Inicjalizacja paska
async function initializeHotbar() {
    const lang = getLanguage();
    currentItems = await fetchNewsData(lang);
    
    const hotbar = document.getElementById(HOTBAR_CONFIG.HOTBAR_ID);
    
    if (currentItems.length > 0 && hotbar) {
        // Dodanie klasy dla przejścia CSS (zakładając, że .hotbar-transition jest zdefiniowany)
        hotbar.classList.add('hotbar-transition'); 
        
        // Obsługa najechania myszą
        hotbar.addEventListener('mouseenter', () => {
            isHovering = true;
            clearTimeout(hotbarTimer);
            // Pauzowanie i ustawienie timera na długą pauzę, aby użytkownik mógł przeczytać
            isPaused = true;
            setTimeout(() => {
                isPaused = false;
                if (!isHovering) {
                    startHotbarTimer();
                }
            }, HOTBAR_CONFIG.PAUSE_ON_HOVER_MS);
        });

        hotbar.addEventListener('mouseleave', () => {
            isHovering = false;
            // Jeśli timer nie jest w trakcie długiej pauzy, zacznij od nowa
            if (!isPaused) {
                startHotbarTimer();
            }
        });
        
        // Wyświetl pierwszy element i uruchom pętlę
        itemIndex = 0;
        showNextItem();
    } else if (hotbar) {
         // Ukryj hotbar lub ustaw domyślny komunikat, jeśli dane są puste
        hotbar.style.display = 'none';
        console.log("Hotbar: Brak danych do wyświetlenia.");
    }
}

// Uruchomienie inicjalizacji
document.addEventListener('DOMContentLoaded', initializeHotbar);
