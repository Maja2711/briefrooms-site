# Daily EUR/USD A/B/C prospective experiment

## Purpose

Daily EUR/USD is evaluated as three parallel research arms on the same frozen market timestamp and reference price:

- **A — TECHNICAL_ONLY**
- **B — BELIEF_ONLY**
- **C — HYBRID**

The experiment remains `research_shadow`. It has zero authority over the active Daily EUR/USD engine, sizing or execution.

## Arm A — multi-timeframe technical engine

The technical contract uses the requested H1/D1 structure and is frozen prospectively.

### Moving averages

Both **H1** and **D1** calculate:

- MA30
- MA60
- MA100
- MA200

The MA family evaluates the ordering of 30/60/100/200 and the current price relative to all four averages. Inside the multi-timeframe family H1 carries 65% and D1 35%, because this is a day-trading experiment while D1 supplies the structural trend.

### Pivot levels

Classic daily floor pivots are derived from the previous completed D1 bar:

`P = (H + L + C) / 3`

The engine freezes:

- Pivot (P)
- R1, R2, R3
- S1, S2, S3

The current EUR/USD reference price is classified into the corresponding pivot zone. No hand-drawn support/resistance is used.

### MACD

Both **H1** and **D1** calculate standard MACD:

- fast EMA: 12
- slow EMA: 26
- signal EMA: 9
- MACD line
- signal line
- histogram

MACD is normalized by local ATR before it contributes to the technical score.

### Bollinger Bands — v1.2

Both **H1** and **D1** use a **30-period middle band**, aligned with the short MA horizon requested for the system.

For each timeframe the capture freezes:

- MA30 middle band;
- population standard deviation `sigma`;
- price `z_score = (price - MA30) / sigma`;
- upper/lower **1 sigma** bands;
- upper/lower **2 sigma** bands;
- upper/lower **3 sigma** bands;
- bandwidth for the conventional 2-sigma envelope;
- %B for the 2-sigma envelope;
- flags describing whether price is above/below each 1/2/3-sigma level.

The Bollinger family therefore records not only a band touch, but the actual degree of statistical displacement from MA30. The directional score uses the z-score normalized to a 2-sigma reference plus the slope of the 30-period middle band. The 1/2/3-sigma bands are diagnostic state variables, not three separate votes, which avoids triple-counting the same volatility move.

Backward-compatible `upper` and `lower` fields point to the conventional **±2 sigma** envelope.

### Retained technical context

The following families remain because they measure different aspects of price state:

- RSI14 (H1/D1 blend)
- H1 algorithmic trendline, 24 bars, with slope and R²
- H1 ATR14
- H1 momentum at 3h / 12h / 24h

## Frozen technical weights

| Feature family | Weight |
| --- | ---: |
| H1/D1 MA30/60/100/200 | 0.24 |
| Daily Pivot + R/S levels | 0.16 |
| H1/D1 MACD | 0.18 |
| H1/D1 Bollinger(30), 1σ/2σ/3σ state | 0.14 |
| H1/D1 RSI | 0.08 |
| H1 trendline | 0.09 |
| H1 price momentum | 0.11 |

Weights sum to 1.00 and are frozen engineering hypotheses. They are not PnL tuned and there is no runtime optimizer.

## Arm B — Belief only

Arm B reads the actual frozen Belief Core state available at or before the market observation:

- `eurusd.trend.bullish`
- `eurusd.usd_environment.supportive`
- `eurusd.us_rates_pressure.supportive`

Future-dated Belief state is rejected fail-closed.

## Arm C — hybrid

Arm C uses exactly the same technical payload as A, including Bollinger(30) with 1σ/2σ/3σ state, and combines it with Belief context:

- technical: 70%
- Belief context: 30%

`eurusd.trend.bullish` remains excluded from the C context sub-score because Arm A already contains direct EUR/USD price-trend information. C therefore adds only the current USD-environment and US-rates-pressure Beliefs.

## Data feeds and timeframes

The experiment uses one EUR/USD source but separate frozen windows:

- 30m: reference timestamp and forward outcome measurement
- 1h: technical timing / medium intraday structure
- 1d: structural MA/MACD/Bollinger context and prior-day Pivot

The collector requests enough H1 and D1 history to calculate MA200. Technical capture fails closed if fewer than 220 bars are available on either timeframe.

## Prospective capture and outcomes

Every capture freezes the A/B/C decisions and receives `decision_sha256`. Forward outcome resolution may populate only future outcome slots. Any mutation of the frozen decision payload invalidates validation.

Outcome horizons remain:

- 30 minutes
- 1 hour
- 2 hours
- 4 hours
- 24 hours

No synthetic bid/ask spread or transaction cost is invented from Yahoo OHLC. Cost-adjusted performance remains disabled until an executable spread source exists.

## Governance

Hard invariants remain:

- prospective only;
- no historical signal backfill;
- no future Belief state;
- no Belief writeback;
- no active Daily-engine writeback;
- no trade execution;
- no automatic tuning;
- no PnL-tuned weights;
- no promotion based on retrospective optimization.

## v1.2 implementation note

`scripts/daily_eurusd_experiment_v12.py` is the canonical PR20 v1.2 entry/configuration layer. It upgrades the v1.1 research harness to Bollinger(30) with explicit 1σ/2σ/3σ dispersion while preserving the same A/B/C, anti-hindsight and zero-authority contracts.
