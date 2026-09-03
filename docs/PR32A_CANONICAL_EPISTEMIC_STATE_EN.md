# PR32A — Canonical EpistemicState contract + builder

## Purpose

PR32A introduces one canonical, deterministic and hash-addressed representation of the existing Belief Core Epistemic State projection.

It does **not** create another belief engine. The existing `belief-epistemic-state-v1` projection remains the upstream aggregate epistemic view. PR32A canonicalizes that already-computed state so downstream consumers can bind decisions, verification and research records to exact immutable epistemic lineage.

## Architecture

```text
Belief Core
  -> belief-epistemic-state-v1
  -> PR32A canonical builder
  -> briefrooms-epistemic-state-v1
  -> downstream read-only consumers
```

The reversible provenance path is mandatory:

```text
EpistemicState -> Belief -> Evidence -> Observation -> Source
```

## Canonical identity

`briefrooms-epistemic-state-v1` contains deterministic:

- `state_id` (`eps-*`),
- `state_hash` (SHA-256),
- `belief_hash` for every canonical belief,
- `evidence_hash` for every referenced evidence item,
- `observation_hash` for every referenced observation,
- hashes of the upstream projection, Belief Core state and observation source.

Collection ordering does not change canonical identity.

## Point-in-time safety

All timestamps must be timezone-aware and are normalized to UTC. The builder rejects future observations, future evidence and belief states that exceed the canonical state cutoff.

No missing lineage is interpreted as PASS. Every canonical evidence item must resolve to at least one observation and every canonical belief evidence reference must exist.

## Authority

The canonical state has zero trading or policy authority. The following remain false:

- decision authority,
- risk-limit authority,
- trade-execution authority,
- Belief Core writeback,
- LLM override,
- automatic tuning.

The Aggregate Authority Principle remains upstream: reasoning clients inspect the authoritative aggregate state; they do not privately replace its posterior.

## Runtime integration

The existing `Belief Epistemic State Projection` workflow now:

1. restores the exact triggering Belief Core artifact;
2. builds the existing authoritative `belief-epistemic-state-v1` projection;
3. builds and verifies `canonical_epistemic_state.json`;
4. packs the canonical file into the existing private `belief-epistemic-state` artifact.

The canonical state is not committed as mutable runtime data to the repository.

## Relationship to PR32B

PR32B may bind a later verification target to `state_id + state_hash` and to the exact canonical belief/evidence hashes. PR32A itself performs no verification, outcome scoring or learning.

## Migration policy

PR32A is prospective:

- no historical canonical-state fabrication,
- no Belief Core rewrite,
- no DecisionEnvelope backfill,
- no Learning Ledger rewrite,
- no Experience Store rewrite.

Legacy states remain legacy/non-canonical.
