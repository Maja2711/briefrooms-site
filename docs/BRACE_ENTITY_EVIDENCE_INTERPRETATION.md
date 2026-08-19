# PR #14 — Entity Evidence Interpretation Foundation

## Purpose

PR #14 is the governed boundary between PR13 authoritative issuer facts and future Company/Entity Belief forecasts.

The stack is now:

```text
Broad-market Beliefs
        ↓
Sector / Factor Beliefs
        ↓
PR12 Company / Entity Framework
        ↓
PR13 Primary-Source Observations
        ↓
PR14 Deterministic Evidence Interpretation   ← this PR
        ↓
future Entity Belief state + forecasts
        ↓
future calibration
        ↓
future BRACE ↔ Entity bridge
        ↓
prospective paired WITH vs WITHOUT BELIEF
        ↓
Promotion Review
```

PR14 answers a narrow question:

> Given two genuinely prospective, like-for-like issuer facts, does the change support, oppose, or remain neutral for a predeclared Entity Belief dimension?

It does **not** create an Entity forecast and it does **not** change BRACE.

---

## Why a separate interpretation layer is necessary

A raw number is not a Belief.

Examples:

```text
CapEx increased
≠ automatically good
≠ automatically bad

Deposits increased
≠ automatically strong funding

Credit-loss provisions increased
≠ automatically deteriorating credit quality
```

The economic meaning depends on context, denominators, business model and time horizon.

PR14 therefore refuses the shortcut:

```text
more = positive
less = negative
```

Only explicitly reviewed comparison contracts may assign polarity.

---

## PR14 v1 enabled contracts

The first contract set is intentionally narrow.

### 1. Revenue durability

Belief:

```text
entity.<entity>.revenue_durability
```

Input:

```text
PR13 entity_primary_fact.revenue
```

Comparison:

```text
same fiscal period
same canonical XBRL tag/taxonomy
same unit
similar period duration
approximately one year apart
```

Interpretation:

```text
YoY revenue change > +2%  → support
YoY revenue change < -2%  → oppose
inside ±2%                → neutral
```

The ±2% band is a frozen engineering materiality/noise band. It was not optimized against PnL.

### 2. Earnings momentum

Belief:

```text
entity.<entity>.earnings_momentum
```

Preferred input:

```text
diluted EPS
```

Fallback:

```text
net income
```

The same-fiscal-period YoY comparison is required. Diluted EPS is preferred when both current and baseline EPS are available.

Materiality band:

```text
±5%
```

A non-positive baseline or sign-crossing case is not forced through a percentage-growth formula. It becomes `context_required`.

### 3. Margin trajectory

Belief:

```text
entity.<entity>.margin_trajectory
```

PR14 does **not** interpret operating-income growth directly.

It computes:

```text
operating margin = operating income / revenue
```

for the same accession and exact economic period, then compares the same fiscal period one year later.

Materiality band:

```text
±0.005 = ±50 bp
```

This means a company can report higher operating income but still receive an `oppose` interpretation when revenue grew faster and operating margin deteriorated.

### 4. Net-interest-income durability

Financials only:

```text
entity.<entity>.net_interest_income_durability
```

Input:

```text
PR13 entity_primary_fact.net_interest_income
```

Comparison:

```text
same-fiscal-period YoY
```

Materiality band:

```text
±2%
```

This contract is not enabled for non-Financials entities.

---

## Explicitly deferred dimensions

PR14 v1 deliberately does not assign polarity to:

```text
earnings_quality
valuation
balance_sheet_strength
competitive_position
capital_allocation
capex_returns
regulatory_risk
credit_quality
deposit_funding
capital_strength
pipeline_durability
product_concentration
cycle_position
capacity_utilization
```

Each remains `context_required` until a separately reviewed source + interpretation contract exists.

Examples:

- `capex_returns` needs later output/cash-flow/return linkage, not just CapEx amount,
- `credit_quality` needs loan-book denominator, charge-offs and growth context,
- `deposit_funding` needs deposit mix/cost/stability, not only deposit balance,
- `valuation` needs point-in-time market valuation inputs,
- `regulatory_risk` needs event/legal interpretation.

This is a deliberate false-positive control.

---

## Anti-hindsight

### First PR14 run

The first PR14 run is activation-only.

Existing PR13 observations are already prospectively collected primary facts, but PR14 did not yet have a frozen interpretation contract when they arrived.

Therefore:

```text
existing PR13 facts
        ↓
seed cursor + comparison baselines
        ↓
NO historical PR14 interpretation
NO historical PR14 Evidence
```

This lets future facts be compared against a known baseline without pretending PR14 produced a signal in the past.

### Future observations

Only PR13 observations not yet in the PR14 cursor are processed.

The derived interpretation observation is timestamped at **PR14 interpretation runtime**, not backdated to the filing timestamp.

The original SEC acceptance timestamp remains in provenance.

### Dormancy / reactivation

PR13 closes the source collection window when a company leaves the active Portfolio 10K + BRACE candidate set.

When PR13 opens a new collection window after reactivation, PR14 resets its comparison baselines.

```text
pre-dormancy baseline
        X
        X  never bridged across missing dormant period
        X
post-reactivation first eligible fact
        ↓
new baseline only
```

This prevents a missing-data interval from masquerading as continuous observation history.

---

## Same-fiscal-period comparison rule

PR14 v1 requires like-for-like YoY comparison.

For a direct metric the baseline key includes:

```text
contract
canonical metric
fiscal period
unit
XBRL taxonomy
XBRL tag
duration bucket
```

A comparison additionally requires:

```text
period-end gap approximately 300–430 days
period duration difference <= 14 days
same fiscal period
same unit
```

This intentionally sacrifices sample speed for cleaner causal semantics.

Sequential Q1→Q2 or YTD→quarter comparisons do not produce v1 polarity.

---

## Amendments / restatements

A later filing for the same economic period is not interpreted as growth or deterioration.

It receives:

```text
status = amendment_baseline_refresh
```

and refreshes the baseline without producing Evidence.

---

## Support / oppose / neutral

PR14 interpretation status is one of:

```text
support
oppose
neutral
baseline_only
context_required
amendment_baseline_refresh
source_metric_unavailable
```

Only `support` and `oppose` materialize a Belief-compatible `Evidence` object.

`neutral` is deliberately not encoded as fake ±1 Evidence because the shared Evidence contract has directional semantics.

---

## Evidence strength

Magnitude is mapped to a conservative stepped strength schedule based on multiples of the frozen materiality band:

```text
<= 1× band    → neutral / no Evidence
1–2× band     → 0.20
2–4× band     → 0.35
4–8× band     → 0.50
>= 8× band    → 0.65
```

The ceiling remains intentionally below 1.0.

These values are not calibrated or optimized against portfolio PnL in PR14.

---

## Provenance and independence

A PR14 derived observation records:

```text
contract_id
contract_version
current primary observation IDs
baseline primary observation IDs
materiality band
comparison basis
pnl_tuned = false
```

Evidence inherits the **current issuer filing independence cluster**.

Therefore multiple interpreted facts from the same accession cannot pretend to be independent issuer events.

---

## No Belief Core update yet

PR14 materializes objects compatible with the shared `Evidence` schema, but does not call Belief Core to update entity probabilities.

```text
PR14 Evidence
    ↓
stored / shadow research only
    X
    X no Belief state update
    X no forecast
```

A later PR must separately review:

- Entity Belief priors,
- evidence aggregation across dimensions and filing clusters,
- half-lives,
- forecast horizons,
- outcome contracts,
- calibration.

---

## Hard safety boundary

PR14 keeps all of the following false:

```text
active_decision_influence
score_change
candidate_ranking_change
target_exposure_change
sizing_change
veto
direction_reversal
forced_exit
trade_execution
policy_output
automatic_tuning
bounded_influence
historical_interpretation_backfill
llm_interpretation
belief_core_state_update
entity_forecast_capture
entity_promotion
```

PR14 can create research Evidence; it cannot act on it.

---

## Promotion governance remains unchanged

No Entity Belief may be promoted merely because PR14 creates convincing-looking evidence.

A future BRACE ↔ Entity bridge still requires prospective paired:

```text
WITHOUT BELIEF
vs
WITH BELIEF
```

with the same timestamp, market state, candidate, instrument, entry/cost model and realized outcome; only the frozen Belief modifier may differ.

Promotion review still requires effective N, positive and stable uplift, regime robustness, concentration control, no material drawdown/tail-risk deterioration, Belief calibration, drift/data quality/provenance checks and anti-hindsight compliance.

PR14 never auto-promotes.
