# Daily EUR/USD — Saxo canonical cutover plan and admission gates

## Target
Single authoritative TR-03 **private** Daily ledger: one saved position, complete append-only history, one governed decision/policy epoch, with Saxo SIM FX Spot UIC 21 as the EUR/USD observation source and a separately confirmed, short-lived BID/ASK execution price. Macro, Fed, ECB, rates, events and cross-asset Evidence still pass through Belief -> Epistemic -> CF-07 -> Native Daily. This is simulation, not a Saxo order or evidence of broker fills.

## Current facts and gates (2026-10-29)
- An isolated D1-backed private Daily and a separate GitHub-backed public Daily still coexist.
- The private EPE uses Saxo WebSocket BID/ASK under `SAXO_STREAM_PRIMARY=1`; entries require the correct ASK/BID, 15-second freshness, two independent matching quote references and no backdated fills.
- Until this migration, Yahoo is still used in the Daily EURUSD technical and 1m lifecycle observations, and global Belief EURUSD trend Evidence uses Yahoo.
- Saxo SIM private Chart 1m, 30m, and daily 1440m are read-only verified; 5d/1m, 10d/30m, 6mo/1d and 2y/1d are the **actual** consumer ranges. H1 Bollinger needs 33+ closed H1 hours and regime needs 34, so the verified 10d/30m window suffices without 60d pagination, whose page-boundary historical samples diverged by >=1 pip. The incompatible 60d sequence remains fail-closed, never silently stitched. `SAXO_CHART_PRIMARY` remains gated until private Belief/ledger migration and final live cycle checks.
- Saxo SIM may not supply all available LIVE data. A successful WebSocket quote does not prove Chart entitlement. Chart API carries bar-open times and partially formed current bars, whereas streaming Trade prices are not necessarily synchronized with chart closes.

## Non-negotiable cutover gates
1. Verify in live Saxo SIM private channel that Chart 1m and 30m samples are allowed, timely, coherent, non-delayed, and that sufficient immutable 1m/30m history can be paged without provider revisions or rate limits. Do not log or publish prices.
2. Validate the Chart candle adapter end-to-end with simulated market day and weekend/no-market, malformed data, mid/side divergence, closed bars only, missing 1m minutes and source/time consistency. Audit all imported Daily technical components for injected `MarketSnapshot`/Yahoo dependencies.
3. Recompute private **market-derived** EURUSD Observation/Evidence/Belief/CF-07 inputs from Saxo bars; do not pass a global Yahoo EURUSD trend disguised as Saxo. Keep UUP/TLT, rates, macro Fed/ECB/events and independent EPE reference prices as separate sourced inputs and original timestamps.
4. Reconcile existing public and D1 positions/histories and exact decision/policy manifest epoch via deterministic read-only hashes. Block cutover if either has a different OPEN position or earlier trade IDs conflict; do not merge/re-label closed trades and do not initialize/reset D1 automatically. Snapshot and hold immutable before/after evidence.
5. Exactly one authorized position/decision/history writer post-cutover (D1 CAS with controlled recovery, no second Git push writer). The 5-second exit-only watcher must be re-routed against the SAME D1 ledger, or gated off with explicit latency acceptance; never allow the public 5-second watcher to mutate a detached state.
6. Publish ONLY information legally permitted by Saxo and necessary for public display. The existing Saxo feed states `redistributable=false`; publication of broker quotes, price-linked positions or derivable Saxo market information must not occur without appropriate written permission. A separate public vendor may still provide the public market quote with its own license, explicitly labelled independent. Implement a read-only projection with no mutation authority and prove no private fields can escape.
7. A monitored future-only switch then disables the legacy public writer, enables the private single Champion, and proves successful scheduled cycles, one EPE paper fill, one exit, expected liveness and rollback-safe failures. No retroactive changes to past outcomes. If any gate fails, keep existing public history and the private test environment separated, show BLOCKED rather than claiming unified production.

## Validation evidence
- `workers/saxo-eurusd-stream/test/stream.test.mjs`: private Chart HTTP routing and OAuth request containment.
- `tests/test_saxo_private_chart.py`: chart time, BID/ASK geometry and non-public access contract.
- `tests/test_daily_eurusd_private_chart_source.py`: fail-closed source selection with no Yahoo fallback for private Chart mode.
- `saxo-private-eurusd-daily-d1-cycle.yml`: regression, D1 read-only preflight; a successful preflight is NOT proof of full cycle.
- Existing `tests/test_saxo_private_primary.py` and `tests/test_saxo_private_execution_exit.py` protect private EPE prices and quote freshness.

### Source reference
Official Saxo Developer Portal: `/openapi/referencedocs/chart/v3/charts`, `/openapi/learn/chart`, `/openapi/learn/environments`. Chart API maximum 1200 samples/request; Forex historical BID and ASK OHLC fields; chart sample times are **bar opens**. Do not treat SIM or indicative quote as an actual execution.
