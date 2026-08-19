# BRACE P0 baseline — 2026-08-19

This document freezes the repository state used for the BRACE learning P0 repair.
It is descriptive only and does not rewrite historical shadow or learning artifacts.

## Repository baseline

- Base branch: `main`
- Base commit: `25feecec4839f910d425cc0b3099dee3a76b1e88`
- Repair branch: `agent/brace-learning-p0`
- Scope: BRACE economic feedback loop and effective-sample semantics only.
- Explicitly out of scope: WES, Belief Core, BRACE-SPX methodology, production baseline holdings, broker integration, promotion thresholds.

## Current controller / operational state

As of the latest committed BRACE operational snapshot before this repair:

- controller state: `FALLBACK_BASELINE`
- champion: `portfolio-10k-baseline`
- challenger: `brace-portfolio-engine` v3.0.0
- challenger status: `FALLBACK_BASELINE`
- real broker access: disabled
- paper-only authorization remains recorded
- operational safe mode: `true`
- safe-mode reasons: `STALE_MARKET_DATA`, `TOO_MANY_INVALID_INSTRUMENTS`
- critical data errors: 2
- timestamps complete: false
- workflow stable: true
- decisions reproducible: true
- entry history unchanged: true

The data-freshness issue is retained as a separate P0 item. This PR does not disguise or bypass it.

## Current adaptive-learning state

Latest committed `adaptive_policy.json` before this repair:

- status: `WARMUP`
- `apply_to_shadow_decisions`: false
- active overrides: none
- candidate overrides: none
- outcome events: 47
- eligible events: 0
- effective samples: 0
- research gate: passed
- minimum effective sample gate: 12

## P0 finding 1 — canonical REPLACE decisions were not learning inputs

`build_pending_decisions()` stores the executable/canonical action in `pending["decisions"]`.
A current example is the proposed `REPLACE spgi -> jpm` decision.

Before this repair, `shadow_record()` iterated only over `pending["recommendations"]`.
Therefore the canonical rotation decision, its replacement leg, and the rotation-specific gates did not enter the self-learning outcome stream.

Repair contract:

1. preserve recommendation snapshots for existing shadow/promotion reporting;
2. add a separate `economic_decisions` stream built from canonical `pending["decisions"]`;
3. assign stable `economic_decision_id` values;
4. freeze incumbent and replacement signal prices where available;
5. fail closed when either REPLACE leg is unavailable;
6. evaluate REPLACE as replacement return minus incumbent return minus the frozen incremental cost buffer.

## P0 finding 2 — one decision could become 2.85 effective samples

The legacy horizon weights are:

- 7D: 0.35
- 30D: 1.00
- 90D: 1.50

Their raw sum is 2.85. Before this repair the self-learning statistics summed them directly, so one economic decision could contribute 2.85 effective samples.

Repair contract:

- keep all three horizons as trajectory diagnostics;
- normalize their contribution by the total horizon weight;
- one economic decision can contribute at most 1.0 effective sample after all three horizons mature;
- duplicate copies of the same economic decision/horizon do not increase effective sample count.

## Historical integrity

This PR intentionally does **not** mutate:

- `data/portfolio10k/shadow_log.json`
- `data/portfolio10k/learning_state.json`
- `data/portfolio10k/adaptive_policy.json`
- historical decisions or outcomes
- the production baseline portfolio

New-schema runs become learning-compatible prospectively. Legacy runs remain readable through the backward-compatible fallback path.

## Acceptance checks

The repair is accepted only if automated tests prove:

1. a canonical REPLACE enters `economic_decisions` with both legs;
2. REPLACE reward is replacement minus incumbent minus cost;
3. missing replacement data produces no learning event;
4. 7D + 30D + 90D for one economic decision equals 1.0 effective sample;
5. duplicate economic decision/horizon events do not inflate sample count;
6. existing warmup, research-gate and policy-activation safeguards remain intact.
