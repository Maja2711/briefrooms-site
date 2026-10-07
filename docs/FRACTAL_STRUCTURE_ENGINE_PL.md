# FSE — Fractal Structure Engine

**Module ID:** `IN-09`  
**Tryb:** `SHADOW_ONLY` / research challenger  
**Authority:** zero production authority

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
- cadence: 5 minut, przez dedykowany workflow `fse-intraday-fast.yml` uruchamiany w minutach 02/07/12/17/22/27/32/37/42/47/52/57; awaria WES/Weekly nie może zatrzymać FSE fast, a nowy cykl nie kasuje poprzedniego w trakcie publikacji;
- 1m/5m/15m/1h są odświeżane z jednego wspólnego 1-minutowego feedu;
- 4h/1d/1w pozostają z pełnego godzinowego FSE-PHASE;
- ścieżka nie tworzy snapshotów/resolution i nie zasila HSE2;
- zero production authority;
- frontend odświeża aktywną zakładkę FSE co 60 s;
- snapshot jest oznaczany STALE po 12 minutach lub gdy ostatnia świeca źródłowa ma >12 minut; próg daje bufor ponad nominalny rytm 5-minutowy.
