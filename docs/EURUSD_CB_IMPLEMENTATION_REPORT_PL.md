# Raport implementacji EURUSD Fed / EBC

Data: 2026-10-29. Repozytorium: Maja2711/briefrooms-site. Gałąź: `feat/eurusd-fed-ecb-live-event-risk`. PR do `main`; bez połączenia z main i bez uruchomienia produkcyjnego.

## Wynik

Zaimplementowano pełną ścieżkę official RSS → dokument i weryfikacja czasu → wspólny snapshot z SHA-256 → adapter Weekly i Daily → ekonomika pozycji → HOLD / PROTECT_PROFIT / CLOSE_BEFORE_EVENT w SHADOW → niezmienny audyt i status PL/EN. Naprawiono actuals FOMC/EBC, oczekiwanie na oficjalny komunikat po terminie oraz obsługę niespodziewanej decyzji poza kalendarzem. Kierunek nadal należy do właściwego silnika, a wykonanie do jego lifecycle/EPE.

Nowe zamknięcia i zmiany stopa nie mają uprawnień wykonawczych. Parametry produkcyjnego zamknięcia są odrzucane. Monitoring jest włączony; osobna flaga nowych blokad wejścia jest przygotowana i początkowo wyłączona. Jej włączenie wymaga sprawdzenia zdrowego kanału i osobnego przeglądu konfiguracji. Obecne SL/TP działają niezależnie od newsa. Worker i usługa 60 s są przygotowane, niewdrożone.

## Istotne zabezpieczenia

- Historia transakcji, ceny, stare manifesty i stopy nie są przepisywane. Jedyną dodaną do repo zmianą danych jest nowa polityka EURUSD; brak wygenerowanych fikcyjnych publikacji i notowań.
- Dwa silniki używają tej samej paczki faktów, własnych pozycji, stopów i targetów. Brak nowych wywołań dla BTC, S&P500, akcji i innych par.
- BID dla wyjścia LONG, ASK dla SHORT, jawny koszt. MID/OHLC nie uzyskuje statusu wykonalnej ceny. Stary kurs i skok spreadu dają oczekiwanie na ochronę, bez pozornego fillu.
- Oryginalny SHORT w syntetycznym teście +50 pips ma 111,8 pips możliwego cofnięcia do stopa. Fixture nie zmienia żadnej rzeczywistej pozycji.
- Nieznany koniec konferencji przetrwa restart i kolejną dobę. Poprawny dowód zakończenia nie zostaje cofnięty przez późniejszy kalendarz UNKNOWN; przekroczenie limitu okien wymusza PARTIAL i ręczny recovery.
- Podział budżetu dokumentów chroni decyzję EBC przed zagłodzeniem przez przemówienia Fed. Powtórzone adresy są pobierane raz na cykl.
- Odczyt kalendarza Fed jest ograniczony do właściwego roku także przy malejącej kolejności lat. Daty z poprzednich roczników nie stają się wydarzeniami bieżącego roku. Ogólny adapter innych instrumentów pozostaje bez zmian.
- Opcjonalny audyt Daily nie zatrzymuje publikacji kanału. Publikacja sprawdza pieczęć, czas, hash wire i tożsamość aktualnego stanu; odrzuca prywatne dane Saxo i deklaracje wykonania.

## Walidacja

Wcześniejszy pełny zestaw: 336/336 Python PASS, przy identycznym hashu kodu przed i po teście. Po ostatnich rozszerzeniach końcowy lokalny przebieg wykonał 303 testy: 301 PASS i dwa błędy importu zależności pandas zablokowanej przez Windows App Control; brak niepowodzeń asercji. Końcowy pełny zestaw jest uruchamiany w workflow walidacyjnym PR na Linux.

JavaScript: 26/26 PASS, w tym 9 testów odizolowanego Workera, 3 widoku ryzyka i 14 istniejących testów Weekly. Podgląd PL/EN Daily/Weekly: 12/12 wariantów (1366, 768, 390 px), bez błędów JavaScript i poziomego przewijania. Sprawdzono obraz na desktopie i telefonie. Składnia 8 workflow i 85 bloków bash oraz watcher: PASS. Architecture Bootstrap 1.46 i Architecture Reconciliation: PASS. `git diff --check`: PASS.

Końcowy wynik CI zostanie dopisany po ukończeniu uruchomienia PR. Testy obejmują NO RETROACTIVE, EPE, stare SL/TP, niezmienne manifesty, awarię feedu, korekty i deduplikację, przyszłe publikacje, DST, nieaktualne ceny, niezaplanowane decyzje oraz izolację innych instrumentów.

## Rzeczywisty dowód źródeł i SHADOW

Odczyt `2026-10-29T08:55:02Z`: 4/4 oficjalnych RSS poprawnych, 11 publikacji, 1 zweryfikowana decyzja. Poprawny hash snapshotu: `43228202e544efc1edcba52e66bc0504549beb280d825f869be3879bdd571038`. Po ograniczeniu roku kalendarz Fed zawiera dwie właściwe daty: 28 października i 9 grudnia 2026; nie przejmuje dat z poprzednich lat. Kalendarz EBC: 13 zapisów.

Oba adaptery potwierdziły identyczny hash paczki faktów. Odczyt istniejącego, zamrożonego checkoutu z nieaktualną ceną pozostawił `executed_at=null`, Weekly `NO_ACTION_UNVERIFIED/PROTECTION_PENDING`, Daily bez pozycji `BLOCK_NEW_ENTRY/SHADOW`. To dowód przepływu i braku fikcyjnego wykonania, nie pomiar wyniku strategii ani odczyt aktualnego brokerowego rachunku. Początkowy pojedynczy cykl kolektora trwał 2,08 s; nie wyznacza to p50/p95/p99 ani SLA GitHub cron.

## Ograniczenia przed aktywacją

Nie ma jeszcze kwalifikującej się rzeczywistej próby prospektywnej dla automatycznego zamykania. Nie podano wyniku backtestu z wymyślonych cen. Silnik rozkładów obsługuje sześć okien 1–120 min, odrębne banki/typy/reżimy, niezależność zdarzeń i koszty. Bez odpowiednich danych zwraca RISK_MODEL_UNCALIBRATED i null; większa próba pozostaje EMPIRICAL_UNVALIDATED, bez uprawnień wykonawczych. Replay zamraża skład i reguły przed wynikami; raport nie może automatycznie promować polityki.

Wdrożenie wymaga prospektywnego zbierania danych, oceny netto i ryzyka, parytetu z rzeczywistym lifecycle, kontroli kosztów/opóźnień, budżetu przechowywania audytu oraz jawnego zatwierdzenia operatora. Konfiguracja Workera nie zawiera sekretu, konta ani fikcyjnego endpointu. Google News pozostaje opcjonalne i wyłączone; brak deklaracji licencji na artykuły wydawców. Powiadomienia pozycyjne nadal wynikają wyłącznie z rzeczywistych zmian kanonicznego stanu.

Pełny kontrakt i flagi: [EURUSD_CENTRAL_BANK_EVENT_RISK.md](EURUSD_CENTRAL_BANK_EVENT_RISK.md).

## Pełna lista plików w zmianie

- `.github/workflows/daily-eurusd-macro-fast.yml`
- `.github/workflows/daily-eurusd-realtime-lifecycle.yml`
- `.github/workflows/eurusd-central-bank-wire.yml`
- `.github/workflows/investments-weekly-freshness-watchdog.yml`
- `.github/workflows/investments-weekly.yml`
- `.github/workflows/investments-wes.yml`
- `.github/workflows/weekly-live-prices.yml`
- `.github/workflows/wes-live-execution.yml`
- `data/investments/eurusd_cb_risk_policy.json`
- `docs/ARCHITECTURE_MAP_EN.md`
- `docs/ARCHITECTURE_MAP_PL.md`
- `docs/EURUSD_CB_IMPLEMENTATION_REPORT_PL.md`
- `docs/EURUSD_CENTRAL_BANK_EVENT_RISK.md`
- `en/investing/daily-trading.html`
- `en/investing/open-weekly-positions.html`
- `en/investing/weekly-forecasts.html`
- `pl/inwestycje/daily-trading.html`
- `pl/inwestycje/pozycje-tygodniowe.html`
- `pl/inwestycje/prognozy-tygodniowe.html`
- `scripts/audit_intraday_risk_exits.py`
- `scripts/daily-eurusd-spot-public.js`
- `scripts/daily_eurusd_belief_decision.py`
- `scripts/daily_eurusd_decision_record.py`
- `scripts/daily_eurusd_fast_lifecycle.py`
- `scripts/daily_eurusd_macro_event_risk.py`
- `scripts/daily_eurusd_macro_fast.py`
- `scripts/daily_eurusd_realtime_watch.sh`
- `scripts/daily_eurusd_spot_v19.py`
- `scripts/eurusd-cb-risk-public.js`
- `scripts/eurusd_cb_event_distribution.py`
- `scripts/eurusd_cb_poll_service.py`
- `scripts/eurusd_cb_shadow_observe.py`
- `scripts/eurusd_cb_shadow_replay.py`
- `scripts/eurusd_central_bank_event_risk.py`
- `scripts/eurusd_central_bank_news_interpreter.py`
- `scripts/eurusd_central_bank_wire.py`
- `scripts/eurusd_daily_cb_event_adapter.py`
- `scripts/eurusd_weekly_cb_event_adapter.py`
- `scripts/investments-weekly-public.js`
- `scripts/investments_weekly_v5.py`
- `scripts/publish_eurusd_cb_wire.py`
- `scripts/render_weekly_public_pages.py`
- `scripts/update_weekly_live_prices_fast.py`
- `scripts/validate_architecture_reconciliation.py`
- `tests/test_eurusd_cb_daily_publication_safety.py`
- `tests/test_eurusd_cb_event_distribution.py`
- `tests/test_eurusd_cb_event_risk.py`
- `tests/test_eurusd_cb_news_interpreter.py`
- `tests/test_eurusd_cb_no_other_instruments.py`
- `tests/test_eurusd_cb_poll_service.py`
- `tests/test_eurusd_cb_publisher.py`
- `tests/test_eurusd_cb_risk_public.js`
- `tests/test_eurusd_cb_shadow_replay.py`
- `tests/test_eurusd_cb_unresolved_windows.py`
- `tests/test_eurusd_cb_wire_worker.js`
- `tests/test_eurusd_central_bank_wire.py`
- `tests/test_eurusd_daily_cb_event_adapter.py`
- `tests/test_eurusd_weekly_cb_event_adapter.py`
- `workers/eurusd-cb-wire/README.md`
- `workers/eurusd-cb-wire/src/index.js`
- `workers/eurusd-cb-wire/wrangler.toml`
