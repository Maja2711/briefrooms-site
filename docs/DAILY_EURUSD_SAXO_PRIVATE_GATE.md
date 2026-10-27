# Saxo SIM → BriefRooms EUR/USD Daily — prywatny adapter (disabled by default)

## Co działa, a czego nie wolno mylić

Cloudflare Worker `briefrooms-saxo-eurusd` ma własne OAuth SIM, rotację tokenów w D1 i cykliczny Cron. Odrębne repozytorium GitHub **nie otrzymuje automatycznie** sekretów Workera ani danych z jego bazy D1.

Ten patch łączy kod EURUSD Daily EPE z prywatnym adapterem, **ale domyślnie nie wysyła żądań do Saxo**. Dotychczasowe publiczne feedy (Stooq / fxapi / Currency Exchange Tool / Yahoo) nadal wyliczają cenę do papierowego EPE.

Saxo SIM zwraca `PRIVATE_TEST_SIM_ONLY` i `redistributable: false`: nie wolno publikować surowych kwotowań, strumienia, cache, publicznych plików JSON, historii cen, wykresów ani reklamować ich jako licencjonowanego źródła publicznego. Nie aktywuj private gate w **publicznym** Daily bez zgody Saxo obejmującej konkretny sposób użycia. Pośrednia kontrola jakości decyzji również może podlegać warunkom dostawcy.

## Mechanizm w repo

- `scripts/saxo_private_feed.py`: wymaga HTTPS do jednego, stałego hosta Worker; odczytuje Basic Auth z sekretu procesu, nie z plików; waliduje SIM, spread, instrument, timing UTC, świeżość ≤90s, `redistributable: false`, zero delay oraz wyłączony handel; nie loguje odpowiedzi ani sekretów.
- `scripts/execution_price_engine.py`: gdy `SAXO_PRIVATE_QUALITY_GATE=1`, podczas zwykłego cyklu Daily z natywnymi publicznymi feedami i **już zweryfikowaną** publiczną ceną porównuje ją w pamięci z prywatnym mid Saxo. Rozbieżność >2,5 pipsa lub problem dostępu blokuje nową papierową pozycję. Nie wybiera Saxo jako primary fill; nie serializuje Saxo bid/ask/mid, spread, timestamp ani różnicy do publicznego artefaktu.
- Inny horyzont (H1/D1/1m), natywna decyzja Belief, istniejący SL/TP i ścieżka wyjścia z **otwartej** pozycji nie są modyfikowane.
- `tests/test_saxo_private_feed.py`: sprawdza izolację, brak wycieku sekretu, staleness i veto. Włączono do Daily validation CI.

## Gdyby uprawnienia umożliwiały prywatne testowanie przez GitHub Actions

1. GitHub → repo → Settings → Secrets and variables → Actions → **New repository secret**: `SAXO_PRIVATE_BASIC_PASSWORD` = hasło Basic Auth dla **Cloudflare Workera**, zgodne z Worker Secret `SAXO_ADMIN_PASSWORD`. To **nie** jest hasło konta Saxo ani `SAXO_APP_SECRET`.
2. Repo → Settings → Secrets and variables → Actions → **Variables**: `SAXO_PRIVATE_QUALITY_GATE=1` wyłącznie po potwierdzeniu uprawnień do takiego sposobu użycia. **Pozostaw nieustawione / 0 dla publicznego BRs Daily**.
3. Następny prawdziwy cykl `daily-eurusd-monitor.yml` może uruchomić prywatną kontrolę w `Reconcile position...`. GitHub nie kopiuje automatycznie haseł z Cloudflare.
4. Kontrola jakości: Actions success + EPE `private_saxo_gate: PASS_NO_PRICE_REDISTRIBUTION` przy kwalifikującym się nowym wejściu. Brak pozycji/FLAT oznacza, że bramka nie musiała się uruchomić; brak wpisu **nie** dowodzi awarii.
5. Każdy błąd pobrania w trybie włączonym blokuje tylko **nowy** fill (`private_saxo_quote_unavailable`), nie zmienia SL/TP istniejących pozycji. Wyłączenie: ustaw zmienną `SAXO_PRIVATE_QUALITY_GATE=0`.

## Warunek publicznej integracji źródła cen

Aby silnik publicznego BRs EURUSD Daily naprawdę używał **cen Saxo jako podstawowego feedu**, potrzebne jest odpowiednie źródło LIVE/market-data entitlement i prawo do zamierzonego wykorzystania/redystrybucji (w tym prezentacji cen i cen wynikowych). Wtedy trzeba osobno zrewidować kontrakt EPE i prawa do publikacji artefaktów. Obecny SIM nie uprawnia do ogłoszenia takiej integracji.
