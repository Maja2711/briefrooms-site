# BRs Infrastructure — Etap 2: R2 Shadow Archive Pilot

## Zasada nadrzędna
GitHub pozostaje jedynym źródłem prawdy. Cloudflare R2 służy WYŁĄCZNIE jako dodatkowe, prywatne, kontrolowane archiwum. Nie zmieniamy tradingu, cen ani reguł BRACE i EURUSD.

## Zakres
- BRACE: data/investments/portfolio_10k_brace_memory.json
- BRACE: data/investments/portfolio_10k_brace_historical_learning.json
- EURUSD Daily: data/investments/eurusd_daily_history.json
- EURUSD Daily: data/investments/eurusd_posttrade_audits.json

Nie kopiujemy innych plików, nie modyfikujemy źródła i nie stosujemy ARIS ani dodatkowych kodeków na pierwszym etapie R2.

## Zabezpieczenia i raport
1. Wszystkie cztery istniejące JSON-y muszą przejść walidację schematu, liczby rekordów i unikalnych ID PRZED jakimkolwiek wysłaniem do R2.
2. Obliczamy SHA-256 z dokładnych oryginalnych bajtów, bez zmiany formatu.
3. Przechowujemy kopie wyłącznie pod kluczami zawierającymi hash treści, w katalogu pilot/brs-v1, osobno dla BRACE i EURUSD.
4. Istniejący obiekt R2 jest weryfikowany, a nie nadpisywany. Zmienione dane otrzymują nowy klucz hash.
5. Po każdym uploadzie lub wykryciu istniejącego obiektu sprawdzamy HEAD, GET, SHA-256, zgodność pełnych bajtów oraz liczby rekordów.
6. Raport jest dołączany jako GitHub Actions artifact (14 dni), nie jest commitowany do main.
7. Błąd odczytu, brak obiektu, 403 lub rozbieżność oznacza FAILURE bez ingerencji w dane GitHub.

## Aktywacja (wymaga właściciela konta Cloudflare)
1. Cloudflare → Storage & databases → R2: załóż osobny prywatny bucket, np. briefrooms-archive-pilot.
2. R2 → Manage API Tokens: utwórz klucze Object Read & Write ograniczone TYLKO do bucketa pilotażowego.
3. W GitHub repo → Settings → Secrets and variables → Actions skonfiguruj:
   Variables:
   - BRS_R2_ACCOUNT_ID: 32 znaki Cloudflare Account ID.
   - BRS_R2_PILOT_BUCKET: nazwa nowego prywatnego bucketa.
   - BRS_R2_PILOT_ENABLED: najpierw false; po konfiguracji true.
   Secrets:
   - BRS_R2_ACCESS_KEY_ID
   - BRS_R2_SECRET_ACCESS_KEY
4. W GitHub Actions uruchom ręcznie workflow BRs R2 Shadow Archive Pilot (workflow_dispatch). Sprawdź job mirror-to-r2 i dołączony artifact.
5. Oczekiwany wynik: cztery wpisy UPLOADED_VERIFIED lub EXISTING_VERIFIED z licznikami rekordów i SHA-256.
6. Ponów run bez zmian źródeł: powinny pojawić się EXISTING_VERIFIED, bez powtórnych uploadów.

NIE przesyłaj sekretów przez czat, w Pull Request ani jako pliki Git. Bez poświadczeń replikacja R2 pozostaje celowo wyłączona.

## Harmonogram i ograniczenia
- Gdy BRS_R2_PILOT_ENABLED jest true, uruchomienie co 6 godzin (UTC 00:13, 06:13, 12:13, 18:13) rewiduje kopie.
- GitHub Actions schedule nie gwarantuje punktualności; pilotaż to backup, nie real-time market feed.
- Pull Request i push do main uruchamiają TYLKO walidację lokalną i dry-run. Workflow ma permissions: contents: read.
- Pilot nie usuwa danych z GitHuba, nie zmniejsza dziś liczby commitów i nie zwalnia dotychczasowych silników z ich zależności.

## Dezaktywacja / rollback
Ustaw BRS_R2_PILOT_ENABLED=false. Harmonogram przestaje wysyłać obiekty do R2. GitHub nadal pozostaje kanoniczny, więc rollback aplikacji lub silników NIE jest konieczny.

## Warunek ukończenia etapu 2
Etap 2 można uznać za zweryfikowany w chmurze dopiero po PASS prawdziwego workflow:
- wszystkie 4 archiwa w prywatnym bucket R2,
- HEAD + GET, bajty i SHA-256 zgodne z GitHub,
- pełne liczby rekordów zgodne,
- powtórzenie idempotentne,
- potwierdzona możliwość odczytu i odzyskania archiwum,
- niezmieniona zawartość repo i stan wszystkich silników.

Do tego momentu rezultat to gotowy, lecz nieaktywny pilot, a nie produkcyjna migracja.
