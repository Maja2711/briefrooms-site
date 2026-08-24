# PR36 — Champion vs Challenger + Statistical Gate v1

## Purpose

PR36 strengthens PR35. A challenger can no longer reach production merely because its own shadow results are positive. It must beat the current policy on the **same future cases**, after a conservative cost stress, with a positive bootstrap confidence bound.

## Comparison contract

For the score-threshold candidates supported by PR35:

- **Champion** = current score threshold; the exact marginal candidate remains FLAT.
- **Challenger** = threshold lowered by one point; the prospectively frozen rejected LONG is taken.
- The comparison uses only PR29.1 candidates frozen before the outcome.
- Training observations never become validation observations.
- Publication-day OHLC remains excluded by PR35 settlement.

Therefore every validation row is paired:

```text
same candidate / same future path
        │
        ├── champion:   FLAT = 0 incremental return
        └── challenger: frozen LONG outcome - cost stress
```

This is not a replay chosen after the fact. The candidate, score, gate, price, stop and target existed at T0.

## Cost stress

PR36 uses a deliberately conservative research allowance, not a broker fee quote:

- GPW Daily: 0.20% round trip
- US Daily: 0.10% round trip

The challenger must remain superior after this deduction.

## Statistical Promotion Gate

A PR35 descriptive PASS becomes only a provisional promotion. PR36 is the final production authorization.

Minimum requirements:

- paired validation N >= 25,
- challenger net incremental mean >= +0.10%,
- challenger net positive rate >= 55%,
- deterministic 90% bootstrap confidence interval lower bound > 0,
- at least 5 distinct symbols,
- validation spans at least 10 calendar days,
- first half net mean > 0,
- second half net mean > 0,
- no single positive observation contributes more than 50% of all positive contribution.

If these conditions are not yet met before N=50, the candidate remains held and the champion stays active. At N>=50 an unresolved statistical failure rejects the transition and blocks the same transition for 30 days.

## Production write boundary

PR36 adds a persistent, hash-verified authorization store:

`statistical_policy_authorizations.json`

A non-baseline policy can be materialized only if the exact tuple matches a stored PASS:

- engine,
- policy id,
- effective policy version,
- source candidate id.

The production workflow no longer calls the PR35 materializer directly. It calls `statistical_policy_materializer.py`, which fails closed if authorization is missing or mismatched.

Previously authorized parent versions remain valid rollback targets.

## What happens while evidence is still collecting

PR35 may provisionally mark a candidate promoted after its descriptive gate. PR36 immediately evaluates it before any production write.

If PR36 returns `COLLECTING` or `HOLD`:

1. the parent champion is restored in the private registry,
2. the candidate returns to `SHADOW_VALIDATION`,
3. no production config changes,
4. the next cycle can add more unseen validation observations.

This prevents an unproven provisional policy from spawning another lower-threshold candidate.

## Autonomous flow after PR36

```text
PR29.1 frozen rejected candidates
              ↓
PR35 Policy Candidate
              ↓
PR35 descriptive gate
              ↓
provisional promotion
              ↓
PR36 same-case Champion vs Challenger
              ↓
net of conservative costs
              ↓
deterministic bootstrap CI
        ┌─────┴─────┐
   NO PASS          PASS
      │               │
restore champion   statistical authorization
      │               ↓
collect more       production materializer
or reject              ↓
                   AUTO-PROMOTE
                       ↓
                 PR35 rollback monitor
```

## Non-goals

PR36 does not:

- create new policy parameters,
- rewrite engine code,
- use an LLM to tune policy,
- expand autonomy beyond GPW/US score thresholds,
- execute broker trades,
- turn rejected candidates into hindsight trades.
