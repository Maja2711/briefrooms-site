# PR #15 — Entity Belief State & Forecast Foundation

## Purpose

PR #15 is the first Company/Entity layer that is allowed to update a real `BeliefCore` state and freeze prospective Entity forecasts.

The architecture is now:

```text
PR12 Entity activation + definitions
        ↓
PR13 authoritative primary-source facts
        ↓
PR14 deterministic support / oppose / neutral interpretation
        ↓
PR15 Entity Belief state + prospective forecasts + calibration memory
        ↓
future BRACE ↔ Entity bridge
        ↓
prospective paired WITH vs WITHOUT BELIEF
        ↓
future Belief-specific Promotion Gate review
```

PR15 still has **zero BRACE decision authority**.

---

## What enters Belief Core

PR15 accepts only PR14 Evidence that satisfies the frozen v1 contract:

- `source_type = derived`,
- `evidence_type = entity_fundamental_yoy`,
- direction is `+1` or `-1`,
- PR14 contract version is exact,
- dimension contract ID matches the reviewed PR14 contract,
- `pnl_tuned = false`,
- `promotion_authority = false`,
- derived lineage is present,
- Evidence timestamp is not in the future.

Anything else fails closed and is reported as a source issue.

The first enabled Entity Belief dimensions are the dimensions that PR14 can already interpret deterministically:

```text
revenue_durability
earnings_momentum
margin_trajectory
net_interest_income_durability   # Financials only
```

Context-sensitive dimensions remain in PR12/PR14 but do not receive PR15 state/forecast authority until their interpretation and outcome contracts are separately reviewed.

---

## Belief definition contract

Each enabled Entity Belief starts with:

```text
prior_probability = 0.50
half_life = 180 days
forecast horizon = 120 days
```

The half-life and horizon are frozen engineering choices for the first prospective calibration program. They are **not fitted to PnL**.

Examples:

```text
entity.amzn.revenue_durability
entity.amzn.earnings_momentum
entity.amzn.margin_trajectory

entity.jpm.revenue_durability
entity.jpm.earnings_momentum
entity.jpm.margin_trajectory
entity.jpm.net_interest_income_durability
```

The forecasts concern future fundamental reporting outcomes. They are explicitly **not** forecasts of tomorrow's stock return.

---

## First-run anti-hindsight boundary

The first PR15 run is activation-only.

If PR14 already contains Evidence when PR15 is first enabled:

```text
existing PR14 Evidence
        ↓
PR15 cursor only
        ↓
NOT ingested into Belief Core
NOT used to move probability
NOT used to create historical forecasts
```

Belief definitions and prior states are initialized, so the initial probability is `0.50`, but no historical Evidence is manufactured.

Only a **new PR14 directional Evidence record observed after PR15 activation** may update Entity Belief state and trigger a forecast.

---

## Forecast contract

PR15 deliberately limits forecast dependence.

### One live forecast per Belief

For a given `entity.X.dimension`, PR15 will not create another live forecast while a prior forecast is still inside its frozen future window.

This reduces overlapping forecast windows and avoids artificially inflating the apparent sample size.

### Frozen 120-day outcome window

When new eligible PR14 Evidence changes a Belief, PR15 freezes:

```text
forecast_at = current PR15 runtime

target_at = forecast_at + 120 days
```

The forecast predicts the first future **comparable PR14 interpretation** for the same Entity Belief inside that window.

### Binary outcome semantics

```text
first future comparable PR14 status = support
→ outcome = TRUE

first future comparable PR14 status = oppose
→ outcome = FALSE

first future comparable PR14 status = neutral
→ censored; NO binary verification

no comparable outcome by target_at
→ closed without a calibration observation
```

Neutral is never coerced into `false`. A missing report is never coerced into `false`.

A support/oppose event may become known before `target_at`, but Belief Core verification is recorded only once the forecast window is due. The frozen forecast snapshot remains unchanged.

---

## Forecast provenance

Each forecast stores:

- Entity and dimension,
- forecast timestamp,
- frozen target timestamp,
- predicted probability,
- confidence,
- representative Evidence snapshot,
- PR14 contract version,
- PR15 forecast contract version,
- Entity sector/reporting regime,
- source collection-window lineage,
- IDs of new PR14 Evidence that caused forecast capture,
- `pnl_tuned = false`,
- `historical_backfill = false`,
- `engine_influence = false`,
- `promotion_authority = false`.

---

## Dormancy / reactivation

PR12/PR13/PR14 remain the source of the Entity activation lifecycle.

PR15 preserves old Belief history, but:

```text
entity dormant
→ no new PR15 forecast
```

If valid PR14 Evidence was prospectively created before dormancy and reaches PR15 later, it may remain in the research ledger, but it cannot open a forecast while the Entity is dormant.

On reactivation, a new PR13/PR14 source window is tracked in PR15 forecast metadata. PR14 itself prevents dormant-period replay and resets comparison baselines.

---

## Calibration

PR15 uses the existing `BeliefCore` frozen forecast and verification memory.

Binary support/oppose outcomes create calibration-eligible verifications with:

- frozen predicted probability,
- Brier score,
- log loss,
- forecast Evidence snapshot,
- deterministic PR14 interpretation reference.

Neutral and missing-outcome closures are retained in PR15 runtime state but are excluded from binary calibration.

PR15 therefore starts **real prospective Entity calibration**, but it does not define a promotion-grade effective-N threshold.

---

## Effective N and promotion

Raw verification count is not promotion evidence.

PR15 explicitly leaves promotion effective N undefined because later evaluation must account for:

- temporal dependence,
- overlapping/similar reporting events,
- repeated issuer observations,
- Entity concentration,
- sector/regime clustering,
- correlated Belief states.

The future Engine↔Entity bridge must define and freeze its own dependency policy.

The program-level rule remains:

> **No Belief promotion without prospective paired WITH/WITHOUT economic evidence.**

Promotion review will also require stable uplift, confidence intervals, regime robustness, concentration controls, no material drawdown/tail-risk deterioration, acceptable Belief calibration, drift/data-quality/provenance controls and anti-hindsight compliance.

---

## Still disabled

PR15 does not enable:

```text
BRACE score change
candidate ranking change
target exposure change
sizing change
veto
direction reversal
forced exit
trade execution
policy output
automatic tuning
bounded influence
BRACE ↔ Entity bridge
WITH/WITHOUT economic bridge
promotion gate
automatic promotion
```

The next bridge must be a separate reviewed change.
