# Stock Trading v2 — drugi etap naprawy, 28.10.2026

Zakres użytkownika: punkty 1, 2 i 4. Punkt 3 pominięty: kontrakt PRE/REGULAR nie zmienia się.
Moduły: TR-04, LE-01..06, LE-09, LE-11. Main pozostaje jedynym writerem produkcji;
research zapisuje wyłącznie badania i dowody. Zmiana kodu nie jest zdarzeniem otwarcia pozycji.

## Rzeczywista pętla i niezmienność historii

Historyczny przebieg harmonogramu [40433099178](https://github.com/Maja2711/briefrooms-site/actions/runs/40433099178)
kończył się konfliktem niezmiennego HOLD/EXIT, blokując wszystkie dalsze etapy.
Po wcześniejszej naprawie kontrolowany [40598025731](https://github.com/Maja2711/briefrooms-site/actions/runs/40598025731)
przeszedł cały łańcuch: ponownie wykorzystał 1499 wyników i dopisał 5297, generując osiem ograniczonych Challengerów.
Kolejny harmonogram [40615274510](https://github.com/Maja2711/briefrooms-site/actions/runs/40615274510)
wykorzystał ponownie 6796 wyników i dopisał 14; zamrożenie 37 istniejących decyzji nie tworzyło duplikatów.
Ujawnił rzeczywisty błąd monitora: pliki `.cohorts` odczytywano jak oceny. Ten etap naprawia jego odczyt.

Porównanie obiektów Git między `a659c7c141245fb384c05f66f3b2b826556029a6` i
`e299d715e90e159d8042861769a51b4d20586de1`: wszystkie **4799** wcześniej obecnych plików
experience/admission/position-experience/position-observations/outcomes/challengers/evaluations
zachowały identyczne obiekty, brak usunięć; przybyło **5426** plików.
Nie jest to dowód rentowności Challengerów: żaden nie przeszedł formalnej oceny.

Monitor ma osobne liczniki rozliczeń, regret i ocen; kolekcja przyszłej taśmy ma oddzielny stan.
Zmieniony czas raportu nie jest postępem. Uszkodzenie źródeł zachowuje ostatnie zweryfikowane
liczniki, raport FAILED/UNAVAILABLE i błąd procesu. Zmiana istniejącego rekordu immutable
blokuje commit; diagnostyka zostaje także w artefakcie Actions.

## Dane i ekonomika

Nowe obserwacje sektora weryfikują emitenta: GPW ticker/ISIN oraz oficjalna strona spółki,
US ticker/nazwa/giełda oraz dane Nasdaq. Obserwacje mają źródło, hash i czas odbioru.
Są osobnym ledgerem, a risk view korzysta z nich w pamięci, bez przepisywania starych decyzji.
Brak lub błąd źródła jest widoczny w raporcie pokrycia. Klasyfikacja bieżąca nie staje się
historyczną klasyfikacją spółki.

Stan odczytu 28.10.2026: potwierdzone **3/3 GPW** (LPP, ALE, OPL), **0/3 US**
(SAP, NXPI, MCK — timeout oficjalnego Nasdaq). Nowy ledger i raport pokrycia są
osobnymi plikami danych. Dla ALE prawidłowo zweryfikowano kompletny zestaw pól
klasyfikacji w części odpowiedzi HTML; receipt jawnie zachowuje `transport_complete=false`.
Dla OPL odpowiedź była kompletna. Hashe odpowiedzi, rzeczywiste czasy i wąskie
wyciągi pól dowodowych są zapisane w `stock_trading_metadata_receipts/`.
Nie dopisano sektorów US ani nie przedstawiono ich jako uzupełnionych.

Bid/ask ma jawny czas i stany OBSERVED/STALE/INVALID/UNAVAILABLE. Spread nie jest wywodzony
z ostatniej ceny ani udawany jako faktyczny koszt brokera. Koszty papierowe pozostają
osobnymi, zamrożonymi założeniami. Nieznanych kosztów historycznych nie uzupełnia się zerami.
Nowy ciągły test v2 ocenia zmianę od wspólnego, zamrożonego początkowego marku i ilości;
koszty sprzed początku próby są wspólne i utopione. Przyszła rezerwa kosztu likwidacji
jest jawnie stresowym założeniem. Nowe pozycje mają pełne zamrożone koszty obu stron.
Nie zmienia to historycznej ekonomiki ani rygorystycznej oceny v1.

Surowe dane 15-minutowe są zamrażane dla obserwowanych kandydatów i watchlisty; wirtualne
re-entry wylicza cechy względem własnego stopa. Brak potrzebnych danych daje PENDING.
Nowe subskrypcje danych trwają 180 dni, mają limit 40 aktywnych nazw na rynek oraz
40 nowych tygodniowo. Stare subskrypcje 168h pozostają identyczne; dłuższa obserwacja
jest odrębnym zapisem od rzeczywistego czasu utworzenia. Osiągnięcie limitu jest widoczne
w zamrożonym raporcie pokrycia. Limity dotyczą zbierania danych, nie liczby pozycji.
Wycenę otwartych pozycji na granicy sesji wyznacza źródłowy bar kończący się dokładnie
o oczekiwanym zamknięciu. Nie powstaje transakcja ani wymuszone wyjście. Brak tej wyceny
blokuje formalną ocenę; sam czas zamknięcia indeksu nie wystarcza.

## Prospektywny protokół v2

Szczegółowy kontrakt i źródła metody: [STOCK_TRADING_CASH_EDGE_LEARNING.md](STOCK_TRADING_CASH_EDGE_LEARNING.md).
Pierwsza kwalifikowana okazja zamraża pełną przyszłą taśmę 60 sesji; portfele prowadzone są ciągle.
Nie dobiera się spółek po wynikach, nie resetuje pozycji i nie przycina zwycięzców na granicy okna.
Oficjalny kalendarz jest zapisany przed decyzją, a każde oczekiwane zamknięcie musi mieć późniejszy
rzeczywisty receipt providera. Brak dnia nie przesuwa endpointu. GPW obejmuje tylko zweryfikowane
lata 2026/2027, NYSE istniejący adapter 2026–2028; koniec zakresu pozostaje PENDING.
Źródła: [dni bez sesji GPW](https://www.gpw.pl/szczegoly-sesji),
[godziny GPW, §5](https://www.gpw.pl/pub/GPW/files/PDF/regulacje/SZOG_GPW_WATS_pl.pdf),
[NYSE](https://www.nyse.com/trade/hours-calendars).

Okna 1/2 sesji dają 60/30 porównań, ale jednostką formalnego testu jest tygodniowa zmiana netto
całego portfela. E-value dotyczy jawnej ograniczonej warunkowej średniej; nie dowodzi bezwarunkowej
średniej surowego zysku ani przyszłej rentowności. Surowy wynik netto, obie połowy, koncentracja
wkładu i obsunięcie mają oddzielne bramki. Bootstrap blokowy jest wyłącznie analizą wrażliwości.
Werdykt jest pojedynczy i niezmienny. Budżet rodziny pozostaje dożywotni: 24 próby i alpha 0,05/24.
Stare próby v1 nie są konwertowane ani ponownie oceniane pod nową metodą.

## Ograniczenia odbioru

Około 12 tygodni jest czasem zbierania 60 sesji, a nie terminem gwarantowanego PASS.
Źródła sektorów i bid/ask mogą odmówić odpowiedzi; raport ma wówczas pozostać częściowy.
Pełny przyszły wynik nowego protokołu musi powstać prospektywnie. Testy deterministyczne
potwierdzają integralność mechanizmu, nie przewagę rynkową.

## Weryfikacja przed PR

25 plików kontraktu CASH jest identycznych bajtowo w main i research.
Sprawdzono 253 testy Stock Trading main oraz 211 research; regresja pełnego
historycznego snapshotu wymagała poprawy samej kopii fixture: zawiera ona już
prospektywne admission, więc musi zachować ich istniejący epoch. Po tej poprawce
10/10 testów admission przeszło, włącznie z zachowaniem wszystkich oryginalnych bajtów.
Osobna końcowa weryfikacja niezależnego dowodu main i ciągłego protokołu: 18/18 PASS.
Przeszły także cztery samodzielne zestawy promocji/fabryki, 26 testów airlock/handoff,
bootstrap mapy 1.44 oraz kontrola składni workflow. Wyniki CI i późniejszy przebieg
rzeczywisty należy dołączyć po publikacji PR i wdrożeniu poprawki monitora.
