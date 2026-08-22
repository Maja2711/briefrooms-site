# Learning Ledger / Outcome Loop v1

## Status

Research-shadow, zero authority. This layer records prospective facts required for later learning. It does not learn weights yet and cannot modify Belief Core, causal edges, engine policy, ranking, sizing or execution.

## Why now

Belief Core already freezes forecasts and verifies outcomes. PR19 adds prospective epistemic bindings and causal hypotheses. The missing cross-engine primitive is a durable, append-only learning history that can join forecasts, decisions, outcomes, verifications and later learning observations without hindsight rewriting.

## Loop

```text
Observation / Evidence
        -> frozen Belief forecast
        -> shadow or active decision snapshot
        -> time passes
        -> real outcome
        -> verification
        -> Learning Ledger
        -> later diagnostics / candidate policy
        -> prospective shadow validation
        -> reviewed promotion (future work)
```

## Event contract

Supported event types in v1:

- `forecast`
- `decision`
- `outcome`
- `verification`
- `learning_observation`

Every event has deterministic `event_id`, `occurred_at`, `subject_id`, optional `source_ref`, payload, append-time `recorded_at`, `previous_hash` and `event_hash`.

The ledger is JSONL and SHA-256 hash chained. Before append the complete existing chain is verified. Corrupt/tampered history fails closed. Identical events are idempotent.

## Durability

Default path: `data/research/learning_ledger.jsonl`.

Writes use append mode and `fsync`. Existing rows are never rewritten by the API. This complements, rather than replaces, the domain-specific frozen forecast stores and PR19 research state.

## Zero-authority invariant

All remain false in v1:

- automatic tuning
- belief writeback
- evidence-weight writeback
- causal-edge writeback
- engine-policy writeback
- ranking/sizing writeback
- trade execution
- automatic promotion

The ledger records what happened. It does not decide what should happen next.

## Next empirical gate

After enough prospective records exist, build diagnostics over immutable history: calibration delta, source/evidence utility, LLM interpretation performance, causal-hypothesis support diagnostics and WITH-vs-WITHOUT decision uplift. Any proposed change must become a versioned candidate and win prospective shadow validation before promotion.
