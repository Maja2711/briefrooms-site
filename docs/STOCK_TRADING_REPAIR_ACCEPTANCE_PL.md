# Naprawa uczenia i oceny Stock Trading v2 — odbiór

Stan: 27 października 2026. Zmiany przygotowano do przeglądu na dwóch gałęziach. Nie uruchomiono produkcji, nie przeliczono jej historii i nie dokonano promocji na żywych danych.

## Co zmienia decyzję wejścia

Analiza Deep Evidence dostarcza użyteczność kandydata i plan ryzyka. Produkcyjny bridge porównuje tę użyteczność z CASH i ponownie sprawdza aktualny kurs, świeżość, geometrię ryzyka, re-entry oraz wolne miejsce i ryzyko portfela. Cena referencyjna analizy nie jest ceną wykonania. Oddalenie bieżącej ceny od referencji o więcej niż jedną początkową jednostkę ryzyka wymaga odświeżenia analizy.

Uczona zmienna odpowiada temu rzeczywistemu porównaniu: `minimum_cash_edge_points`, początkowo 5 ponad CASH=50. Kandydat o użyteczności 54,5 może stać się kwalifikowany przy zmianie przewagi z 5 na 4, jeśli przechodzi pozostałe bramki. Zmiana z 5 na 6 może z kolei wstrzymać kandydata o użyteczności 55,5. Każdy wariant dotyczy jednego rynku i jednej zmiennej; nie optymalizuje nieaktywnego progu legacy 72.

Ta poprawka nie dodaje ograniczenia premarketu. Dotychczasowy kontrakt sesji wykonywania pozycji pozostaje bez zmian; nie należy utożsamiać dostępności analizy/kursu z uprawnieniem bridge do wykonania pozycji.

## Kryteria odbioru

| Wymaganie | Implementacja i sprawdzenie | Co wymaga przyszłych danych |
|---|---|---|
| Ponowienie nie zmienia historii | Decyzja HOLD/EXIT pozostaje niezmienna; kolejny odczyt trafia do osobnego ledgeru. Zmienione dane OHLC lub wersja replay nie zastępują terminalnego wyniku. Testy zachowują bajty 1 059 doświadczeń, 2 078 admissions, 1 499 wyników i 15 dawnych decyzji zarządzania. | Regularność rzeczywistych nowych zapisów po wdrożeniu. |
| Rzeczywisty błąd v2 tworzy ograniczony wariant | Rozdzielono błąd przewagi nad CASH od rankingu, zajętych slotów i hard gates. Factory deklaruje ±1, granice 1..15, oba rynki i oba horyzonty. Nowe odrębne okazje w ciągu dnia nie giną przez stare dzienne dedupe. | Rzeczywista przewaga wariantu nad aktualnym Championem. |
| Uczciwy test przed promocją | Main rejestruje wszystkie próby i pierwsze odczyty członków. Kohorta obejmuje pierwsze kwalifikujące okazje z natywnych danych main, nie dowolny podzbiór research. Pełny aktualny hash polityki i manifestu musi pasować. Zamrożenie i receipt muszą poprzedzać źródłowe zamknięcie sesji. | Niezależna przyszła próba dla każdej zmiany. |
| Zamknięty werdykt nie zmienia składu | Stałe N≥30, epizody co najmniej 168h, brak podmiany brakujących/przegranych, jeden werdykt terminalny. Rodzina ma dożywotnio 24 próby i alpha 0,05/24. Brak rejestru blokuje działanie zamiast resetować budżet. | Zebranie kompletnego N. Kontrakt jest celowo konserwatywny i może wymagać miesięcy. |
| Testuje się faktyczną strategię po kosztach | Replay uruchamia rzeczywisty bridge i lifecycle: CANARY/FULL, sloty, SL, ratchet, runner, re-entry, koszty, NAV i obsunięcie. Uwzględnia benchmark i zależność od największego zwycięzcy. Main sam odtwarza próbki i statystykę; PASS/hash nie wystarczą. Końcowa bramka wymaga niezależnych wyników dla 1 i 2 sesji tego samego deploymentu. | Porównywalny, dodatni wynik netto na rzeczywistych przyszłych obserwacjach. |
| Kontrola ryzyka portfela | Limit potencjalnej straty przy SL 1 050 PLN lub USD na rynek, maksymalnie 2 pozycje w zweryfikowanym sektorze, osobny limit 3 UNKNOWN. Sektory i ich źródła przechodzą przez pełny pipeline. | Uzupełnianie brakujących klasyfikacji ze źródeł; brak sektora nie staje się zgadywaną kategorią. |
| Monitoring wdrożonego wariantu i rollback | Przy promocji zamraża się poprzedni/nowy wariant i 30-dniowy kontrakt. DD≥5% albo końcowe pogorszenie netto względem kontroli o ponad 1 pp wyzwala ograniczony rollback w nowej rewizji. Test przechodzi przez rzeczywisty replay i automatyczny rollback bez zmiany portfela. | Wyniki po rzeczywistej promocji; obecnie nie ma nowej promowanej wersji. |

## Przepływ i authority

`stock-trading-v2` prowadzi discovery i dowody research. `main` pozostaje jedynym writerem produkcyjnej polityki, manifestu, portfela i rollbacku. Research nie dostarcza wykonywalnego kodu do promocji.

Discovery zamraża pełne snapshoty polityki, manifestu i konfiguracji bridge oraz aktywuje nową epokę admission przed decyzjami. Dopiero natywny odczyt okazji przez main dopuszcza ją do kohorty. Po udanym cyklu produkcji osobny szybki job zamraża członków w research bez ponownego skanowania i bez ponownego wywołania produkcji. Main potwierdza ich odbiór przed wynikiem.

Zapisane obserwacje źródłowe, watchlist i potwierdzenia zamknięcia sesji nie mają prawa otwierać ani zamykać pozycji. Produkcyjni writerzy, promocja i monitor współdzielą lock. Push kodu wykonuje walidację; nie uruchamia wejść, discovery ani rozliczania uczenia. Monitor postępu zapisuje osobno watermark outcome, regret i ocen, również po nieudanym jobie.

## Ograniczenia pomiaru

- Koszty są jawnym założeniem stresowym: GPW 20 bps round trip, US 10 bps przy niezmienionej cenie. Nie są pomiarem brokera.
- Dawne pozycje bez zamrożonych kosztów pozostają nieocenialne netto. Nie dopisuje się fikcyjnych kosztów ani zer do historii.
- Brak natywnych kwotowań, świec dla wirtualnej pozycji, benchmarku lub danych re-entry daje `PENDING`. Dotyczy to m.in. sytuacji, gdy tylko wariant wirtualny zaliczył stop, a potrzebnych później danych 15-minutowych wcześniej nie zebrano.
- Brak pełnej pary danych lub zmiana kodu/polityki zatrzymuje ocenę monitora; nie oznacza automatycznie dobrej jakości wdrożenia. Status wskazuje przyczynę.
- Test pełnych 30 epizodów używa syntetycznych, zamrożonych wejść przez prawdziwe funkcje produkcyjne. Wykazuje działanie mechanizmu, nie przyszły zysk rynkowy.

## Weryfikacja końcowa

Na obu gałęziach wykonano pełny zestaw testów Stock Trading offline: **214/214 main** i **178/178 research**. Dodatkowo przeszło **26/26** testów NO RETROACTIVE i przekazywania powiadomień oraz cztery samodzielne zestawy dla promocji, exact binding, bounded intake i Factory. Sprawdzono składnię YAML, 100 bloków powłoki i 11 wbudowanych bloków Python. Guard map architektury potwierdza zgodność PL/EN wersji 1.43.

Testy na rzeczywistych, skopiowanych ledgerach porównują oryginalne bajty, nie tylko liczbę plików. Test pełnego dowodu 30 epizodów i test ekonomicznego rollbacku wykonują rzeczywiste funkcje bridge/lifecycle/replay/statystyki na syntetycznych, zamrożonych danych. Nie wykonano live settlement, pobierania bieżących cen na potrzeby testów ani zapisu kanonicznych pozycji.

## Kolejność wydania i wycofanie zmian

Najpierw należy wydać zgodną część research w PR #982, następnie ponowić kontrolę zgodności gałęzi i wydać PR #981 do `main`. Do powstania rejestru main nowa część research oczekuje na receipt i nie może uzyskać formalnej promocji. Guard nadal porównuje rzeczywistą gałąź research: nie zastępuje się jej wygodnym snapshotem z PR. Zestaw 20 audytowanych plików musi mieć identyczną zawartość na obu gałęziach. Samo otwarcie PR nie wdraża tych zmian.

Wycofanie kodu i konfiguracji nie usuwa nowych ledgerów ani nie cofa historii. Terminalne dowody pozostają niezmienne; nowe polityki działają wyłącznie na przyszłe decyzje. Brakującego rejestru prób nie wolno zastąpić pustym plikiem. Odzyskuje się jego zachowaną wersję.

Szczegółowe kontrakty: [persystencja](STOCK_TRADING_LEARNING_PERSISTENCE.md), [uczenie CASH](STOCK_TRADING_CASH_EDGE_LEARNING.md), [ryzyko, replay i monitoring](STOCK_TRADING_PORTFOLIO_EVALUATION.md).
