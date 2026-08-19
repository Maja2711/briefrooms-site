# PR #10 — BRACE Broad-Market Belief Foundation

## Decision

BRACE Belief expansion is hierarchical. Company/entity beliefs are **not** the first step.

The order is:

```text
1. broad market belief
   - rates
   - liquidity
   - macro regime
   - risk regime

2. sector / factor belief

3. company / entity belief
   - AMZN
   - GOOGL
   - SPGI
   - JPM
   - MSFT
   - COST
   - TSM
   - V
   - NOVO-B
   - LLY
   - SAP
   - ...
```

Company/entity dimensions are intentionally deferred until the broad-market layer exists as a prospective, calibrated, governed object.

Examples of later company/entity dimensions:

- earnings momentum,
- margin direction and durability,
- revenue durability,
- valuation,
- regulatory risk,
- capex returns,
- earnings quality,
- balance-sheet resilience,
- competitive position,
- capital allocation quality.

PR #10 does **not** activate any of them.

## Why market first

An entity belief without market context mixes two different questions:

1. Is the company improving or deteriorating fundamentally?
2. Is the market regime supportive or hostile to that type of company?

BRACE should be able to answer those independently before combining them.

The intended future hierarchy is:

```text
Broad Market State
        ↓
Sector / Factor State
        ↓
Company / Entity State
        ↓
BRACE relationship / calibration
        ↓
only after separate promotion review: possible bounded influence
```

This prevents a strong company belief from silently overwhelming a hostile rates, liquidity or risk regime.

## PR #10 beliefs

The first BRACE-wide layer contains exactly four beliefs:

### `market.rates.supportive`

Claim: US rates pressure remains supportive for risk assets into the target horizon.

Evidence foundation:

- TLT duration/rates-pressure proxy.

Important limitation: TLT is **not** represented as the Fed policy rate or as a complete Treasury curve model.

Deterministic outcome contract:

- TLT at the target observation is not below the frozen reference.

### `market.liquidity.supportive`

Claim: cross-asset credit/liquidity conditions remain supportive into the target horizon.

Evidence foundation:

- HYG/LQD relative behavior.

Important limitation: this is a credit-risk/liquidity proxy. It does not claim direct observation of dealer balance sheets, funding markets, order-book depth or executable bid/ask liquidity.

Deterministic outcome contract:

- HYG/LQD at the target observation is not below the frozen reference.

### `market.macro_regime.supportive`

Claim: the US macro/financial backdrop remains supportive for risk assets into the target horizon.

Evidence foundation:

- market-implied TLT/HYG/UUP macro proxy,
- existing primary-source BLS inflation and labor observations when available.

The BLS source lineage is preserved. Market proxies and BLS evidence are separate independence clusters.

Deterministic target outcome:

- majority of TLT, HYG and UUP conditions remain supportive versus the frozen references.

This is explicitly a **tradable macro/financial backdrop** outcome contract, not a declaration that the underlying economy itself improved within one day.

### `market.risk_regime.supportive`

Claim: the broad US risk regime remains non-defensive into the target horizon.

Evidence foundation:

- SPY,
- RSP/SPY,
- HYG/LQD,
- VIX,
- UUP.

Deterministic outcome contract uses a majority test across:

- SPY not materially below the frozen reference,
- VIX below the frozen dynamic cap,
- credit ratio not materially below the frozen reference.

## Prospective-only calibration

The first production run is activation-only.

It establishes:

```text
activated_at
historical_backfill = false
```

The layer may compute current state on activation, but it does not freeze a retroactive forecast.

Subsequent after-close runs freeze at most one daily forecast per belief and verify matured forecasts from deterministic target data.

No historical forecasts are reconstructed.

Calibration interpretation:

```text
N < 12      collecting / warm-up
12 <= N <30 descriptive only
N >= 30     broad-market calibration analysis available
```

These thresholds authorize analysis only. They do not authorize any BRACE influence.

## Isolation from existing SPX bridges

The existing BRACE-SPX and WES-SPX Belief bridges continue to use their predeclared five SPX beliefs.

PR #10 does not alter those selectors and does not reinterpret prior SPX bridge observations.

This is important: broad-market research can start without changing any existing WES or BRACE-SPX decision contract.

## Company/entity gate

PR #10 hard-codes:

```text
sector_factor_beliefs_enabled = false
company_entity_beliefs_enabled = false
company_entity_activation_authorized = false
automatic_promotion = false
```

The next entity stage requires a separate reviewed PR.

That later stage should introduce entity beliefs only after defining, for each dimension:

- canonical claim,
- data source and provenance,
- source independence cluster,
- evidence half-life,
- deterministic or explicitly governed outcome rule,
- forecast horizon,
- stale-data behavior,
- missing-data behavior,
- calibration metric,
- anti-lookahead rule,
- relationship to broad market and sector/factor states.

## Safety

PR #10 remains research-shadow only:

```text
active decision influence = false
candidate ranking change  = false
target exposure change    = false
sizing change             = false
veto                      = false
trade execution           = false
policy output             = false
automatic tuning          = false
bounded influence         = false
historical backfill       = false
```

Private cumulative runtime state is stored as the GitHub Actions artifact:

`brace-broad-market-belief-state`

No calibration output is written back into BRACE production decisions.
