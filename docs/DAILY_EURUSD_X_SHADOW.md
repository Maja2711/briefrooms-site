# EURUSD X - adaptive shadow research track

## Status

EURUSD X is a **research_shadow** track inside TR-03 Daily EUR/USD Spot and is displayed in the existing EUR/USD A/B/C Research Lab. It is not a production engine and has zero execution authority.

## Fixed technical core

The following technical contract is immutable inside X and is not calibrated:

- MA30 / MA60 / MA100 / MA200 on H1, D1, W1 and M1;
- classic daily floor Pivot: P, R1-R3, S1-S3;
- Bollinger Bands: window 20, **2.5 standard deviations**, on H1 and D1;
- read-only BriefRooms Belief Core input, frozen at or before the market observation.

Any change to these periods, timeframes or the 2.5-sigma Bollinger contract is a new engine version, not a calibration.

## Adaptive layer

Around the fixed core, X may evaluate additional bounded technical components and strategy hypotheses, including:

- MA slope consensus across timeframes;
- MA compression / breakout structure;
- H1 momentum acceleration;
- H1/D1 MACD state;
- H1/D1 RSI state;
- Bollinger trend continuation vs stretch mean-reversion;
- fast-vs-slow timeframe conflict / reversal.

The anomaly hunter may create a new X-AUTO-* challenger only from already resolved prospective X observations. A newly discovered setup starts with **zero retrospective credit**: it receives predictions only on future captures after creation.

## Frequent calibration

The X workflow is scheduled hourly on weekdays. The primary calibration outcome is the frozen 4-hour EUR/USD direction; 24-hour outcome is retained as a secondary horizon.

Each market capture freezes predictions for every setup that existed at that moment. This allows Champion and Challenger to be compared on the same future outcomes without reconstructing predictions after the fact.

Current X-local gates:

- minimum 8 common prospective 4h observations before a Challenger may replace the Champion;
- Challenger needs materially lower Brier (at least 0.01), without material hit-rate deterioration and without worse signed return;
- recent Champion degradation is checked on an 8-observation rolling window;
- if the current Champion deteriorates and the previously proven setup is materially better on the same recent frozen observations, X automatically rolls back to that last-best setup;
- a rolled-back failing setup receives an 8-outcome cooldown before it can challenge again.

Promotion and rollback are **X-local shadow state only**. They never change production Daily EUR/USD, A/B/C, Belief Core or execution.

## Anti-hindsight and authority invariants

- no historical signal backfill;
- no mutation of frozen captures after outcome;
- no production execution;
- no active Daily EUR/USD writeback;
- no A/B/C writeback;
- no Belief Core writeback;
- no automatic production promotion;
- public UI is a sanitized read-only projection.

## UI

EURUSD X is shown inside the existing EUR/USD A/B/C Research Lab and is visually marked in red so it is clearly distinguishable from A/B/C. The central BriefRooms LAB may reference X only as registry/status information; the full X research view remains in the EUR/USD domain.
