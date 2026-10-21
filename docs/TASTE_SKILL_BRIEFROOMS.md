# Taste Skill dla BriefRooms — integracja wyłącznie deweloperska

## Status i pochodzenie

- Repozytoryjny Agent Skill: `.agents/skills/design-taste-frontend/`.
- Upstream: [Leonxlnx/taste-skill](https://github.com/Leonxlnx/taste-skill), commit `6010f91a9935f1c167dfc500fb1a475681892e6b` (20.10.2026), licencja MIT; plik `LICENSE` jest zachowany.
- Zawartość: oryginalny `SKILL.md`, referencje, biblioteka inspiracji, skrypty Node oraz wymagane zasoby. Pominięto katalog upstream `evals/`, niepotrzebny do zwykłego użycia skillu.
- Aktualizacje upstream wymagają osobnego PR i audytu zmian. Nie pobieraj ani nie wykonuj automatycznie nowego kodu z `main`.
- Instalacja **nie** zmienia strony, procesu publikacji ani harmonogramu; skrypty nie uruchamiają się automatycznie.

## Sposób używania

Do pracy z frontendem agent najpierw czyta `AGENTS.md`, tę instrukcję i `.agents/skills/design-taste-frontend/SKILL.md`. Jeśli zakres dotyczy architektury lub logiki inwestycyjnej, obowiązuje także wcześniejszy odczyt obu kanonicznych map architektury.

Wymagania do uruchamiania lokalnego: **Node.js 22+**, **Chrome, Edge lub Chromium** do weryfikacji w przeglądarce. Wyszukiwanie fotografii przez `scripts/assets.mjs` wymaga połączenia sieciowego i weryfikacji praw do materiałów.

```bash
# Uruchom z katalogu głównego repozytorium
node .agents/skills/design-taste-frontend/scripts/draw.mjs --list sectors
node .agents/skills/design-taste-frontend/scripts/draw.mjs --list roles

# Podgląd statycznej wersji strony na localhost, bez deploymentu
python3 -m http.server 8000 --bind 127.0.0.1
# W osobnym terminalu:
node .agents/skills/design-taste-frontend/scripts/inspect.mjs http://127.0.0.1:8000/pl/ --out .taste-skill/audit-pl
node .agents/skills/design-taste-frontend/scripts/inspect.mjs http://127.0.0.1:8000/en/ --out .taste-skill/audit-en

# Audyt konkretnego dashboardu: podstaw właściwy lokalny adres
node .agents/skills/design-taste-frontend/scripts/inspect.mjs http://127.0.0.1:8000/pl/ --app --out .taste-skill/audit-app
```

Dla dashboardów przed wykonaniem poleceń przeczytaj `references/app-screens.md`. Nie zakładaj, że ścieżka `/pl/` jest dashboardem; powyżej to tylko przykład składni `--app`. Lokalny serwer statyczny może nie odtwarzać wszystkich zależności produkcyjnych. Audyt należy przeprowadzić świadomie w odizolowanym środowisku testowym. Pliki robocze `.taste-skill/` są ignorowane przez Git.

## Brief projektowy BriefRooms

BriefRooms to dwujęzyczny portal źródłowych briefów oraz interfejsów danych i strategii inwestycyjnych. Celem projektu wizualnego jest **czytelność, weryfikowalność informacji i zaufanie**, nie marketingowe efekty dla samych efektów.

1. **Nie zgaduj marki**: przed zmianą przeczytaj faktyczny HTML/CSS/JS, zobacz logo, obecne kolory i typografię, istniejące `/pl/` i `/en/`. Zachowaj rozpoznawalność.
2. **Dobierz tryb pracy**: publiczne strony i strony redakcyjne to *page*; terminale, dashboardy, ekrany analiz, tabele i wykresy to *app screen*. Reguły dla nich są różne.
3. **Wyłącznie prawdziwe dane**: nie wolno wymyślać notowań, wyników inwestycyjnych, danych z modeli, track record, opinii klientów, cytatów, źródeł ani statusów SLA. Testowe dane muszą być wyraźnie oznaczone i nie mogą zostać opublikowane jako rzeczywiste.
4. **Frontend jest read-only wobec tradingu**: brak zmian w decision state, Belief Core, risk, learning, publikacji lub egzekucji. Żaden skrypt graficzny nie ma prawa inicjować workflow transakcyjnego lub mutować produkcyjnego stanu.
5. **Bez regresji platformy**: weryfikuj spójność PL/EN, SEO i canonical/hreflang, PWA, linki, kontrast, nawigację klawiaturą, responsywność, WCAG i `prefers-reduced-motion`. Nie zmieniaj mechanizmu redirectu językowego bez osobnego zlecenia.
6. **Prawa i prywatność**: nie dodawaj zewnętrznych trackerów, skryptów ani CDN bez uzasadnienia i przeglądu; obrazy tylko na znanych licencjach z odpowiednią atrybucją.
7. **Cykl walidacji**: najpierw audyt istniejącego widoku, potem projekt i zmiana w osobnym PR, screenshoty mobilne/tablet/desktop, raport najważniejszych usterek i testy regresji. Nie łącz automatycznie PR bez akceptacji.

## Granice wdrożenia

To integracja narzędziowa i dokumentacyjna; żaden plik aplikacji, workflow ani kontrakt architektoniczny nie jest zmieniony. Jeśli przyszły projekt zmieni architekturę, obowiązuje jednoczesna aktualizacja `docs/ARCHITECTURE_MAP_PL.md` i `docs/ARCHITECTURE_MAP_EN.md`.
