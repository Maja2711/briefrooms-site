# AI Tournament — metodologia v1

## Cel

AI Tournament porównuje pięciu uczestników: BRACE, OpenAI, Claude, Gemini i DeepSeek. Każdy prowadzi oddzielny portfel modelowy o kapitale początkowym 10 000 PLN.

## Wspólne warunki

Każdy uczestnik otrzymuje ten sam zamrożony zestaw danych po zakończeniu sesji USA. Zestaw obejmuje dzienne ceny otwarcia i zamknięcia, wyniki za 1, 5, 20 i 60 sesji, zmienność, obsunięcie, relację do średnich 20- i 60-sesyjnych oraz kurs USD/PLN.

Uczestnicy nie widzą decyzji ani wyników konkurentów przed wydaniem własnej decyzji.

## Egzekucja bez look-ahead

Decyzja powstaje po zamknięciu sesji i określa docelowe wagi portfela. Nie może zostać wykonana po znanej już cenie zamknięcia. Jest wykonywana dopiero po cenie otwarcia kolejnej dostępnej sesji USA, z doliczeniem kosztów i poślizgu.

## Zasady portfela

- wyłącznie pozycje długie,
- brak dźwigni i krótkiej sprzedaży,
- dopuszczone udziały ułamkowe,
- maksymalnie 6 pozycji,
- maksymalnie 30% portfela w jednym instrumencie,
- minimum 2% gotówki,
- maksymalny dzienny obrót 50% portfela,
- koszt transakcyjny 0,10%,
- modelowy poślizg 0,05%,
- minimalna transakcja 50 PLN.

## Uniwersum i benchmark

Pierwszy sezon korzysta ze stałego uniwersum 30 płynnych akcji i ETF-ów notowanych w USA. Benchmarkiem jest SPY. Wartości portfeli są prezentowane w PLN i obejmują wpływ USD/PLN.

## Ranking

Podstawą miejsca jest skumulowana stopa zwrotu od startu. W przypadku równego wyniku decyduje mniejsze maksymalne obsunięcie, następnie wyższy współczynnik Sharpe'a. Sharpe jest pokazywany dopiero po zgromadzeniu co najmniej pięciu dziennych stóp zwrotu.

## Audytowalność

Każdy uczestnik ma osobny dziennik append-only połączony łańcuchem hashy. Zapisywane są wyceny, decyzje, wykonania i błędy dostawcy. Zamknięta runda nie jest przeliczana ponownie, nawet po ręcznym uruchomieniu workflow.

## Harmonogram

Główny przebieg działa od poniedziałku do piątku o 21:45 UTC, po standardowym zamknięciu rynku USA. Audyt naprawczy działa o 23:15 UTC. Jeśli nie pojawiła się nowa sesja, system nie tworzy nowej rundy.

## Charakter projektu

Turniej jest publicznym eksperymentem portfeli modelowych i nie stanowi rekomendacji ani porady inwestycyjnej.
