/**
 * scripts/hotbar.js
 * * Skrypt do pobierania i wyświetlania aktualnych nagłówków wiadomości (Hotbar).
 * Zakłada, że nagłówki są zapisane w formacie JSON w pliku .cache/news_summaries_[lang].json
 */

// =========================
// KONFIGURACJA
// =========================
const HOTBAR_CONFIG = {
    HOTBAR_ID: 'news-hotbar',
    JSON_PATH_TEMPLATE: '/.cache/news_summaries_{lang}.json',
    UPDATE_INTERVAL_MS: 15000, // Co 15 sekund
    PAUSE_ON_HOVER_MS: 60000, // Pauza na 60 sekund po najechaniu myszą
    MAX_ITEMS: 15 // Maksymalna liczba nagłówków do wyświetlenia
};

// =========================
// ZMIENNE STANU
// =========================
let hotbarTimer;
let currentItems = [];
let itemIndex = 0;
let isHovering = false;
let isPaused = false; // Flaga blokująca timer podczas długiej pauzy

// =========================
// FUNKCJE POMOCNICZE
// =========================

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
            console.warn(`Hotbar: Plik danych nie znaleziony lub błąd statusu: ${response.status} dla ścieżki: ${path}`);
            return [];
        }
        const data = await response.json();
        // Klucze w JSON to tytuły w formacie 'v2|TYTUŁ|DATA'. Wyciągamy sam TYTUŁ.
        return Object.keys(data)
                     .filter(key => key.startsWith('v2|'))
                     .map(key => key.split('|')[1])
                     .slice(0, HOTBAR_CONFIG.MAX_ITEMS);
    } catch (e) {
        console.error('Hotbar: Błąd ładowania lub parsowania danych JSON:', e);
        return [];
    }
}

// Restartuje timer wyświetlania
function startHotbarTimer() {
    clearTimeout(hotbarTimer);
    if (isPaused) return;
    hotbarTimer = setTimeout(showNextItem, HOTBAR_CONFIG.UPDATE_INTERVAL_MS);
}

// =========================
// LOGIKA WYŚWIETLANIA
// =========================

// Wyświetla kolejny nagłówek
function showNextItem() {
    const hotbar = document.getElementById(HOTBAR_CONFIG.HOTBAR_ID);
    
    // Warunek wczesnego wyjścia
    if (!hotbar || isPaused || currentItems.length === 0) {
        if (hotbar) hotbar.style.display = 'none'; // Ukryj, jeśli nie ma treści
        return;
    }

    const text = currentItems[itemIndex];
    
    // 1. Ukrycie z przejściem (CSS opacity)
    hotbar.style.opacity = '0';

    setTimeout(() => {
        // 2. Wyczyść zawartość i wstaw nowy tekst
        hotbar.innerHTML = ''; // Dodatkowe czyszczenie na wypadek statycznego/brudnego HTML
        hotbar.textContent = text;
        
        // 3. Odkrycie
        hotbar.style.opacity = '1';
        
        // 4. Przejście do kolejnego elementu
        itemIndex = (itemIndex + 1) % currentItems.length;
    }, 500); // Czas musi pasować do przejścia CSS (hotbar-transition)

    // Zaplanuj następne wyświetlenie
    startHotbarTimer();
}


// =========================
// INICJALIZACJA
// =========================

async function initializeHotbar() {
    const hotbar = document.getElementById(HOTBAR_CONFIG.HOTBAR_ID);
    if (!hotbar) return;
    
    // ZAWSZE czyścimy hotbar przy inicjalizacji
    hotbar.innerHTML = ''; 

    const lang = getLanguage();
    currentItems = await fetchNewsData(lang);
    
    if (currentItems.length > 0) {
        // Dodanie klasy dla przejścia CSS (jeśli używasz)
        hotbar.classList.add('hotbar-transition'); 
        hotbar.style.display = 'block'; // Upewnij się, że jest widoczny
        
        // --- Obsługa Najechania Myszą (Hover) ---
        hotbar.addEventListener('mouseenter', () => {
            isHovering = true;
            clearTimeout(hotbarTimer);
            // Pauzowanie i ustawienie timera na długą pauzę
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
    } else {
         // Ukryj hotbar, jeśli brak danych
        hotbar.style.display = 'none';
        console.log("Hotbar: Brak danych do wyświetlenia. Ukrywanie paska.");
    }
}

// Uruchomienie inicjalizacji
document.addEventListener('DOMContentLoaded', initializeHotbar);
