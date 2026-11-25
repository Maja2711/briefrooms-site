# BriefRooms weekly paper methodology v4

## Status

The filename is retained for historical continuity, but the current governed runtime is newer than the original v4 exposure rule. Runtime authority lives in `data/investments/multi_instrument_exposure_policy.json` (currently policy v5.9.1) together with `data/investments/wes_methodology.json` (WES 1.3.1).

The system remains experimental governed paper trading only. It places no broker orders.

## NO_TRADE and exposure

A weekly position is **not mandatory**.

Current policy explicitly sets:

- `mandatory_monday_position = false`;
- `continuous_position_required = false`;
- `NO_TRADE` is a first-class active state.

The tournament may rank a directional candidate, but ranking alone does not authorize a position. EUR/USD, S&P 500 futures or BTC/USD is opened only when the current admission gates qualify the candidate. Otherwise WES remains in `NO_TRADE` and continues monitoring for a later qualified trigger.

After a governed close, re-entry is not automatic merely because a position slot is empty. Re-entry requires the applicable WES trigger, validation and lifecycle rules. Qualified replacement is available on any weekday. Material-event exits remain fail-closed and are not automatically replaced.

## Execution lifecycle contract

A directional weekly forecast is not the same fact as an executed paper position. The canonical `trade_status` lifecycle is:

- `planned` — the frozen forecast exists, but there is no execution claim; `entry_price` is not required.
- `pending` — WES has frozen a qualified immutable execution decision and is waiting for the first eligible bar at or after that decision; `pending_entry_decision` is required, but `entry_price` is not yet required.
- `open` — execution occurred; a positive entry price and entry timestamp are mandatory.
- `no_trade` — admission abstained; no entry is expected.
- `expired_no_entry` — an authorized execution did not obtain a valid fill before its governed deadline; no entry, exit or P/L may be fabricated.
- `closed` — an executed position has been settled; both entry and exit execution facts are mandatory.

Integrity checks, the public Weekly Trading UI and WES execution must interpret these states identically. A directional `planned` forecast must never be quarantined merely because it has no entry price. Conversely, `open` or `closed` without the required execution facts remains fail-closed.

The legacy Monday `entry_latest_local` field is not a universal WES execution deadline. WES may remain in `NO_TRADE` and admit a later qualified trigger under the active lifecycle policy. The effective position/weekly deadline is enforced separately by the governed close-deadline verifier.

## WES 1.3.1 — Directional Admission, adaptive delta, MARKET/LIMIT & persistent execution

WES 1.3.1 keeps Champion/Challenger separation and NO_TRADE as a first-class decision, but removes the mechanical failure mode in which an already very strong trend had to improve by another fixed delta before WES could even consider execution.

Every new entry still requires:

- an execution-authorized Champion-pool method;
- current directional admission;
- at least the active profile's required number of independent confirmations;
- the active raw-score and utility floors;
- no hard Daily/Weekly directional conflict;
- prospective execution only; no historical fill reconstruction.

### Adaptive absolute-strength bypass for delta

The ordinary trigger still uses `delta` to demand genuine improvement from a weak or moderate initial NO_TRADE state. WES 1.3.1 may bypass only that delta condition when absolute conviction is already exceptional.

The bypass is deliberately relative to the active trigger profile rather than a second unrelated set of fixed numbers:

- raw score must be at least `1.30 × active raw threshold`;
- utility must be at least `1.30 × active utility threshold`;
- the active confirmation-count requirement still applies;
- both Daily and Weekly must confirm the same direction;
- directional admission and Champion execution authority remain mandatory.

The active trigger profile is now time-neutral (raw 48, utility 8, at least two independent confirmations and delta 15), including Friday. Only absolute conviction and valid market evidence—not minutes remaining until Friday's historical deadline—govern admission.

### Adaptive MARKET versus LIMIT decision

Directional admission answers **whether WES may trade the thesis**. The Entry Engine separately answers **how to enter**.

WES first constructs the same deterministic pullback plan from the decision-time reference price, ATR14, EMA20 distance, ret5/ret20, 55-day range location and post-stop state. MARKET/LIMIT is then evaluated in two stages.

The **primary score belongs to Weekly** and uses:

- Weekly trend strength;
- selected-candidate utility;
- number of independent confirmations;
- raw market momentum alignment;
- an explicit penalty for overextension.

The Daily EUR/USD engine remains independent. Its score is **not** a primary weight in WES. Daily can only modify execution timing within bounded limits:

- aligned Daily evidence adds at most `+0.08`;
- weak or unavailable Daily contributes `0`;
- moderate opposed Daily subtracts at most `-0.10`;
- strongly opposed Daily (`|score| >= 55`) may veto MARKET.

Daily therefore cannot change the Weekly thesis or become a second Weekly decision engine. It can only make WES more or less willing to execute that already-authorized Weekly thesis immediately.

A passing Weekly-primary score plus bounded Daily modifier may select `market_now`. A weaker or more extended setup remains `limit_pullback`.

MARKET retains hard vetoes. Weekly itself must align with the authorized WES direction. Clearly opposed market momentum, excessive overextension, missing directional admission, immediate post-stop chase, or a very strong opposed Daily signal block MARKET. Every MARKET fill must still be a fresh completed post-decision 5-minute close verified by EPE.

### Persistent frozen LIMIT plan

A valid pullback LIMIT no longer disappears merely because the original 60-minute authorization window elapsed while the same thesis remained valid.

Repeated WES cycles may **reaffirm** the same pending plan when the exact method/direction remains execution-authorized and still clears the base raw, utility, confirmation and directional-admission gates. Reaffirmation may extend `expires_at`, but it may **never change the frozen target price or move the limit toward the market**.

A still-valid LIMIT may promote one-way to MARKET when the same thesis later reaches the composite MARKET score. If the thesis changes, execution authority disappears, or liveness exceeds the bounded reaffirmation gap, the plan fails closed instead of being silently recreated.

The normal entry-control path still follows the 5-minute governed exposure/risk chain, and `Weekly Engine Star` keeps its independent 15-minute fallback schedule. A separate WES cycle watchdog checks persisted preflight/postflight freshness during the active week and dispatches recovery if a full cycle becomes stale.

A stop-loss reversal keeps the additional pullback requirement and completed-bar delay before execution can become eligible.

## Risk execution reliability

SL/TP execution is a separate safety authority inside TR-05 and does not depend on the hourly strategy lifecycle.

The canonical monitor is `scripts/audit_intraday_risk_exits.py`, scheduled by `.github/workflows/investments-exposure-watch.yml` every five minutes, 24 hours a day and seven days a week. This matters because BTC/USD trades through weekends.

The contract is:

- SL and TP must already be frozen in `risk_plan`; the monitor cannot move or create thresholds.
- Every run replays market evidence from the time the risk plan became active, rather than only checking the current quote. A delayed scheduler therefore cannot silently lose a transient threshold touch.
- BTC/USD uses Coinbase Exchange 5-minute candles plus the live ticker as the primary execution evidence. Yahoo Finance 5-minute BTC data is used only when Coinbase evidence is unavailable.
- EUR/USD and S&P 500 futures use 5-minute Yahoo market bars.
- If SL and TP are both present in the same bar, the existing conservative rule executes SL first.
- The paper exit price is the already-frozen SL or TP level, not a later observed quote.
- Missing or unusable market data never means that the threshold was not touched. The position remains unresolved/open and a later run retries the full frozen-risk interval.
- This safety path has no entry authority and does not run the strategy tournament. Re-entry remains governed by WES admission and re-entry rules after the risk exit is persisted.

The full maintenance lifecycle calls the same canonical risk monitor. There is therefore one SL/TP interpretation, not a fast implementation and a different slow implementation.

## Why an inverse signal is tested separately

A negative result for a short method does not prove that the corresponding long method is profitable. Transaction costs, timing, stop-losses, take-profits and asymmetric price behaviour can make both directions unprofitable. Therefore `base_v2` and `inverse_v2` remain independent research candidates and receive separate walk-forward results. Under WES 1.2, `inverse_v2` is a Challenger/Shadow method and cannot execute unless explicitly promoted.

## Candidate methods

The strategy tournament evaluates:

1. `base_v2` — direction from the saved daily trend, momentum and breakout model.
2. `inverse_v2` — the opposite direction, tested as a separate Challenger/Shadow hypothesis; research-only under WES 1.2 until governed promotion.
3. `weekly_trend` — direction from weekly EMA, momentum, breakout and candle structure.
4. `daily_weekly_blend` — weighted combination of daily and weekly scores.
5. `ema_mean_reversion` — controlled counter-trend response to distance from daily EMA20 measured in ATR.
6. `macro_weekly_blend` — bounded blend that incorporates approved macro context where configured.

The tournament always produces a ranked research candidate, but the admission layer may still return `NO_TRADE`. A default/tie direction never overrides the NO_TRADE gate.

## Learning

Learning uses only closed earlier paper legs. For each instrument and market regime, the system records:

- selected method and direction;
- entry and exit;
- gross result;
- estimated transaction cost;
- net result;
- weekly regime;
- frozen risk plan.

Historical method performance is shrunk toward zero, capped and combined with a small exploration bonus. Contextual learning can also use frozen counterfactual candidate outcomes to reduce exploration uncertainty, while realized selected-strategy legs remain the source for the legacy global performance adjustment. The system does not increase notional after losses, rewrite closed history or optimise weights on the current bar.

## Daily analysis

At entry, re-entry and during the scheduled daily review, the weekly file stores:

- current open direction;
- selected method and direction;
- daily v2 score;
- weekly score and regime;
- learning adjustment;
- current mark and unrealised result when available;
- a short PL and EN explanation.

The latest review is shown in the public `Pozycje tygodniowe / Weekly positions` card.

## Validation

`backtest_multi_strategy_v4.py` performs a separate walk-forward comparison for every instrument and every candidate method. Method selection at a simulated date can use only outcomes from earlier dates. Costs and frozen ATR SL/TP levels are included.

The historical report approximates one selected trade per week with daily OHLC data. It does not reconstruct every live same-week 5-minute re-entry, so it must not be presented as live validation or a guarantee of profit.

## TR-05 — continuous weekly positions (2026-11-11)

The original ISO-week forecast remains a historical research partition, **not** the forced close of a newly admitted WES position. New WES-authorized EURUSD, BTC/USD and S&P 500 paper entries have an actual-fill plus **seven calendar days** frozen holding deadline, so the position may remain open overnight, through weekends and into the next ISO week. It is always shorter than a month. Existing positions are **not retroactively migrated**; a legacy Friday close or earlier qualified re-entry keeps its own frozen contract.

The Friday-only veto, rising late-week trigger hurdles, time-compressed Friday TP and Wednesday-to-Friday SL/TP re-entry ban are removed for new WES entries. A same-week re-entry still requires a new qualifying Champion thesis; event/thesis invalidation may not be auto-waived. Executable market availability, exact frozen EPE entry proof, valid source timestamps, minimum two independent direction confirmations and hard SL/TP remain mandatory. A carried open leg blocks a duplicate entry for the same instrument in the next ISO week.

The independent 5-minute hard-risk monitor now audits every still-open weekly ledger in its bounded scan window, including a prior-week carried position. The hourly lifecycle settles a carried leg at its verified personal deadline without inventing a future fill. The deadline verifier accepts new carry only when the exact authorized entry, direction, decision ID and actual timestamp agree.

## W46 EUR/USD — invalid settled chronology, publication-only NO TRADE

W46 contained a Friday SHORT fill on 2026-11-13 23:02:32 Europe/Warsaw, but an earlier scheduled Friday close at 22:00:00 was subsequently assigned to it. A close before an actual fill cannot establish any P/L. This sealed historical fact must never be backdated, deleted or reclassified in the immutable execution ledger. Its *public* trading-room card uses an explicit `no_trade_invalid_settlement` correction, shown as NO TRADE, without the invalid P/L, and the public aggregate/history excludes this artifact. The consumer independently verifies the `exit_before_entry` defect before applying the correction. WES settlement now freezes valid +7d carry before due settlement and disallows any deadline or sampled exit bar earlier than the entry. New valid records are unaffected. Impossible pre-entry settlements also cannot influence learned strategy performance: live learning iterators reject the invalid leg and derived memory assigns grade D without training eligibility, preserving the original source facts.

## EUR/USD Weekly WES — actively lock large profits at D1 bands and MFE giveback (2026-11-24)

Previous WES H1 snapback exits were wired into the lifecycle `auto/close` modes, whereas main `ensure-exposure` and 5-minute risk runtime did not call that branch; `wes_risk_observatory.py` also recorded giveback **OBSERVE_ONLY**, not executable action. This explains a missing early realization path even though the immutable TP was distant. It does NOT prove the paper book actually observed a 100-pip favorable move: the persisted W48 snapshot on 2026-11-23 10:48 CET only records an open short from 1.09841835, fixed TP 1.08191835, stop 1.10602325, and no auditable MFE thereafter.

New `wes_eurusd_profit_protection.py` runs inside the **canonical SL/TP-first, 5-minute risk audit**, independent of the WES 15-minute entry lifecycle; only `eurusd` and only an authorized open WES position. For source-verified completed 5-minute candle CLOSES since actual entry, record confirmed as-of MFE; the previous daily Bollinger band is computed from prior completed 30 daily closes at 2.5σ. Profit may be realized immediately when near the adverse outer D1 band (short lower, long upper), with net current gain >=65 pips and prior net MFE >=65 pips. Otherwise close when a previously realized-in-the-data 5m closing MFE >=65 pips net gives back >=18–32 pips according to a 28% peak fraction while keeping >=4 pips net at present. H1 MA30 is recorded for diagnosis, not promoted to a separate signal.

Fills are never backdated; the exit is the latest COMPLETED, fresh 5m close, not the earlier wick, MFE, or historic touch. SL/TP replay wins, original risk and Champion/EPE remain immutable, missing data prevent optional exits, and other instruments are unchanged. Costs are subtracted for policy comparisons. Frozen 7-day max holding stays intact. This rule needs paired forward tracking against a no-profit-lock Challenger to establish whether fewer reversals outweigh profits lost by exiting strong trends.

## EUR/USD H1 Bollinger 30/2.5 — fast reentry timing and profit protection (2026-11-23)

EUR/USD WES now consumes a verified fast outer-band reentry from the existing Yahoo 1h MA-structure feed. A completed hour must materially pierce its then-known Bollinger 30/2.5 band, and a subsequent completed H1 (within two hours) must clearly return inside and reverse hourly momentum. This is not a standalone new LONG/SHORT thesis or evidence of certain reversal. A confirmed LOWER_FAST_REENTRY pauses a new EUR/USD SHORT entry; UPPER_FAST_REENTRY pauses LONG. Previously frozen touches may only be vetoed when the snapback existed by that touch's source timestamp; never rewrite earlier fills.

After hard SL/TP check, a profitable existing EUR/USD WES position may close via `wes_h1_bollinger_snapback_profit_exit` only on the latest fresh **completed 5m** bar after the completed H1 signal, if the candidate remains directionally opposed, the original entry predates the breach, gross profit reaches 0.20R and net gain after configured EUR/USD spread/round-trip cost is at least 6 pips. No change to existing SL/TP, Champion, 7-day deadlines or trade history. This does not affect BTC/USD and S&P 500 futures. The independent Daily EUR/USD engine uses completed private Saxo H1/1m candles and the same pure calculation but retains its separate Belief/EPE/exit control.

## TR-05 / LE-08 / LE-09 — recursive blocker review (RSI)

Every 15-minute WES cycle now writes `data/investments/wes_blocker_learning.json` via `scripts/wes_blocker_watchdog.py` in **OBSERVE_ONLY** mode; the independent ten-minute `WES Cycle Watchdog` also invokes the same diagnostic and requests a WES recovery dispatch if an active pending authorization is paired with a stale source, a legacy time veto persists, a current instrument is missing, or an obsolete ECB block remains latched. Every Friday the report explicitly marks any instrument whose trading week finished without an executed/carry position.

For each instrument the review captures actual trades/carry, directional candidate, confirmations, raw score, admission status, EPE evidence, monitoring lock type, source age, central-bank status and a bounded history of recorded no-trade causes. The monitor asks if the block prevented a known bad trade, but **does not claim benefit** without prospective point-in-time rejected-candidate outcomes, costs and out-of-sample verification. It records research hypotheses only; no automatic threshold promotion, direction creation, synthetic P/L or trading authority. Legitimate abstention remains allowed. Only the identified obsolete ECB Weekly time/event gates may be lifted by a separately governed, deterministic lifecycle policy. Unresolved Fed and EURUSD Daily controls retain their prior protection.



The diagnostic must read the current, time-valid `wes_entry_authorization.candidate` after governed approval, not obsolete `wes_trigger_candidate` monitoring evidence from a prior rejection. A pending execution with a live admission is not a missing direction; a pending execution after authorization expiry is a recoverable safety incident, never permission to fill retrospectively.

This change is experimental paper trading only and **not a claim that a changed rule will be profitable**. Follow-up should measure paired WES Champion vs historical baseline on prospective episodes with cost-adjusted performance, drawdown and false-positive/false-negative blocker rates before any wider automatic relaxation.


## TR-05 / TR-03 — Bollinger extreme favorable excursion profit realization (2026-11-25)

**REDUCE_EXPOSURE ≠ REVERSE_TREND.** The pure `assess_profit_extension` in `scripts/eurusd_h1_bollinger_snapback.py` evaluates a current completed mark against previously known Bollinger 30/2.5. SHORT below lower and LONG above upper: depth must reach max(5 pips, 10% band width = 0.5 sigma, and for H1 0.5 ATR14). H1 uses complete prior hours, D1 prior completed days, never a forming reference candle or historical wick fill. No rebound, countertrend momentum, MFE giveback or reversal thesis is required; the signal has no direction/entry authority.

**Daily:** `daily_eurusd_range_exit.assess_extreme_profit` in canonical v14 evaluator after hard SL/TP/time and before snapback/R_PACE. H1 from own completed 1m data; latest completed minute ≤120 seconds; exit-side profit ≥max(10 pips, 0.50R). **Weekly:** common 5m risk watch `wes_eurusd_profit_protection.assess` (V2) before existing D1-touch and MFE-giveback, H1 or D1, fresh completed 5m mark ≤6 minutes, net profit ≥max(20 pips, 0.50R). Earlier observations, including pre-entry, may supply the H1 reference only, never a historical fill. Existing hard-risk priority and frozen SL/TP, BTC, SPX, direction unchanged. Initial thresholds are experimental and require prospective evaluation.

The recommendation contract `exposure_reduction` explicitly records `requested_reduction_fraction=1.0`, `partial_execution_supported=false`. The current ledger only supports full CLOSE; a fractional request cannot silently close the whole position. Future scale-outs require a separately governed quantity-aware migration (remaining quantity, partial fills, realized/unrealized P/L, costs, idempotency, notifications). Never opens an opposite position.


### TR-03 / TR-05 — Bollinger Dynamics Engine H1 V1 (2026-11-25)
The existing pure `scripts/eurusd_h1_bollinger_snapback.py::assess_dynamics` evaluates completed H1 SMA30 ±2.5σ: 3-hour slopes of both bands, MA30 and bandwidth (pips/hour), acceleration versus the previous 3 hours, percentage bandwidth change and %B. Regimes: EXPANSION, COMPRESSION, EXHAUSTION, TREND_UP/DOWN, CONSOLIDATION, TRANSITION. EXHAUSTION requires opposing contraction following expansion in a contiguous up-to-48h window, a linked outer-edge excursion and >=4 pip completed-hour recovery. Missing/stale/future observations or recent 6h gaps produce no extra action; no gap interpolation. Earlier market sessions may contribute to the rolling 30 observations.

**Consumers:** Daily H1 admission and WES runner/v5 add an exhaustion no-chase veto only near the original favorable outer edge (short %B<=0.35, long %B>=0.65). A correction toward MA30 is not vetoed by this new rule; all other gates remain. WES never applies this veto to a touch preceding the signal. The existing SL/TP-first Daily/Weekly profit evaluators may reduce existing exposure after a post-entry expansion excursion and verified compression/recovery still present at the current fresh completed 1m/5m mark, with >=0.35R and >=10 pips Daily exit-side profit / >=20 pips Weekly net. Extreme extension can still take profit without a rebound. Expansion and other regimes are context, not authority to suppress existing exits. Evidence is included in existing H1 and risk-audit records, Daily runtime fingerprint includes the shared calculator. No new source, direction, independent confirmation, risk mutation, partial fill or other instrument change. Thresholds are starting hypotheses for prospective cost-adjusted validation.
