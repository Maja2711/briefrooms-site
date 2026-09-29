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
