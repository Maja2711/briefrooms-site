# TimesFM3 — wewnętrzny benchmark EUR/USD

## Cel

TimesFM3 jest zewnętrznym benchmarkiem jakości wyboru kierunku dla własnych silników BriefRooms. Nie jest częścią architektury decyzyjnej BriefRooms i nie ma roli tradingowej.

Zakres v1:
- **Daily EUR/USD** — porównanie kierunku LONG/SHORT z TimesFM3 na horyzoncie 24h, zdefiniowanym jako 48 kolejnych zamkniętych świec 30m.
- **WES EUR/USD** — porównanie kierunku LONG/SHORT z TimesFM3 do zamrożonego `exit_target_local` danego tygodnia.

## Metodologia paired benchmark

Dla każdej nowej decyzji ekonomicznej po prospektywnej aktywacji:
1. zamrażany jest kierunek własnego silnika,
2. kontekst TimesFM3 zawiera wyłącznie zamknięte świece dostępne do momentu tej decyzji,
3. TimesFM3 generuje własny kierunek z tego samego punktu decyzyjnego,
4. wynik obu prognoz jest później rozliczany względem tego samego przyszłego kursu.

Raport prywatny liczy osobno dla Daily i WES:
- paired resolved N,
- hit rate własnego silnika,
- hit rate TimesFM3,
- różnicę hit rate,
- agreement rate,
- liczbę sporów,
- kto wygrywa, gdy kierunki się różnią,
- przypadki oba poprawne / oba błędne.

## Granice

TimesFM3:
- nie ma `decision_influence`,
- nie ma Risk Policy,
- nie otwiera pozycji,
- nie ma PnL, sizing ani execution authority,
- nie zapisuje do Belief Core ani EpistemicState,
- nie może automatycznie stroić ani promować naszych silników,
- nie ma publicznej projekcji wyników.

PnL i metryki ryzyka naszych silników pozostają nadrzędną miarą jakości tradingowej. TimesFM3 odpowiada tylko na pytanie: **czy zewnętrzny model lepiej czy gorzej wybiera kierunek na porównywalnym horyzoncie?**

## Anti-hindsight

- brak historycznego backfillu,
- decyzja musi powstać po `activated_at`,
- inferencja musi zostać wykonana zanim outcome benchmarku stanie się obserwowalny,
- kontekst modelu jest obcięty do danych dostępnych w chwili decyzji,
- ledger jest append-only i hash-chained.

## Licencja TimesFM3

Kod integracji jest gotowy do prywatnego researchu, ale runtime wag TimesFM3 działa wyłącznie po ustawieniu repozytoryjnej zmiennej GitHub Actions:

`TIMESFM3_RESEARCH_LICENSE_OK=true`

Jest to świadomy fail-closed gate. Obecne pretrained weights TimesFM3 są objęte licencją ograniczającą użycie do non-commercial / non-production. Samo ustawienie zmiennej nie zmienia warunków licencji; oznacza tylko, że właściciel repo potwierdził podstawę do takiego użycia.

Stan badawczy jest przechowywany wyłącznie w prywatnym artefakcie GitHub Actions `timesfm3-internal-benchmark-state`.
