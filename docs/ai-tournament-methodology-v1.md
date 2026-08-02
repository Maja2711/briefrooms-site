# AI Tournament — metodologia v1

## Cel

AI Tournament jest publicznym eksperymentem porównującym jednorazowe, ręcznie zgłoszone portfele modeli AI w okresie od 3 sierpnia do 3 listopada 2026 roku.

Każdy uczestnik podejmuje jedną ostateczną decyzję przed rozpoczęciem turnieju. Po zablokowaniu odpowiedzi nie wolno zmieniać składu portfela, wag ani udziału gotówki.

## Dwa równoległe rachunki

Ten sam skład i te same procentowe wagi są stosowane do dwóch rachunków:

- rachunek PL: 10 000 PLN, codzienna wycena w PLN z uwzględnieniem zmian USD/PLN,
- rachunek EN: 10 000 USD, codzienna wycena wyłącznie w USD.

Ranking PL i ranking EN są prowadzone oddzielnie według procentowej stopy zwrotu.

## Zbieranie decyzji

Decyzje modeli są pozyskiwane ręcznie w czatach. Turniej nie korzysta z API modeli. Każde zgłoszenie jest walidowane i blokowane kryptograficznym hashem przed ujawnieniem portfeli konkurentów.

## Zakup i strategia

Modelowy zakup następuje na otwarciu regularnej sesji giełdowej w USA 3 sierpnia 2026 roku. Dopuszczone są udziały ułamkowe.

Strategia jest buy-and-hold. Po wykonaniu zakupu nie ma rebalansowania, wymiany spółek, dokupowania ani sprzedaży przed końcem turnieju.

## Gotówka i oprocentowanie Fed

Niewykorzystana gotówka jest traktowana jako modelowa gotówka w USD na obu rachunkach.

Gotówka zarabia codziennie według stawki IORB — Interest Rate on Reserve Balances — publikowanej przez System Rezerwy Federalnej.

Zasady naliczania:

- konwencja ACT/365,
- kapitalizacja codzienna,
- naliczanie za wszystkie dni kalendarzowe, w tym weekendy i święta,
- okres naliczania dla danego odcinka to `[from_date, to_date)`,
- pierwsza wycena na zamknięciu 3 sierpnia nie zawiera jeszcze odsetek overnight,
- wycena 4 sierpnia zawiera jeden dzień odsetek,
- wycena w poniedziałek zawiera odsetki za piątek, sobotę i niedzielę,
- każda zmiana IORB obowiązuje od oficjalnej daty wejścia w życie,
- historyczne stawki nie są nadpisywane.

Wzór dzienny:

`cash_next = cash_current × (1 + IORB / 100 / 365)`

Na rachunku EN gotówka i odsetki pozostają w USD. Na rachunku PL gotówka i odsetki są również prowadzone modelowo w USD, a następnie codziennie przeliczane na PLN po tej samej metodologii USD/PLN co pozycje akcyjne.

Szczegółowa, wersjonowana polityka znajduje się w `data/ai_tournament/cash_rate_policy.json`.

## Codzienna wycena

Po każdej zakończonej sesji USA obliczane są:

- wartość rachunku PL,
- stopa zwrotu rachunku PL,
- wartość rachunku EN,
- stopa zwrotu rachunku EN,
- miejsce w obu rankingach,
- maksymalne obsunięcie,
- najlepsza i najgorsza pozycja,
- narosłe odsetki od gotówki.

## Zakończenie

Końcowa wycena następuje po zamknięciu regularnej sesji USA 3 listopada 2026 roku. Zwycięzca PL i zwycięzca EN są ogłaszani oddzielnie.

## Charakter projektu

Turniej jest publicznym eksperymentem portfeli modelowych i nie stanowi rekomendacji ani porady inwestycyjnej. IORB pełni funkcję jawnego, syntetycznego benchmarku oprocentowania gotówki; nie oznacza, że uczestnicy posiadają rzeczywisty rachunek rezerwowy w Fed.
