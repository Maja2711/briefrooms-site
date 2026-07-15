# BriefRooms — zasady jakości komentarzy

Te zasady są stałe i mają chronić jakość komentarzy pod briefami.

## Jedna główna zasada
Najpierw czytamy dostępny tekst artykułu, potem streszczamy jego sens. Komentarz nie może być budowany z samego tytułu.

## Komentarz pod artykułem
Komentarz może zostać opublikowany tylko wtedy, gdy spełnia wszystkie warunki:

1. Ma od 3 do 6 zdań.
2. Jest logiczny, gramatyczny i zrozumiały dla czytelnika bez znajomości poprzednich zdań.
3. Każde zdanie zaczyna się pełną informacją albo normalnym podmiotem.
4. Nie zaczyna się od urwanego czasownika typu „Dodał”, „Zaznaczył”, „Powiedział”, „Wskazał”.
5. Nie zawiera krzaków kodowania ani urwanych polskich słów, np. „rz du”, „mwi”, „koz w”, „obowi zek”, „rozwi za”, „Å”, „Ä”, „Ã”, „Â”, „â”.
6. Nie zawiera poleceń redakcyjnych, nazwisk autorów, podpisów, źródeł zdjęć ani elementów UI, np. „Agnieszka Loosen /”, „Skomentuj”, „FOTONEWS”, „PAP”, „czytaj także”, „zobacz także”.
7. Nie zawiera ogólników typu „źródłem wpisu jest”, „pełne tło jest w artykule” ani zdań o kategorii zamiast sensu tekstu.
8. Jeśli tekst nie przejdzie kontroli jakości, komentarz nie jest publikowany. Nie wolno zastępować go krótkim, niegramatycznym albo tytułowym streszczeniem.
9. News bez pełnego komentarza spełniającego powyższe warunki nie może być pokazany na stronie głównej. Usuwamy całą kartę, a nie publikujemy pusty lub pozorny brief.
10. Komentarz powstaje dopiero po poprawnym zdekodowaniu bajtów strony źródłowej. Nie wolno ufać błędnemu nagłówkowi kodowania ani usuwać nierozpoznanych znaków z tekstu.
11. Każdy nowy komentarz musi przejść trzy etapy: generację z materiału źródłowego, niezależną recenzję AI oraz deterministyczną kontrolę językową. Jedno wadliwe zdanie odrzuca cały komentarz.
12. Surowy tekst artykułu, opis RSS, tytuł ani stary wpis w pamięci podręcznej nie mogą być komentarzem awaryjnym. Przy awarii zachowujemy wyłącznie ostatni zestaw zatwierdzony bieżącą wersją kontroli jakości.

## Krótkie komentarze na stronach Aktualności / News
1. Komentarz ma 1–2 pełne zdania i wynika z konkretnego opisu RSS, a nie z samego tytułu.
2. Obowiązuje ten sam proces: generacja, niezależna recenzja AI i deterministyczna kontrola PL/EN.
3. Brak klucza, błąd API, zbyt krótki materiał lub odrzucenie recenzenta oznacza brak publikacji danego komentarza.
4. Jeśli cały nowy zestaw ma zbyt mało zatwierdzonych komentarzy, generator kończy się błędem i nie nadpisuje działającej strony.

## Strona główna
Krótki opis na karcie jest tworzony z pierwszych zdań zaakceptowanego pełnego komentarza. Nie może pochodzić z samego tytułu, podpisu autora ani przypadkowego fragmentu RSS.

## Stały rytm aktualizacji strony i newsów
1. Strona główna oraz pliki newsów PL i EN są odświeżane co 4 godziny.
2. Przed każdym przebiegiem zapisywany jest ostatni poprawny zestaw kart.
3. Błąd pojedynczego generatora, tłumaczenia, walidatora albo skryptu wyglądu nie może zatrzymać zapisania ostatnich poprawnych danych.
4. Pusta, częściowa albo uszkodzona aktualizacja nie może zastąpić widocznego zestawu newsów.
5. Niezależny watchdog sprawdza stan co godzinę. Gdy dane są starsze niż 5 godzin, uruchamia ścieżkę odzyskiwania.
6. Kontrakt częstotliwości i zabezpieczeń jest zapisany w `data/content_update_contract.json` i kontrolowany automatycznie.

## Duplikaty tematów
Na stronie głównej jeden realny temat albo jedno zdarzenie może mieć tylko jedną kartę, nawet jeśli opisują je dwa różne źródła i prowadzą do dwóch różnych linków. Drugi link nie tworzy nowego briefu, jeśli sedno sprawy jest takie samo.

## Pilne / Breaking
„Pilne” i „Breaking” są tylko sygnałem priorytetu sortowania. Nie pokazujemy ich jako etykiety na zdjęciu karty.

## Hot X
1. Sekcja Hot X jest aktualizowana automatycznie dwa razy dziennie.
2. Każdy przebieg ma próbować pobrać nowe tematy albo konkretne posty z X, a nie tylko ponownie zapisywać stare dane.
3. Każda widoczna karta musi mieć konkretny komentarz. Sam tytuł, pusty opis lub ogólnik typu „Na X monitorowany jest temat” nie jest komentarzem.
4. W wersji PL wszystkie widoczne tytuły i komentarze muszą być po polsku; angielski tekst nie może zostać użyty jako fallback.
5. W wersji EN tytuły i komentarze pozostają po angielsku.
6. Jedno realne zdarzenie może pojawić się w Hot X tylko raz.
7. Aktualizacja nie może wyczyścić całej sekcji. Jeżeli nowe dane są puste, uszkodzone, ogólnikowe albo nie mają poprawnego linku do X, pozostają ostatnie poprawne karty.
8. Jeżeli brak ostatnich poprawnych danych, ładowany jest zapisany zestaw awaryjny z pełnymi komentarzami.
9. Zawsze muszą pozostać co najmniej trzy karty z komentarzem. Brakujące miejsca są uzupełniane ostatnimi poprawnymi kartami.
10. Watchdog uznaje Hot X za przeterminowany po 13 godzinach i uruchamia ponowną próbę aktualizacji.
