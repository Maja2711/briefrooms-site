# PR29 — Counterfactual Decision & Gate Diagnostics

## Purpose

PR29 adds one shared, prospective diagnostics contract above the PR27/PR28 Learning Ledger.

It answers questions the trade-only outcome history cannot answer:

- Was `FLAT` a good decision?
- Which hard gate prevented a profitable alternative (`false negative`)?
- Which gate correctly kept the system out of a losing alternative (`true negative`)?
- Did the selected direction outperform an alternative direction or strategy that **actually existed at T0**?
- Which engines expose enough point-in-time state for a real counterfactual and which still expose only gate frequency?

PR29 is descriptive research only. It does not tune anything.

## Core rule

A counterfactual is eligible only if the alternative existed in the frozen decision snapshot **before the outcome**.

PR29 never creates a missing LONG/SHORT, price, stop, target or risk plan after observing the market result.

If the source engine did not expose enough T0 state, the candidate is stored as:

`insufficient_counterfactual_state`

That candidate can contribute to gate-frequency diagnostics, but it cannot receive a fabricated return or R-multiple.

## Shared event model

PR29 does not create a second learning database. It writes only `learning_observation` events into the existing PR27 Learning Ledger:

1. `counterfactual_decision_snapshot`
2. `counterfactual_candidate_outcome`

The PR27 SHA-256 append-only chain remains authoritative.

A dedicated `counterfactual_activation.json` creates a new prospective boundary for PR29. Existing PR27/PR28 history is not retroactively turned into counterfactual history.

## Decision snapshot

Every frozen decision has:

- deterministic `snapshot_id`,
- engine and instrument identity,
- decision timestamp and stage,
- actual action,
- optional upstream PR28 subject identity,
- only candidates that were visible at T0,
- candidate score/confidence when available,
- frozen market/risk fields when available,
- explicit gate pass/fail state,
- settlement mode,
- canonical SHA-256 of the snapshot.

### Settlement modes

- `risk_plan` — entry/stop/target existed at T0.
- `directional_market_return` — only a frozen directional mark/window is legitimate.
- `directional_same_window` — alternative direction evaluated on the same frozen WES entry/observed exit window; not a full strategy replay.
- `portfolio_relative` — BRACE portfolio action/recommendation needs portfolio-relative settlement.
- `research_shadow` — research gate state, not a trading P&L claim.
- `flat_zero` — explicit observed abstention baseline.
- `insufficient_counterfactual_state` — no economic settlement is allowed.

## Engine registry

### Daily GPW

Actions currently exposed by the production engine: `LONG / FLAT`.

Selected trades contain a frozen price/risk plan and can be linked to PR28 real outcomes. Rejected names currently expose gate identity (`screened_out`, `analysis_rejections`, `review_rejections`) but do not always expose the rejected candidate's frozen price/risk plan. Those rows are therefore recorded as `insufficient_counterfactual_state` instead of inventing R after the fact.

This is intentional. A later producer-enrichment PR can expose point-in-time rejected-candidate marks without changing the PR29 contract.

### Daily US Stocks

Same shared Daily Stock contract as GPW. Current engine is effectively LONG/FLAT. PR29 does not invent SHORT candidates.

US single-position lifecycle identity is preserved when the source exposes the canonical `position_id`, preventing refreshes from becoming fake new entries.

### Daily EUR/USD Spot

Actions: `LONG / SHORT / FLAT / HOLD_OPEN`.

PR29 freezes:

- actual open-position risk plan when a new position exists,
- refresh candidate and its explicit `gate_reasons`,
- only the direction actually emitted by the candidate.

An opposite direction is never synthesized for symmetry.

### Weekly Engine Star (WES)

Instruments: `EUR/USD`, `S&P 500 futures`, `BTC/USD`.

WES already exposes rich `continuous_entry_decision.candidates`. PR29 freezes every strategy/direction that existed T0 and marks the selected strategy.

After the weekly item closes, alternatives can be compared on the same frozen entry-to-observed-exit window. This is labelled `directional_same_window`; it is **not** represented as a full replay of a strategy whose own TP/SL was never frozen.

The existing WES V5 replay/counterfactual remains authoritative for its narrower WES-vs-V5 experiment. PR29 normalizes broader decision/gate evidence; it does not replace the existing replay engine.

### BRACE Portfolio 10K

PR29 freezes:

- HOLD/WATCH/REDUCE recommendations,
- ADD/REPLACE proposals,
- explicit `checks` as named gates,
- signal prices and weights when exposed.

A `PROPOSED` action remains a proposal. PR29 does not relabel it as executed. Settlement mode is `portfolio_relative` until a legitimate paper-execution/outcome source is connected.

### BRACE-SPX Generation 6

Generation 6 remains `research_shadow`.

PR29 freezes research gates such as:

- strict development gate,
- shadow warm-up completion,
- sealed-holdout integrity.

It does not turn BRACE-SPX into a trading engine and does not assign a fictitious trade return while Generation 6 has no authorized economic decision.

## Upstream layers that are not PR29 economic decision engines

Belief Core and GSE remain upstream forecast/evidence layers. Their forecasts and verifications belong to PR28/Belief calibration. They are listed in PR29 coverage explicitly so “not adapted” cannot be mistaken for “forgotten”.

## Outcome binding

PR29 outcome binding follows the same anti-hindsight pattern as PR28:

```
cycle N
  freeze decision/candidates/gates

cycle N+1 or later
  outcome becomes observable
  verify snapshot existed before this cycle
  append candidate outcome
```

A snapshot first seen in the same collector cycle as its outcome is not eligible for binding.

Selected GPW/US/EURUSD outcomes can reuse the actual PR28 outcome event. WES can settle its prospectively frozen strategy directions after the weekly item closes.

Future engine-specific monitors may call `append_candidate_outcome()` directly, but only for a candidate contained in a valid frozen snapshot. `insufficient_counterfactual_state` is rejected by the API.

## Diagnostics

PR29 builds private `counterfactual_diagnostics.json` from ledger events.

### Gate diagnostics

For each engine and gate:

- observations,
- blocked count,
- blocked + economically evaluable count,
- `false_negative`: gate blocked an alternative with positive later outcome,
- `true_negative`: gate blocked a non-positive later outcome,
- false-negative rate,
- average return of evaluable blocked alternatives.

These are descriptive labels, not causal proof.

### FLAT value

For an actual `FLAT`/`HOLD_OPEN` snapshot, FLAT can be evaluated only when at least one **frozen non-flat alternative** has a legitimate later outcome.

- best alternative <= 0 → `CORRECT_ABSTENTION`
- best alternative > 0 → `MISSED_OPPORTUNITY`
- `flat_value_percent = 0 - best_frozen_alternative_return`

No alternative visible at T0 means no FLAT verdict.

### Action diagnostics

Selected actions are summarized separately by engine/action with sample count, positive rate and mean selected return when outcomes exist.

## Zero-authority contract

All remain false:

- automatic tuning,
- belief writeback,
- evidence-weight writeback,
- causal-edge writeback,
- engine-policy writeback,
- ranking writeback,
- sizing writeback,
- trade execution,
- automatic promotion,
- source-engine writeback,
- gate-threshold writeback,
- FLAT-policy writeback,
- synthesized directions,
- risk-plan reconstruction after outcome,
- historical backfill,
- same-cycle snapshot/outcome binding.

PR29 therefore creates **evidence for future calibration**, not calibration authority.

## Promotion path after PR29

Only after a sufficient prospective sample should a later layer be allowed to create a `policy_candidate` such as “source gate appears over-restrictive”. Even then the candidate must go through separate shadow validation and promotion governance before any production threshold changes.
