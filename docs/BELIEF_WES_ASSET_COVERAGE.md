# Belief Core — WES EUR/USD + BTC Coverage Foundation

## Purpose

This layer extends Belief Core beyond its SPX-centric starting point with calibrated EUR/USD and BTC market/cross-asset beliefs.

For WES/BTC research it remains a shadow evidence foundation. For **Daily EUR/USD v1.9**, the EUR/USD beliefs are now upstream production epistemic inputs: the adapter itself still has no decision or execution authority, but its Evidence can change Belief Core and the final Daily EUR/USD decision is made only after Belief Core.

## Atomic beliefs

### EUR/USD

1. `eurusd.trend.bullish`
   - direct EUR/USD multi-horizon price trend,
   - deterministic outcome: `EURUSD=X` target close above frozen reference.

2. `eurusd.usd_environment.supportive`
   - broad USD environment represented by inverse UUP momentum,
   - deterministic outcome: UUP at/below frozen reference.

3. `eurusd.us_rates_pressure.supportive`
   - US rates-pressure proxy represented by TLT,
   - deterministic outcome: TLT at/above frozen reference.

This third belief is **not** called an EUR-vs-USD rate differential. The market adapter itself still does not claim ECB policy, euro-area rates or a true EUR/USD rate differential. ECB/Fed/news/macro Evidence is supplied by separate primary-source Belief adapters and may affect the corresponding EUR/USD macro/policy beliefs upstream of Daily v1.9.

### BTC

1. `btc.trend.bullish`
   - direct BTC/USD multi-horizon price trend.

2. `btc.liquidity.supportive`
   - cross-asset liquidity proxy from HYG/LQD and TLT,
   - deterministic outcome requires credit ratio and duration proxy to remain supportive.

3. `btc.volatility.benign`
   - realized-volatility state from BTC bars,
   - deterministic outcome uses a frozen 24h absolute-return cap bounded between 4% and 12%.

4. `btc.usd_environment.supportive`
   - broad USD environment represented by inverse UUP momentum.

BTC coverage deliberately does **not** claim on-chain flows, stablecoin liquidity, exchange flows or crypto-derivatives positioning.

## Data isolation

`EURUSD=X` and `BTC-USD` are additive optional Yahoo market sources.

If either optional source is unavailable:

- the established SPX Belief pipeline still runs,
- no EUR/USD/BTC evidence is fabricated,
- no new forecast is frozen for a belief whose required source is unavailable.

The original SPX market symbols remain hard-required.

## Consumer isolation

This PR also fixes forecast scoping before adding non-SPX beliefs.

`BRACE+BRACE-SPX` freezes only definitions tagged for BRACE / BRACE-SPX.

`WES` freezes definitions tagged for WES.

Therefore adding EUR/USD and BTC does not silently expand BRACE-SPX or BRACE forecast scope.

```text
BRACE + BRACE-SPX
    -> 5 existing SPX beliefs only

WES
    -> 5 SPX beliefs
    -> 3 EUR/USD beliefs
    -> 4 BTC beliefs
```

When an asset-specific WES forecast is frozen, `market_observed_at` is taken from that asset (`EURUSD=X` or `BTC-USD`) rather than always from SPY.

For EUR/USD Evidence, source timestamps are preserved per input: fresh EUR/USD bars cannot re-stamp an older UUP or TLT observation. Outside US cash hours, EUR/USD trend Evidence may refresh across the FX week while UUP/TLT naturally age.

## Evidence boundaries

All new evidence is derived from existing secondary Yahoo market data. It has explicit provenance and independence clusters.

No new source is treated as primary evidence.

Current coverage status is exposed explicitly:

- EUR/USD: `partial_market_macro_proxy_coverage`
- BTC: `partial_market_cross_asset_coverage`

## What remains before full WES coverage

### EUR/USD

Still incomplete at the market/cross-asset foundation level:

- direct euro-area rates data,
- true EUR-vs-USD short-rate / OIS differential,
- richer deterministic euro-area macro surprise / growth-inflation state.

Separate primary-source Belief paths now provide Fed/ECB/news/macro Evidence, including official ECB press-release intake. That does not turn the TLT proxy into a real rate differential.

### BTC

Still needed:

- stablecoin liquidity,
- exchange flows,
- on-chain flows,
- crypto derivatives / funding / positioning.

Those should be separate evidence adapters with their own calibration rather than being inferred from market-price proxies.

## Safety

```text
adapter trade execution authority        = false
adapter policy-output authority           = false
automatic tuning                          = false
direct adapter direction authority        = false
Daily EUR/USD v1.9 downstream Belief use  = production epistemic input
WES/BTC asset forecast use                = shadow/research
```

The adapter produces auditable Evidence. Daily EUR/USD v1.9 consumes the resulting Belief state through its dedicated read-only Belief-first decision consumer; it does not call this adapter as a trading engine.
