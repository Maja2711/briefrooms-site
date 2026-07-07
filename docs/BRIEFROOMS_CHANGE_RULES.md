# BriefRooms change discipline

Kluczowa zasada pracy nad stroną:

1. Usuwamy wyłącznie elementy, które użytkownik wyraźnie kazał usunąć.
2. Nie upraszczamy strony przez kasowanie historii, sum, sekcji wyników albo danych, jeśli nie ma takiego polecenia.
3. Przy zmianach wizualnych nie zmieniamy logiki danych, chyba że użytkownik o to poprosi.
4. Przy zmianach logiki danych nie zmieniamy układu strony ponad zakres polecenia.
5. Wersje PL i EN mają pozostać zsynchronizowane, ale z właściwymi lokalnymi danymi.
6. Jeśli jakaś wartość nie jest jeszcze dostępna, pokazujemy `—` albo komunikat o braku danych, nigdy sztuczne `0`.
7. Historia i łączny wynik zamkniętych pozycji nie mogą zostać usunięte bez wyraźnego polecenia.
