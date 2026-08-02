# AI Outlook Engine v1

## Zakres

Pierwsza wersja publikuje prognozy wyłącznie w czterech obszarach, w następującej kolejności priorytetu:

1. ekonomia,
2. geopolityka,
3. zdrowie,
4. nauka.

Priorytet nie oznacza automatycznego wyboru ekonomii. Kandydat z danego obszaru musi przejść minimalny próg jakości. Gdy ekonomia nie ma wystarczająco mocnego kandydata, silnik przechodzi do geopolityki, następnie zdrowia i nauki.

## Dwa etapy

1. Model językowy tworzy od 4 do 10 kandydatów na prognozę na podstawie aktualnych materiałów BriefRooms.
2. Deterministyczny silnik punktowy ocenia kandydatów i wybiera zwycięzcę.

Model językowy nie wybiera sam publikowanej prognozy.

## Kryteria punktowe

- jakość dowodów: 22%,
- mierzalność: 20%,
- siła mechanizmu przyczynowego: 18%,
- możliwość późniejszej weryfikacji: 15%,
- jakość źródeł: 15%,
- nowość względem wcześniejszych prognoz: 10%.

Od wyniku odejmowane są kary za wysokie ryzyko spekulacji i podobieństwo do ostatnich publikacji.

## Progi publikacji

- ekonomia: 68/100,
- geopolityka: 68/100,
- zdrowie: 72/100,
- nauka: 72/100.

Zdrowie i nauka wymagają dodatkowo źródła autorytatywnego albo co najmniej dwóch niezależnych źródeł. Każdy kandydat musi zawierać obserwowalne kryterium rozstrzygnięcia w określonym czasie.

## Prawdopodobieństwo

Wersja v1 wylicza konserwatywną ocenę prawdopodobieństwa z wyniku jakości. Nie jest to jeszcze prawdopodobieństwo skalibrowane historycznie. Plik publikacji oznacza metodę jako `heuristic_v1_not_historically_calibrated`.

Kalibracja historyczna i Brier Score wymagają zgromadzenia odpowiednio dużego archiwum rozstrzygniętych prognoz i będą kolejnym etapem rozwoju.

## Bezpieczne zachowanie

Jeśli generacja się nie powiedzie albo nie powstanie kandydat spełniający wymagania, strona zachowuje ostatnią poprawną prognozę. Drugi audyt dzienny ponawia próbę 2,5 godziny po podstawowym przebiegu.
