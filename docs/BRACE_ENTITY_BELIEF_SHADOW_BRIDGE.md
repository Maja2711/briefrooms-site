# PR #17 — BRACE ↔ Entity Belief Prospective Shadow Bridge

## Purpose

PR17 is the first Company/Entity bridge that creates a real prospective paired
`WITHOUT BELIEF` / `WITH BELIEF` experiment for BRACE Portfolio.

It remains **research-shadow only**. BRACE does not consume the modifier and no
production score, ranking, sizing, exposure, execution or policy is changed.

## Inputs

PR17 consumes only reviewed state:

- BRACE Portfolio `analysis.json` and `pending_decisions.json`;
- current model-portfolio notional from `data/investments/portfolio_10k.json`;
- PR15 Entity BeliefCore forecasts + PR15 runtime contract;
- PR16.1 World State ledger + prospective forecast bindings.

A PR15 forecast is eligible only when:

1. it belongs to the same Entity as the BRACE position;
2. `forecast_at <= BRACE decision_at < target_at`;
3. it has a PR16.1 `entity-forecast-world-state-binding-v1` binding;
4. the bound World State existed before the forecast and its source cutoff was
   not later than the forecast;
5. the BRACE decision itself has a World State snapshot that existed before the
   decision.

No historical decision or Belief backfill is allowed. The first PR17 run is an
activation cursor only for the recommendation set visible at activation.

## Primary modifier contract

The primary modifier is frozen ex ante and is not PnL tuned:

```text
ceiling = ±2 BRACE score points

per Entity forecast signal:
    (2 * predicted_probability - 1) * forecast_confidence

aggregate signal:
    mean(one active forecast per enabled Entity dimension)

modifier:
    clamp(2 * aggregate_signal, -2, +2)
```

Sensitivity telemetry is allowed only for frozen ceilings `±1 / ±2 / ±3`.
Promotion evidence comes from the primary `±2` contract only.

The PR16.1 semantic contract remains authoritative:

- BRACE `confidence_score` is data-quality confidence, not probability;
- PR15 `predicted_probability` is a prospective-under-calibration model
  probability;
- PR15 `forecast_confidence` is Belief evidence-quality confidence.

## WITHOUT / WITH contract

PR17 compares the same Entity recommendation twice:

```text
WITHOUT BELIEF
    original BRACE score
    same risk score
    same data-quality confidence
    same current/target/proposed weights

WITH BELIEF
    original score + frozen hypothetical Entity modifier
    same risk score
    same data-quality confidence
    same current/target/proposed weights
```

The WITH score is passed through a frozen local parity copy of the current BRACE
position thresholds. It does **not** rerank candidates and does not rerun the
optimizer.

No own sizing model is introduced. A hypothetical action can only reuse weights
already supplied by BRACE:

- `HOLD/WATCH` → current weight;
- `ADD` → `max(current, proposed)`;
- `REDUCE` → `min(current, proposed)`;
- `EXIT` → zero only when EXIT was already the WITHOUT action.

Belief may neither create nor cancel EXIT. This enforces the no-forced-exit and
no-veto boundary.

## Paired economics

The primary economic outcome horizon is frozen at **7 days**. Resolution uses
the first current BRACE analysis available on/after the target date (within a
14-day maximum lag).

Both arms use exactly the same:

- instrument;
- signal/entry price;
- evaluation price/date;
- evaluation horizon;
- portfolio notional frozen at decision time;
- transaction cost rate.

Transaction cost assumption is frozen at **5 bp per unit turnover** and is not
optimized from observed PnL.

The report always contains:

```text
ENGINE ORIGINAL / WITHOUT BELIEF
PnL
Max drawdown
Sharpe
Hit rate
turnover
costs
tail risk

ENGINE + hypothetical Belief / WITH BELIEF
same metrics

DELTA
PnL
return
Sharpe
hit rate
turnover
costs
drawdown_change
drawdown_improvement
tail risk
```

Required drawdown semantics:

```text
max_drawdown_without
max_drawdown_with
drawdown_change = DD_with - DD_without
drawdown_improvement = abs(DD_without) - abs(DD_with)
```

At `N=0`, the report still materializes and metrics are `null`; missing outcomes
are never fabricated.

Economic scope in PR17 v1 is the **paired Entity-position contribution slice**.
Unchanged non-Entity portfolio exposures are omitted because they are identical
in both arms and cancel in the delta. The report must not claim this slice is a
full reconstructed BRACE portfolio PnL.

## Marginal Information Value seed

PR17 begins collecting the raw material needed for the next research question:

> Did Entity Belief information change a BRACE decision, and did the change add
> incremental economic value beyond what BRACE already knew?

PR17 reports descriptive seeds such as non-zero modifier rate, decision-change
rate and mean paired delta return. It does **not** yet implement the Marginal
Information Value model, Causal Belief Graph, engine-specific trust or
disagreement topology.

## Promotion boundary

PR17 can only emit:

```text
NOT_ELIGIBLE_FOR_PROMOTION_REVIEW
```

It does not define a global effective-N threshold and does not implement the
Promotion Gate. Future promotion review still requires sufficient effective N,
positive paired uplift with acceptable uncertainty, temporal/regime stability,
concentration controls, non-worsening drawdown/tail risk, acceptable Belief
calibration, drift/data-quality/provenance checks, anti-hindsight integrity and
stable shadow runtime.

## Files

- `scripts/brace_entity_belief_shadow_bridge.py`
- `tests/test_brace_entity_belief_shadow_bridge.py`
- `.github/workflows/brace-entity-belief-shadow-bridge.yml`
- `.github/workflows/brace-entity-belief-shadow-bridge-validation.yml`

Private cumulative production artifact:

```text
brace-entity-belief-shadow-bridge-state
```

with:

```text
BRACE_ENTITY_BELIEF_SHADOW_BRIDGE_STATE.json
BRACE_ENTITY_BELIEF_WITH_WITHOUT_REPORT.json
```
