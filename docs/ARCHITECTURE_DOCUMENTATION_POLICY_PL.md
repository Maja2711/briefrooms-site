# Polityka dokumentacji architektury BriefRooms — PL

## Zasada

Każda zmiana modyfikująca architekturę BriefRooms musi być udokumentowana równolegle po angielsku i po polsku w tym samym pull requeście.

Za zmianę architektury uznajemy co najmniej: nowy lub zmieniony kontrakt kanoniczny, granice authority, przepływ danych, komponent runtime, pętlę learning/verification, interfejs silnika, semantykę persystencji, granice migracji lub invariant bezpieczeństwa.

## Obowiązkowa para dokumentów

Dla dokumentu `docs/<NAZWA>_EN.md` ten sam pull request musi zawierać semantycznie równoważny `docs/<NAZWA>_PL.md` i odwrotnie.

Wersje nie muszą być tłumaczeniem słowo w słowo, ale muszą opisywać tę samą architekturę, invarianty, zakres migracji i granice authority.

## Wymóg kanonicznej Architecture Map

`docs/ARCHITECTURE_MAP_EN.md` i `docs/ARCHITECTURE_MAP_PL.md` są kanonicznymi mapami nawigacyjnymi całej architektury BriefRooms i stanowią jeden logiczny artefakt.

Każdy pull request zmieniający architekturę MUSI zaktualizować obie mapy w tym samym pull requeście. Po zmianie muszą pozostać poprawne: stabilny `module_id`, odpowiedzialność modułu, granica authority, wejścia/wyjścia, state/learning mode, odwołania do implementacji oraz invarianty bezpieczeństwa.

Nowy subsystem musi otrzymać stabilny `module_id`. Zastąpiony albo wycofywany subsystem musi pozostać śledzalny przez jawny wpis migration/deprecation/status zamiast zniknąć z mapy bez śladu.

Jeżeli zmiana architektury nie zmienia top-level dependency graph, mapa nadal musi zostać sprawdzona, a dotknięty wpis modułu/statusu/referencji zaktualizowany, jeśli jest to potrzebne. Rozjazd pomiędzy kodem runtime i mapą traktujemy jako defekt architektoniczny.

## Obowiązkowy bootstrap AI / agentów

Plik `AGENTS.md` w katalogu głównym repozytorium jest obowiązkowym punktem wejścia dla AI i agentów kodujących. Przed projektowaniem lub wdrażaniem zmiany dotyczącej architektury, tradingu, learningu, Belief/Epistemic, decision logic, risk, workflow, persystencji, promocji/rollbacku lub execution agent musi najpierw otworzyć kanoniczną Architecture Map, zidentyfikować dotknięte `module_id`, przeczytać wskazaną dokumentację szczegółową i dopiero potem analizować implementację.

Agent nie może rekonstruować architektury BriefRooms z pamięci rozmowy ani rozpoczynać nowego subsystemu bez wcześniejszego sprawdzenia, czy równoważna lub nakładająca się funkcja już istnieje. `README.md` utrzymuje widoczny wskaźnik do tego bootstrapu, a `scripts/validate_architecture_bootstrap.py` wraz z workflow `Architecture Bootstrap Guard` chronią jego obecność oraz synchronizację wersji map PL/EN.

## Semantyczna zgodność runtime

Od Architecture Reconciliation 1.5 sama zgodność numeru wersji PL/EN nie wystarcza. `scripts/validate_architecture_reconciliation.py` sprawdza wybrane krytyczne fakty runtime, authority i wiring przeciwko mapie. `Architecture Bootstrap Guard` wykonuje ten test także cyklicznie, aby wykrywać drift między `main` i aktywną gałęzią research.

## Wymóg pull requestu

PR architektoniczny jest niekompletny, dopóki nie istnieją obie wersje językowe oraz obie kanoniczne Architecture Maps poprawnie opisujące system po zmianie. Kod runtime pozostaje źródłem prawdy implementacyjnej; sparowane dokumenty architektury i Architecture Map są czytelnym dla człowieka zapisem projektu.

## Zakres

Polityka obowiązuje prospektywnie od PR32A. Nie wymaga uzupełniania wszystkich historycznych dokumentów architektury BriefRooms. Wymóg kanonicznej Architecture Map obowiązuje prospektywnie od Architecture Map v1.0.
