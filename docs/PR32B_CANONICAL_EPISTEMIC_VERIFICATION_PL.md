# PR32B — Kanoniczne powiązanie EpistemicState z weryfikacją/outcome

## Cel

PR32B domyka granicę pozostawioną celowo w PR32A: późniejszy wynik może być mierzony wyłącznie względem dokładnie zamrożonej, kanonicznej linii epistemicznej.

PR32B **nie** tworzy drugiego belief engine i nie otrzymuje żadnej władzy nad decyzją, ryzykiem, egzekucją ani automatycznym tuningiem.

## Architektura

```text
Belief Core
  -> belief-epistemic-state-v1
  -> PR32A briefrooms-epistemic-state-v1
  -> PR32B immutable VerificationTarget
  -> późniejszy jawny outcome
  -> canonical verification + proper scoring
  -> istniejący format wejściowy Belief Calibration (tylko pomiar)
```

## Niezmienny target weryfikacji

`briefrooms-epistemic-verification-target-v1` zamraża:

- `state_id + state_hash`,
- `belief_id + belief_hash`,
- dokładny zestaw `evidence_id + evidence_hash`,
- timestamp stanu i belief,
- przewidywane prawdopodobieństwo i confidence,
- domain/entity,
- opcjonalny opis oczekiwanego wyniku.

ID i hash targetu są deterministycznie wyliczane z SHA-256. Późniejsza zmiana stanu, belief, evidence, prawdopodobieństwa lub flag authority unieważnia target.

Target powstaje tylko dla belief oznaczonego w canonical EpistemicState jako `verify_later=true`.

## Rozliczenie późniejszego outcome

`briefrooms-epistemic-verification-v1` może powstać wyłącznie z poprawnego, wcześniej istniejącego targetu. Outcome musi być zaobserwowany później niż `as_of` zamrożonego canonical EpistemicState.

Weryfikacja zamraża:

- pełną linię targetu,
- binarny outcome,
- czas weryfikacji,
- źródło/referencję outcome,
- Brier score,
- log loss.

ID/hash weryfikacji są deterministyczne. Manipulacja scoringiem albo linią provenance powoduje fail-closed.

## Integracja z istniejącą kalibracją

`calibration_record()` mapuje poprawną canonical verification do istniejącego kontraktu wejściowego `belief_calibration`. PR32B korzysta więc z obecnego subsystemu kalibracji zamiast tworzyć drugi silnik learning/calibration.

Adapter jest read-only. Nie zmienia automatycznie probability mapping, reliability evidence, polityki, limitów ryzyka ani stanu silników.

## Pliki runtime

Artifact Epistemic State przechowuje prospektywne historie:

- `epistemic_verification_targets.jsonl`
- `canonical_epistemic_verifications.jsonl`

Opcjonalny jawny feed outcome może być podany jako:

- `epistemic_outcomes.jsonl`

Outcome wskazujący nieznany target jest odrzucany. PR32B nigdy nie fabrykuje historycznego targetu dopiero po poznaniu wyniku.

## Authority i safety

Wszystkie flagi authority PR32B pozostają `false`:

- decision authority,
- risk-limit authority,
- trade-execution authority,
- Belief Core writeback,
- evidence-weight writeback,
- automatic tuning,
- LLM override.

## Migracja

Wyłącznie prospective:

- bez fabrykowania historycznych targetów,
- bez backfill historycznych canonical verification,
- bez przepisywania Belief Core,
- bez resetu Learning Ledger,
- bez resetu Experience Store.

Stare weryfikacje pozostają legacy. Kanoniczne outcome learning zaczyna się dopiero od targetów utworzonych przez PR32B.
