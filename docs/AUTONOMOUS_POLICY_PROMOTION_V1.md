# PR35 — Policy Candidate + Autonomous Promotion Gate + Rollback v1

## Purpose

PR35 closes the first bounded self-improvement loop in BriefRooms.

The system may now:

1. observe prospectively frozen Daily Stock decisions and rejected candidates,
2. settle rejected LONG candidates under a predeclared shadow rule,
3. create a one-parameter policy candidate after enough training evidence,
4. validate that candidate only on later unseen observations,
5. automatically promote the candidate when all Promotion Gate conditions pass,
6. automatically materialize the promoted value into the production config,
7. monitor later real paper-trade outcomes under the new policy version,
8. automatically roll back to the parent policy when live performance breaches the rollback gate.

No human approval is required after the Promotion Gate passes.

## Initial autonomous scope

Autonomous production influence is deliberately narrow.

### Daily GPW

- parameter: `minimum_composite_score`
- checked-in baseline: `72`
- immutable allowed range: `68..76`
- one candidate step: `-1` point

### Daily US Stocks

- parameter: `target_score`
- checked-in baseline: `72`
- immutable allowed range: `68..76`
- one candidate step: `-1` point

### Not autonomous in v1

- Daily EUR/USD
- WES
- BRACE Portfolio 10K
- BRACE-SPX Generation 6
- Belief Core probability mapping / evidence reliability
- GSE

Those remain observation/calibration layers until they receive their own versioned bounded-policy contract.

## Why v1 does not rewrite code

PR35 never asks an LLM to edit Python and never changes arbitrary config fields.

The only production write path is:

```text
hash-verified Policy Registry
        ↓
immutable allowlist
        ↓
exactly one allowed scalar parameter
        +
policy_version
        ↓
checked-in production config
```

Any unknown parameter, out-of-range value, corrupted registry, manual policy-version divergence or invalid hash fails closed before a repository write.

## Prospective activation

The first successful PR35 production run creates `policy_activation.json`.

Only PR29 decision snapshots whose decision time is at or after that activation are eligible for PR35 shadow evidence. No historical PR29 record is retroactively converted into an autonomous-training observation.

## Rejected-candidate shadow settlement

PR29.1 freezes rejected Daily Stock LONG candidates with their T0 price/risk plan and decision path.

PR35 settles only prospectively frozen `risk_plan` candidates. To avoid using any part of the publication session that occurred before the decision, the v1 challenger rule is:

```text
entry = frozen reference price at T0
ignore publication-day OHLC for outcome settlement
observe the next two full market sessions
if SL and TP touch in the same daily bar -> STOP wins conservatively
else first SL/TP touch wins
else exit at second full-session close
```

This rule is intentionally conservative and fixed before evaluation. It is a policy-challenger outcome, not a claim that an executable fill occurred.

## Candidate generation

A candidate is created only for the one-point score-threshold reduction that would have admitted a frozen rejected candidate.

Training uses only candidates:

- from the same engine,
- blocked first by `minimum_composite_score`,
- with all other hard gates passing,
- whose frozen score lies in the exact marginal band affected by the proposed one-point threshold change.

Minimum training sample:

- `N >= 30`
- mean shadow return at least `+0.15%`
- positive outcome rate at least `55%`
- mean R at least `+0.10R` when R is available

Creating a candidate freezes `validation_start_at`. Training rows can never become validation rows.

## Promotion Gate

The candidate remains `SHADOW_VALIDATION` until at least 20 later observations exist in the same marginal score band.

Promotion requires all of:

- validation `N >= 20`,
- mean return `>= +0.15%`,
- positive rate `>= 55%`,
- mean R `>= +0.10R` when available,
- shadow max drawdown not worse than `-3R`,
- first half of validation has positive mean return,
- second half of validation has positive mean return.

If the sample reaches 50 observations without satisfying the gate, the candidate is rejected and the same transition is blocked for 30 days.

A PASS causes automatic promotion. There is no human-review state in v1.

## Production materialization

The private PR35 state contains the authoritative `policy_registry.json`.

On promotion, the registry receives a new immutable policy identity and revision, for example:

```text
baseline: gpw-short-horizon-pl-v1.3-post-open-dual-source
revision: 1
effective: gpw-short-horizon-pl-v1.3-post-open-dual-source+auto1
minimum_composite_score: 71
```

The PR35 workflow then runs `policy_repo_materializer.py` and commits only the allowlisted production config change to `main`.

This means existing GPW/US publishers do not need a second hidden runtime configuration path: they continue reading their canonical repository config and naturally publish the effective `policy_version` into every decision/history record.

## Automatic rollback

After promotion, PR35 monitors resolved real paper-trade history carrying the exact promoted `policy_version`.

Regular rollback is allowed after at least 8 resolved outcomes when any of these holds:

- cumulative result `<= -2R`,
- mean result `<= -0.20R`,
- positive-return rate `<= 30%`.

Emergency rollback can occur after at least 3 resolved outcomes when cumulative result is `<= -3R`.

Rollback restores the parent policy automatically, rematerializes the parent config, marks the candidate `ROLLED_BACK`, and blocks the failed transition for 30 days.

## State and audit

Private artifact state:

- `policy_activation.json`
- `policy_registry.json`
- `promotion_ledger.jsonl`
- `policy_shadow_outcomes.jsonl`
- `policy_promotion_status.json`

`promotion_ledger.jsonl` is append-only and SHA-256 chained. Shadow outcomes are individually SHA-256 protected. The policy registry itself has a canonical SHA-256.

Production artifacts:

- `autonomous-policy-state`
- `autonomous-policy-checkpoint`

## Hard controls

Always false in v1:

- code mutation
- arbitrary parameter mutation
- trade execution
- historical backfill
- same-sample training and validation
- multi-parameter candidate
- LLM policy mutation

Always true in v1:

- bounded autonomous promotion
- automatic rollback

## Closed loop

```text
real decisions
      ↓
real outcomes + rejected-candidate shadow outcomes
      ↓
Learning / diagnostics
      ↓
Policy Candidate
      ↓
future-only Shadow Validation
      ↓
Promotion Gate
   ┌──┴───┐
 FAIL    PASS
   │       ↓
 reject  AUTO-PROMOTE
           ↓
      production config
           ↓
      live paper outcomes
           ↓
      rollback monitor
        ┌──┴──┐
      OK     FAIL
      │        ↓
      └──── AUTO-ROLLBACK
```
