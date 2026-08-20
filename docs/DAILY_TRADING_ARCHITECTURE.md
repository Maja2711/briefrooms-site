# BriefRooms Daily Trading architecture

## Information architecture

```text
INWESTYCJE
├── DAILY TRADING
│   ├── Daily GPW
│   ├── Daily US Stocks
│   └── Daily EUR/USD Spot
│
├── WEEKLY POSITIONS
│   ├── EUR/USD
│   ├── S&P 500 Futures
│   └── BTC/USD
│
├── PORTFEL 10K
│   └── BRACE / portfolio
│
└── LONG VIEW
    ├── S&P 500
    └── future: Nasdaq / EURUSD / BTC / other
```

The Investments landing page exposes only three primary products: **Daily Trading**, **Portfel 10K**, and **Long View**. Weekly Positions is one level lower inside the trading domain and is reachable through the `Daily Trading | Weekly Positions` switch.

## Daily engine boundary

Every Daily engine must be representable by `scripts/daily_engine_contract.py`:

```text
instrument
timestamp
direction
score
confidence
entry
stop
target
horizon
engine_version
status
decision_mode
metadata
```

`decision_mode` is explicitly `WITHOUT` or `WITH`. A market engine does not need to know Belief Core internals; a future Belief Bridge consumes or augments the canonical output.

## GPW and US Stocks

The existing `Daily Stock Core` remains unchanged. GPW and US keep separate market memory, source gates, calendars and currencies. The new Daily Trading page moves their **presentation boundary**, not their proven engine logic.

## Daily EUR/USD Spot

`scripts/daily_eurusd_spot.py` is the third Daily adapter.

v1 properties:

- deterministic market-state score;
- EUR/USD price trend + broad USD proxy + US rates-pressure proxy;
- ATR-based stop/target geometry;
- horizon: intraday to 24h;
- shared `daily-engine-output-v1` contract;
- rollout stage: `shadow`;
- `decision_mode=WITHOUT`;
- Belief decision influence: **false**.

The existing WES EURUSD Belief coverage is intentionally not imported into the v1 decision. It remains a later shadow calibration input for a controlled WITHOUT/WITH comparison.

## Safety / governance invariants

1. Do not pool GPW, US and EUR/USD learning samples.
2. Do not let Belief influence the Daily EUR/USD decision before explicit WITH/WITHOUT calibration.
3. Do not claim ECB policy or EUR-vs-USD rate-differential coverage from the current UUP/TLT proxy layer.
4. Do not fabricate executable bid/ask spread from Yahoo OHLCV.
5. Preserve existing GPW and US hard gates.
6. Promote EUR/USD from shadow to paper-trading only after outcome tracking and calibration are reviewed.

## Canonical normalization layer

`scripts/daily_engine_adapters.py` is the anti-corruption layer for the existing
GPW and US engines. It maps their established payloads to
`daily-engine-output-v1` without changing source decisions, hard gates or
learning histories. This is intentionally safer than rewriting mature engines
just to satisfy a new UI/Belief contract.

`confidence` in this normalized contract is **decision strength, not a calibrated
probability**. Probability calibration must remain a separate Belief/forecasting
concern; the bridge must not reinterpret a score as an event probability.

## Non-breaking presentation cutover

The existing Polish Portfolio 10K HTML still contains the historical Daily GPW
mount point because current automated render/verification jobs reference that
file. `scripts/gpw-daily-pick-public.js` now acts as a compatibility shim: on
Portfolio 10K it removes the legacy Daily block at runtime; on the new Daily
Trading page it boots the existing shared GPW/US renderer normally. This keeps
the user-facing ownership clean without risking the active GPW publication
workflow during the first cutover.
