# AI Outlook Engine v1.1

## Zakres i priorytet

Pierwsza wersja publikuje prognozy wyłącznie w czterech obszarach:

1. ekonomia,
2. geopolityka,
3. zdrowie,
4. nauka.

Priorytet nie oznacza automatycznego wyboru ekonomii. Kandydat musi przejść próg jakości dla swojego obszaru. Gdy ekonomia nie ma wystarczająco mocnego kandydata, silnik przechodzi do geopolityki, zdrowia i nauki.

## Dwie warstwy, bez udawanej statystyki

1. Model językowy tworzy od 4 do 10 kandydatów na podstawie aktualnych materiałów BriefRooms.
2. Deterministyczny silnik punktowy ocenia kandydatów i wybiera zwycięzcę.

Wersja 1.1 używa warstwy regułowej i jednej oceny AI. Warstwa statystyczna jest wyłączona do czasu zebrania własnego archiwum rozstrzygniętych prognoz.

## Zamrożone wagi

Każda publikacja zapisuje:

- `engine.version`,
- `weights_version`,
- pełny `weights_snapshot`,
- `scoring_policy_version`,
- wersje schematów provenance, rozstrzygnięcia i governance.

Wagi v1:

- jakość dowodów: 22%,
- mierzalność: 20%,
- siła mechanizmu przyczynowego: 18%,
- możliwość późniejszej weryfikacji: 15%,
- jakość źródeł: 15%,
- nowość: 10%.

Zmiana wag w przyszłości nie zmienia historycznych rekordów, ponieważ każdy Outlook przechowuje własny snapshot.

## Provenance i niezależność źródeł

Każdy dowód otrzymuje rekord `provenance` oraz `provenance_id`. Liczba niezależnych potwierdzeń jest liczona po unikalnych `provenance_id`, a nie po liczbie domen lub artykułów. Dwa portale powtarzające tę samą depeszę nie zwiększają liczby niezależnych dowodów.

Provenance może pochodzić z:

- jawnego identyfikatora w danych,
- adresu dokumentu pierwotnego,
- organizacji pierwotnej, daty i fingerprintu historii,
- kanonicznego adresu źródła jako ostrożnego fallbacku.

## Strukturalne rozstrzygnięcie

Przed publikacją każdy kandydat musi zawierać obiekt `resolution` z polami:

- `metric`,
- `comparison_operator`,
- `threshold`,
- `unit`,
- `data_source_for_verification`,
- `resolution_date`,
- opcjonalnie `baseline_date`, `baseline_value`, `verification_url` i `geography`.

Brak poprawnego JSON-u rozstrzygnięcia powoduje odrzucenie kandydata. Proza widoczna na stronie jest wyłącznie podsumowaniem zamrożonego obiektu.

## Kategorie regulowane i disclaimer

Każdy rekord zawiera `governance` z:

- `content_category`,
- `risk_class`,
- `disclaimer_required`,
- `disclaimer_id`,
- tekstami disclaimerów PL i EN.

Treści inwestycyjne otrzymują klasę `regulated_financial_content`, a zdrowotne `medical_information`. Generator blokuje publikację bez wymaganego disclaimeru. Osobny skrypt przeglądarkowy ponownie weryfikuje dane przed wyświetleniem karty.

## Progi i bezpieczeństwo

- ekonomia: 68/100,
- geopolityka: 68/100,
- zdrowie: 72/100,
- nauka: 72/100.

Zdrowie i nauka wymagają źródła autorytatywnego albo co najmniej dwóch niezależnych `provenance_id`.

## Log decyzji

Każdy przebieg zapisuje wewnętrzny audyt w `data/internal/ai_outlook_audit/YYYY-MM-DD.json`. Log obejmuje:

- wszystkich poprawnych i niepoprawnych kandydatów,
- wynik punktowy i próg,
- decyzję `selected` lub `rejected`,
- kody powodów odrzucenia,
- snapshot dowodów i provenance,
- zamrożone wagi i wersje polityk.

## Brier Score

Publiczny Brier Score jest ukryty do czasu zgromadzenia co najmniej 30 rozstrzygniętych prognoz. Wcześniej publikowane są tylko surowe liczniki: liczba prognoz, liczba w toku i liczba rozstrzygniętych.

Metryka jest budowana przez `scripts/ai_outlook_metrics.py` i zapisywana w `data/ai_outlook_metrics.json`.

## Bezpieczne zachowanie

Jeśli generacja się nie powiedzie albo żaden kandydat nie przejdzie progów, pozostaje ostatnia poprawna prognoza. Drugi audyt dzienny ponawia próbę 2,5 godziny po podstawowym przebiegu.
