# FSE — Fractal Structure Engine

**Module ID:** `IN-09`  
**Tryb:** `SHADOW_ONLY` / research challenger  
**Authority:** zero production authority

**Aktualna metodologia:** `FSE-CORE-2.0` / `FSE-PHASE-2.0` / `FSE-ADAPTIVE-2.0`; projekcja `FSE-FAST-2.0`. Kontrakt naprawy w sekcji 12 zastępuje wcześniejsze zasady czasu, horyzontu i zaliczania kohort opisane historycznie poniżej.

## 1. Cel

FSE opisuje geometrię rynku w wielu skalach i rozdziela dwa pytania:

1. **Structural Risk** — czy struktura rynku staje się niestabilna, wieloskalowo niespójna lub gruboogonowa?
2. **Fractal Memory** — czy bieżący wieloskalowy fingerprint przypomina wcześniejsze struktury, po których rozkład przyszłych zmian miał określony kierunek i ryzyko?

FSE nie jest klasycznym wskaźnikiem „fractals”, nie wyszukuje wizualnych figur Mandelbrota i nie jest samodzielnym silnikiem transakcyjnym.

## 2. Dane i skale

Pierwsza implementacja używa OHLC z Yahoo Chart jako wtórnego źródła badawczego dla:

- EUR/USD: `EURUSD=X`,
- BTC/USD: `BTC-USD`,
- S&P 500: `SPY` jako płynny proxy indeksu.

Skale:

`5m -> 15m -> 1h -> 4h -> 1D`

Skala 4h jest deterministycznie resamplowana z 1h. Każda skala analizuje do 256 ostatnich świec.

## 3. Structural Risk

Koncepcyjnie:

[
Risk_t=f(scale, persistence, multifractality, tails)
]

Implementacja v1 wyznacza cztery znormalizowane komponenty:

- **scale_instability** — rozjazd zrealizowanej zmienności po przeskalowaniu czasem,
- **persistence_extreme** — odchylenie Hurst `h(2)` od 0,5 i niespójność pomiędzy skalami,
- **multifractality** — proxy `max(h(q))-min(h(q))` dla `q={1,2,3}`,
- **tails** — excess kurtosis + relacja 95. percentyla bezwzględnych zwrotów do mediany.

Wynik:

[
Risk =
0.28,ScaleInstability+
0.18,Persistence+
0.24,Multifractality+
0.30,Tails
]

Wagi są **research priors**, nie parametrami uznanymi za optymalne.

### Regime Detector

FSE klasyfikuje strukturę jako:

- `STABLE`,
- `TRENDING`,
- `MEAN_REVERTING`,
- `TRANSITION`,
- `TURBULENT`.

Regime nie oznacza kierunku. `TURBULENT` może wystąpić przy poprawnym LONG albo SHORT.

FSE pokazuje także badawczą geometrię ryzyka, np. redukcję sizingu lub zmianę dystansu SL, ale zawsze z:

`production_applied=false`.

Nie może ona zmienić realnego sizingu ani SL bez późniejszej, jawnej integracji z polityką ryzyka konkretnego silnika.

## 4. Fractal Memory

Fingerprint nie porównuje nominalnego poziomu ceny. Ścieżka jest normalizowana lokalnym ATR:

[
x_i=rac{P_i-P_0}{ATR}
]

Bieżący fingerprint jest porównywany z historycznymi strukturami metodą odległości RMS po normalizacji. Similarity:

[
S=rac{1}{1+RMS}
]

Najbliższe analogi tworzą ważony rozkład forward:

- `P(up)`,
- median forward return,
- median adverse excursion,
- top/mean structural similarity.

### Dwa etapy pamięci

**Bootstrap:** historyczne analogi 1h dostarczają pierwszego rozkładu, ale nie są uznawane za prospektywny dowód skuteczności.

**Prospective Fractal Memory:** każdy bieżący pełny feature vector zostaje zamrożony przed outcome. Po co najmniej 12 rozliczonych snapshotach analogi mogą być wybierane z własnej, prospektywnie zbudowanej pamięci FSE.

## 5. Freeze i weryfikacja

Prywatny durable state:

- `fse_snapshots.jsonl`,
- `fse_resolutions.jsonl`.

Oba są append-only i hash-chained.

Snapshot zawiera m.in.:

- timestamp i cenę referencyjną,
- `risk_score`,
- regime,
- `P(up)`,
- ATR,
- feature vector,
- authority = zero.

Outcome jest wiązany dopiero z **4 następnymi świecami 1h**, czyli 4 kolejnymi godzinami rynkowymi. Pozwala to uniknąć błędu weekendów i zamkniętych sesji.

## 6. HSE2 — formalne hipotezy

FSE jest siódmym research producerem Hypothesis Shadow Engine 2.0.

Dla każdego instrumentu wystawia dwie rozłączne miary:

### A. Directional Fractal Memory

[
Brier=(P(up)-Y_{up})^2
]

edge wobec baseline 50/50:

[
Edge_{dir}=0.25-Brier
]

### B. Structural Risk

Zamrożony `risk_score` jest traktowany jako prawdopodobieństwo dużego ruchu. W v1 event dużego ruchu oznacza:

[
|R_{4h}| geq 0.75 	imes ATR_{1h,frozen}
]

i analogicznie:

[
Edge_{risk}=0.25-Brier_{risk}
]

Każda hipoteza ma formalne `N=40`. HSE2 uznaje tylko próbki powstałe po własnym freeze. Historyczny bootstrap FSE nie dostaje credit w HSE2.

## 7. Authority i ścieżka ewentualnej promocji

FSE v1 ma jawnie wyłączone:

- production policy writeback,
- ranking writeback,
- sizing writeback,
- stop-loss writeback,
- source-model writeback,
- execution,
- automatic promotion.

Ewentualna przyszła ścieżka:

`FSE -> HSE2 prospective evidence -> statistical gate / challenger governance -> jawny per-engine bridge -> engine-owned RiskPolicy`

Nie wolno zbudować globalnego FSE risk actuatora omijającego politykę ryzyka EURUSD, WES, Stock Trading lub BRACE. Zgodnie z mapą architektury **risk remains per-engine**.

## 8. Ograniczenia v1

- Yahoo jest źródłem wtórnym; nie jest feedem egzekucyjnym.
- `SPY` jest proxy S&P 500, nie futures ani sam indeks.
- 4h jest resamplingiem 1h.
- `multifractality_proxy` nie jest pełnym MF-DFA; to spread generalized Hurst `h(q)`.
- Progi regime i wagi risk score są hipotezami startowymi.
- Podobieństwo struktury nie dowodzi przyczynowości.
- Bootstrap historyczny służy inicjalizacji, nie walidacji alpha.
- Produkcyjny wpływ sizing/SL pozostaje wyłączony do czasu prospektywnej walidacji.

## 9. FSE-PHASE — Phase Engine / Intrabar / Cross-Scale

Rozszerzenie FSE-PHASE-1.0 pozostaje częścią modułu IN-09; nie jest osobnym silnikiem produkcyjnym.

Pipeline:

FSE core -> Phase Engine -> Intrabar Formation -> Cross-Scale Alignment -> Phase Fractal Memory -> prospective HSE2 -> P Calibration Challenger -> manual promotion gate

### 9.1 Skale

Phase Engine analizuje:

1m -> 5m -> 15m -> 1h -> 4h -> 1d -> 1w

- 1m, 5m, 15m, 1h, 1d są pobierane z wtórnego feedu badawczego Yahoo Chart.
- 4h jest deterministyczną agregacją 1h.
- 1w jest agregacją dziennych barów według tygodnia ISO.
- Skale miesięczne / wieloletnie nie są włączone do pierwszej wersji Phase Engine; długie konteksty są badane przez rolling daily history, nie przez pojedyncze „świece 10Y/100Y”.

### 9.2 Phase Engine

Każda skala otrzymuje deterministyczny opis:

- structure_id,
- phase: CONSOLIDATION / DEVELOPMENT / EXPANSION / MATURATION / TRANSITION / REVERSAL,
- direction,
- confidence,
- path efficiency,
- trend z-score,
- volatility ratio,
- curvature.

structure_id jest stabilnym hashem skwantowanego fingerprintu struktury. Nie oznacza odkrycia uniwersalnego „prawa fraktalnego”; służy do śledzenia podobnych stanów w pamięci badawczej.

### 9.3 Phase Fractal Memory

Bieżący częściowy kształt jest porównywany z prefiksami historycznych motywów przy siatce ukończenia:

40% / 55% / 70% / 85%.

Najlepiej dopasowany etap tworzy rozkład:

- phase_progress,
- P(up remaining),
- top/mean similarity,
- median remaining return,
- median remaining bars,
- liczba analogów.

To jest estymacja etapu na podstawie analogów, a nie deterministyczna deklaracja, że każdy rynek przechodzi te same fazy.

### 9.4 Intrabar Formation

Wyższa skala jest obserwowana od środka przez niższą:

- 5m <- 1m,
- 15m <- 5m,
- 1h <- 15m,
- 4h <- 1h,
- 1d <- 1h,
- 1w <- 1d.

FSE publikuje m.in. formation_progress, kierunek mikrostruktury, efficiency, pozycję wewnątrz bieżącego range i range fraction. Liczba oczekiwanych child-bars jest wyznaczana z historycznej mediany, dzięki czemu sesja SPY nie jest traktowana tak samo jak 24/7 BTC.

### 9.5 Cross-Scale Alignment

Dla sąsiednich skal liczony jest:

- directional agreement,
- podobieństwo efficiency / volatility-ratio / curvature,
- confidence,
- informacyjny fast-scale lead score.

Wynik syntetyczny:

- alignment_score,
- cascade_state = COHERENT / MIXED / FRACTURED,
- dominant_scale.

Alignment jest Evidence badawczym. Nie tworzy sygnału wykonawczego.

### 9.6 P Calibration / Regime-Phase Challenger

Challenger bierze:

- P_base z Deep Fractal Memory,
- P_phase z Phase Fractal Memory 1h,
- cross-scale alignment,
- regime z Structural Risk.

Wyznacza osobne P_challenger. Korekta jest twardo ograniczona do ±6 pp względem P_base.

W TURBULENT i TRANSITION challenger stosuje jawny shrink confidence. Parametry są zamrożonym research prior, a nie wytrenowanym kalibratorem produkcyjnym.

Nie wolno nadpisywać source-model P.

### 9.7 Prospective HSE2

Od wersji FSE-PHASE-1.0 FSE wystawia dodatkowe hipotezy:

1. Phase Fractal Memory vs 50/50 — metryka: prospective directional Brier edge.
2. Regime-Phase P Calibration Challenger vs frozen FSE v2 base P — metryka: Brier_base - Brier_challenger.

Każdy eksperyment ma N=40. Liczone są wyłącznie resolution powstałe z snapshotów zamrożonych po wdrożeniu FSE-PHASE-1.0. Stare FSE v2 snapshoty pozostają w ledgerze, ale nie otrzymują credit dla nowych hipotez.

### 9.8 Promotion

Publiczny promotion_gate może osiągnąć tylko:

- NOT_ELIGIBLE,
- READY_FOR_MANUAL_REVIEW.

automatic_promotion=false.

Nawet po pozytywnym HSE2 potrzebny jest jawny, per-engine bridge i promotion gate właściciela danego silnika. FSE nie może utworzyć globalnego risk/probability actuatora.

## 10. FSE Intraday Fast Projection

Krótki horyzont FSE ma osobną, lekką ścieżkę prezentacyjną uruchamianą przez niezależny workflow co 5 minut.

Pipeline:

Yahoo 1m / 7d -> 1m -> resample 5m / 15m / 1h -> Phase descriptor -> merge z ostatnim pełnym kontekstem 4h/1d/1w -> Cross-Scale Alignment -> `data/investments/fse_intraday_public.json`.

Zasady:
- docelowa świeżość: 5 minut; źródłem pierwszego wyboru dla LAB jest niezależny Cloudflare Worker `briefrooms-fse-fast` z własnym cronem `*/5 * * * *` i on-demand compute/cache, a `fse-intraday-fast.yml` + `data/investments/fse_intraday_public.json` pozostają fallbackiem statycznym; awaria GitHub `schedule` nie może już sama zatrzymać prezentacyjnej ścieżki FSE fast;
- 1m/5m/15m/1h są odświeżane z jednego wspólnego 1-minutowego feedu;
- 4h/1d/1w pozostają z pełnego godzinowego FSE-PHASE;
- ścieżka nie tworzy snapshotów/resolution i nie zasila HSE2;
- zero production authority;
- frontend odświeża aktywną zakładkę FSE co 60 s;
- snapshot jest oznaczany STALE po 12 minutach lub gdy ostatnia świeca źródłowa ma >12 minut; próg daje bufor ponad nominalny rytm 5-minutowy.


## 10. Autonomous Structure Discovery (ASD-1.0) — nowy, niezależny moduł SHADOW

**Cel:** odkrywać rodziny podobnych zachowań ceny bez katalogu nazwanych formacji giełdowych i bez przypisywania nowych struktur do sześciu etykiet FSE-PHASE. Dotychczasowy silnik Phase i Fractal Memory pozostają niezależnymi punktami odniesienia.

### Rozpoznawanie bez szablonów
- Analiza godzinowych świec EUR/USD (`EURUSD=X`), BTC (`BTC-USD`) i S&P 500 (`SPY`, **proxy, nie futures ES**); okna 32 i 64 świec.
- Normalizacja kształtu logarytmicznej ścieżki cenowej, bez zależności od nominalnego poziomu ceny.
- Nienadzorowane grupowanie z adaptacyjnym progiem odległości, łączeniem gęstych próbek i ponownym sprawdzeniem względem reprezentatywnego medoidu. Minimum sześć analogii historycznych do utworzenia rodziny.
- Własny, niezmienny `ASD-XXXXXXXXXX`, nazwa opisująca geometrię prototypu i neutralny opis eksploracyjny. **Zastosowanie nieustalone** aż do rzeczywistego prospektywnego potwierdzenia. Nazwy nie są klasami wzorców technicznych; medoid jest pokazywany na karcie jako znormalizowany wykres, nie prognoza ceny.
- Nie ma trwałego limitu 14 rodzin na instrument. Co cykl można dodać maksymalnie cztery nowe rodziny na instrument, a kolejne cykle kontynuują odkrywanie. Bliskie duplikaty (również rodzin już odrzuconych) są tłumione; rozdzielne odmiany mogą mieć niezmienny `parent_family_id`, bez przepisywania historii rodzica.

### Rygor wyników
- Historyczne analogie wolno wykorzystać do opisu i *wstępnej* częstości `P UP`. Nie są dowodem prospektywnym.
- Przy nowej, zakończonej świecy 1h zapisuje się snapshot **przed** wynikiem z `family_id`, ceną i zamrożonym `p_up` oraz prawdopodobieństwem bazowym. Dopasowania bez znanej rodziny również zapisuje się jako `FROZEN_NOVEL_UNMATCHED`.
- Dopiero po zamknięciu następnych czterech świec rynku można dopisać wynik do niezmiennego łańcucha SHA-256. Brak backdatingu i brak ponownego zaliczania tej samej obserwacji.
- Raport rozdziela `historical_sample_n` od `prospective_n`. Badamy oddzielnie poprawę **Brier dla kierunku** oraz **MAE prognozy bezwzględnej wielkości ruchu** względem parowanych benchmarków zamrożonych przed rezultatem. Wyniki 4-godzinne nakładają się w czasie, więc test ma estymator błędu HAC (opóźnienia 1–3) i konserwatywny próg 2,58. Status `REVIEW_CANDIDATE` wymaga co najmniej 40 naprawdę rozliczonych wystąpień tej rodziny i dodatniego testu dla co najmniej jednego zastosowania. Nie jest to dowód opłacalności transakcji.
- Nazwy i opisy są deterministycznie tworzone na podstawie danych; nie przypisujemy struktury do znanej formacji ani nie generujemy narracji językiem LLM bez evidence.

### Przepływ danych i uprawnienia
`FSE Autonomous Structure Discovery Shadow` — osobny, godzinny workflow z trwałym prywatnym stanem w GitHub Actions Artifact. Publiczny, tylko do odczytu `data/investments/fse_discovery_public.json` prezentuje katalog i evidence w zakładce FSE (PL/EN).

Zapis wyłącznie artefaktu publicznego, automatyczna promocja, Belief Core writeback, egzekucja oraz zmiany polityki ryzyka są **wyłączone**. Gdy brak źródła rynku lub archiwum pamięci, proces fail-closed — bez nadpisywania poprzedniej wiarygodnej publikacji.

**Ograniczenia:** silnik nadal korzysta z deterministycznego uczenia nienadzorowanego, nie z modelu generatywnego. Nie tworzy szablonowych zastosowań; do czasu walidacji publikuje `application_status=UNDETERMINED` i puste `applications`. Po >=40 rozliczeniach bez dodatniego wyniku usuwa rodzinę z publicznego aktywnego katalogu i dopasowań oraz zapisuje `RETIRED / NO_PROVEN_EDGE_AFTER_40` w prywatnym append-only ledgerze `lifecycle_events`. Nie kasuje oryginalnych definicji i wyników, bo byłoby to wymazywaniem dowodów. Blokuje ponowne odkrycie tej samej odrzuconej geometrii pod nowym ID. `REVIEW_CANDIDATE` pozostaje wyłącznie kandydatem do niezależnego przeglądu; **brak automatycznej promocji**. Łączenie dotyczy zapobiegania duplikatom geometrycznym, a rozdzielanie tworzenia nowych, niezmiennych podrodzin — nie nadpisujemy wcześniej zamrożonych rodzin.

## 11. FSE Adaptive Learning v1 — prospektywne uczenie w istniejącym FSE v2

**Komponent:** `FSE-ADAPTIVE-1.0`, wewnątrz modułu `IN-09`. Nie uruchamia nowego silnika ani niezależnego learning/promotion fabric. Zapisuje dodatkowe pola w istniejącym, prywatnym, hash-chained ledgerze `fse_v2_snapshots.jsonl` / `fse_v2_resolutions.jsonl`; emituje dwie nowe hipotezy do istniejącego HSE2 (`source_engine=FSE` i ten sam kontrakt FSE-PHASE).

**Adaptacyjny Ensemble:** korzysta wyłącznie z zamrożonych Deep Memory P, Phase Memory P i już istniejącego Regime-Phase Challenger P. Uczy nieujemnych wag z wcześniej zakończonych, niezależnych (nienakładających się czterogodzinnych targetów) rozliczeń; po >=12 takich blokach stosuje silnie regularizowaną, łagodnie aktualizowaną stratę Brier. Przewidywanie jest ograniczone do ±6 pp od Deep Memory i nie zmienia żadnego P źródłowego. Zero retrospektywnego odtwarzania wyniku: przy ponownej publikacji tej samej świecy stosowany jest wcześniej zamrożony wynik, nie aktualna reoptymalizacja.

**Osobny kalibrator ryzyka:** odróżnia structural risk index od P(dużego ruchu). Dopiero nowe snapshoty z risk index i zamrożonym ATR(1h) wyznaczają prawdziwe zdarzenie `abs(forward_return)>=0.75*frozen_atr_fraction`. Model estymuje historyczną częstość z wygładzaniem Beta i nieujemne, regularizowane nachylenie względem risk index. Aktywacja wymaga >=40 zweryfikowanych obserwacji oraz >=12 nienakładających się bloków; do tego czasu `p_large_move=null` (nie udaje skalibrowanego prawdopodobieństwa). Benchmarkiem jest **zamrożona empiryczna częstość dużego ruchu**, nie 50/50.

**Zamknięcie świecy:** resolver nie uznaje ostatniej świecy godzinowej, dopóki nie minie jej czas zamknięcia (timestamp otwarcia + 1 godzina). Rozliczenia i trening respektują ten warunek; brak backdatingu.

**Evidence:** oba dodatkowe pola P i oba benchmarki są zamrażane *przed* outcome. Resolver oblicza sparowane `Brier_baseline-Brier_adaptive` i `Brier_prior-Brier_risk` tylko z tych snapshotów. HSE2 rejestruje nowe, oddzielne hipotezy `FSE-ADAPTIVE-1.0` z własnym prospektywnym N=40 i nie dopisuje nowych wyników do historycznych hipotez FSE-PHASE. Wskaźniki w publikacji są read-only. `automatic_promotion=false`, wszystkie zero-authority flags pozostają bez zmian.

**ASD:** niezależny godzinny proces nadal zbiera immutable prospective matched/unmatched. Nie zwiększamy N z historii, nie przypisujemy niedopasowanych rodzinom, nie zmieniamy promieni/medoidów przed osobnym eksperymentem. Uczenie cech i generowanie nowych metodologii są odroczone na późniejsze etapy.

**Warunki niezbędne przed jakimkolwiek realnym użyciem:** dodatni prospective paired Brier vs poprawny benchmark, adekwatne bloki czasowe/HAC, odporność na drift, kontrola multiple testing i osobna promocja per-engine przez właściwy Evolution Gate. Shadow Champion nie jest Championem produkcyjnym.

## 12. Kontrakt 2.0 — naprawa błędów pomiaru i publikacji

- `Bar.timestamp` jest kanonicznym otwarciem świecy, oddzielonym od `source_observed_at`. Końcowy punkt kwotowania aktualizuje właściwy koszyk, nie tworzy nowej świecy o przypadkowej godzinie. Adapter sortuje/deduplikuje dane i kontroluje ceny finite/dodatnie oraz poprawność OHLC. SPY respektuje otwarcie sesji 09:30 America/New_York, DST i dostępne informacje o jej końcu. Agregacja 4h ma stałe granice i nie łączy różnych sesji.
- Snapshot wiąże wersję metodologii i kanoniczną świecę. Zapisuje oddzielnie źródłowy czas obserwacji, `bar_open_at`, rzeczywisty `frozen_at` i późniejsze `outcome_closed_at`. Ponowne wywołanie dla tej samej świecy nie zmienia zamrożonej prognozy. Resolver rozlicza wyłącznie cztery przyszłe świece i czeka na zamknięcie ostatniej; trening uwzględnia rzeczywisty koniec wyniku. Liczbowe P=0 pozostaje zerem, a nie wartością domyślną 0,5.
- Historyczny kontekst dzienny Deep Memory dopuszcza tylko świece zakończone w danym historycznym momencie. Nie może korzystać z późniejszego dziennego close.
- Phase publikuje osobno opisowe `p_up_remaining` do końca motywu i `p_up_4h` dla czterech następnych świec godzinowych. Wyłącznie stały target 4h zasila challenger, ensemble, Brier i odpowiednią hipotezę HSE2.
- Stare snapshoty/rozliczenia zachowują oryginalne bajty i hashe. Nowe wersje nie rozliczają zaległych starych snapshotów nową metodą i nie uczą się na poprzednich kohortach; legacy jest jawnie raportowane. HSE2 tworzy nowy freeze i własne N. Wcześniejsze oceny FSE trafiają do `historical_fse_experiments/results/lessons`, poza bieżącym formalnym wynikiem; źródłowe epoki/evidence/wyniki nie są przepisywane.
- ASD zachowuje własny append-only stan. Kontrola backendu i integralności nadal blokuje niepoprawne zbieranie; test prezentacji jest osobnym zadaniem i nie zatrzymuje kolektora. PL/EN pokazują aktualne poprawne dopasowania i katalog bez trwałego ucięcia liczby rodzin. Istniejące Shadow Observatory monitoruje katalog ASD i oznacza publikację starszą niż 3h jako STALE.
- Fast ma `transport_version=FSE-FAST-2.0`, ograniczone czasowo pobieranie i `source_status` per instrument: `market_state`, `source_reference_at`, surowy wiek oraz sesyjny `source_lag_minutes`. Stare lub przyszłe dane nie są uznawane za sukces tylko dlatego, że serwer zwrócił HTTP 200. Frontend próbuje fallbacku również przy brakujących/starych instrumentach i wybiera najnowszą poprawną skalę. Niedostępny deskryptor nie głosuje w trendzie. Stara pamięć analogów nie zostaje przypięta do nowej struktury.
- Publikacja PL/EN ma nową wersję URL skryptów; produkcyjne parity obejmuje oba renderery centralnego LAB. Deployment Edge kontroluje rzeczywistą świeżość źródeł, nie sam czas wygenerowania odpowiedzi.

Uprawnienia pozostają niezmienione: SHADOW_ONLY, brak execution, zmian produkcyjnej polityki/Belief i automatycznej promocji. Zmiana metodologii nie jest dowodem poprawy modelu; zbiera nowy prospektywny wynik od własnej granicy.
