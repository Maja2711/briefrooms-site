# PR #16.1 — Investment Semantics & World State Foundation

## Purpose

PR16.1 creates a shared investment-language and timing contract before the next Engine↔Belief bridge.

It does **not** create alpha and does **not** change any decision. Its job is to stop two classes of architectural error before BRACE, WES, Daily engines and Belief Core are connected more tightly:

1. treating unrelated fields called `confidence` as if they meant the same thing;
2. attaching market/regime context to a forecast retrospectively.

The layer is `research_shadow` only.

## Canonical semantic registry

The contract version is:

`investment-metric-semantics-v1`

Canonical semantic types:

- `heuristic_signal_strength` — bounded signal strength; not a probability;
- `model_probability` — model probability, calibration not implied;
- `calibrated_probability` — reserved for a probability that has passed an explicit prospective calibration contract;
- `belief_probability` — current Belief probability with explicit calibration status;
- `data_quality_confidence` — confidence in data/freshness/estimation quality;
- `belief_confidence` — evidence quality/coverage of a Belief state;
- `conviction_score` — ordinal/bounded decision conviction, not a probability.

Known source-field mappings are explicit. In particular:

- `investments_weekly_v2.confidence` → `heuristic_signal_strength`;
- `brace_portfolio.confidence_score` → `data_quality_confidence`;
- `brace_portfolio.probability_of_reaching_target` → `model_probability`;
- `belief_core.belief_state.probability` → `belief_probability`;
- `belief_core.belief_state.confidence` → `belief_confidence`.

PR16.1 does not rewrite legacy engine outputs. Future bridges must consume the semantic contract instead of interpreting field names by convention.

## World State v1

The World State contract version is:

`investment-world-state-v1`

Each immutable snapshot freezes the already-produced, timestamped context from:

- PR10 Broad-Market Beliefs;
- PR11 Sector/Factor Beliefs.

Every Belief probability and confidence is wrapped in its canonical semantic envelope.

The snapshot stores:

- `world_state_id`;
- `created_at`;
- `context_as_of` / `source_cutoff_at`;
- source timestamps and SHA-256 provenance;
- Broad-Market Belief context;
- Sector/Factor Belief context;
- source-time skew and data-quality flags;
- immutable content hash.

### Why direct raw market observables are not in v1

PR16.1 deliberately does **not** re-fetch VIX, yields, FX or equity prices while creating World State. A fresh asynchronous query would create a different information set from the one that actually existed when the upstream Belief layers ran.

Direct market observables can be added later through a separately reviewed timestamped adapter. Until then, v1 freezes only already-produced Broad-Market and Sector/Factor Belief context.

This limitation is explicit in the runtime report and capability flags.

## Prospective Entity forecast context binding

Binding contract:

`entity-forecast-world-state-binding-v1`

PR15 forecasts remain immutable. PR16.1 does not edit BeliefCore forecast records.

Instead it maintains a separate append-only binding ledger:

`forecast_id → world_state_id`

A binding is permitted only when:

- the forecast is new after PR16.1 activation;
- the World State snapshot itself already existed at or before `forecast_at`;
- the World State source cutoff is at or before `forecast_at`.

Therefore a context snapshot cannot be created after the market outcome and then attached to an old forecast.

### Activation boundary

On the first PR16.1 run:

- existing PR15 forecast IDs are marked as `pre_activation_pr15_forecast_ids`;
- they are cursor-only;
- they are never regime-tagged retroactively.

If a forecast appears later but its timestamp predates PR16.1 activation, it is recorded as terminally unbound.

If a new forecast has no qualifying pre-forecast World State, it is also recorded as terminally unbound. A later World State snapshot cannot fill the gap.

## State

Private cumulative artifact state contains:

- `INVESTMENT_WORLD_STATE_RUNTIME_STATE.json`;
- `INVESTMENT_SEMANTICS_WORLD_STATE_REPORT.json`.

The runtime state includes append-only:

- World State snapshots;
- prospective forecast-context bindings;
- terminal unbound records;
- activation cursors.

## Relationship to PR16 calibration

PR16.1 starts collecting the context needed for future regime-robustness analysis **now**.

PR16 does not need to reconstruct old market regimes with hindsight. Once Entity forecast verifications exist, PR16 can join those verifications to the prospective binding ledger and slice calibration by the World State that truly existed before each forecast.

## Relationship to PR17

PR17 should consume:

- the engine output;
- the Belief state;
- the canonical semantic registry;
- `world_state_id` / World State context;
- frozen engine/bridge versions.

That gives the first BRACE↔Entity paired prospective `WITHOUT BELIEF` vs `WITH BELIEF` experiment a consistent information set and explicit metric semantics.

## Hard boundaries

PR16.1 has no authority to:

- change BRACE/WES/Daily scores or rankings;
- change exposure or sizing;
- veto/reverse/force exits;
- trade;
- update Belief probabilities;
- tune an engine;
- create WITH/WITHOUT economics;
- promote any Belief or bridge.

All safety-control flags remain `false`.

The program-level promotion rule remains unchanged:

> No Belief promotion without prospective paired WITH/WITHOUT economic evidence.

## Research path beyond the scaffold

PR16.1 intentionally does **not** implement the proposed research-edge layers yet:

- Causal Belief Graph;
- Marginal Information Value;
- disagreement topology;
- engine-specific trust.

Those layers should be tested on prospective bridge data rather than invented as unvalidated plumbing.
