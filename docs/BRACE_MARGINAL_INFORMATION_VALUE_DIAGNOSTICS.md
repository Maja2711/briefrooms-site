# PR #18.1 — Marginal Information Value Diagnostics

## Purpose

PR18.1 is the first research layer that asks a stricter question than whether an Entity Belief was correct:

> Did prospectively available Entity Belief information improve a BRACE decision beyond what BRACE already knew at the time?

It consumes only:

1. prospectively frozen PR18 BRACE/Belief/World-State information captures, and
2. the corresponding prospective PR17 paired WITHOUT/WITH economic outcomes.

It does **not** create a composite alpha score and has zero decision authority.

## Research object

The diagnostic object is a vector:

- incremental economic value;
- information redundancy proxy;
- information orthogonality proxy;
- temporal/dependence diagnostics;
- concentration diagnostics;
- disagreement × regime slices.

`composite_miv_score` is intentionally `null`.

## Economic unit

The economic unit is an **instrument contribution inside a prospectively captured PR17 pair**.

For a matured item PR18.1 records:

- WITHOUT contribution return;
- WITH contribution return;
- delta contribution return;
- turnover delta;
- transaction-cost delta;
- delta PnL when portfolio notional is available.

A multi-instrument PR17 pair is never duplicated into multiple copies of the pair-level delta. Item-level outcome rows must exist. Pair-level fallback is allowed only for a one-item pair, where the pair and item are economically identical.

The report also preserves the PR17 aggregate WITH/WITHOUT economics as source telemetry.

## Incremental economic diagnostics

PR18.1 reports, descriptively:

- mean and median delta contribution return;
- cumulative event delta return;
- event-sequence delta drawdown;
- positive/negative/zero uplift rates;
- mean turnover and cost deltas;
- summed delta PnL where available;
- worst delta event and empirical 10% CVaR;
- deterministic non-parametric bootstrap 95% interval for mean delta when at least two matured rows exist.

These values are not a promotion decision.

## Redundancy and orthogonality

PR18.1 does not call a Belief novel merely because it differs from the BRACE final score.

The first dependence screen uses a frozen ex-ante set of BRACE fields:

- quality score;
- valuation score;
- momentum score;
- risk score;
- diversification score;
- thesis score;
- final score;
- risk-adjusted score;
- expected base return;
- expected drawdown;
- model probability of reaching target.

The screen is not selected from PnL.

### Repeated Belief states

A forecast state may survive across many BRACE decisions. Those repeated decisions must not pretend to be independent Belief information.

For the redundancy screen, observations are collapsed to **equal-weight unique prospectively frozen Belief states**. Engine features are averaged within each repeated Belief state.

The first proxy is:

`redundancy_proxy = max absolute Spearman correlation(Belief signal, frozen BRACE feature)`

and:

`orthogonality_proxy = 1 - redundancy_proxy`

This is only a descriptive dependence screen. It is **not** proof of conditional redundancy, causal independence or market edge.

The proxy becomes technically estimable only with at least 4 unique Belief states and variation in both Belief signal and an engine feature. `4` is an engineering availability minimum, **not a promotion threshold**.

## Dependence and effective N

PR18.1 reports:

- raw matured item N;
- unique PR17 pair N;
- unique Belief-state N;
- lag-1 serial dependence of delta returns when technically estimable;
- a descriptive effective-N floor capped by repeated pair and Belief-state dependence.

No global effective-N promotion threshold is defined. `promotion_grade_effective_n` remains `null`.

## Concentration

For matured rows PR18.1 reports count, maximum share, HHI and effective category count for:

- instrument;
- Belief signature;
- Belief state;
- disagreement pattern;
- World State.

No concentration promotion cutoff is defined here.

## Disagreement × regime

The PR18 frozen topology is sliced by:

- full disagreement pattern;
- Engine↔Entity relation;
- top-down state;
- market/sector/factor regime signature;
- Belief signature;
- instrument;
- Engine↔Entity relation × regime signature.

These slices are descriptive research telemetry. No slice is declared alpha from a positive mean alone.

## Anti-hindsight

PR18.1 consumes only PR18 captures that assert:

- `prospective_to_economic_outcome=true`;
- `historical_information_backfill=false`;
- `source_reconstruction=false`;
- `promotion_authority=false`.

PR17 pair governance must also remain shadow-only.

A matured outcome is accepted only when it is `calibration_eligible=true`, closes at or after its frozen target time and is not future-dated relative to the PR18.1 run.

The first PR18.1 run may diagnose already-existing PR18 captures because those captures were themselves created prospectively before the economic outcome. This is **not historical forecast or information-set backfill**.

## State and idempotence

State files:

- `BRACE_MIV_DIAGNOSTICS_STATE.json`
- `BRACE_MIV_DIAGNOSTICS_REPORT.json`

A source fingerprint is based on the immutable PR18 captures/terminal coverage plus PR17 pair/outcome state. A new append-only diagnostic snapshot is created only when that source fingerprint changes. Rerunning the same source is idempotent.

## Hard governance boundary

PR18.1 cannot:

- write BRACE scores;
- write Belief probabilities;
- alter ranking, optimizer, exposure or sizing;
- veto or force exits;
- execute trades;
- tune policy;
- output engine-specific trust;
- create the Causal Belief Graph;
- output a composite MIV score;
- promote anything.

Promotion remains:

`NOT_ELIGIBLE_FOR_PROMOTION_REVIEW`

## Interpretation

PR18.1 deliberately separates three statements that must not be conflated:

1. **Belief was correct.**
2. **Belief changed/improved the economic decision.**
3. **Belief added information that was not already embedded in BRACE.**

Only the second and third together are candidates for future marginal information value. Even then, repeated-state dependence, concentration, regime robustness, uncertainty and future Promotion Gate requirements remain mandatory.
