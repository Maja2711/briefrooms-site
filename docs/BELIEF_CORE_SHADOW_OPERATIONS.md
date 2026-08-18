# Belief Core v2 — Live Shadow Operations

## Purpose

This pipeline starts empirical Belief Core collection without giving Belief Core any decision, sizing, execution, or automatic tuning authority.

The operational loop is:

```text
30-minute market evidence
    -> Belief Core evidence store
    -> hourly World State
    -> scheduled frozen forecasts
    -> deterministic target outcomes
    -> automatic verification
    -> calibration memory
```

## Cadence

The GitHub Actions workflow runs every 30 minutes during a UTC window wide enough to cover the US cash session through both EDT and EST. The Python scheduler uses `America/New_York` and applies the actual gates.

- Evidence refresh: every 30 minutes while a current US session is available.
- World State: one snapshot per market hour.
- BRACE + BRACE-SPX frozen forecasts: 10:00, 13:00, 16:00 New York time, Monday-Friday.
- WES frozen forecast: Friday 16:00 New York time.
- Verification: every workflow run scans all matured unresolved forecasts.

A forecast slot has a 45-minute grace period. If infrastructure misses the entire window, the system records the gap and does **not** reconstruct the forecast later from data that was not known at forecast time.

## Initial evidence families

The first live adapter intentionally starts with transparent market evidence rather than engine outputs, preventing a circular `engine -> belief -> same engine` feedback loop.

| Belief | Evidence | Independent clusters |
|---|---|---|
| SPX trend bullish | SPY 3h/1d/5d momentum | SPY trend |
| Breadth healthy | RSP/SPY and IWM/SPY relative performance | equal-weight breadth, small-cap breadth |
| Volatility benign | VIX level and change | VIX |
| Liquidity supportive | HYG/LQD and HYG | credit ratio, high-yield return |
| Financial conditions supportive | TLT, UUP, HYG | rates proxy, USD proxy, credit proxy |

Yahoo Finance chart data is treated as a secondary market-data source and carries a conservative initial reliability. Reliability is **not** automatically changed from outcomes.

## Forecast horizons

### Shared BRACE / BRACE-SPX

- 10:00 forecast -> target at 16:00 same session.
- 13:00 forecast -> target at 16:00 same session.
- 16:00 forecast -> target at 16:00 next weekday session.

These are shadow research forecasts only. They are not read by BRACE or BRACE-SPX policy code.

### WES

- Friday 16:00 forecast -> target at Friday 16:00 one week later.

The WES forecast observes the shared belief layer but is not wired into WES admission, exposure, TP, SL, or weekly decision logic.

## Deterministic outcome rules

Outcome rules are frozen inside each forecast's metadata before the result exists.

- Trend: target SPY is above the frozen reference SPY.
- Breadth: target RSP/SPY is above its frozen reference ratio.
- Volatility: target VIX remains below the dynamic cap frozen with the forecast.
- Liquidity: target HYG/LQD is above the frozen reference ratio.
- Financial conditions: at least two of three are supportive versus their frozen references: TLT non-lower, HYG non-lower, UUP non-higher.

If the nominal target date is a market holiday and no target observation exists, verification waits for the first later available market session. It never uses an earlier observation and never verifies before `target_at`.

## Runtime persistence and publication boundary

Real evidence, belief history, forecasts, verifications, calibration output, the ledger, and World State history are **not committed to the public repository and are not published by GitHub Pages**.

Each run restores the newest cumulative `belief-core-shadow-state` Actions artifact, executes the cycle, validates the safety invariants, and uploads a new cumulative artifact. A rolling set of recent artifacts is retained as recovery copies.

This artifact boundary is suitable for the initial research/shadow collection period because it keeps runtime state out of the public site and Git history. It is **not** a substitute for a dedicated authenticated private database. Before storing sensitive, licensed, user-specific, or commercially restricted evidence, move Belief Core persistence to a proper private storage/backend.

## Hard safety invariants

The live adapter and the workflow assert all of the following:

```text
mode = shadow
decision_engine_connected = false
trade_execution_enabled = false
policy_output_enabled = false
automatic_tuning_enabled = false
```

There is no trade API in the live adapter. Forecast results only feed verification and calibration memory.

## Initial acceptance period

During the first 2-4 weeks, evaluate collection quality before any read-only engine integration:

- evidence freshness and missing-run rate,
- provenance/independence warnings,
- probability and confidence distributions,
- forecast counts and duplicate protection,
- verification completion rate,
- Brier/log-loss calibration,
- over/under-confidence,
- domain/regime/horizon slices,
- belief flip/trajectory behavior,
- pipeline reliability and ledger integrity.

P&L or strategy promotion is not an acceptance criterion for this first stage. No trading-engine behavior changes in this phase.
