# Epistemic Consumer Interface v1

## Purpose

`epistemic-consumer-interface-v1` is the shared read-only contract through which BRACE and WES consume Belief Core epistemic state.

Consumers no longer need direct knowledge of Belief Core persistence internals. They receive a bounded projection from `belief-epistemic-state-v1`.

Canonical path:

```text
Belief Core
  -> EpistemicState
  -> Epistemic Consumer Interface
  -> BRACE / WES
```

## Authority

Aggregate Authority remains unchanged.

Consumers may:

- inspect the compact state;
- request bounded drill-down;
- explain the aggregate;
- challenge evidence;
- request Belief Core recalculation.

Consumers may not:

- override probability or confidence;
- ignore the aggregate because one source is persuasive;
- write back into Belief Core;
- write directly into decision state through this interface;
- auto-tune policy.

Any state change must follow:

```text
consumer challenge
  -> Belief Core recalculation
  -> new EpistemicState
  -> new consumer envelope
```

## Initial profiles

- `BRACE_SPX`
- `WES_SPX`

Both consume the same five SPX supportive beliefs:

- `spx.trend.bullish`
- `spx.breadth.healthy`
- `spx.volatility.benign`
- `spx.liquidity.supportive`
- `spx.financial_conditions.supportive`

This intentionally prevents BRACE and WES from inventing different interpretations of the same Belief Core state.

## Envelope

Each consumer receives:

- stance: risk_on / neutral / defensive;
- aggregate probability;
- aggregate confidence;
- maximum contradiction;
- maximum absolute probability delta;
- compact per-belief state;
- drill-down required flag and reasons;
- immutable source hash;
- explicit authority controls.

The interface fails closed when any required state is missing or authority invariants are not present.

## Bounded drill-down

Consumer drill-down requests are capped at:

- max depth 4;
- max 3 evidence items per side;
- max 6 sources;
- max 1 reinspection cycle.

Drill-down is inspection only. It cannot mutate the aggregate.

## Relationship to PR31

PR31 ARIS diagnostics are not part of this consumer contract. They remain research shadow only. BRACE/WES consume authoritative `EpistemicState`, not PR31-selected alternative representations.

## Migration

This PR creates the common interface and runtime bundle. Existing BRACE/WES legacy Belief bridge code can then be migrated behind this interface without changing engine decision authority in the same step.
