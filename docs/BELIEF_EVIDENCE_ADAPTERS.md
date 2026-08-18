# Belief Core Evidence Adapters v1

## Purpose

Belief Core v2 now receives market evidence through small deterministic adapters instead of one monolithic live-data function.

The shared boundary is:

```text
RAW SOURCE
   -> Observation
   -> explicit EvidenceAssessment
   -> observation_to_evidence()
   -> Belief Core Evidence
```

An `Observation` is a timestamped fact or derived feature with provenance. It does **not** influence a belief merely because it exists. Only an explicit assessment can convert an eligible observation into Evidence.

`status=unavailable`, `stale`, or `invalid` observations are rejected by the Observation -> Evidence boundary. Missing fields are never invented.

## Common Observation contract

Every adapter uses `scripts/belief_adapter_contract.py` and records:

- stable `observation_id`,
- adapter name and metric,
- entity,
- `observed_at`,
- value and unit,
- source / source type / source ref,
- initial reliability,
- independence cluster,
- status,
- tags and metadata.

Every Evidence produced through the contract carries the originating `observation_id`, adapter and metric in Evidence metadata, preserving the reasoning chain.

## 1. Market Data Adapter v1

File: `scripts/belief_market_data_adapter.py`

Current source: Yahoo Finance chart OHLCV, treated as a secondary source.

For SPY, RSP, IWM, VIX, HYG, LQD, TLT and UUP it records:

- price,
- 1-day change,
- session open/high/low,
- session volume,
- estimated dollar turnover (`sum(close * bar volume)`),
- gap versus the previous session close,
- current-session realized volatility,
- bid/ask spread availability.

Yahoo chart data does not expose an executable bid/ask spread. Therefore `bid_ask_spread` is stored with `status=unavailable` and `value=null`. v1 does not estimate or fabricate a spread.

Market Data Adapter is primarily a source adapter and emits raw observations rather than directly increasing belief probability.

## 2. Technical Evidence Adapter v1

File: `scripts/belief_technical_adapter.py`

Current SPY features:

- 3h / 1d / 5d momentum,
- RSI(14),
- distance from MA20 and MA50,
- 20-bar breakout/breakdown position,
- session VWAP distance using volume-weighted typical price from available intraday OHLCV,
- ATR(14) as percentage of price,
- rolling support and resistance,
- deterministic composite trend score.

Only the composite trend observation becomes one Evidence item for `spx.trend.bullish`. Intermediate technical observations remain available for audit and future adapter development without multiplying the belief weight.

## 3. Liquidity Evidence Adapter v1

File: `scripts/belief_liquidity_adapter.py`

Current observations:

- time-of-day adjusted relative volume,
- excess / abnormal volume,
- SPY dollar turnover,
- Amihud-style price-impact proxy,
- a clearly-labelled `tradability_score` proxy,
- HYG/LQD relative performance,
- HYG return.

RVOL compares cumulative volume through the same number of intraday bars against recent sessions; it does not compare a partial morning with full previous days.

The `tradability_score` is a research proxy based on RVOL and dollar turnover only. It explicitly records that executable bid/ask spread and order-book depth are unavailable, so it must not be treated as an execution guarantee.

Only HYG/LQD and HYG return currently become Evidence for `spx.liquidity.supportive`.

## 4. Regime / Cross-Asset Adapter v1

File: `scripts/belief_regime_adapter.py`

Current cross-asset observations:

- RSP/SPY relative performance,
- IWM/SPY relative performance,
- VIX level and change,
- HYG/LQD credit ratio,
- TLT return,
- UUP return,
- HYG return,
- deterministic regime score and label.

Regime labels are `risk_on`, `neutral`, `risk_off`, and `high_vol`.

The adapter produces Evidence for:

- breadth,
- volatility,
- financial conditions.

The same regime label is frozen into scheduled BRACE / BRACE-SPX / WES forecast metadata for later calibration by regime.

## Live pipeline integration

`scripts/belief_core_live.py` runs all four adapters on one shared market snapshot.

The initial adapter suite deliberately keeps the same **nine Evidence items per live refresh** that the previous monolithic implementation produced, while adding a much richer Observation layer. This prevents a sudden increase in Belief Core conviction merely because the code was modularized.

With all eight current symbols and complete OHLCV, a standard snapshot produces:

- Market Data: 80 observations / 0 Evidence,
- Technical: 12 observations / 1 Evidence,
- Liquidity: 7 observations / 2 Evidence,
- Regime / Cross-Asset: 9 observations / 6 Evidence,
- total: 108 observations / 9 Evidence.

Observations are stored only in the private cumulative workflow artifact as `observations.jsonl`; they are not published by GitHub Pages and are not committed to the public repository.

## Safety and scope

All adapter code is deterministic. There is no LLM interpretation in these four v1 adapters.

The adapter suite cannot:

- place orders,
- size positions,
- emit policy output,
- automatically tune source reliability,
- modify BRACE, WES or BRACE-SPX decisions.

The live workflow compiles the adapter modules and runs the adapter test suite before any collection cycle.

## Next adapter families

After stable collection, the natural next families are:

1. News / Event Adapter with primary-source provenance and text classification.
2. Macro / Event Calendar Adapter.
3. Historical / Pattern Adapter using frozen World State similarity and verified outcomes.
4. A richer executable-liquidity source for real bid/ask spread and depth, replacing the current explicit `unavailable` field where licensing and access permit.
