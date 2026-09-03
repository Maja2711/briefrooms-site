# PR32B — Canonical EpistemicState verification/outcome binding

## Purpose

PR32B completes the boundary intentionally left by PR32A: a later outcome can be measured only against an exact frozen canonical epistemic lineage.

It does **not** create another belief engine and does not grant any decision, risk, execution or automatic-tuning authority.

## Architecture

```text
Belief Core
  -> belief-epistemic-state-v1
  -> PR32A briefrooms-epistemic-state-v1
  -> PR32B immutable VerificationTarget
  -> later explicit outcome
  -> canonical verification + proper scoring
  -> existing Belief Calibration input shape (measurement only)
```

## Immutable target binding

A `briefrooms-epistemic-verification-target-v1` freezes:

- `state_id + state_hash`,
- `belief_id + belief_hash`,
- exact `evidence_id + evidence_hash` bindings,
- belief/state timestamps,
- predicted probability and confidence,
- domain/entity,
- optional expected outcome description.

Target ID and hash are deterministic SHA-256 derivatives of those facts. Any later mutation of state, belief, evidence, probability or authority flags invalidates the target.

Targets are created only for canonical beliefs explicitly marked `verify_later=true`.

## Later outcome resolution

A `briefrooms-epistemic-verification-v1` can be produced only from a valid pre-existing target. The outcome must be observed strictly after the frozen canonical state's `as_of` timestamp.

The verification freezes:

- the complete target lineage,
- binary outcome,
- verification timestamp,
- outcome source/reference,
- Brier score,
- log loss.

Verification ID/hash are deterministic. Score tampering or lineage tampering fails closed.

## Existing calibration integration

`calibration_record()` adapts a valid canonical verification to the existing `belief_calibration` input contract. PR32B therefore reuses the existing calibration subsystem rather than introducing a second learning/calibration engine.

The adapter is read-only. No probability mapping, evidence reliability, policy, risk limit or engine state is changed automatically.

## Runtime files

The Epistemic State runtime artifact carries prospective append-only histories:

- `epistemic_verification_targets.jsonl`
- `canonical_epistemic_verifications.jsonl`

An optional explicit outcome feed can be supplied as:

- `epistemic_outcomes.jsonl`

An outcome referencing an unknown target is rejected; PR32B never fabricates a historical target after seeing the outcome.

## Authority and safety

All PR32B authority flags remain false:

- decision authority,
- risk-limit authority,
- trade-execution authority,
- Belief Core writeback,
- evidence-weight writeback,
- automatic tuning,
- LLM override.

## Migration policy

Prospective only:

- no historical target fabrication,
- no historical verification backfill,
- no Belief Core rewrite,
- no Learning Ledger reset,
- no Experience Store reset.

Legacy verifications remain legacy. Canonical outcome learning starts only from targets created by PR32B.
