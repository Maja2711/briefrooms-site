# Belief Core — WES EUR/USD + BTC Coverage Foundation

## Purpose

This change extends Belief Core beyond its SPX-centric starting point so WES can begin collecting calibrated, frozen beliefs for EUR/USD and BTC before any Engine–Belief bridge is enabled for those instruments.

The layer remains **shadow-only** and has zero decision influence.

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

This third belief is **not** called an EUR-vs-USD rate differential. ECB policy, euro-area rates and a true EUR/USD rate differential are not yet covered.

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

## Evidence boundaries

All new evidence is derived from existing secondary Yahoo market data. It has explicit provenance and independence clusters.

No new source is treated as primary evidence.

Current coverage status is exposed explicitly:

- EUR/USD: `partial_market_macro_proxy_coverage`
- BTC: `partial_market_cross_asset_coverage`

## What remains before full WES coverage

### EUR/USD

Still needed:

- ECB policy/event adapter,
- euro-area rates data,
- true EUR-vs-USD rate differential,
- euro-area macro surprise / growth-inflation state.

### BTC

Still needed:

- stablecoin liquidity,
- exchange flows,
- on-chain flows,
- crypto derivatives / funding / positioning.

Those should be separate evidence adapters with their own calibration rather than being inferred from market-price proxies.

## Safety

```text
trade execution     = false
policy output       = false
automatic tuning    = false
decision influence  = false
```

This PR only creates the evidence and frozen-forecast foundation required for a later EUR/USD and BTC WES↔Belief bridge.
