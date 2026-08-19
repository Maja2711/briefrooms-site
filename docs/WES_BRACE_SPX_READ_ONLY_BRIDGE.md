# WES-SPX / BRACE-SPX Read-Only Bridge

## Purpose

This bridge measures whether BRACE-SPX Generation 6 adds useful specialist context to WES S&P 500 decisions without changing any live/paper decision.

It is deliberately separate from the generic `investments_research_bridge.py`, which may apply bounded adjustments for explicitly promoted Research Lab candidates. The BRACE-SPX bridge in this document has **zero runtime decision influence**.

## Governance

The bridge must preserve all of these invariants:

- `active_decision_influence = false`
- `bounded_influence_enabled = false`
- no WES threshold, candidate score, direction, TP, SL or exposure is changed
- no BRACE-SPX candidate, parameter, research file or holdout state is changed
- BRACE-SPX sealed holdout must remain unaccessed
- only point-in-time BRACE-SPX states may become alpha-eligible
- BRACE-SPX warm-up produces `UNAVAILABLE`, never an inferred directional opinion
- retrospective BRACE-SPX states created after the WES decision are excluded from alpha

## Two-stage freeze

The normal WES workflow captures two snapshots for the same economic SPX decision.

### 1. `pre-wes`

Runs after the governed V5 decision/admission step and before WES postflight. It freezes:

- V5 direction
- strategy id
- raw score
- entry price/time when present
- V5 risk plan before WES adaptive TP/SL
- contemporaneous BRACE-SPX G6 shadow state

This is the future V5 counterfactual baseline. The outcome is not fabricated at capture time.

### 2. `post-wes`

Runs after WES postflight and freezes:

- actual WES direction/strategy
- actual WES entry class
- WES adaptive risk plan
- observed WES outcome when the matching leg is already closed

The BRACE-SPX state frozen in `pre-wes` is never replaced by a later state.

## BRACE-SPX specialist state

Generation 6 currently requires 70 clean post-holdout observations before it emits candidate snapshots. During that warm-up the bridge records:

`BRACE_SPX_WARMUP -> UNAVAILABLE -> no opinion`

Once G6 itself reaches `shadow_active_no_orders`, the bridge reads all eight predeclared candidate snapshots from the **research branch in read-only mode**. It does not select a champion.

The bridge summarizes `target_exposure_next_session` across all eight candidates:

- mean >= 0.60 -> `risk_on`
- mean <= 0.40 -> `defensive`
- otherwise -> `neutral`

Confidence is based on candidate directional agreement and distance from neutral exposure. This is a research descriptor only, not a trading signal.

## Agreement / conflict classes

For WES SPX directional decisions:

- WES long + BRACE-SPX risk-on -> agreement
- WES short + BRACE-SPX defensive -> agreement
- WES long + BRACE-SPX defensive -> conflict
- WES short + BRACE-SPX risk-on -> conflict
- neutral / unavailable -> neutral or unavailable

Classes are:

- `STRONG_AGREEMENT`
- `WEAK_AGREEMENT`
- `NEUTRAL`
- `WEAK_CONFLICT`
- `STRONG_CONFLICT`
- `UNAVAILABLE`

Only point-in-time actionable observations may enter future agreement/conflict alpha statistics.

## Point-in-time rule

A BRACE-SPX state is alpha-eligible only if:

1. it was generated no later than the WES decision time,
2. it was not more than 36 hours old at the WES decision,
3. G6 itself had completed warm-up and emitted valid parallel candidate snapshots,
4. all governance guards pass.

This prevents hindsight from entering the bridge.

## Counterfactual design

The ledger keeps three logically separate states:

1. **V5 frozen baseline before WES postflight**
2. **actual WES plan/outcome**
3. **BRACE-SPX relationship label**

The bridge intentionally does **not** invent the V5 counterfactual result. It freezes the V5 plan so a later evaluator can replay it against point-in-time market data using the original execution rules.

Until that evaluator resolves both legs:

`incremental_wes_vs_v5_percent = null`

This is preferable to using the actual WES exit as a fake V5 result.

## Outputs

- `data/investments/wes_spx_brace_bridge.json`
- `data/investments/wes_spx_brace_alpha_report.json`

The alpha report shows observed WES outcomes by agreement/conflict class and tracks how many V5 counterfactual baselines have been frozen/resolved.

## Promotion path

The intended sequence is:

1. read-only bridge
2. collect point-in-time agreement/conflict observations
3. resolve V5 counterfactual outcomes
4. test whether agreement/conflict predicts incremental WES alpha
5. require sufficient effective sample size and stability
6. only then consider a separately reviewed bounded influence PR

No bounded influence is authorized by this bridge.
