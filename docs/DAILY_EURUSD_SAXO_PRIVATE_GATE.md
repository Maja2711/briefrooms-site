# Saxo SIM EUR/USD → prywatny BRs Daily: PRIMARY dla papierowego EPE

## Dwa różne uruchomienia

**Publiczny BRs Daily** (standardowy `.github/workflows/daily-eurusd-monitor.yml`) nadal wyznacza papierowe ceny z publicznych źródeł i publikuje swoje artefakty. Nie publikuje ani nie wybiera cen Saxo.

**Prywatny BRs Daily** uruchamiany poza katalogiem publicznego repozytorium może używać **Saxo SIM jako źródła podstawowego** dla bieżącej symulowanej ceny wejścia EPE. Ma własne, lokalne `eurusd_daily_spot.json`, `eurusd_daily_history.json`, policy i ledger, które nigdy nie są commitowane do GitHub. Żadnych zleceń brokerskich.

Nie jest to przełączenie całej analizy na dane Saxo: H1/D1/W1 oraz ukończone 1m świece nadal są pobierane dotychczasowym modułem (Yahoo), bo Worker Saxo obsługuje teraz wyłącznie **aktualny bid/ask/mid EUR/USD**, a nie Saxo OHLC. Kierunek nadal zatwierdza Belief Core.

## Ścieżka papierowego fill (tylko private runtime)

1. Standardowe feedy publiczne muszą dostarczyć dwie niezależne, świeże, zgodne referencje. Brak → NO_FILL.
2. Worker Saxo `/saxo/quote` jest odczytywany przez prywatny adapter (HTTPS + administrator). Sprawdzamy wyłącznie `source=SAXO_SIM`, `EUR/USD`, otwarty rynek, zero opóźnienia, świeżość ≤90s, spójny BID/ASK/MID, `trading_enabled=false`, `redistributable=false`. Błąd → NO_FILL.
3. MID Saxo musi zgadzać się z wynikiem publicznej kontroli w granicach 2,5 pipsa; inaczej NO_FILL.
4. MID Saxo zostaje **ceną bazową papierowego wejścia**; dodajemy dotychczasowy konserwatywny bufor 1,5 pipsa na stronę. To symulacja, nie broker-verified executable fill. Lokalny, prywatny history/position przechowuje wynik z identyfikacją Saxo.
5. Zasady H1 range, stop loss, profit protection, SL/TP, Belief Direction oraz ochrona makro pozostają bez zmian.

## Ustawienie prywatne na Windows (PowerShell)

Warunki: repo lokalnie na dysku, Python 3.11+; aktywny Worker Cloudflare z odświeżaniem OAuth i danymi EUR/USD; prywatny odczyt endpointu z działającym mechanizmem autoryzacji. Wykorzystuj jedynie do swoich prywatnych, niekomercyjnych obliczeń. Nie kopiuj wyniku do publicznego GitHub/BRs.

Uruchom PowerShell w katalogu repo:

```powershell
$env:BRIEFROOMS_PRIVATE_RUNTIME = "1"
$env:SAXO_PRIVATE_PRIMARY = "1"
$env:SAXO_PRIVATE_QUALITY_GATE = "0"
$env:BRIEFROOMS_PRIVATE_DATA_DIR = Join-Path $HOME "BRsPrivateSaxoEURUSD"
$env:SAXO_PRIVATE_BASIC_USER = "admin"
New-Item -ItemType Directory -Force -Path $env:BRIEFROOMS_PRIVATE_DATA_DIR | Out-Null
$securePassword = Read-Host "Haslo prywatnego Workera Cloudflare (NIE haslo Saxo)" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
try {
    $env:SAXO_PRIVATE_BASIC_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
python scripts/daily_eurusd_spot_v19.py `
  --output "$env:BRIEFROOMS_PRIVATE_DATA_DIR/eurusd_daily_spot.json" `
  --history "$env:BRIEFROOMS_PRIVATE_DATA_DIR/eurusd_daily_history.json"
Remove-Item Env:SAXO_PRIVATE_BASIC_PASSWORD
```

**Ważne o logowaniu Workera:** obecny adapter zakłada uwierzytelnienie HTTP Basic. Wcześniejsze testy przeglądarkowe mogły korzystać z ciasteczka sesyjnego (administrator login). Należy sprawdzić implementację `worker.js` i potwierdzić, że endpoint akceptuje Basic; samo działanie endpointu w zalogowanej przeglądarce tego **nie potwierdza**. Nie umieszczaj hasła ani cookie w repo/chat.

Gdy Worker wymaga wyłącznie sesji cookie, adapter Basic nie jest jeszcze gotowym produkcyjnym połączeniem. Wprowadzenie osobnego, ograniczonego read-only dostępu maszynowego w Cloudflare Worker jest wtedy kolejnym etapem. Nie używaj publicznego endpointu bez autoryzacji jako obejścia.

## Zabezpieczenia

- Ustawienia `SAXO_PRIVATE_PRIMARY=1` oraz `BRIEFROOMS_PRIVATE_RUNTIME=1` są wymagane jednocześnie. Dodatkowo `BRIEFROOMS_PRIVATE_DATA_DIR` musi być absolutną ścieżką **poza repo**; oba argumenty `--output` i `--history` muszą wskazywać pliki wewnątrz tej ścieżki. Runtime sprawdza to jeszcze przed pobraniem danych rynkowych.
- Przy braku hasła, błędzie Workera, starym kursie albo rozbieżności dwóch źródeł silnik nie tworzy nowego papierowego fill (fail closed).
- Nie ustawiaj `SAXO_PRIVATE_PRIMARY=1` w standardowym publicznym workflow GitHub. Nie uploaduj prywatnych plików przez GitHub Actions artifact, nie publikuj ich przez Pages i nie wypychaj Web Push z prywatnymi cenami.
- Sama integracja nie dowodzi niezawodności SIM. Saxo może mieć infrastrukturę SIM o innym SLA niż LIVE; monitorowanie feedu i quality metrics należy prowadzić prywatnie.

## Kontrola poprawności

```powershell
$env:PYTHONPATH = "scripts"
python -m unittest tests.test_saxo_private_feed tests.test_saxo_private_primary -v
```

Kryterium końcowe to: autoryzowany odczyt rzeczywistego Workera, świeży Saxo MID, dwie zgodne niezależne referencje, wynik papierowy w katalogu prywatnym z `private_saxo_primary: true`, a **brak** zmian zawierających kurs Saxo w publicznych artefaktach. Gdy decyzja Belief = FLAT, bez otwarcia nowej pozycji, brak znacznika `private_saxo_primary` nie oznacza jeszcze awarii konektora.
