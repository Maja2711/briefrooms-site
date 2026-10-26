# EUR/USD Daily — produkcyjny tryb H1 Range-Edge

Zakres: **wyłącznie EUR/USD Daily, paper trading**. To jest zmiana zasad aktywnego silnika, a nie obserwator Shadow. Nie uruchamia zleceń u brokera.

## Wejście

Wyłącznie natywna decyzja Belief Core (LONG/SHORT). H1 Bollinger 30×2,5 z ukończonych świec. RANGE wymaga płaskiej środkowej bandy, szerokości 12–70 pipsów i ekspansji ostatnich 3h ≤6 pipsów. LONG wymaga rzeczywistego dotknięcia dolnej bandy i odbicia o ≥0,5 pipsa; SHORT analogicznie górnej. Potwierdzenie to **sześć ciągłych, zamkniętych świec 1-minutowych** bez przyszłych danych. Pozycja w strefie do 12% szerokości od bandy. W środku RANGE nowe wejście jest blokowane. Silny trend zachowuje dotychczasowe zasady.

Sygnał **nie** omija ochrony zdarzeń makro, zabezpieczeń D1, re-entry i natywnego filtra polityki. EPE musi ponownie zatwierdzić fill w strefie bandy i spójność dwóch feedów (≤2,5 pipsa tylko dla RANGE, w pozostałych przypadkach dotychczasowy kontrakt).

## Wyjście

Dla **nowej** pozycji z zapisanym, niezmiennym dowodem wejścia w RANGE na maksymalnie 45 minut opóźnia się jedynie miękki warunek R_PACE `−0,35R`, jeżeli obserwacja minutowa jest ciągła, strata pozostaje powyżej `−0,70R`, a cena nie wybija oryginalnego kanału o więcej niż 3 pipsy. Twardy SL/TP ma pierwszeństwo; osobny exit przy zdarzeniu makro pozostaje aktywny. Nie zmieniono starych ani trendowych pozycji.

## Weryfikacja

Przed scaleniem: CI i regresja wszystkich aktualnych testów Daily. Po scaleniu: potwierdzenie pierwszego rzeczywistego cyklu, świeżości H1/1m, wejścia przez EPE i watchera oraz SLA. Bez tego nie ma dowodu poprawności produkcyjnej. Ta zmiana nie dowodzi przewagi statystycznej i może generować większe straty do `−0,70R` przed powrotem lub SL. W razie regresji natychmiastowy rollback PR.