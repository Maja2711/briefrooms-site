# Daily EUR/USD A/B/C prospective experiment

## Purpose

Daily EUR/USD is evaluated as three **parallel research arms** on the same
market timestamp and the same EUR/USD reference price. The experiment asks a
narrow question:

> Does Belief Core add incremental intraday value beyond deterministic
> technical market structure, and does an overlap-controlled hybrid outperform
> either input alone?

The experiment is `research_shadow`. It has **zero influence** on the active
Daily EUR/USD engine, position lifecycle, sizing or execution.

## Arms

### A — TECHNICAL_ONLY

Arm A is the timing / price-action arm. It uses only EUR/USD 30-minute bars.

Frozen v1 feature families:

- SMA20 / SMA50 structure;
- EMA20 diagnostic continuity with the existing Daily EUR/USD engine;
- RSI14;
- algorithmic trend line: 24-bar least-squares slope + R²;
- algorithmic support / resistance: prior 48-bar range with deterministic
  breakout, bounce and rejection rules;
- ATR14 for normalization;
- multi-horizon price momentum.

No hand-drawn line or discretionary support/resistance interpretation is
allowed. Every feature is machine-reproducible from the frozen bar set.

### B — BELIEF_ONLY

Arm B reads the actual frozen Belief Core state available **at or before** the
market observation:

- `eurusd.trend.bullish`
- `eurusd.usd_environment.supportive`
- `eurusd.us_rates_pressure.supportive`

The current Belief implementation is intentionally tested as it really exists.
This means B is not falsely labelled "pure macro": the current WES EUR/USD
Beliefs contain price/UUP/TLT-derived evidence. The experiment records this
known overlap instead of pretending the information sets are independent.

Future-dated Belief state is rejected fail-closed.

### C — HYBRID

Arm C treats technical structure as the primary timing layer and Belief as
context.

Frozen v1 blend:

- technical: `70%`
- Belief context: `30%`

To avoid direct double-counting, `eurusd.trend.bullish` is **excluded from the
hybrid context sub-score** because Arm A already contains direct EUR/USD trend
information. C therefore uses:

- `eurusd.usd_environment.supportive`
- `eurusd.us_rates_pressure.supportive`

If technical and Belief context are both strong (`|signed score| >= 0.35`) and
point in opposite directions, C records `FLAT`. This is a hypothetical shadow
filter only; it has no authority over the active Daily engine.

## Frozen scoring contract

Directional thresholds are shared by A/B/C:

- `LONG`: score >= 60
- `SHORT`: score <= 40
- otherwise `FLAT`

Technical weights:

| Feature | Weight |
| --- | ---: |
| MA structure | 0.24 |
| RSI momentum | 0.16 |
| trend line | 0.20 |
| support / resistance | 0.20 |
| price momentum | 0.20 |

Belief-only weights preserve the existing Daily EUR/USD information hierarchy:

| Belief | Weight |
| --- | ---: |
| EUR/USD trend | 0.55 |
| broad USD environment | 0.25 |
| US rates pressure proxy | 0.20 |

These are frozen engineering hypotheses. They are **not PnL tuned** and the
runtime contains no automatic parameter tuning.

## Prospective capture and outcomes

Every new 30-minute market observation may create at most one immutable
capture. A capture freezes:

- EUR/USD market observation time;
- reference price;
- A/B/C decisions;
- technical features;
- Belief states used by B/C;
- all arm weights and overlap controls;
- future target timestamps.

The frozen decision payload receives `decision_sha256`. Forward outcome
resolution may populate only the outcome slots; changing the frozen decision
payload invalidates validation.

Outcome horizons:

- 30 minutes
- 1 hour
- 2 hours
- 4 hours
- 24 hours

Outcome price is the first available EUR/USD 30-minute close at or after the
frozen target time.

Reported descriptive metrics include:

- decision rate;
- directional hit rate;
- mean signed return in bps for signal observations;
- mean strategy return in bps across all available observations, with `FLAT`
  contributing zero.

No synthetic bid/ask or transaction-cost estimate is invented from Yahoo OHLC.
Cost-adjusted performance therefore remains disabled until an executable spread
source exists.

## Anti-hindsight and governance

Hard invariants:

- no historical signal backfill;
- no future Belief state;
- one capture per frozen market observation;
- forward-only outcome resolution;
- frozen-decision hash validation;
- no Belief writeback;
- no active Daily-engine writeback;
- no trade execution;
- no automatic tuning;
- no promotion based on retrospective optimization.

## Repository status

`scripts/daily_eurusd_experiment.py` implements the research harness and
append-only-compatible state contract.

`tests/test_daily_eurusd_experiment.py` verifies the technical indicator
families, A/B/C separation, overlap control, future-Belief fail-closed behavior,
capture immutability and forward-only outcome resolution.

The first change is intentionally **validation-only**. It does not activate a
scheduled production collector or create public Git research state. Runtime
activation should restore the existing private Belief Core artifact and persist
the A/B/C state in private durable research storage after validation.
