# PR #11 — BRACE Sector / Factor Belief Foundation

## Purpose

PR #11 creates the middle layer in the BRACE Belief hierarchy:

```text
Broad market
    ↓
Sector / factor   ← PR #11
    ↓
Company / entity  ← deferred to PR #12+
```

The layer is **research-shadow only**. It does not change BRACE decisions, scores, candidate ranking, exposure, sizing, vetoes, exits, direction, execution or policy.

The design goal is to learn whether persistent sector/style leadership can be calibrated prospectively before company-specific beliefs are introduced. It also prevents company beliefs from having to absorb sector and factor effects that should be modeled separately.

## Taxonomy v1

PR #11 starts deliberately small and liquid.

### Sectors

- `sector.technology.leadership` — XLK vs SPY
- `sector.financials.leadership` — XLF vs SPY
- `sector.health_care.leadership` — XLV vs SPY
- `sector.consumer_discretionary.leadership` — XLY vs SPY
- `sector.consumer_staples.leadership` — XLP vs SPY
- `sector.communication_services.leadership` — XLC vs SPY
- `sector.semiconductors.leadership` — SOXX vs SPY

These sectors cover the main economic contexts represented in the governed BRACE portfolio universe without creating company/entity beliefs.

### Factors

- `factor.growth.leadership` — IWF vs IWD
- `factor.quality.leadership` — QUAL vs SPY
- `factor.momentum.leadership` — MTUM vs SPY
- `factor.small_cap.leadership` — IWM vs SPY

This is a foundation, not an exhaustive factor zoo. New factors require separate evidence/outcome contracts and review.

## Evidence contract

Each belief uses one derived relative-leadership observation built from:

- approximately one-session relative return,
- approximately four-session relative return,
- a fixed 60/40 blend.

Both horizons come from the **same underlying price history** and are therefore intentionally represented as one evidence cluster. PR #11 does not pretend that 1-day and 4-day ETF momentum are independent sources.

The source is Yahoo Finance chart data and the output is explicitly marked `proxy_only`.

### Important limitation

ETF relative performance is a market leadership proxy. It is **not** a complete sector fundamental model and does not directly measure:

- earnings revisions,
- aggregate margins,
- sector valuation,
- regulatory risk,
- credit creation,
- balance-sheet quality,
- capex returns.

Those data families can be added later only with explicit provenance and anti-double-counting rules.

## Prospective outcome

Every frozen belief asks a narrow, deterministic question:

> Is the numerator/denominator relative-price ratio at the next available US trading session close at least as high as the ratio frozen at forecast time?

Examples:

```text
Technology: XLK / SPY
Financials: XLF / SPY
Growth:     IWF / IWD
Quality:    QUAL / SPY
```

The exact reference ratio is frozen inside the forecast metadata before the outcome exists.

For weekends and exchange holidays, the contract resolves on the first available US trading session on or after the predeclared target date. It never reconstructs an older missed forecast.

## Anti-hindsight boundary

The first valid production run is activation-only.

```text
first run
    establish activation_market_date
    compute current shadow beliefs
    freeze zero forecasts
```

Only a later market session may create forecasts.

Other safeguards:

- historical backfill is disabled,
- one forecast per belief per market date,
- stable deterministic forecast IDs,
- target reference is frozen before the outcome,
- resolved verifications are append-only in Belief Core state,
- missing source pairs do not fabricate evidence or forecasts.

## Missing data behavior

Sector/factor ETF feeds are additive. A missing ETF does not create a synthetic value and does not take down unrelated beliefs.

The report lists:

- required symbols,
- available symbols,
- missing symbols,
- fetch failures,
- `data_available` per belief.

A belief with an unavailable numerator or denominator receives no new PR #11 evidence and no new forecast for that session.

## Calibration and effective N

The canonical report is:

`BRACE_SECTOR_FACTOR_BELIEF_REPORT.json`

It includes normal Belief Core calibration plus a conservative serial effective-N diagnostic per belief.

The estimator uses lag-1 autocorrelation of forecast residuals and caps effective N to `[1, raw N]`. The layer-level effective N is the minimum across all taxonomy beliefs once every belief has observations.

This estimate is **descriptive only**. It does not authorize promotion.

Analysis labels remain:

```text
N < 12       collecting / warm-up
12–29        descriptive analysis
N >= 30      sector/factor calibration analysis available
```

These thresholds mean only when it becomes useful to inspect results. They are not Promotion Gate thresholds.

## WITH vs WITHOUT BELIEF standard

PR #11 is a foundation, not an Engine ↔ Belief bridge, so it intentionally does **not** manufacture hypothetical PnL.

Every later sector/factor bridge must freeze a paired prospective counterfactual:

```text
ENGINE ORIGINAL / WITHOUT BELIEF
PnL
Max DD
Sharpe
Hit rate
Turnover / costs

ENGINE + HYPOTHETICAL BELIEF / WITH BELIEF
PnL
Max DD
Sharpe
Hit rate
Turnover / costs

DELTA
Δ PnL
Δ Max DD
Δ Sharpe
Δ Hit rate
Δ Turnover
```

The pair must use the same timestamp, engine state, instrument, entry, costs and realized outcome. Only the predeclared Belief modifier may differ.

**No Belief promotion without prospective paired WITH/WITHOUT economic evidence.**

A belief probability or confidence value is not promotion evidence by itself.

## Promotion evidence standard

PR #11 encodes the future requirements but cannot evaluate or pass a Promotion Gate.

Before any later influence review, evidence must include at least:

1. sufficient **effective N**, not a raw-count shortcut,
2. stable positive WITH/WITHOUT uplift,
3. uplift across different regimes,
4. no result concentration in one or two observations,
5. no material drawdown deterioration,
6. acceptable calibration of the Belief itself,
7. no material drift,
8. healthy data quality and provenance,
9. prospective paired counterfactual evidence only.

A future gate may return `eligible for promotion review`; it must not auto-promote.

## Future bounded modifier

PR #11 authorizes none.

The report merely records the intended first-stage design boundary for a later reviewed PR:

```text
max ±2 score points
NO veto
NO forced exit
NO direction reversal
NO direct sizing command
paper/shadow first
```

Even this small modifier requires a separate bridge, WITH/WITHOUT evidence accumulation and Promotion Gate review.

## Company/entity boundary

PR #11 does not define AMZN, GOOGL, JPM, SPGI or any other company belief.

PR #12+ should build a reusable Company/Entity Belief Framework only after this middle layer exists. Company evidence can then distinguish:

```text
broad-market state
        ↓
sector/factor state
        ↓
company-specific state
```

This hierarchy is intended to reduce double-counting and make incremental information measurable at each layer.

## Safety contract

All of the following remain hard false:

```text
active decision influence
candidate ranking change
target exposure change
score change
sizing change
veto
direction reversal
forced exit
trade execution
policy output
automatic tuning
bounded influence
historical backfill
company/entity beliefs
```

PR #11 only enables sector/factor **shadow collection and calibration**.
