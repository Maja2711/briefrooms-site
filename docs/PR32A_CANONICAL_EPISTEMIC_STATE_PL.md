# PR32A — Kanoniczny kontrakt EpistemicState + builder

## Cel

PR32A wprowadza jedną kanoniczną, deterministyczną i adresowaną hashem reprezentację istniejącej projekcji Epistemic State z Belief Core.

Nie tworzy drugiego silnika belief. Istniejąca projekcja `belief-epistemic-state-v1` pozostaje nadrzędnym zagregowanym obrazem epistemicznym. PR32A kanonizuje już obliczony stan, aby dalsi konsumenci mogli wiązać decyzje, weryfikację i rekordy badawcze z dokładną, niezmienną linią pochodzenia.

## Architektura

```text
Belief Core
  -> belief-epistemic-state-v1
  -> builder kanoniczny PR32A
  -> briefrooms-epistemic-state-v1
  -> konsumenci read-only
```

Obowiązkowa jest odwracalna ścieżka provenance:

```text
EpistemicState -> Belief -> Evidence -> Observation -> Source
```

## Kanoniczna tożsamość

`briefrooms-epistemic-state-v1` zawiera deterministyczne:

- `state_id` (`eps-*`),
- `state_hash` (SHA-256),
- `belief_hash` dla każdego kanonicznego belief,
- `evidence_hash` dla każdego użytego elementu evidence,
- `observation_hash` dla każdej użytej obserwacji,
- hashe projekcji upstream, stanu Belief Core i źródła obserwacji.

Kolejność elementów w kolekcjach nie zmienia kanonicznej tożsamości.

## Bezpieczeństwo point-in-time

Wszystkie timestampy muszą zawierać strefę czasową i są normalizowane do UTC. Builder odrzuca przyszłe obserwacje, przyszłe evidence i stany belief wykraczające poza cutoff kanonicznego stanu.

Brak lineage nigdy nie jest interpretowany jako PASS. Każdy kanoniczny element evidence musi prowadzić do co najmniej jednej obserwacji, a każde odwołanie belief do evidence musi istnieć.

## Authority

Kanoniczny stan nie ma authority do handlu ani polityki ryzyka. Następujące możliwości pozostają wyłączone:

- decision authority,
- risk-limit authority,
- trade-execution authority,
- writeback do Belief Core,
- override przez LLM,
- automatyczne strojenie.

Aggregate Authority Principle pozostaje upstream: konsumenci reasoning odczytują autorytatywny stan zagregowany i nie zastępują prywatnie jego posterioru.

## Integracja runtime

Istniejący workflow `Belief Epistemic State Projection` teraz:

1. odtwarza dokładny artefakt Belief Core, który wywołał workflow;
2. buduje istniejącą autorytatywną projekcję `belief-epistemic-state-v1`;
3. buduje i weryfikuje `canonical_epistemic_state.json`;
4. pakuje plik kanoniczny do istniejącego prywatnego artefaktu `belief-epistemic-state`.

Kanoniczny stan runtime nie jest commitowany do repozytorium jako zmienny plik danych.

## Relacja do PR32B

PR32B może związać późniejszy verification target z `state_id + state_hash` oraz dokładnymi hashami belief/evidence. Sam PR32A nie wykonuje weryfikacji, scoringu outcome ani uczenia.

## Polityka migracji

PR32A działa prospektywnie:

- bez tworzenia sztucznych historycznych canonical states,
- bez przepisywania Belief Core,
- bez backfillu DecisionEnvelope,
- bez przepisywania Learning Ledger,
- bez przepisywania Experience Store.

Stare stany pozostają legacy/non-canonical.
