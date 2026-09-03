# Polityka dokumentacji architektury BriefRooms — PL

## Zasada

Każda zmiana modyfikująca architekturę BriefRooms musi być udokumentowana równolegle po angielsku i po polsku w tym samym pull requeście.

Za zmianę architektury uznajemy co najmniej: nowy lub zmieniony kontrakt kanoniczny, granice authority, przepływ danych, komponent runtime, pętlę learning/verification, interfejs silnika, semantykę persystencji, granice migracji lub invariant bezpieczeństwa.

## Obowiązkowa para dokumentów

Dla dokumentu `docs/<NAZWA>_EN.md` ten sam pull request musi zawierać semantycznie równoważny `docs/<NAZWA>_PL.md` i odwrotnie.

Wersje nie muszą być tłumaczeniem słowo w słowo, ale muszą opisywać tę samą architekturę, invarianty, zakres migracji i granice authority.

## Wymóg pull requestu

PR architektoniczny jest niekompletny, dopóki nie istnieją obie wersje językowe. Kod runtime pozostaje źródłem prawdy implementacyjnej; sparowane dokumenty architektury są czytelnym dla człowieka zapisem projektu.

## Zakres

Polityka obowiązuje prospektywnie od PR32A. Nie wymaga uzupełniania wszystkich historycznych dokumentów architektury BriefRooms.
