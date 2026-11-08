# BriefRooms S&P 500 — automatyczny miesięczny House View (research-only)

## Cel i zakres
Miesięczny, źródłowy bilans ryzyka na 1M/6M/12M w pokoju Inwestycje / Long View. Jest to **automatyczny przegląd makro + taktyki**, a nie pełna wycena S&P 500, nie target indeksu, nie rekomendacja i nie wynik o zweryfikowanej trafności. Pierwotny redakcyjny House View z października 2026 pozostaje oznaczony jako archiwalny.

**Module IDs:** `TR-08` (czytany wyłącznie informacyjnie), `CT-05` (publikacja); **bez ingerencji** w `TR-05` WES/Weekly, `EP-05`, `IN-03`, `IN-09`, pozycje, risk policy lub dane wykonawcze.

## Źródła
Bez klucza API pobierane są surowe szeregi CSV FRED (Federal Reserve Bank of St. Louis), z przypisanym źródłem pierwotnym:
- `GDPC1`: BEA real GDP (poziom, najnowszy kwartał; obliczenie q/q SAAR).
- `PCEPI`, `PCEPILFE`: BEA headline i core PCE (zmiana r/r na bazie obserwacji sprzed 12 miesięcy).
- `DGS10`: rentowność 10Y USA (Treasury).
- `DCOILBRENTEU`: Brent EIA (USD/bbl).
- Opcjonalnie `data/investments/weekly/<week>.json`: ostatni zweryfikowany snapshot futures S&P 500, wyłącznie historyczna obserwacja `signals.ret20_pct`, a nie transakcyjny sygnał WES.

**Każda liczba ma datę obserwacji.** Data pobrania nie jest mylona z datą obserwacji ani release timestamp; nie prowadzimy nieudokumentowanego backfillu "co znano tego dnia". Filtr wieku: GDP ≤200 dni, PCE ≤100 dni, UST10Y ≤15 dni, Brent ≤22 dni, WES opcjonalnie zakończony tydzień ≤21 dni. Brak jednej z pięciu serii podstawowych, brak bazy rocznej, przyszła data lub anomalia zakresu -> **NO PUBLICATION**, stary snapshot i oryginalne daty pozostają.

## Sygnał i ograniczenia
Wersja `monthly_macro_risk_balance_v1` nie estymuje prawdopodobieństw.
- Wzrost q/q SAAR: +1 przy ≥2%, -1 przy <1%, -2 przy <0%, inaczej 0.
- Core PCE r/r: -2 przy ≥3,5%, -1 przy ≥2,7%, +1 przy ≤2,3%, inaczej 0.
- UST 10Y: -1 przy ≥5%, +1 przy ≤3,5%, inaczej 0.
- Brent: -1 przy ≥110 USD, +1 przy ≤70 USD, inaczej 0.
- Dynamika futures S&P (20 sesji): +1 przy ≥+3%, -1 przy ≤-3%, inaczej 0.
- `1M = 2×momentum + core + UST10Y + Brent`; bez świeżego momentum `unassessed`.
- `6M = 2×growth + 2×core + UST10Y + Brent`.
- `12M = growth + core + UST10Y + Brent`.
- Wynik ≥2 = umiarkowanie pozytywny, ≤-2 = defensywny, inaczej neutralny. **Neutralny to wynik z dostępnych czynników, nie prognoza zerowej stopy zwrotu**.
- `conviction=3/10` to jawny limit prezentacyjny, nie model statystyczny ani confidence z Belief.
- Brak aktualnego konsensusu EPS, wycen i forward multiples blokuje silne 6M/12M tezy. Nie wolno opisywać tego generatora jako walidowanego prognostyka. Cross-check WES/BRACE/GSE w dotychczasowych panelach pozostaje oddzielny.

## Publikacja i rollback
`.github/workflows/house-view-monthly.yml` wykonuje pierwszą próbę 8. dnia miesiąca, ponawia 10. i 12. (09:17 UTC) i dopuszcza `workflow_dispatch`. `scripts/publish_monthly_house_view.py` zapisuje `data/investments/house_view_monthly_archive/YYYY-MM.json` (immutable na miesiąc) i `house_view_monthly_current.json`. Ponowne uruchomienie tego miesiąca jest idempotentne; archiwum nie jest przepisywane. Jeżeli bieżący wskaźnik świadomie cofnięto na starszy miesiąc, workflow go nie nadpisuje (wymaga ręcznej weryfikacji). Zapisywany commit zawiera tylko te dwa typy danych i nie wywołuje wykonania transakcji.

`scripts/house-view-monthly.js` prezentuje czytany JSON po polsku i angielsku na czterech stronach oraz ostrzega po 45 dniach. Gdy publikacja nie powiedzie się, nie zmienia starej analizy. Rollback polega na cofnięciu PR lub wskazaniu poprzedniego poprawnego zamrożonego JSON przez zmianę wskaźnika `current` w kontrolowanym commicie; **nigdy nie edytować historycznego pliku miesiąca**. Monitoring błędów: status czerwony GitHub Actions, bez fałszywej zielonej publikacji.

## Testy i niezależność
`python -m unittest tests/test_house_view_monthly.py -v` oraz `node --check scripts/house-view-monthly.js`. Bez Internetu testy korzystają z małych deterministycznych fixture'ów; fetch FRED odbywa się wyłącznie w realnym harmonogramie. Kontrakty wejść/wyjść nie mają pól order, stop, fill, risk-policy, champion. Publikacja jest odseparowana od trading execution.
