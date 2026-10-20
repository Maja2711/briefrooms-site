# Daily EUR/USD post-loss technical admission

Owner: TR-03. Implementation: `scripts/daily_eurusd_reentry_safety.py`.
This is execution safety after the final Belief-first decision, not a new direction engine.
No Shadow/A-B-C/X output is consumed. Existing-position exits have no dependency on this gate.

## Scope

Every negative canonical same-direction result counts, including losses smaller than 0.15R and any strategy label. The most recent such loss resets the required sequence. The additional gate applies throughout the active source D1 candle containing the loss; the full first hour is also protected if the loss precedes a D1 rollover. The existing 12-hour meaningful-loss/thesis-improvement gate remains an additional condition.

The runtime reads D1 only when a recent same-direction loss exists. D1 session boundaries come from the source candle timestamp in UTC, not the user's local midnight. No first entry without a relevant loss gains an additional price-location veto.

## Initial criteria, subject to prospective learning

1. At least 30% of the current D1 open-to-extreme movement must have been corrected after the loss.
2. A strong D1 technical level must hold during that correction and a completed confirmation candle.
3. The current authoritative native Belief-first signal must be strong and backed by source EURUSD trend evidence observed after the loss.

These are initial engineering criteria. The local learner can change correction, support and signal requirements in either direction only through the versioned prospective promotion path in [DAILY_EURUSD_ADAPTIVE_REENTRY.md](DAILY_EURUSD_ADAPTIVE_REENTRY.md). Without an approved alternative, score/confidence improvement alone cannot release the initial gate. The active criteria and their hash are frozen in admission.

### Correction arithmetic and sequence

For LONG, with current D1 opening O and the high H known before the touch candle:
`(H - touch_low) / (H - O) >= 0.30`, with H > O.

For SHORT, with the low L known before the touch candle:
`(touch_high - L) / (O - L) >= 0.30`, with L < O.

Example: O=1.1000 and H=1.1100 require a post-loss touch at or below 1.1070. Entry follows a confirmed reaction; it need not occur at the intrabar low itself. A new directional daily extreme after the touch invalidates that old correction and forces a new setup.

The active D1 supplies its opening and boundary only. Its unfinished high/low cannot provide a future daily extreme. The impulse is reconstructed from the complete current-session 1m path, excluding unfinished/future bars. Gaps, conflicting duplicate bars, invalid OHLC, source/open mismatch or a path older than 180 seconds fail closed.

The touch and confirmation are the two most recent adjacent, fully completed 5m candles constructed from five complete 1m bars each. Both start strictly after the loss. A pre-loss touch, a same-bar assumed high/low ordering, or an unfinished confirmation cannot qualify.

### Strong technical support/resistance

Eligible levels are:

- SMA60, SMA100 or SMA200 computed from completed D1 closes before the active candle;
- alternatively, a structural D1 swing level with at least two distinct confirmed pivots, at least three source sessions apart. Each pivot requires two completed candles on both sides and is therefore available before admission.

A single isolated daily low/high is not silently classified as a strong structural level. Current/future D1 closes do not enter moving averages.

The held zone has half-width max(1 pip, 0.10*ATR14 D1), using completed daily true ranges. LONG must hold the zone and confirm with a bullish 5m close above the touch high and upper zone edge. SHORT is mirrored. All subsequent complete minute bars must still hold the level. Entry is limited to the confirmed side within 0.25*ATR14 of the level, capped by the pre-correction daily extreme. These are explicit engineering defaults, not statistically optimized claims.

### Strong native signal

Preparation defaults are LONG score >=60 / SHORT score <=40, confidence >=0.50, and at least three aligned qualified beliefs including the EURUSD trend anchor. Belief alignment uses probability >=0.52 for LONG / <=0.48 for SHORT. The native final direction must agree, and the CF-07 consumer must be available and authoritative.

The trend anchor's original source observation must be after the last loss and no later than the decision. A refreshed wrapper cannot restamp old evidence. Confidence is an epistemic reliability measure, not a calibrated probability of trade profit.

## Execution and immutable proof

Calendar safety, the existing same-thesis guard, current admission policy, EPE multi-provider verification and frozen risk geometry all remain mandatory.

After EPE, the actual paper fill is checked against the frozen technical entry zone. Its selected source quote must not predate the confirmation. Crossing the D1 boundary requires a new decision. An otherwise verified quote cannot chase price outside the support/resistance zone.

Admission records freeze the loss ID, D1 opening/boundary, correction arithmetic, touch/confirmation OHLC, level/ATR/zone, strong-signal diagnostics and the hash of the point-in-time D1/1m source data. The ordinary production manifest carries this proof into the position and immutable history. The safety implementation is included in the runtime code hash, so existing production policy cohort/change handling applies prospectively.

No historical trade, entry time, outcome or policy label is rewritten. Open-position SL/TP, dynamic, macro and time exits retain their current rules and authority. Missing technical data stops a new admission, never risk management of an existing position. Code pushes and pull requests are validation-only; production trading-state mutation remains scheduled/governed runtime.

## Validation and limits

Tests cover 29.9% versus exactly 30%, LONG/SHORT symmetry, each D1 MA, confirmed structural support, absent/broken support, weak signals, old source evidence, small losses, latest-loss reset, intraday continuity/future-data boundaries, rollover and EPE price/timestamp revalidation. The regression is observed failing before the implementation.

Passing engineering tests establishes the admission contract, not improved strategy profitability or optimal 60/40 and confidence thresholds. Real-source availability and a naturally occurring qualified production setup still require post-merge verification.
