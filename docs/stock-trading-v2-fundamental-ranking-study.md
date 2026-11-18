# Stock Trading v2 — Fundamental ranking weight trial (US, research only)

## Current production V1 is NOT changed

`US_QUALITY_GATE_55_V1` remains the only pre-approved fundamental production policy, exclusively a **filter after existing eligibility** with the threshold 55/100 and its existing 30-session promotion and rollback controller. No changes to `main` production bridge, risk limits, GPW, EURUSD, BTC, S&P, or executable orders are part of this study.

## New independent experiment

`scripts/stock_trading_v2_fundamental_ranking_study.py` runs solely on the `stock-trading-v2` research branch. Starting **2026-11-20 UTC** (not retrospectively), after the original immutable V1 session decision has been frozen, it records the same US free-slot eligible candidates and point-in-time SEC facts. It compares:

- **Control** `v1`: same top candidate as the V1 quality gate, no ranking contribution from fundamentals.
- **Experimental w10, w20, w30**: 10/20/30% fundamental score in an experimental **ranking formula only**: `(1-w)*native_utility + w*fundamental_score`.

All arms are restricted to the *same* previously eligible quality-approved candidates (V1 score ≥55, valid temporally available source facts, existing cash/opportunity edge and hard gates). If none is available, all remain CASH. This does **not** assign 10%, 20% or 30% production authority; 25% is not silently introduced. None of the arms changes underlying utility, risk, order count, stop/target, GPW or current live decisions.

Trial receipts are stored append-only at `data/investments/stock_trading_v2_fundamental_trials/ranking_v1/<US-session-date>.json` beside, not inside, the existing V1 gate's fixed-N proof receipts. They bind source opportunity, evidence, SEC fundamentals, original V1 trial SHA, the unmodified V1 policy digest, the ranking code digest and frozen underlying experience events. A second snapshot of a previously frozen session cannot be substituted. Original immutable sources and future outcomes are never rewritten.

## Paired net-return assessment

- Fixed, **first 60** prospectively frozen session episodes; **no early peeking**, rolling winner selection, retrospective backfill or replacement of missing/bad episodes.
- The exact frozen choices for all four arms are assessed on the *same sessions*, with the same existing conservative next-session-open SL/TP replay and **US cost-stressed net return** for +1 and +2 sessions. These are virtual free-slot outcomes, **not proven live fills or portfolio P&L**.
- Minimum 12 decisions changed by an arm, 5 symbols, span of 70 calendar days; mean uplift ≥0.05 percentage points in **both** horizons, >55% positive outcomes among changed sessions and no positive outcome producing over 50% of total positive uplift.
- Fixed 4,000-resample **five-session circular block bootstrap** for serial dependence; one-sided alpha **0.05/6** per combination (3 weights × 2 horizons), rather than testing all combinations at uncorrected 5%. Require positive lower confidence bound for both horizons.
- A missing or unplayable earliest outcome cannot be replaced with a later profitable observation; evaluation stays pending or fails data integrity. No auto-tuning thresholds or repeated alternative weight grids.
- Even a research `RESEARCH_CANDIDATE` result **cannot promote a ranking weight to production**. A separate independent confirmation holdout, security review and separately governed production policy would be required.

## Automatic research operation and visibility

The existing US continuous-discovery workflow freezes a ranking receipt immediately after the V1 gate receipt. Existing immutable-history airlocks and git persistence already include the entire fundamental-trials subtree, including `ranking_v1/`. The existing learning loop verifies the receipts and generates `data/investments/stock_trading_v2_learning/fundamental_ranking_study.json` after outcomes settle, committing it through the existing research-learning persistence mechanism. CI runs ranking tests with the other v2 tests.

Sample sizes and the date of a result are not guaranteed: no eligible observations, absent data or unresolved future settlements are reported honestly. There is no automatic transition from research ranking to production ranking.
