# WES-SPX Engine–Belief Calibration + WITH/WITHOUT

## Purpose

PR #9 asks two separate questions without changing WES:

1. Does frozen Belief agreement/conflict predict the quality of the WES SPX decision?
2. Does Belief conflict identify cases where a small, predeclared reduction in risk would have improved the same WES outcome?

It also joins the same frozen relationship with the existing WES-vs-V5 incremental alpha produced by the prospective V5 replay framework.

## Inputs

PR #9 does not reconstruct WES or Belief.

It consumes:

- prospective `WES–Belief Observation` records from PR #8,
- observed WES outcomes from `wes_spx_brace_bridge.json`,
- the existing prospectively frozen V5 counterfactual outcome when available.

## Frozen PR #9 contract

For each new calibration-eligible PR #8 record, a separate contract must be frozen within 15 minutes of PR #8 capture.

No historical backfill is allowed.

### WITHOUT BELIEF

`WITHOUT BELIEF` is the observed WES result exactly as recorded by the governed WES ledger.

### WITH BELIEF

The economic counterfactual is intentionally conservative:

- agreement: risk scale stays `1.00`; Belief cannot add leverage,
- neutral: risk scale stays `1.00`,
- conflict: risk may be attenuated by at most `10% × frozen relationship strength`,
- direction never changes,
- entry never changes,
- TP/SL never changes,
- veto is never applied.

Thus PR #9 tests whether **conflict is useful as a risk-warning signal**, not whether Belief can invent a new trade.

## Score telemetry

The contract additionally records a non-economic score sensitivity:

- agreement: up to `+2` WES score points,
- conflict: down to `-2` WES score points,
- neutral: `0`.

This value is telemetry only. It is not fed into WES and it is not used to change the observed trade.

## WES vs V5

When the existing prospective V5 replay is resolved, PR #9 reports:

- observed `WES - V5` incremental alpha,
- the same incremental alpha grouped by Belief agreement/conflict,
- hypothetical `WITH BELIEF - V5` incremental alpha.

PR #9 never creates, backfills or rewrites a V5 counterfactual.

## Canonical report

`WES_SPX_ENGINE_BELIEF_CALIBRATION_REPORT.json`

The report contains:

- overall WES outcome,
- WITH vs WITHOUT cumulative return,
- mean/median/worst `delta_pnl`,
- drawdown and Sharpe,
- agreement/conflict/neutral slices,
- strategy and entry-class slices,
- conflict warning rate for negative WES outcomes,
- agreement confirmation rate for positive WES outcomes,
- WES-vs-V5 incremental alpha by relationship.

## Sample interpretation

- `N < 12`: collecting / warm-up,
- `12 <= N < 30`: descriptive analysis only,
- `N >= 30`: relationship analysis available.

These are analysis thresholds only. They do **not** authorize promotion.

## Hard safety

All production influence remains disabled:

```text
active decision influence = false
direction change           = false
entry change               = false
TP/SL change               = false
score change               = false
sizing/exposure change     = false
veto                       = false
execution                  = false
automatic tuning           = false
bounded influence          = false
historical backfill        = false
```

A later Promotion Gate remains a separate reviewed decision.
