# Stock Trading v2 — stały nominał pozycji 5K

## Status

Ta polityka jest obowiązkowym kontraktem nowych pozycji Stock Trading v2.

- GPW: **5 000 PLN na jedną spółkę**.
- US: **5 000 USD na jedną spółkę**.
- Reguła dotyczy każdej nowej pozycji otwieranej przez produkcyjny Stock Trading v2.
- Nie zależy od ceny pojedynczej akcji.

## Cel

Nominał pozycji ma być porównywalny między spółkami. Cena jednej akcji nie może powodować, że jedna transakcja reprezentuje ekspozycję rzędu kilkudziesięciu jednostek waluty, a inna kilkudziesięciu tysięcy.

Dlatego sizing jest zdefiniowany jako stały gross notional:

```text
GPW target_position_notional = 5000 PLN
US  target_position_notional = 5000 USD
```

Przy pełnym wykorzystaniu trzech slotów maksymalny nominalny gross exposure wynosi odpowiednio 15 000 PLN na GPW i 15 000 USD na rynku US.

## Quantity

BriefRooms jest obecnie systemem paper-trading / research. Aby utrzymać dokładnie porównywalny nominał także dla bardzo drogich akcji, quantity jest liczone jako:

```text
quantity = 5000 / entry_price
```

i może być ułamkowe.

Przykład:

```text
US entry = 421.82 USD
quantity ~= 11.8534
entry_notional ~= 5 000 USD
```

Jeżeli w przyszłości pojawi się broker/execution venue bez obsługi fractional shares, warstwa execution musi jawnie zdefiniować rounding lub rejection. Nie wolno po cichu zmienić tej architektonicznej reguły.

## Pola pozycji

Każda nowa pozycja zgodna z polityką zapisuje co najmniej:

- `sizing_policy_version = FIXED_NOTIONAL_V1`,
- `position_currency`,
- `target_position_notional = 5000`,
- `quantity`,
- `entry_notional`,
- `initial_risk_cash`.

Zamknięta pozycja dodatkowo zapisuje:

- `exit_notional`,
- `pnl_amount`.

Kwotowy P&L jest liczony jako:

```text
pnl_amount = (exit_price - entry_price) * quantity
```

a nie jako zmiana ceny jednej akcji.

## Authority

Źródłem prawdy jest:

- `data/investments/stock_trading_policy.json`.

Produkcja fail-closed sprawdza:

- GPW = 5 000 PLN,
- US = 5 000 USD,
- `fractional_quantity_allowed = true`,
- `sizing_policy_version = FIXED_NOTIONAL_V1`.

Główna implementacja:

- `scripts/stock_trading_portfolio.py`,
- `.github/workflows/stock-trading-v2-production.yml`.

## Relacja do risk policy

Stały nominał **nie zastępuje** SL/TP ani risk policy.

Dwie pozycje mają tę samą nominalną ekspozycję 5K, ale mogą mieć różny procentowy risk-to-stop. `maximum_risk_percent`, reward/risk oraz wszystkie istniejące bramki pozostają aktywne.

## Legacy / NO RETROACTIVE

Pozycje otwarte przed aktywacją `FIXED_NOTIONAL_V1` nie dostają sztucznie dopisanej quantity ani nominału.

Nie wolno:

- twierdzić po fakcie, że stara transakcja miała 5K,
- wyliczać historycznego kwotowego P&L na fikcyjnej quantity,
- przepisywać entry/exit historii.

Dla legacy pozycji procentowy zwrot pozostaje ważny, ale kwotowy P&L bez frozen quantity powinien być oznaczony jako niedostępny.

## Public UI PL/EN

Obie wersje Stock Trading pokazują:

- wartość pozycji,
- quantity / liczbę akcji,
- P&L policzony od całej ekspozycji.

Nowe pozycje pokazują około 5 000 PLN lub 5 000 USD entry notional. Legacy pozycje bez historycznie zamrożonej quantity są jawnie oznaczone jako legacy zamiast otrzymać zmyślony nominał.
