# Stock Trading portfolio risk and native policy replay

This extends `TR-04` and the existing `LE-05`/`LE-06`/`LE-09` learning contracts. `main` owns production admission, lifecycle, native input receipts and policy changes. Research reads frozen receipts; a replay result never creates a live position or rewrites an execution record.

## Portfolio admission risk

`stock_trading_portfolio.portfolio_risk_assessment` runs before each new admission, after the ordinary quote, thesis, re-entry and risk-geometry gates. Markets are independent: GPW is evaluated in PLN, US in USD; no FX rate or cross-market aggregation is invented.

The policy retains three slots and 5,000 notional per position. The aggregate stop-loss ceiling is 1,050 per market, anchored at `3 × 5000 × 7%`. Existing positions consume current mark-to-stop downside plus their remaining estimated exit cost. A new candidate consumes entry-to-stop downside and both transaction legs. Therefore three positions at the maximum individual risk may exceed the aggregate budget once costs are included. Missing position quantity or risk information blocks a new admission; it does not close or alter an existing position.

At most two positions may share a verified sector. Sector identity requires a non-future timestamp and explicit source metadata; a ticker, company name or unsourced sector label is never enough. Unverified sectors enter the separate `UNKNOWN` bucket, capped at three positions. These prospective gates preserve the existing book and closed history.

## Explicit paper costs

New positions freeze `transaction_cost_model` and `entry_cost_amount` at entry. Exit economics use that same model even if the policy changes later. Gross prices, quantities and fills remain the observed paper execution facts; separate cost fields carry the economic adjustment.

`STOCK_PAPER_COST_STRESS_V1` is an assumption, not measured broker execution. GPW charges 5 bps half-spread plus 5 bps slippage per side (20 bps round trip at an unchanged price); US charges 2.5 + 2.5 bps per side (10 bps round trip). Commission and fixed fees are explicitly zero in this model. Historical positions without a frozen model remain unscorable for net comparisons; no zero-cost backfill is allowed.

`stock_trading_portfolio_analytics.py` calculates realized net P&L and open mark-to-liquidation equity, including an estimated exit leg. It reports per-market net NAV/return, drawdown including open positions, sourced benchmark excess return and dependence on the largest winner. Replay subtracts each inherited open position's initial mark and cost contribution, so earlier gains and sunk entry costs cannot become a new trial's alpha or winner attribution.

## Main-owned native inputs

`stock_trading_v2_production_replay.capture_frame` appends content-addressed receipts beneath `data/investments/stock_trading_v2_production_replay_frames/<UTC date>/<SHA256>.json`. Each receipt freezes the production policy, initial book, reviewed runtime-code fingerprint, observations, opportunity frontier, quotes, bridge config/runtime/manifest, re-entry inputs, benchmarks and provider session receipts. Content conflicts fail; native observations and decision/collection times are preserved separately.

The production bridge freezes quote `observed_at`, `received_at` and each prospective evaluation's `decision_at`. The frame also preserves the bridge's original `decision_started_at`; the later frame capture time must not change an opportunity-age gate or entry timestamp. Replay rejects future, inverted or decreasing execution times and preserves the original quote-age field. It never obtains a replacement quote from the network.

The lifecycle collector freezes raw current-session 5-minute `source_bars` with each bar's actual collection receipt, plus the observed daily closes, their receipt time and ATR. Both real positions and active counterfactual watches are collected. The real book is reviewed through the same raw-bar projection used by replay, in the exchange timezone after collection.

`data/investments/stock_trading_v2_replay_watchlist.json` is a production-owned research subscription ledger. The bridge may register at most two new symbols per market per exchange calendar week for 168 hours. Repeated registration while active preserves the original receipt and expiry. The lifecycle collector reads those watches even when the real book holds only CASH. A watch grants no admission, sizing, risk, exit or policy authority. Invalid watch receipts are reported; missing data remains pending. Expired watches stop collection and are retained for audit.

For each simulated open position, `project_observation` derives a fresh snapshot from the frozen raw bars using that book's own `max(opened_at, risk_last_changed_at)`. It excludes the bar overlapping an entry or ratchet boundary because its pre-boundary high/low cannot be separated. It also excludes future receipts and bars outside the observed regular session. An already reduced real-book OHLC window cannot stand in for a different virtual window. Legacy receipts without raw bars are usable only if their explicit post-effective snapshot passes the lifecycle contract.

Re-entry evidence and observed quote features are frozen separately. Replay injects those inputs into the actual bridge; it never calls the live feature collector. A required missing feature/evidence receipt makes the comparison pending rather than silently bypassing the guard.

Closed-session lifecycle receipts reproduce the canonical data-error HOLD and retain the existing book without interpreting the known closed interval as missing regular-session evidence. They cannot trigger SL/TP, thesis exits or risk recalculation. Missing eligible observations during the original regular-session window still make the replay pending. The optional `markets=[market]` argument scopes a trial while leaving native frame bodies and hashes intact.

## Exact policy replay and evidence status

`replay_portfolio` calls the actual production bridge and canonical portfolio review functions, including slot caps, aggregate/sector gates, cost freezing, conservative stop-first resolution, thesis exits, daily risk ratchets and `LET_WINNERS_RUN_THESIS_CONTROLLED`. It advances the actual CANARY health transition before subsequent frames. A strong runner remains open and is marked to liquidation; the evaluation horizon does not force an artificial exit.

`compare_policies` uses the same chronological tape and initial book for both policies, producing paired net NAV increments, final net-return differences, drawdown differences and separate GPW/US benchmark comparisons. Classification is always `COUNTERFACTUAL_PRODUCTION_POLICY_REPLAY`, with `live_execution_claim=false`.

Missing or changed native receipts/code, missing virtual-symbol observations, invalid timestamps, absent execution or re-entry inputs, unscorable costs/marks, and missing sourced benchmarks yield `PENDING` with explicit diagnostics. They never become zero returns, assumed fills or successful evidence. `COMPLETE` means the native replay is economically scorable; prospective registration, frozen cohort membership, independent episodes and the statistical promotion gate still require separate proof.

`deployment_economic_monitor` is a read-only calculation over a predeclared future window and paired whole-portfolio net NAV. It checks policy/cost/deployment identity and can recommend rollback when the frozen drawdown or net-underperformance conditions are met. The production component writer owns any actual future-only policy rollback; this analytics module has no writer authority.

`stock_trading_component_monitor.py` is the main-owned continuation of that existing component-promotion authority. `register_activation` freezes the prior/activated policy and manifest, initial book, bridge config/runtime, native code and costs before the successful promotion workflow releases its shared stock-state writer lock. Its monitoring boundary defaults to the real registration time after health checks. The immutable contract is 30 days, at least two paired observations, 5% maximum drawdown, and rollback if final net return underperforms the control by more than one percentage point. A final paired mark more than 96 hours before the deadline cannot complete the window.

The periodic monitor independently replays both books over future main-owned native receipts and appends immutable paired observations. Missing control data, source-policy changes, code drift and changed active revision/policy are explicit `PENDING` conditions. A qualifying rollback rechecks the exact active deployment and revision, snapshots that revision, restores only the prior entry component and the affected market's CASH edge, and advances to a new manifest revision. Unrelated components, positions and history remain unchanged. Preparation and completion events are audited; repeated completed rollback runs do not mutate again. The caller must use the same workflow concurrency group as every canonical stock-state/policy writer.

## Validation

`tests/test_stock_trading_portfolio_evaluation.py` covers aggregate and verified-sector admission gates, frozen two-leg costs, immutable legacy history, whole-book drawdown/winner attribution, canonical profit-runner then stop behavior, native-frame tampering, cap/missing-quote behavior, actual CASH-edge pairing, bounded immutable watches, watch-only collection, virtual entry/ratchet windows, quote-specific clocks, inherited-book attribution, CANARY progression, frozen re-entry injection without network fallback and predeclared monitoring.

The suite uses isolated temporary receipts and mocked observations. It does not settle live research, request current market data or mutate production history.

`tests/test_stock_trading_component_monitor.py` additionally exercises a complete native paired loss tape through automatic rollback, immutable activation/observation receipts, dry runs, stale revisions, code drift and missing controls. Replay and statistical calculations are not mocked in its end-to-end rollback test.
