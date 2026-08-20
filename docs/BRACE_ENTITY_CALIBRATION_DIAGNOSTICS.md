# PR #16 — Entity Calibration & Diagnostics Foundation

## Purpose

PR16 is a read-only diagnostics layer over the prospective Entity forecast and verification memory created by PR15.

It answers:

> Are Entity Belief probabilities calibrated, sufficiently independent and broadly distributed enough that later economic testing can be interpreted responsibly?

It does **not** answer whether an Entity Belief should influence BRACE.

The program order remains:

```text
PR13 primary source
        ↓
PR14 deterministic interpretation
        ↓
PR15 Entity Belief + prospective forecast
        ↓
PR16 calibration / dependency diagnostics
        ↓
future BRACE ↔ Entity shadow bridge
        ↓
prospective paired WITH vs WITHOUT BELIEF economics
        ↓
future Promotion Review
```

## Hard boundary

PR16 cannot update Belief probabilities and cannot change BRACE score, ranking, exposure, sizing, direction, veto, exit or execution. It has no WITH/WITHOUT bridge and no promotion gate.

Every promotion-facing field remains non-authorizing. PR16 always reports `NOT_ELIGIBLE_FOR_PROMOTION_REVIEW` because paired economic WITH/WITHOUT evidence does not yet exist.

## Accepted sample

PR16 reads only PR15 `Verification` records that are:

- `calibration_eligible = true`,
- non-legacy,
- linked to an existing frozen PR15 forecast,
- resolved by the exact outcome source `PR14 deterministic entity interpretation`,
- temporally consistent with the frozen forecast window,
- not future-dated relative to the diagnostic runtime.

Invalid provenance fails closed and is reported as a data-quality issue rather than silently entering calibration statistics.

## Calibration diagnostics

PR16 reports:

- raw verified N,
- mean forecast probability,
- realized outcome rate,
- Wilson 95% interval for the outcome rate,
- calibration gap: `outcome rate - mean probability`,
- Brier score,
- log loss,
- hit rate at the 0.50 threshold,
- in-sample climatology Brier reference and descriptive Brier skill,
- fixed 10-bin calibration table,
- fixed-bin expected calibration error (ECE).

Probability bins are frozen deciles and are not learned from outcomes or PnL.

Diagnostics are also sliced by:

- Entity Belief dimension,
- entity,
- sector,
- reporting regime.

These slices are descriptive. Small slices are not treated as independent proof.

## Effective N

Raw N is not accepted as promotion evidence.

PR16 measures several dependence channels independently and then reports a conservative floor across the estimable components.

### Serial dependence

The primary serial diagnostic uses lag-1 correlation of forecast residuals:

```text
residual = binary outcome - frozen predicted probability
```

When N is sufficient to estimate it, the descriptive serial ESS is:

```text
N_eff = N × (1 - rho) / (1 + rho)
```

with rho clipped to `[-0.80, +0.80]` and N_eff bounded to `[1, N]`.

N < 4 is explicitly `not_estimable`; PR16 does not pretend raw N is effective N.

### Cluster dependence

PR16 separately estimates residual intraclass correlation and design-effect ESS for:

- repeated observations from the same entity,
- sector clustering,
- reporting-season clustering.

A cluster diagnostic requires multiple independent groups and repeated observations. When that structure does not exist yet, its effective N is `null`.

### Overlapping forecast windows

PR15 forecasts can remain open for up to 120 days. PR16 therefore reports:

- fraction of overlapping forecast-window pairs,
- maximum simultaneous open windows,
- maximum number of non-overlapping windows,
- a conservative effective-N cap based on that non-overlapping set.

### Promotion-grade effective N

PR16 distinguishes:

```text
descriptive_effective_n_floor
```

from:

```text
promotion_grade_effective_n
```

The latter is available only when the required dependency diagnostics are actually estimable. Missing dependence information defaults to insufficient evidence.

PR16 deliberately defines **no global effective-N threshold**. It therefore reports:

```text
effective_n_threshold_defined_here = false
effective_n_sufficient = null
```

A later bridge must freeze its own reviewed effective-N policy and rationale.

## Concentration diagnostics

For entity, sector, dimension and reporting season PR16 reports:

- category counts,
- top concentrations,
- maximum share,
- Herfindahl-Hirschman Index (HHI),
- effective category count `1 / HHI`.

PR16 defines no promotion cutoff for those values. They are diagnostic evidence used by a later reviewed gate.

## Drift diagnostics

Once enough verified observations exist for a minimally meaningful chronological split, PR16 compares the earlier and later portions of the prospective sample using:

- Brier score,
- log loss,
- calibration gap.

This is descriptive only. PR16 does not define a threshold for `drift_ok` and does not emit a promotion verdict from the split.

## Regime robustness: deliberately unavailable in v1

Current PR15 forecasts freeze the Entity fundamental regime label and sector metadata, but they do **not** yet freeze the contemporaneous Broad-Market Belief state and Sector/Factor Belief state with each forecast.

Therefore PR16 v1 explicitly reports:

```text
broad_market_context_frozen_at_forecast = false
sector_factor_context_frozen_at_forecast = false
multi_regime_robustness_assessable = false
promotion_regime_robust = null
```

It would be hindsight-prone to attach a later market regime retrospectively. A future reviewed extension must freeze those contexts prospectively at forecast time before regime robustness can become promotion evidence.

## First run and anti-hindsight

PR16 may bootstrap its diagnostics from already-existing PR15 records because those PR15 forecasts and verifications were themselves prospectively frozen and resolved.

That is not historical Belief or forecast backfill. PR16 does not reconstruct a forecast that did not exist at the original timestamp.

The state stores a source fingerprint and appends a new diagnostic snapshot only when the PR15 forecast/verification source changes. Rerunning unchanged input is idempotent.

## Promotion governance

PR16 preserves the program rule:

> **No Belief promotion without prospective paired WITH/WITHOUT economic evidence.**

A future Entity bridge must additionally demonstrate a reviewed combination of:

- sufficient effective N,
- positive paired uplift and acceptable uncertainty,
- stability through time,
- robustness across prospectively frozen regimes,
- acceptable entity/sector/event concentration,
- no material drawdown deterioration,
- no tail-risk deterioration,
- acceptable Belief calibration,
- acceptable drift,
- data quality and provenance,
- anti-hindsight compliance,
- stable shadow runtime.

PR16 itself has no authority to promote anything.
