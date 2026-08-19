# BRACE-SPX Engine–Belief Calibration + WITH/WITHOUT

## Purpose

PR #7 measures whether the frozen Belief State contains useful information about the quality of BRACE-SPX Generation 6 shadow decisions and whether a small hypothetical Belief overlay would have improved the same next-session outcome.

This is a **research-only counterfactual evaluator**. It has zero authority over BRACE-SPX, Belief Core, exposure, candidate ranking, scores, sizing, vetoes, orders or production policy.

## Inputs

The evaluator consumes only prospective records created by the PR #6 read-only bridge:

```text
frozen BRACE-SPX G6 state
        +
frozen Belief State
        ↓
Engine–Belief Observation
```

PR #7 then freezes a second contract **before the outcome exists**:

```text
Engine–Belief Observation
        + exact raw G6 source hash
        ↓
Counterfactual Contract
        ├─ WITHOUT BELIEF = frozen mean target exposure of 8 G6 candidates
        └─ WITH BELIEF    = same exposure + fixed hypothetical Belief sensitivity tilt
```

If the exact raw G6 source is no longer available or the contract is not captured within the prospective window, the pair is recorded as missed and is never reconstructed later.

## WITH BELIEF policy

The overlay is deliberately simple, symmetric and predeclared:

- risk-on Belief: positive tilt,
- defensive Belief: negative tilt,
- neutral Belief: zero tilt,
- maximum absolute tilt: `0.10` exposure,
- actual tilt: `0.10 × frozen Belief confidence`,
- final hypothetical exposure is clipped to the G6 mandate `[-1, +1]`.

This is **not** a production modifier proposal. It is a fixed sensitivity test used to answer whether the Belief layer contains incremental economic information.

## Outcome horizon

The primary outcome is the next trading session SPY close-to-close return, because G6 explicitly emits `target_exposure_next_session` and applies target exposure with a one-session lag.

The accounting contract mirrors G6:

- SPY asset return,
- unused capital earns the risk-free return derived from `^IRX`,
- turnover cost = `0.0005` per unit of exposure change,
- short borrow = `1%` annualized,
- cash weight = `1 - abs(exposure)`,
- both WITH and WITHOUT variants start from the same frozen previous applied exposure.

## Anti-hindsight rules

1. First production run only establishes PR #7 activation. It does not backfill older PR #6 records.
2. A counterfactual contract must be frozen within 3 hours of the G6 state timestamp.
3. The raw G6 payload hash and timestamp must exactly match the source hash stored by PR #6.
4. Exactly eight candidate snapshots are required.
5. At most one independent contract is allowed per G6 market date.
6. Settlements are append-only. Later market-data revisions cannot rewrite an already resolved pair.
7. Missing or missed contracts stay missing; no historical reconstruction is allowed.

## Report

The canonical artifact is:

`BRACE_SPX_ENGINE_BELIEF_CALIBRATION_REPORT.json`

It reports:

- prospective sample size and effective N,
- agreement / conflict / neutral slices,
- forward SPX return,
- original G6 consensus return,
- hypothetical WITH BELIEF return,
- `delta_pnl`,
- cumulative return WITH vs WITHOUT,
- maximum drawdown WITH vs WITHOUT,
- annualized Sharpe WITH vs WITHOUT,
- conditional directional hit rates,
- whether conflict warned before negative original G6 outcomes,
- whether agreement accompanied positive original G6 outcomes,
- temporal incremental-information diagnostics versus existing G6 family features.

## Incremental information over G6

Once at least 40 usable prospective pairs exist, the report runs an expanding-window point-in-time comparison:

**Baseline model**

- G6 consensus exposure,
- price/trend family score,
- rates family score,
- liquidity family score,
- options/VIX family score.

**Augmented model**

The same G6 features plus:

- frozen Belief probability,
- frozen Belief confidence,
- signed agreement/conflict strength.

The report compares out-of-sample MSE and directional accuracy. This measurement does not authorize promotion.

## Promotion boundary

PR #7 never promotes Belief and never enables a bounded modifier. Even a positive WITH/WITHOUT result only becomes evidence for a later, separately reviewed Promotion Gate.

Hard controls remain:

```text
active decision influence = false
exposure change           = false
score change              = false
veto                      = false
sizing change             = false
candidate ranking change  = false
trade execution           = false
automatic tuning          = false
bounded influence         = false
historical backfill       = false
```
