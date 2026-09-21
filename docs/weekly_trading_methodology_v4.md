# BriefRooms weekly paper methodology v4

## Status

The filename is retained for historical continuity, but the current governed runtime is newer than the original v4 exposure rule. Runtime authority lives in `data/investments/multi_instrument_exposure_policy.json` (currently policy v5.7.0) together with `data/investments/wes_methodology.json` (WES 1.1.0).

The system remains experimental governed paper trading only. It places no broker orders.

## NO_TRADE and exposure

A weekly position is **not mandatory**.

Current policy explicitly sets:

- `mandatory_monday_position = false`;
- `continuous_position_required = false`;
- `NO_TRADE` is a first-class active state.

The tournament may rank a directional candidate, but ranking alone does not authorize a position. EUR/USD, S&P 500 futures or BTC/USD is opened only when the current admission gates qualify the candidate. Otherwise WES remains in `NO_TRADE` and continues monitoring for a later qualified trigger.

After a governed close, re-entry is not automatic merely because a position slot is empty. Re-entry requires the applicable WES trigger, validation and lifecycle rules. Monday/Tuesday early-close replacement may use the separately governed rolling seven-calendar-day path; material-event exits remain fail-closed and are not automatically replaced.

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

## WES 1.1 — Directional Admission & Champion/Challenger Hardening

WES 1.1 separates research ranking from execution authority. A candidate can be useful for learning without being allowed to open a paper position.

Every new entry, including the initial Monday entry, must pass all of the following:

- the selected method belongs to the execution-authorized Champion pool;
- the exact method and direction have a current WES authorization;
- the authorization is not expired and is valid for that exact candidate;
- the direction has at least two independent confirmations;
- a candidate that opposes aligned, valid Daily and Weekly signals is rejected;
- opposing execution-authorized candidates inside the configured utility margin resolve to `NO_TRADE`.

Method names are never used to resolve a directional tie. Determinism for same-direction candidates comes from an explicit policy priority after utility and absolute signal strength; opposing near-ties fail closed.

`inverse_v2` remains fully calculated for research, counterfactual outcomes, contextual learning and Challenger evaluation, but it is `challenger_shadow` and has no execution authority. It can obtain execution authority only through an explicit governed promotion that changes policy.

The WES preflight is the sole admission authority. The v5 runtime independently verifies the WES authorization before creating any new entry, so a workflow that bypasses preflight cannot silently open a position.

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

The hourly/full WES lifecycle calls the same monitor. There is therefore one SL/TP interpretation, not a fast implementation and a different slow implementation.

## Why an inverse signal is tested separately

A negative result for a short method does not prove that the corresponding long method is profitable. Transaction costs, timing, stop-losses, take-profits and asymmetric price behaviour can make both directions unprofitable. Therefore `base_v2` and `inverse_v2` remain independent research candidates and receive separate walk-forward results. Under WES 1.1, `inverse_v2` is a Challenger/Shadow method and cannot execute unless explicitly promoted.

## Candidate methods

The strategy tournament evaluates:

1. `base_v2` — direction from the saved daily trend, momentum and breakout model.
2. `inverse_v2` — the opposite direction, tested as a separate Challenger/Shadow hypothesis; research-only under WES 1.1 until governed promotion.
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
