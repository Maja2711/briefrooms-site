# BriefRooms House View Intelligence v2 — miesięczny S&P 500 (PL)

## Cel i granice
TR-08 (Long View / BRACE-SPX) i CT-05 (publiczna projekcja) publikują co miesiąc pełną **strukturę bieżącej analizy** w istniejącym pokoju Inwestycje: teza 1M/6M/12M, obserwacje makro, uzasadnienie, sekcja wyników i wycen, scenariusze, porównanie miesiąca, konflikty modeli, warunki zmiany tezy, źródła i luki pokrycia. Polskie i angielskie warianty mają tę samą strukturę.

v2 jest deterministyczną, źródłowo ograniczoną syntezą, **nie** redaktorem LLM, zwalidowaną prognozą, rekomendacją inwestycyjną ani modelem wycen. Brak źródła pozostaje jawnym brakiem, nigdy domniemanym konsensusem.

## Implementacja
- Workflow: .github/workflows/house-view-monthly.yml, 8., 10., 12. dzień miesiąca, 09:17 UTC i workflow_dispatch.
- Makro: scripts/publish_monthly_house_view.py zachowuje stały v1 bilans ryzyka 1M/6M/12M. Obowiązkowe FRED GDPC1 (max 200 dni), PCEPI/PCEPILFE (100), DGS10 (15), DCOILBRENTEU (22). Headline i core PCE muszą mieć ten sam miesiąc. Brak, wartość nieprawdopodobna, zbyt stara lub przyszła => NO PUBLICATION.
- Synteza: scripts/house_view_research_v2.py odczytuje publiczne, istniejące projekcje GSE (3 dni), FSE (7 dni), BRACE-SPX (14 dni) i opcjonalny zamrożony WES SPX (14 dni). Odczyt bez kwalifikowanego źródła/czasu nie jest uznawany za aktualny.
- Widok: scripts/house-view-monthly.js oraz assets/house-view-monthly.css. Cały nowy artykuł jest generowany w istniejącej stylistyce; dawna analiza z października 2026 jest w rozwijanym archiwum, otwartym, gdy brak aktualnego zweryfikowanego reportu.
- Zamrożony zapis: data/investments/house_view_monthly_archive/YYYY-MM.json oraz wskaźnik house_view_monthly_current.json. Nie przepisujemy miesiąca ani świadomego ręcznego rollbacku; wcześniej zamrożony schemat v1 pozostaje historią.

## Rola silników BRs
- **WES**: wyłącznie zamrożone, zweryfikowane momentum futures SPX ret20_pct. To jedyny wkład do bazowej punktacji 1M. Forecast freeze nie jest czasem ticku; price_tick_at=null.
- **GSE**: snapshot SPX/720h z co najmniej 10 efektywnymi klastrami, research_only=true, decision_influence=false i świeżym forecast_at. DOWN i raportowany odczyt co najmniej 0,60 to wyłącznie **flaga ryzyka geopolitycznego**, nie samodzielne ani skalibrowane prawdopodobieństwo spadku indeksu.
- **FSE**: SPX risk_score i reżim; ostrożność przy ryzyku >=0,65 lub TURBULENT. ABSTAIN = brak kierunku. Cena SPY nie jest publikowana jako cena indeksu.
- **BRACE-SPX**: status badawczy, bramka promotora i stan challengera; brak uprawnionego jednego kierunku bez single_champion_authorized. Status challengera nie tworzy głosu w House View.
- **Belief Core**: prywatne mosty SPX nie mają tutaj kompletnego publicznego kontraktu point-in-time. Wyświetlamy brak pokrycia, nie odtwarzamy P ani nie czytamy prywatnych artefaktów.
- **Konflikty**: sprzeczne horyzonty, GSE DOWN kontra wzrost momentum WES i strukturalne ryzyka FSE są opisywane, a nie bezrefleksyjnie uśredniane, mnożone lub stosowane jako trading veto.

## EPS / wyceny — obecnie jawny brak automatycznego źródła
Nie istnieje zatwierdzony w tym workflow automatyczny feed aktualnego indeksowego EPS/revisions/forward P/E. Nie wolno zastępować go październikowym FactSet, wyceną pojedynczych spółek, ceną SPY ani wymyślonym targetem.

Opcjonalnie można wprowadzić **ręcznie zrecenzowany** plik data/investments/house_view_fundamentals.json o schemacie 1.0: generated_at i source_published_at ISO 8601 z timezone, source_document_url HTTPS z domen insight.factset.com / www.spglobal.com / www.yardeni.com, liczbowe forward_pe i forward_eps_growth_pct; max 45 dni. Brak, przeterminowanie lub zła domena => earnings_valuation=unavailable i brak silnej tezy 6M/12M. To źródło kuratorowane, nie feed live.

## Audyt i bezpieczeństwo
Sekcje mają source_ids, pliki projekcji SHA-256 i timestamps, makro ma datę obserwacji i URL; historia jest immutable. Scenariusze warunkowe nie mają przypisanych prawdopodobieństw. Conviction 3/10 to limit niewalidowanej heurystyki, nie Belief confidence. Klient ostrzega, gdy raport ma ponad 45 dni.

Testy: python -m unittest tests/test_house_view_monthly.py tests/test_house_view_research_v2.py -v; python -m py_compile scripts/publish_monthly_house_view.py scripts/house_view_research_v2.py; node --check scripts/house-view-monthly.js; Architecture Bootstrap Guard.

Brak prawa do zapisu TR-05, Belief, EP/IN, BRACE/WES/FSE/GSE, pozycji, risk policy, orderów, produkcyjnego promotora lub autodyfuzji metod. Reguły punktowe nie są automatycznie modyfikowane. Rollback: kontrolowany wskaźnik current lub revert kodu, nigdy edycja zamrożonego archiwum.

## Warstwa redakcyjna v2.1 — wnioski dla inwestora
Silnik nie może ograniczać sekcji do wyliczenia poziomu wskaźnika lub statusu GSE/FSE/BRACE. Każda sekcja ma wyjaśnić **wpływ na S&P 500, horyzont i warunek zmiany tezy**:
- Stopy/energia: 10Y -> koszt kapitału i mnożnik P/E; Brent -> inflacja/koszty/marże; oddzielamy hipotezę warunkową od prognozy decyzji Fed.
- EPS/wyceny: bez wiarygodnego indeksowego konsensusu EPS i forward P/E nie zakładamy wzrostu wycen bez poprawy zysków; brak targetu indeksu jest podany wraz z konsekwencją dla 6M/12M, a nie jako pusty komunikat techniczny.
- Momentum: spadek w 20 sesjach oznacza ryzyko dalszej korekty, nie dowód ceny dna; pozytywne momentum to argument za odbiciem, nie automatyczna hossa na 12M.
- Karty 1M/6M/12M pokazują kierunkowy wniosek; surowa punktacja 3/10 i statusy silników pozostają dostępne w rozwijanej sekcji metodologicznej.
- Scenariusze i bramki pokazują **konkretne czynniki** podnoszące lub obniżające ryzyko, nie sugerując wyliczonego prawdopodobieństwa.

**Archiwum pozostaje immutable.** Raport 2026-11 został zamrożony wcześniej z technicznym opisem. Frontend v2.1 przelicza wyłącznie jego *prezentację językową* z tych samych zamrożonych liczb, ocen i jawnych flag (bez nowego feedu i bez zmiany JSON). Oznacza tę interpretację numerem v2.1. Kolejne raporty otrzymują już czytelne wnioski bezpośrednio z funkcji _investment_editorial_sections w scripts/house_view_research_v2.py. PL/EN stosują te same zasady, ale nie deklarują trafności prognostycznej.

Testy: dodatkowo node tests/test_house_view_editorial_ui.cjs odtwarza listopadowy snapshot, sprawdza polski i angielski wniosek, brak kodów technicznych w głównym tekście, archiwum bez zmian i fail-closed dla niepełnego makro.
