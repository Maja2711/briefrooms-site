# WES Counterfactual Replay and Incremental Alpha

## Purpose

This layer answers one question:

> Did WES improve the result of the same S&P 500 economic decision versus the V5 plan that was frozen immediately before WES changed the risk plan?

It is an evidence and measurement layer only. It does not change WES or V5 decisions.

## What is compared

For each prospective S&P 500 decision created after the read-only bridge was deployed, the system keeps two versions:

1. **V5 baseline** — frozen after governed V5 `ensure-exposure` and before WES postflight.
2. **WES actual** — the real paper plan after WES postflight.

The measured quantity is:

`incremental_alpha = WES net result - replayed V5 baseline net result`

Positive means WES improved that decision versus the frozen V5 baseline. Negative means WES made that decision worse versus the frozen V5 baseline.

## No historical reconstruction

Historical backfill is forbidden.

If the V5 baseline was not captured prospectively inside the bridge capture window, the decision remains `missed_not_reconstructed`. The evaluator does not infer a V5 baseline from later state.

This means the old W34 observation is not turned into a fake WES-vs-V5 pair.

## Frozen replay contract

Immediately after the bridge freezes a valid V5 baseline, the counterfactual layer creates a self-contained replay contract containing:

- decision id and week id
- S&P 500 symbol
- V5 direction
- entry price and entry timestamp
- V5 stop-loss and take-profit
- frozen scheduled week close
- same-bar rule
- frozen round-trip cost assumption
- market-data and scheduled-close execution rules
- contract hash

If a valid pre-WES `risk_plan` already exists, it is used directly. If it is absent, the contract may derive the V5 levels only from the already-frozen `risk_distance` that exists in the same pre-WES week state. No future market data is used for this derivation.

## Replay rules

The replay uses 5-minute OHLC data after the frozen entry timestamp.

Before the frozen weekly deadline:

- long: low <= SL means stop loss; high >= TP means take profit
- short: high >= SL means stop loss; low <= TP means take profit
- if SL and TP are both inside the same bar, **stop loss wins conservatively**

If neither level is reached before the deadline, the baseline closes on the first 5-minute bar at or after the frozen scheduled close.

There is no current-price fallback. Missing bars keep the counterfactual unresolved rather than inventing a result.

## Costs

The round-trip transaction-cost assumption is frozen in the replay contract at baseline-capture time. WES and V5 are compared on net results.

## Outputs

The layer updates:

- `data/investments/wes_spx_brace_bridge.json`
- `data/investments/wes_spx_brace_alpha_report.json`

and adds:

- `data/investments/wes_incremental_alpha_report.json`

The incremental report contains:

- overall resolved WES-vs-V5 pairs
- mean and median incremental alpha
- rate at which WES beats V5
- best and worst incremental result
- breakdown by WES strategy
- breakdown by WES entry class
- agreement/conflict breakdown only where BRACE-SPX was point-in-time alpha eligible

## Sample governance

Each economic decision contributes at most one WES-vs-V5 pair.

Status is:

- `collecting_prospective_pairs` at N=0
- `warmup_insufficient_evidence` for N=1..11
- `analysis_available_not_policy_authorized` from N>=12

N>=12 permits descriptive analysis only. It does not authorize a WES policy change.

## BRACE-SPX agreement/conflict

The same resolved incremental alpha is grouped by the already-frozen BRACE-SPX relationship:

- strong agreement
- weak agreement
- neutral
- weak conflict
- strong conflict

Only point-in-time alpha-eligible BRACE-SPX observations enter those groups. Warm-up, stale, retrospective or unavailable BRACE-SPX states remain excluded.

This lets us answer a second question later:

> Does BRACE-SPX identify the situations in which WES adds or destroys value versus V5?

## Safety invariants

All outputs must preserve:

- `active_decision_influence = false`
- `bounded_influence_enabled = false`
- no change to WES score, direction, TP, SL or exposure
- no change to V5 policy
- no change to BRACE-SPX research
- no broker order capability
- no historical V5 reconstruction

Bounded influence is a separate future decision and requires a separate reviewed PR after enough prospective evidence exists.
